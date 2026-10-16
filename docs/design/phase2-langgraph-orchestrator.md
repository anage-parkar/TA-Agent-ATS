# Phase 2 design note — LangGraph orchestrator

**Revision 2 (2026-10-15).** Decisions 1–4 confirmed with conditions; the
conditions are folded into the sections below and marked **[condition]**.
Design only — **no implementation until this revision is reviewed.**

## What changes

Today the pipeline is a chain of discrete, human-triggered API calls that
mutate the application directly. Phase 2 makes the per-application state
machine explicit as a LangGraph graph with **one thread per application**
(`thread_id = application_id`), persisted in a tenant-scoped Postgres
checkpointer, where every human decision is a graph **interrupt** and every
external wait is an **event** delivered by Celery.

```
ingest ─► score(AI) ─► review_gate ⏸ ─► draft_outreach(AI) ─► send_gate ⏸ ─► await_reply ⏳
                                                                                    │
closed ◄─ scheduled ◄─ interview_gate ⏸ ◄─ classify(AI) ◄──────────────────────────┘
   ▲            (reject is possible at every ⏸ gate; ⏳ = waits for a Celery-delivered event)
```

Existing endpoints keep their URLs and RBAC; internally each becomes "resume
this thread with a command". The UI does not change in Phase 2.

## Confirmed decisions

| # | Decision | Conditions (where addressed) |
|---|---|---|
| 1 | Postgres checkpointer, not Redis | full-history erasure; pinned package; never run built-in `setup()` as `app_user` (§3) |
| 2 | One thread per application | graph versioning; policy for threads paused across deploys (§4) |
| 3 | Send approval is its own gate | idempotency key recorded before any Gmail call; combined UI action records two approvals (§5) |
| 4 | Rollout flag per job | frozen per application at thread start; global kill switch for event delivery (§6) |
| + | | per-thread advisory lock (§2); gate checks use the resuming principal, with the system-resumes-human-thread test (§2); state holds references, not PII (§3) |

## 1. The rejection gate as a LangGraph interrupt

- A gate node calls `interrupt(payload)`; the thread pauses and the checkpoint
  is persisted. The payload is a *reference* (`application_id`, gate name,
  `graph_version`) — the review context the UI shows (scores, evidence, draft)
  is assembled at read time from the repository, never stored in the
  checkpoint (§3).
- The thread resumes **only** through `Command(resume={"decision": ..., "note": ...})`
  issued by an API route. Bulk reject is N resumes under the same principal.
- **The interrupt is the flow mechanism; the repository gate stays the
  enforcement.** The node that applies the decision calls
  `repository.update_application(status="rejected")`, which raises
  `HumanActorRequired` unless a human principal is bound *at resume time*. A
  Celery task, a replay, or a bug that resumes a gate programmatically cannot
  reject: the write fails, the node errors, the thread returns to the
  interrupt. Nothing in graph state ever confers authority.
- **Idempotency rule:** LangGraph re-executes a node from its start on resume,
  so a gate node has no side effects before `interrupt()`; the decision write
  + audit happen after it returns, as the node's single mutation.
- No timeout ever resumes a gate (Workstream C). Celery may emit a
  `review.overdue` reminder event; it never decides.

## 2. Which principal graph runs execute under

| Entry | Principal | Example |
|---|---|---|
| API start/resume | the request's human principal | recruiter clicks Proceed |
| Celery event delivery | `use_system(tenant_id, actor="orchestrator")` | reply arrived, reminder due |
| unbound / default fallback | **refused** — the runner asserts a principal is bound | a script forgetting to bind |

- **Gate checks use the resuming principal, never the thread starter
  [condition].** State records `started_by` for context only. After
  `interrupt()` returns, the node reads `get_principal()` — the principal of
  the *resume* call — and that is what the repository gate and the audit row
  see. Required tests, written before the gate node:
  - **T1** a human starts a thread via the API and it pauses at `review_gate`;
    the system (`use_system`) resumes it with `decision=reject` →
    `HumanActorRequired`; the thread is still interrupted at `review_gate`, the
    application status is unchanged, and an `orchestrator.reject_refused` event
    is recorded with `actor_type=system`.
  - **T2** a *different* human resumes with reject → applied; the audit row's
    actor is the resuming human, not the starter.
  - **T3** the system resumes with a non-adverse event (`reply.received`) →
    succeeds, audited to `system:orchestrator`.
- Contextvars do **not** cross LangGraph's executor threads (the Workstream B
  gotcha), so the runner passes the principal in `config["configurable"]` and
  every node is wrapped by a decorator that rebinds `use_principal(...)` for
  the node's duration. Nodes never read the principal from state.
- The runner refuses to resume a thread whose checkpoint metadata `tenant_id`
  differs from the configurable's tenant (belt; RLS is braces).
- **Per-thread advisory lock against concurrent resumes [condition].** The
  resume transaction takes `pg_advisory_xact_lock(hashtext(thread_id))` on
  the same connection the checkpointer writes through, so lock and checkpoint
  commit together and release together; no lock/commit race, no second
  system. `lock_timeout` is short (5 s): a second resumer gets HTTP 409
  `thread busy` (Celery retries with backoff). In-memory mode mirrors it with
  a per-thread `threading.Lock` registry. Works under Supabase's transaction
  pooler because the lock is transaction-scoped.
- AI nodes keep recording `ai_decisions` with `actor_type="ai"`; RBAC stays at
  the route; the graph trusts the bound principal.

## 3. Tenant scoping of the checkpointer (Postgres)

Checkpoints are inside the same RLS boundary and lifecycle as every other
candidate row. Redis remains Celery's broker only.

- **Pinned package [condition]:** `langgraph-checkpoint-postgres==3.2.0`
  (latest on 2026-10-15), pinned together with `langgraph==1.2.4` and
  `langgraph-checkpoint==4.3.0` (what 3.2.0 requires; PR 1 verified the
  three resolve and import together). `langgraph-checkpoint-redis` is removed.
- **Never run the built-in `setup()` as `app_user` [condition].** Migration
  `0011_checkpointer.sql` owns the DDL: the three tables (`checkpoints`,
  `checkpoint_blobs`, `checkpoint_writes`) vendored from the pinned version's
  `setup()` with the package version in the migration header, plus
  `tenant_id uuid not null default nullif(current_setting('app.tenant_id', true), '')::uuid`,
  **FORCE** RLS with the tenant policy, and DML-only grants to `app_user`.
  `TenantPostgresSaver.setup()` is overridden to raise ("schema is managed by
  migrations"). `scripts/verify_rls.py` asserts
  `has_schema_privilege('app_user', 'public', 'CREATE') = false` and that the
  checkpoint tables are RLS-isolated like the domain tables. A CI test compares
  the vendored DDL's version marker with the installed package: upgrading the
  pin without a new migration fails the build.
- `TenantPostgresSaver(PostgresSaver)` acquires connections through
  `db/supabase_client.tenant_connection()`, so every read/write is RLS-scoped;
  a guessed `thread_id` from another tenant returns nothing; unbound sees zero
  rows (fail-closed). Tenant namespacing is RLS, not string prefixes.
- **Erasure deletes the full thread history [condition].** `erase_candidate`
  → for each of the candidate's applications, delete *every* row in
  `checkpoint_writes`, `checkpoint_blobs` and `checkpoints` for that
  `thread_id` — all namespaces, all checkpoint ids, not just the latest — in
  the same transaction as the candidate delete. `delete_tenant` and retention
  do the same (RLS-scoped delete with no WHERE); `export_tenant` /
  `export_candidate` include the thread rows (DSAR). Test: write three
  checkpoints to a thread, erase the candidate, assert zero rows in all three
  tables and `get_tuple()` returns `None`.
- **Graph state holds references, not PII [condition].** The state schema is:

  ```
  application_id · candidate_id · job_id · tenant_id · graph_version · started_by
  status · last_event_id · orchestrated (frozen flag, §6)
  score_summary {overall, passed_knockouts, below_threshold}
  decisions [{gate, outcome, by, at, event_id}]
  outreach {email_id, idempotency_key, send_status}
  interview {interview_id}
  ```

  The redacted profile, evidence text, draft bodies and replies live in the
  repository (`ai_decisions`, `emails`, `resumes`) and are loaded by id inside
  nodes; interrupt payloads shown to the UI are assembled at read time. A
  test serialises a checkpoint and asserts it contains no email address, name
  or draft text (reusing the `services.redaction` patterns as the detector).
  Consequences: checkpoint erasure is defence in depth, migrations stay small,
  checkpoints stay tiny.
- Local dev / tests: `MemorySaver` behind the same metadata tenant check.

## 4. One thread per application — versioning and deploys

- **Graph versioning [condition].** `GRAPH_VERSION` (integer) lives in
  `orchestrator/graph.py`; it is written into thread metadata at start and
  into state; it is bumped whenever node names, edges, the state schema or an
  interrupt-payload contract change. Node names are stable identifiers.
- **Policy for threads paused across deploys [condition].**
  1. A deploy never touches checkpoints; nothing is migrated in bulk.
  2. On resume, if `state.graph_version < GRAPH_VERSION`, registered forward
     migrations `migrate_state(v → v+1)` run in sequence inside the resume
     transaction, under the resuming principal, audited as
     `orchestrator.thread_migrated {from, to}`.
  3. A thread interrupted at a node that no longer exists must be mapped by
     the migration (`node_map`) to its successor gate; the migration is
     rejected at import time if it leaves any gate unmapped.
  4. If no migration path exists, the thread is **stranded, never dropped**:
     `human_review_requested=true`, event `orchestrator.thread_stranded`, a
     "legacy flow" badge in the review queue, and the direct-mutation
     endpoints remain available for that application.
  5. Pre-deploy report: `GET /api/admin/orchestrator/threads?paused=true`
     groups paused threads by `graph_version` and node, so a deploy that would
     strand threads is visible before it ships. A CI test asserts every
     version from 1 to `GRAPH_VERSION` has a migration path or an explicit
     `NO_MIGRATION` entry (which forces stranding and is flagged in review).

## 5. The send gate

- **Idempotency key recorded before any Gmail call [condition].** On approval
  the node first inserts the `emails` row with `direction=outbound`,
  `status=pending` and `idempotency_key = "idem:{application_id}:send_gate:{approval_event_id}"`
  (prefixed so it validates as an id in graph state — PR 1's `ID_PATTERN`)
  (unique per tenant), commits, and only then calls Gmail with a deterministic
  `Message-ID: <{sha256(key)[:32]}@{outreach domain}>` header; success updates
  `status=sent` + `thread_id`. On retry or replay: key `sent` → skip;
  key `pending` → look the Message-ID up in the mailbox; found → mark sent,
  not found → send. The approval event id comes from the resume command, so
  one approval can never send twice; re-approving after an edit is a new key.
- **Combined UI action records two approvals [condition].** The UI may offer
  "Proceed and send" as one click. The server then issues two resumes under
  the same principal — `review_gate` with `proceed`, then `send_gate` with
  `approve` + the body — and writes two audit events,
  `application.decided` and `outreach.approved`, with the same actor and a
  shared `action_group_id`. The graph keeps two gates; "no email without
  recruiter approval" stays a distinct, auditable fact.

## 6. How Celery and the graph divide responsibilities

| | Graph | Celery |
|---|---|---|
| Owns | per-application decisions and sequence | time and the outside world |
| Does | AI steps, human gates (interrupts), stage writes | scheduled Gmail polling, retention, reminders, running graph steps as tasks |
| Never | sleeps, polls, schedules, calls Gmail outside a node | decides, rejects, writes a stage |
| Principal | request human / `system:orchestrator` | `system:scheduler` → delivers events |

- Celery delivers typed, idempotent events — `reply.received {message_id}`,
  `review.overdue`, `interview.slot_confirmed` — through a
  `run_thread(application_id, event)` task; the per-thread advisory lock (§2)
  serialises them. Phase 1's `reply_poller` shrinks to: poll → emit
  `reply.received` per new message; the stage write moves into the graph node.
- **Rollout flag frozen per application [condition].** `orchestrated` is set
  in state at thread start from the job's flag; flipping the job flag later
  never changes an existing application. Legacy applications keep today's
  direct-mutation endpoints.
- **Global kill switch for event delivery [condition].**
  `ORCHESTRATOR_EVENTS_ENABLED` (env, read at delivery time). When false,
  `run_thread` resumes nothing: events are persisted to a tenant-scoped
  `orchestrator_events` table with `status=held` and replayed in order on
  re-enable (`POST /api/admin/orchestrator/events/replay`). Human gate resumes
  through the API keep working — the switch stops automation, not people.
- Fan-out (score N applicants) = N thread invocations as tasks, bounded by the
  LLM concurrency / rate limiter — not one mega-graph.

## Rollout

Behind the per-job flag: **2a** migration 0011 + `TenantPostgresSaver` +
versioning scaffold + advisory lock + kill switch + state schema (with the
no-PII test) + `review_gate` with tests T1–T3 · **2b** `send_gate` +
idempotent send + reply events · **2c** `interview_gate` · **2d** retire
direct mutation for orchestrated applications.

## Open points to settle in the implementation PR

- Exact version compatibility of the three pinned LangGraph packages.
- Whether `hashtext()` collisions on `thread_id` warrant `hashtextextended`
  (cosmetic: a collision only serialises two unrelated threads).
