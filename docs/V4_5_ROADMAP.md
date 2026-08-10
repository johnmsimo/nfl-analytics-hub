# NFL Analytics Hub v4.5 — Decision Delivery

v4.5 introduces a reliable delivery boundary for analytics decisions. The
release preserves the v4.4 identity, API-key, quota, workspace, retention, and
audit guarantees while separating job intake from outbound network delivery.

## Delivery phases

1. **v4.5.0 Decision delivery foundation** — Redis-backed idempotent delivery
   intake, bounded HTTPS destinations and payloads, organization/API-key
   isolation, inspectable queued status, and explicit capability contracts.
2. **v4.5.1 Dispatch worker** — a separate worker process that consumes the
   Redis stream, signs payloads, applies bounded retry/backoff, and records
   delivery outcomes without blocking web requests.
3. **v4.5.2 Delivery operations** — dead-letter inspection, replay controls,
   delivery metrics, audit integration, and enterprise workspace controls.

## v4.5.0 endpoints

- `GET /api/v4.5/capabilities`
- `POST /api/v4.5/deliveries`
- `GET /api/v4.5/deliveries`
- `GET /api/v4.5/deliveries/{delivery_id}`

## v4.5.0 contract

- Production delivery intake requires reachable Redis and fails closed with
  `503 DELIVERY_BACKEND_UNAVAILABLE` when Redis is unavailable.
- Every delivery request requires an `Idempotency-Key`; a replay of identical
  content returns the original queued job without creating a second job.
- Reusing an idempotency key with different event, destination, or payload
  content returns `409 IDEMPOTENCY_CONFLICT`.
- Event names, HTTPS destinations, and JSON payloads are bounded before queue
  insertion; destinations with embedded credentials are rejected.
- Jobs are isolated by organization and API-key context. Cross-tenant status
  reads return `404 DELIVERY_NOT_FOUND`.
- The intake response is `202` with status `queued`. v4.5.0 does not claim that
  outbound network delivery has occurred; dispatch is the next increment.
- Existing v3.x, v4.0–v4.3, and v4.4 routes remain unchanged.

## v4.5.1 contract

- The Fly `delivery` process consumes the existing Redis stream through a
  consumer group and claims stale pending messages after restart.
- Outbound requests use canonical JSON, HTTPS-only destinations, and an
  HMAC-SHA256 signature over `timestamp.canonical_payload` in
  `X-NFL-Delivery-Signature`.
- `V45_DELIVERY_SIGNING_SECRET` is required in production and is never stored
  in a queue record, payload, response, or log.
- HTTP 408, 425, 429, and 5xx responses plus transport timeouts are retried
  with bounded exponential backoff. Other non-2xx responses fail immediately.
- Delivery attempts are bounded; terminal records are `delivered` or
  `failed`, and every attempt updates the existing status endpoint.
- The web process remains non-blocking. Dead-letter inspection, replay
  controls, and delivery metrics remain v4.5.2 scope.

## Guardrails

- API keys are accepted on `/api/v4.5` only for this new contract and remain
  rejected on all older non-enterprise routes.
- The development memory adapter is test/local-only and is not presented as
  distributed production enforcement.
- Queue records expire after a bounded retention window and contain no secret
  API-key material.
