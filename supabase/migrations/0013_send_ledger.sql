-- 0013_send_ledger.sql — Phase 2 / PR 3: the outreach send ledger.
--
-- Every graph-driven send is recorded in `emails` BEFORE the Gmail call, under
-- an idempotency key minted from the approval event, with the deterministic
-- RFC 5322 Message-ID we set on the message. send_status tracks the attempt:
--   pending — row committed, Gmail call not yet confirmed
--   sent    — Gmail confirmed (provider_message_id / thread_id filled)
--   unknown — ambiguous failure (timeout, 5xx): the message MAY have gone out;
--             resolved by looking the Message-ID / idempotency header up in the
--             mailbox before any re-approval may mint a new key
--   failed  — definite failure (4xx): safe to resend under the same key
-- The legacy send path (routers/emails.send_email) does not use these columns.

alter table emails add column if not exists idempotency_key text;
alter table emails add column if not exists message_id text;           -- the Message-ID header we set
alter table emails add column if not exists provider_message_id text;  -- Gmail's own message id
alter table emails add column if not exists send_status text;          -- pending | sent | unknown | failed
alter table emails add column if not exists last_error text;
alter table emails add column if not exists resolve_attempts integer not null default 0;  -- mailbox lookups so far
alter table emails add column if not exists last_lookup_at timestamptz;

alter table emails drop constraint if exists emails_send_status_check;
alter table emails add constraint emails_send_status_check
  check (send_status is null or send_status in ('pending', 'sent', 'unknown', 'failed'));

-- One attempt per key per tenant: the key is the idempotency boundary.
create unique index if not exists emails_tenant_idempotency_key_uidx
  on emails(tenant_id, idempotency_key) where idempotency_key is not null;

create index if not exists emails_unresolved_sends_idx
  on emails(application_id) where send_status in ('pending', 'unknown');
