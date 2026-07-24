# NFL Analytics Hub v4.4 — Enterprise Access

v4.4 adds explicit organization, identity, authorization, and usage boundaries to the deployed
analytics platform. Enterprise access must remain least-privilege, tenant-scoped, auditable, and
backward-compatible with the existing v3 and v4 contracts.

## Delivery phases

1. **v4.4.0 Enterprise access foundation** — deterministic organization and membership
   identities, a fixed role/permission catalog, subject contracts, membership states,
   deny-by-default authorization decisions, and versioned APIs.
2. **v4.4.1 Persistent identity and API keys** — SQL-backed organizations and memberships,
   tenant-aware sessions, service accounts, hashed API credentials, scopes, rotation, expiry,
   and revocation.
3. **v4.4.2 Quotas and public decision APIs** — Redis-backed usage accounting, organization and
   credential quotas, idempotent request metering, stable public decision endpoints, and
   inspectable limit responses.
4. **v4.4.3 Shared workspaces and enterprise operations** — tenant-scoped saved decisions and
   reports, collaboration controls, append-only enterprise audit history, an administration
   workspace, and export/retention controls.

## v4.4.0 endpoints

- `GET /api/v4.4/capabilities`
- `GET /api/v4.4/access/roles`
- `POST /api/v4.4/organizations/normalize`
- `POST /api/v4.4/memberships/normalize`
- `POST /api/v4.4/access/authorize`

## v4.4.0 Enterprise access foundation

- Deterministic organization identities derived from normalized organization slugs
- Deterministic membership identities derived from organization and subject identities
- User and service subject contracts with bounded, normalized identifiers
- Fixed owner, admin, analyst, and viewer roles
- Explicit organization, membership, workspace, decision, model, audit, API-key, and quota
  permissions
- Active, suspended, and archived organization states
- Invited, active, suspended, and removed membership states
- User-only owner and admin roles
- Deny-by-default access decisions with explicit denial reasons
- Organization status, membership status, role, and permission enforcement
- Contract-version, identity, metadata-fingerprint, and role-permission consistency checks
- Bounded in-memory reference directory for deterministic tests and local development

## v4.4.1 Persistent identity and API keys

- SQLAlchemy-backed organizations and memberships with deterministic v4.4.0 contract identities
- Atomic organization bootstrap with an initial user owner membership
- Persistent user and service-subject memberships with unique tenant/subject identity
- Tenant-aware authenticated sessions that revalidate membership state and permissions
- High-entropy API credentials whose plaintext value is returned once and never persisted
- Server-peppered HMAC-SHA256 credential digests with bounded lookup prefixes
- Least-privilege credential scopes constrained by the subject's current membership
- Explicit credential expiry, last-use metadata, idempotent revocation, and atomic rotation
- API-key authentication limited to v4.4 enterprise routes
- Migration-managed organization, membership, and credential tables for SQLite/PostgreSQL

## v4.4.1 endpoints

- `POST /api/v4.4/directory/organizations`
- `POST /api/v4.4/directory/organizations/{organization_id}/memberships`
- `GET|PUT|DELETE /api/v4.4/session/tenant`
- `GET|POST /api/v4.4/directory/organizations/{organization_id}/api-keys`
- `POST /api/v4.4/directory/organizations/{organization_id}/api-keys/{api_key_id}/rotate`
- `POST /api/v4.4/directory/organizations/{organization_id}/api-keys/{api_key_id}/revoke`

## v4.4.2 Quotas and public decision APIs

- Redis-backed atomic usage accounting across web processes and Machines
- Development-only thread-safe in-memory adapter with the same quota contract
- Fixed-window organization and per-credential request limits
- Organization-owned quota overrides with bounded limits and windows
- Mandatory idempotency keys bound to the exact operation and JSON payload
- Replay-safe metering that never charges an accepted request twice
- Conflict detection when an idempotency key is reused for different content
- API-key-only public ensemble, scenario, and decision-brief endpoints
- Current tenant, membership, scope, expiry, and revocation revalidation on every request
- Inspectable usage snapshots, `429` bodies, retry timing, and rate-limit headers
- Fail-closed `503` responses when the production Redis quota backend is unavailable

## v4.4.2 endpoints

- `GET|PUT /api/v4.4/directory/organizations/{organization_id}/quotas`
- `GET /api/v4.4/directory/organizations/{organization_id}/usage`
- `POST /api/v4.4/public/decisions/ensemble`
- `POST /api/v4.4/public/decisions/scenario`
- `POST /api/v4.4/public/decisions/brief`

## Guardrails

- Existing v3.x and v4.0–v4.3 endpoint contracts remain unchanged.
- v4.4.0 defines provider-neutral access contracts; it does not modify the existing
  environment-backed administrator login or claim tenant persistence.
- Authorization decisions are calculated only from supplied, normalized organization,
  membership, subject, and permission contracts.
- Unknown permissions and roles are rejected rather than treated as implicitly allowed.
- Suspended or archived organizations and non-active memberships never authorize access.
- Service subjects cannot receive owner or administrator roles.
- Metadata fingerprints provide deterministic conflict and consistency checks; they are not
  cryptographic authorization credentials or signatures.
- The in-memory directory is development-only and does not imply cross-process persistence.
- API keys, quotas, shared workspace persistence, public decision APIs, tenant administration,
  and runtime request enforcement remain disabled in v4.4.0.
- No v4.4.0 endpoint grants access to an existing route, changes a session, creates a credential,
  or mutates production data.
- API-key plaintext is returned only at issuance or rotation; the database stores only a
  server-peppered digest and a non-secret lookup prefix.
- API-key scopes can only reduce a persistent membership's permissions and are revalidated on
  every authenticated request.
- API-key authentication is deliberately rejected outside `/api/v4.4/`; existing session
  authorization and v3/v4 route behavior remain unchanged.
- Persistent changes use SQL transactions and require the v4.4.1 migration in production.
- Public decision endpoints require a valid v4.4 API key with `decision.execute`; tenant
  sessions cannot use the public credential surface.
- Public decision requests require an `Idempotency-Key` and bind it to the exact operation and
  payload for 24 hours.
- Quota accounting is fixed-window and atomic across both organization and credential counters.
- Production quota enforcement requires Redis and fails closed when it is unavailable; the
  in-memory backend is development-only and does not claim distributed enforcement.
- Existing `/api/v4` decision endpoints remain unchanged and are not silently placed behind the
  v4.4 enterprise quota boundary.
- v4.4.2 does not add shared workspace persistence, enterprise audit history, administration
  UI, exports, or retention controls.

## Next increment

v4.4.3 should add tenant-scoped saved decisions and reports, collaboration controls,
append-only enterprise audit history, an administration workspace, and export/retention
controls without weakening the v4.4.0-v4.4.2 identity, credential, and quota boundaries.
