# P2.2 — Role Separation and MFA

P2.2 hardens interactive access without changing the current single-administrator production login by default.

## Compatibility

When `AUTH_USERS_JSON` is unset, the application keeps using `ADMIN_USERNAME`, `ADMIN_PASSWORD`, and `ADMIN_DISPLAY_NAME`. That legacy account is treated as the `owner` role. `ADMIN_TOTP_SECRET` is optional; when present, that single account must complete TOTP MFA.

No production secret change is required merely to deploy P2.2.

## Roles

| Role | Access |
| --- | --- |
| `owner` | Full application and admin access. Required for P2.1 player-identity reconciliation apply and warehouse-retention apply operations. |
| `admin` | Full application access plus admin/settings/model/enterprise operations, except owner-only destructive P2.1 apply operations. |
| `analyst` | Normal analytics and authenticated application workflows; blocked from admin operational surfaces. |
| `viewer` | Authenticated read-only application access; mutating API requests are blocked. |

Admin-only browser surfaces are `/settings`, `/admin/data`, `/model-operations`, and `/enterprise-operations`. `/api/admin/*` is limited to `owner` and `admin`.

## Multi-user configuration

Use the `AUTH_USERS_JSON` secret only when access expands beyond the legacy single administrator. It accepts either an object keyed by username or an array of user objects. Every record needs exactly one of `password` or `password_hash`.

Example shape (placeholders only):

```json
{
  "owner-user": {
    "name": "Owner",
    "role": "owner",
    "password_hash": "<werkzeug-password-hash>",
    "totp_secret": "<base32-authenticator-secret>"
  },
  "analyst-user": {
    "name": "Analyst",
    "role": "analyst",
    "password_hash": "<werkzeug-password-hash>",
    "totp_secret": "<base32-authenticator-secret>"
  }
}
```

`AUTH_USERS_JSON` must be stored as a deployment secret, never committed with real credentials.

## MFA policy

MFA is fail-closed in either case:

1. `AUTH_USERS_JSON` defines more than one account; or
2. `REQUIRE_MFA=true` is set.

In those cases every configured account must have a `totp_secret`, otherwise application startup fails. A single legacy administrator can opt into MFA by setting `ADMIN_TOTP_SECRET`.

The login endpoint returns `202` with `MFA_REQUIRED` after valid primary credentials when a verification code is required. The browser then requests the six-digit authenticator code. Invalid codes fail with `INVALID_MFA` and successful sessions include the authenticated role.

## Safe production rollout

1. Deploy P2.2 with the existing single-admin secrets unchanged.
2. Verify the existing administrator can sign in and `/api/auth/session` reports role `owner`.
3. Do not add a second account until a TOTP secret exists for every account that will be configured.
4. Add `AUTH_USERS_JSON` as a protected deployment secret and deploy.
5. Verify each role against its intended access boundary before sharing credentials.
6. Keep the P2.1 reconciliation and retention apply operations disabled until the P2.1 sanitized production preview has been accepted and the cached-data-sync issue is repaired.

## Security notes

- TOTP uses RFC 6238-compatible six-digit codes with a one-step clock-skew window.
- Login remains rate-limited.
- CSRF protection remains required for authenticated browser mutations.
- Enterprise API-key authentication keeps its existing v4.4/v4.5 scope model and does not inherit interactive-session roles.
- Authentication configuration is validated without logging passwords, hashes, TOTP secrets, or the raw `AUTH_USERS_JSON` value.
