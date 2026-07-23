# NFL Analytics Hub v4.2 — Distributed Intelligence Platform

v4.2 moves long-running model, simulation, scouting, backfill, and report work behind stable
job contracts that can scale beyond one web process. The public contract stays
provider-neutral while Redis and external workers are introduced incrementally.

## Delivery phases

1. **v4.2.0 Job foundation** — deterministic idempotency, bounded job payloads, explicit
   lifecycle transitions, retry limits, provider-neutral event envelopes, and versioned APIs.
2. **v4.2.1 Redis transport** — Redis Streams queues, consumer groups, worker leases,
   acknowledgements, stale-lease recovery, and in-memory development fallback.
3. **v4.2.2 Distributed execution** — background model, simulation, scouting, backfill, and
   report workers with typed handlers, timeouts, cancellation, and result persistence.
4. **v4.2.3 Cache and operations** — namespaced distributed cache keys, invalidation events,
   queue depth and latency metrics, dead-letter inspection, health checks, and horizontal
   scaling guidance.

## v4.2.0 endpoints

- `GET /api/v4.2/capabilities`
- `POST /api/v4.2/jobs/normalize`
- `POST /api/v4.2/jobs/transitions/validate`
- `POST /api/v4.2/jobs/events/normalize`

## v4.2.0 job foundation

- Deterministic job IDs derived from namespace, job type, and idempotency key
- Content-derived idempotency keys when callers do not supply one
- Payload digests for conflict detection and auditability
- Explicit queued, running, succeeded, failed, and cancelled states
- Validated state transitions with bounded retries and required worker claims
- JSON-safe payload and result validation with 256 KB limits
- Deterministic, inspectable event IDs and monotonic caller-supplied sequences
- Bounded in-memory reference registry for tests and single-process development

## v4.2.1 endpoints

- `GET /api/v4.2/transport/capabilities`
- `POST /api/v4.2/transport/leases/normalize`

## v4.2.1 Redis Streams transport

- Redis Streams queue with atomic, conflict-safe idempotent enqueueing
- Consumer-group creation and bounded batch claims
- Explicit, deterministic worker leases with configurable expiry
- Terminal-state acknowledgements restricted to the lease owner
- Stale pending-message recovery through `XAUTOCLAIM`
- Retry-aware recovery that never bypasses the v4.2.0 attempt limit
- Thread-safe in-memory fallback with the same enqueue, claim, acknowledgement, and recovery
  semantics for local development
- Redis selection through `REDIS_URL`; configured Redis failures remain visible instead of
  silently switching a production workload to process-local memory
- Redis integration tests use the existing CI Redis 7 service

## v4.2.2 endpoints

- `GET /api/v4.2/execution/capabilities`
- `POST /api/v4.2/execution/jobs/validate`
- `POST /api/v4.2/execution/cancellations/normalize`

## v4.2.2 Distributed execution

- Static, allowlisted handlers for model projection, seeded simulation, scouting analysis,
  historical backfill, and scouting-report generation
- Per-handler payload validation and bounded default or caller-reduced deadlines
- A transport-neutral worker that claims, executes, persists, and acknowledges v4.2 jobs
- Cooperative cancellation checkpoints for all handlers and a POSIX hard-timeout guard when
  the worker runs on its main thread
- Idempotent terminal-result persistence before queue acknowledgement
- Redis-backed production result and cancellation storage with a thread-safe in-memory
  development fallback
- Configured Redis failures remain visible instead of switching production execution to
  process-local memory
- No caller-selected module, function, command, or executable can be dispatched

## Guardrails

- Existing v3.x, v4.0, and v4.1 contracts remain unchanged.
- v4.2.0 defines contracts, v4.2.1 adds transport, and v4.2.2 adds typed execution.
- Arbitrary functions, modules, shell commands, and caller-supplied code are never executed.
- Job and event identities are deterministic, while timestamps remain explicit.
- A reused caller idempotency key with a different payload is a visible conflict.
- Job types, namespaces, priorities, attempts, payloads, results, errors, and identifiers are
  bounded.
- Terminal jobs cannot be restarted; failed jobs can be requeued only while attempts remain.
- Redis and external workers preserve the v4.2.0 job envelope and failure semantics.
- Acknowledgements require a terminal job and matching lease owner.
- Expired leases fail the active attempt before retrying; exhausted jobs are acknowledged as
  failed instead of cycling indefinitely.
- Results are durably stored before their stream message is acknowledged, so a crash between
  those steps can replay safely.
- Handler exceptions, cancellations, and timeouts produce bounded terminal job records.
- Cooperative cancellation is checked before, during, and after built-in work; the Linux
  worker runtime additionally interrupts handlers at their deadline.

## Next increment

v4.2.3 will add namespaced distributed caching, invalidation events, queue depth and latency
metrics, dead-letter inspection, health checks, and horizontal-scaling guidance.
