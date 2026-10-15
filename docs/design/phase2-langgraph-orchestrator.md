# Phase 2 design note — LangGraph orchestrator

Status: **design only, no code.** Four decisions are called out at the end;
everything else follows from them.

## What changes

Today the pipeline is a chain of discrete, human-triggered API calls: score →
review → decide → draft/send outreach → (Phase 1: scheduled poll) → classify →
schedule interview. Each call mutates the application directly. Phase 2 makes
the per-application state machine explicit as a LangGraph graph with **one
thread per application** (`thread_id = application_id`), persisted in a
checkpointer, where every human decision is a graph **interrupt** and every
external wait is an **event**.

```
ingest ─► score(AI) ─► review_gate ⏸ ─► draft_outreach(AI) ─► send_gate ⏸ ─► await_reply ⏳
                                                                                    │
closed ◄─ scheduled ◄─ interview_gate ⏸ ◄─ classify(AI) ◄──────────────────────────┘
   ▲            (reject is possible at every ⏸ gate; ⏳ = waits for a Celery-delivered event)
```

The existing endpoints keep their URLs and RBAC; internally each becomes
"resume this thread with a command". The UI does not change in Phase 2.

## 1. The rejection gate as a LangGraph interrupt

- A gate node calls `interrupt(payload)` with the review context (per-dimension
  scores + evidence, `below_threshold`, `human_review_requested`, draft text at
  the send gate). The thread pauses and the checkpoint is persisted.
- The thread resumes **only** through `Command(resume={"decision": ..., "note": ...})`
  issued by an API route under the request's human principal. Bulk reject is N
  resumes under the same principal.
- **The interrupt is the flow mechanism; the repository gate stays the
  enforcement.** The node that applies the decision calls
  `repository.update_application(status="rejected")`, which raises
  `HumanActorRequired` unless a human principal is bound. So a Celery task, a
  replay, or a bug that resumes a gate programmatically cannot reject: the
  write fails, the node errors, and the thread returns to the interrupt. Nothing
  in graph state (not even `decided_by`) ever confers authority.
- **Idempotency rule:** LangGraph re-executes a node from its start on resume,
  so a gate node has no side effects before `interrupt()`. The payload is
  recomputed deterministically from state; the decision write + audit happen
  after `interrupt()` returns, in the same node, as the single mutation.
- No timeout ever resumes a gate (Workstream C). Celery may emit a
  `review.overdue` reminder event; it never decides.

## 2. Which principal graph runs execute under

Three entry points, and nothing else:

| Entry | Principal | Example |
|---|---|---|
| API start/resume | the request's human principal (`bind_request_context`) | recruiter clicks Proceed |
| Celery event delivery | `use_system(tenant_id, actor="orchestrator")` | reply arrived, reminder due |
| unbound / default fallback | **refused** — the runner asserts a principal is bound | a script forgetting to bind |

- Contextvars do **not** propagate into LangGraph's executor threads (the same
  gotcha Workstream B hit with `ThreadPoolExecutor`). The runner therefore
  passes the principal explicitly in `config["configurable"]` and every node is
  wrapped by a decorator that rebinds `use_principal(...)` for the node's
  duration. Nodes never read the principal from state.
- The runner refuses to resume a thread whose checkpoint metadata `tenant_id`
  differs from the configurable's tenant (belt; RLS is braces, see §3).
- AI nodes keep recording `ai_decisions` with `actor_type="ai"`; the surrounding
  principal only scopes the tenant. Human gates are audited to the human,
  Celery-driven steps to `system:orchestrator`, exactly as Phase 1 does.
- RBAC stays at the route (`application.decide`, `email.send`,
  `interview.schedule`); the graph trusts the bound principal and does not
  re-check permissions.

## 3. Tenant scoping of the checkpointer

**Recommendation: Postgres, not Redis** (the README and `requirements.txt`
planned Redis checkpoints). Checkpoints hold candidate PII — the redacted
profile, scores, draft emails — so they must live inside the same RLS boundary
and the same export / erasure / retention lifecycle as every other candidate
row. Redis would be a second PII store with no RLS and its own lifecycle.
Redis remains Celery's broker only.

- Migration `0011_checkpointer.sql` creates the three LangGraph tables
  (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`; DDL vendored from
  `PostgresSaver.setup()` so the schema is under our control) with an added
  `tenant_id uuid not null default nullif(current_setting('app.tenant_id', true), '')::uuid`,
  **FORCE** RLS with the same tenant policy, and grants to `app_user`.
- A thin `TenantPostgresSaver(PostgresSaver)` acquires connections through
  `db/supabase_client.tenant_connection()`, so the GUC is set and every read /
  write is RLS-scoped. A guessed `thread_id` from another tenant returns
  nothing; an unbound connection sees zero rows (fail-closed).
- Tenant namespacing is enforced by RLS, not by string prefixes on
  `thread_id`. `application_id` is a UUID, so threads never collide.
- Lifecycle: `erase_candidate`, `delete_tenant` and retention also delete the
  candidate's threads; `export_tenant` / `export_candidate` include them (DSAR).
  `scripts/verify_rls.py` gains a check on the checkpoint tables, so the CI job
  proves this boundary too.
- Local dev / tests: `MemorySaver` behind the same metadata tenant check,
  mirroring the repository's in-memory filter.

## 4. How Celery and the graph divide responsibilities

| | Graph | Celery |
|---|---|---|
| Owns | per-application decisions and sequence | time and the outside world |
| Does | AI steps, human gates (interrupts), stage writes | scheduled Gmail polling, retention, reminders/SLAs, running graph steps as tasks |
| Never | sleeps, polls, schedules, calls Gmail | decides, rejects, writes a stage |
| Principal | request human / `system:orchestrator` | `system:scheduler` → delivers events |

- Celery **delivers typed, idempotent events** into the graph —
  `reply.received {message_id, thread_id}`, `review.overdue`,
  `interview.slot_confirmed` — via a `run_thread(application_id, event)` task.
  Classification stays an AI node inside the graph. Phase 1's `reply_poller`
  shrinks to: poll → emit `reply.received` per new message; the stage write
  moves into the graph node, so there is one place that mutates stage.
- One task per thread at a time (Redis lock keyed on `application_id`) so two
  events cannot resume the same thread concurrently.
- Fan-out (score N applicants) = N thread invocations dispatched as tasks,
  bounded by the existing LLM concurrency / per-tenant rate limiter — not one
  mega-graph.
- API requests that start or resume a thread run the step inline when it is
  short (a gate decision) and hand off to Celery when it is long (scoring,
  drafting), returning the thread's new status immediately.

## Rollout

Behind `ORCHESTRATOR_ENABLED` (per job): new applications on enabled jobs get a
thread; legacy applications keep today's direct-mutation endpoints until cut
over. Slices: **2a** checkpointer + RLS + review gate · **2b** outreach send
gate + reply events · **2c** interview gate · **2d** retire direct mutation.

## Risks

- LangGraph 1.x API surface (`interrupt`, `Command`, checkpointer internals)
  — pin versions; add `langgraph-checkpoint-postgres`, drop
  `langgraph-checkpoint-redis`.
- Double side effects on node retry — every external call goes through an
  idempotency key (message id, application id + step).
- Interrupts that wait weeks — fine on Postgres; checkpoint size is bounded
  because state holds references (ids) plus the redacted profile, not raw
  resumes.

## Decisions needed

1. **Postgres checkpointer instead of the planned Redis one** (recommended).
2. **One thread per application** (recommended) rather than per job.
3. **Send approval stays a separate gate** from the review decision
   (recommended; keeps "no email without recruiter approval" explicit).
4. **Rollout flag per job** (recommended) rather than per tenant.
