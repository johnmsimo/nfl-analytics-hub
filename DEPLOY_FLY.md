# Deploying NFL Analytics Hub to Fly.io

## 1. Prerequisites

Install Git, Docker, GitHub CLI, and `flyctl`. Sign in with:

```bash
gh auth login
fly auth login
```

## 2. Create or connect the GitHub repository

```bash
gh repo create nfl-analytics-hub --private --source=. --remote=origin --push
```

The repository must not contain `.env`, API keys, local databases, raw provider downloads, or model artifacts.

## 3. Create the Fly application

The configured Fly application name is `nfl-analytics-hub`. Fly app names are globally unique; update the `app` value in `fly.toml` if you deploy a separate instance.

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

Do not use SQLite for the deployed warehouse.

## 5. Set Fly secrets

```bash
fly secrets set \
  SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  DATABASE_URL="postgresql+psycopg://..." \
  REDIS_URL="redis://..." \
  NWS_USER_AGENT="nfl-analytics-hub/2.2 your-email@example.com"
```

Optional provider credentials:

```bash
fly secrets set SPORTSDATAIO_API_KEY="..."
fly secrets set ODDS_API_KEY="..."
fly secrets set OPENWEATHER_API_KEY="..."
```

## 6. Deploy manually once

```bash
fly deploy
fly status
fly logs
```

The Fly release command runs `flask --app app db upgrade` before replacing the web and worker Machines. Production startup does not call `db.create_all()`; Alembic migrations are the source of truth for schema changes.

## 7. Enable GitHub Actions deployment

Create an app-scoped deploy token:

```bash
fly tokens create deploy -x 720h
```

In the GitHub repository, create an Actions secret named `FLY_API_TOKEN` containing the complete token.

Pushes to `main` run `.github/workflows/ci.yml`. The Fly workflow starts only after that CI workflow completes successfully on `main`. A manual `workflow_dispatch` remains available as an explicit operator-controlled deployment path.

## 8. Verify production

```bash
curl https://nfl-analytics-hub.fly.dev/health
curl https://nfl-analytics-hub.fly.dev/ready
```

Both probes are intentionally public for infrastructure monitoring. `/ready` checks the database but returns only a sanitized availability state; detailed failures remain in application logs.

Also verify:

- Web Machine is healthy.
- Worker Machine is running.
- Release-command migrations completed.
- PostgreSQL and Redis connections succeed.
- No optional provider secret appears in logs.
- Disabled optional sync jobs are not registered by the scheduler.
- `/admin/data` reports integration readiness.

## Process layout

- `web`: Gunicorn application on port 8080.
- `worker`: APScheduler ingestion and analytics service.
- `release_command`: Flask-Migrate/Alembic database upgrade.

The web process does not start the scheduler, preventing duplicate scheduled jobs. Optional external and commercial jobs are registered only when their corresponding feature flags are enabled.
