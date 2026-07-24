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

## Next increment

v4.4.1 should persist organizations and memberships, resolve tenant context into authenticated
sessions, and add hashed, scoped, expiring, revocable API credentials without storing plaintext
secrets.
