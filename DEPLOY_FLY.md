# Deploying NFL Analytics Hub to Fly.io

## 1. Prerequisites

Install Git, Docker, GitHub CLI, and `flyctl`. Sign in with:

```bash
gh auth login
fly auth login
```

## 2. Create the private GitHub repository

```bash
gh repo create nfl-analytics-hub --private --source=. --remote=origin --push
```

The repository must not contain `.env`, API keys, local databases, raw provider downloads, or model artifacts.

## 3. Create the Fly application

The application name in `fly.toml` is `nfl-analytics-hub`. Fly app names are globally unique. Change the `app` value if the name is unavailable.

```bash
fly apps create nfl-analytics-hub
```

## 4. Provision PostgreSQL and Redis

Use a production PostgreSQL database and a Redis-compatible service. Attach or create these resources through Fly.io, then obtain their connection URLs.

The application expects:

```text
DATABASE_URL=postgresql+psycopg://...
REDIS_URL=redis://...
```

Do not use SQLite for the deployed warehouse. Redis provides distributed rate limiting, shared
JSON caching, and v4.4.2 enterprise quota accounting across Gunicorn workers. Legacy rate
limiting and caching can fall back to process-local memory, but v4.4.2 public decision APIs fail
closed when production Redis is unavailable so organization and credential quotas cannot be
bypassed across Machines.

## 5. Set Fly secrets

```bash
fly secrets set \
  SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  API_KEY_PEPPER="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  DATABASE_URL="postgresql+psycopg://..." \
  REDIS_URL="redis://..." \
  NWS_USER_AGENT="nfl-analytics-hub/3.0 your-email@example.com"
```

Optional provider credentials:

```bash
fly secrets set SPORTSDATAIO_API_KEY="..."
fly secrets set ODDS_API_KEY="..."
fly secrets set OPENWEATHER_API_KEY="..."
```

Optional operations settings:

```text
LOG_LEVEL=INFO
HTTP_TIMEOUT_SEC=25
HTTP_RETRY_TOTAL=3
HTTP_RETRY_BACKOFF_SEC=0.5
HTTP_USER_AGENT=nfl-analytics-hub/3.0
V44_ORGANIZATION_QUOTA=1000
V44_CREDENTIAL_QUOTA=100
V44_QUOTA_WINDOW_SECONDS=60
V45_DELIVERY_TTL_SECONDS=604800
```

`API_KEY_PEPPER` must remain stable while v4.4.1 API credentials are active. The application
falls back to `SECRET_KEY` when no dedicated pepper is configured, but production should set an
independent value so session-secret rotation does not invalidate every issued API key.

The v4.4.2 quota values are default fixed-window limits. Organization owners can store bounded
overrides through the v4.4 quota API. Confirm `REDIS_URL` is reachable before exposing public
decision credentials; quota-protected endpoints return `503 QUOTA_BACKEND_UNAVAILABLE` instead
of using process-local counters in production.

v4.5.0 delivery intake also uses the configured Redis instance for idempotent
queued jobs and status inspection. It returns `202` for queue acceptance;
v4.5.1 adds a separate `delivery` process group for outbound dispatch. Set a
stable signing secret independently from `SECRET_KEY`:

```bash
fly secrets set \\
  -a nfl-analytics-hub \\
  V45_DELIVERY_SIGNING_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

The worker consumes the Redis stream, sends canonical JSON over HTTPS with
`X-NFL-Delivery-Signature`, retries only transient failures with bounded
exponential backoff, and records delivery outcomes in the existing status
record. Configure `V45_DELIVERY_MAX_ATTEMPTS`,
`V45_DELIVERY_BACKOFF_SECONDS`, and `V45_DELIVERY_TIMEOUT_SECONDS` only when
the defaults need to change. The signing secret must remain stable for
receivers that verify signatures and must never appear in logs or payloads.

v4.4.3 shared workspaces, decisions, reports, collaborator ACLs, retention policy, and enterprise
audit history use PostgreSQL. The release migration creates those tables before new Machines
start. Enterprise audit appends lock the owning organization row so each tenant receives one
ordered hash chain across concurrent web processes. Retention redacts expired content but keeps
its identity, digest, timestamps, and audit evidence; it never hard-deletes enterprise records.

Every response includes `X-Request-ID`. Incoming `X-Request-ID` values are preserved, which allows Fly proxy logs, application logs, and client reports to be correlated. Outbound provider calls record host-level success, failure, latency, and last-error telemetry in-process.

## 5.1. Activate The Odds API with a one-credit smoke

The canonical integration key is `the-odds-api`. Keep
`ENABLE_ODDS_API=false` while installing or rotating `ODDS_API_KEY`; key
presence alone must never enable provider traffic.

After this change is merged and deployed, the production smoke can be run from
GitHub Mobile or github.com without a terminal:

1. In The Odds API dashboard, copy one current NFL event ID. It must be 32
   lowercase hexadecimal characters.
2. Open **Actions** in this repository and select **Odds API Production Smoke**.
3. Tap **Run workflow**, paste the event ID, and choose
   **SPEND_ONE_CREDIT**.
4. Open the completed job and verify the sanitized JSON reports `"ok": true`,
   the expected event ID, at least one bookmaker, and `"requests_last"` no
   greater than `1`.
5. Stop after that run. Do not repeat it for additional bookmakers or markets.
6. Only after a successful smoke, change `ENABLE_ODDS_API` to `true` in
   `fly.toml` and deploy that reviewed change.

The workflow has no automatic trigger. Its script validates the key and
canonical provider registration before contacting the provider, fixes the
request to NFL / US / head-to-head, makes exactly one request with redirects
and retries disabled, checks the provider's usage headers, and never prints
the API key or request URL.

The scheduled commercial odds importer remains separately fail-closed. It
requires `ENABLE_ODDS_API=true`, `ENABLE_COMMERCIAL_SYNC=true`, and
`ENABLE_COMMERCIAL_ODDS_SYNC=true`; leave the commercial gates off until its
cadence is intentionally approved.

## 6. Deploy manually once

```bash
fly deploy
fly status
fly logs
```

The Fly release command runs `flask --app app db upgrade` before replacing web and worker Machines. If a migration fails, the release stops and the existing Machines remain active.

## 7. Enable GitHub Actions deployment

Create an app-scoped deploy token:

```bash
fly tokens create deploy -x 720h
```

In the GitHub repository, create an Actions secret named `FLY_API_TOKEN` containing the complete token. Automatic deployment runs only after the `CI` workflow succeeds for a push to `main`. Manual deployment remains available through the workflow dispatch control.

## 8. Verify production

```bash
curl https://nfl-analytics-hub.fly.dev/health
curl https://nfl-analytics-hub.fly.dev/ready
```

Also verify:

- Web Machine is healthy.
- Worker Machine is running.
- Migrations completed.
- PostgreSQL and Redis connections succeed.
- A scoped v4.4 API key can call a public decision endpoint with an `Idempotency-Key`.
- Repeating that exact request reports an idempotent replay without increasing usage.
- Quota responses include organization/credential remaining counts and a reset time.
- `/enterprise-operations` loads the selected tenant and lists its visible workspaces.
- A saved decision and report can be created, read, and exported from one workspace.
- A collaborator with viewer access cannot write until explicitly advanced to editor.
- `/api/v4.4/directory/organizations/{organization_id}/audit` reports `chain_valid: true`.
- A retention dry run is not implied: invoking `/retention/apply` performs explicit content
  redaction for records whose `retained_until` has elapsed.
- Responses include `X-Request-ID`.
- Structured request logs contain status and duration fields.
- No optional provider secret appears in logs.
- `/admin/data` reports integration readiness.

## Process layout

- `web`: Gunicorn application on port 8080.
- `worker`: APScheduler ingestion and analytics service.
- `delivery`: Redis stream consumer for v4.5.1 outbound delivery.
- `release_command`: Alembic/Flask-Migrate database upgrade.

The web process does not start the scheduler, preventing duplicate scheduled jobs. Production web and worker startup never calls `db.create_all()`; schema changes must be represented by Alembic migrations.


## v4.5.2 delivery operations

v4.5.2 adds dead-letter inspection, replay, and organization/workspace delivery
metrics under /api/v4.5. Replay preserves the existing delivery ID, resets
bounded attempts, and re-enqueues through the Redis stream. Replay actions are
written to the existing PostgreSQL hash-linked enterprise audit chain. Use the
workspace filter only for workspaces visible to the authenticated enterprise
principal; cross-tenant and hidden-workspace records remain inaccessible.

Verify the new endpoints with a scoped API key:

GET  /api/v4.5/deliveries/dead-letters
GET  /api/v4.5/deliveries/metrics
POST /api/v4.5/deliveries/{delivery_id}/replay
