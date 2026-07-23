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

## Guardrails

- Existing v3.x, v4.0, and v4.1 contracts remain unchanged.
- v4.2.0 defines contracts and validation; it does not claim distributed execution.
- Arbitrary functions, modules, shell commands, and caller-supplied code are never executed.
- Job and event identities are deterministic, while timestamps remain explicit.
- A reused caller idempotency key with a different payload is a visible conflict.
- Job types, namespaces, priorities, attempts, payloads, results, errors, and identifiers are
  bounded.
- Terminal jobs cannot be restarted; failed jobs can be requeued only while attempts remain.
- Redis and external workers must preserve these contracts and failure semantics.

## Next increment

v4.2.1 will add Redis Streams transport, consumer groups, worker leases, acknowledgements,
stale-lease recovery, and an in-memory fallback without changing the v4.2.0 job envelope.
