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
```

`API_KEY_PEPPER` must remain stable while v4.4.1 API credentials are active. The application
falls back to `SECRET_KEY` when no dedicated pepper is configured, but production should set an
independent value so session-secret rotation does not invalidate every issued API key.

The v4.4.2 quota values are default fixed-window limits. Organization owners can store bounded
overrides through the v4.4 quota API. Confirm `REDIS_URL` is reachable before exposing public
decision credentials; quota-protected endpoints return `503 QUOTA_BACKEND_UNAVAILABLE` instead
of using process-local counters in production.

Every response includes `X-Request-ID`. Incoming `X-Request-ID` values are preserved, which allows Fly proxy logs, application logs, and client reports to be correlated. Outbound provider calls record host-level success, failure, latency, and last-error telemetry in-process.

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
- Responses include `X-Request-ID`.
- Structured request logs contain status and duration fields.
- No optional provider secret appears in logs.
- `/admin/data` reports integration readiness.

## Process layout

- `web`: Gunicorn application on port 8080.
- `worker`: APScheduler ingestion and analytics service.
- `release_command`: Alembic/Flask-Migrate database upgrade.

The web process does not start the scheduler, preventing duplicate scheduled jobs. Production web and worker startup never calls `db.create_all()`; schema changes must be represented by Alembic migrations.
