# Multi-source NFL data coverage

Phase v4.5.5 adds a read-only coverage contract for the warehouse and dashboard.

## Sources

- **nflverse** is the default public source for historical play-by-play, weekly rosters, injuries, depth charts, and snap counts.
- **SportsDataIO** is an opt-in credentialed source for live games, transactions, and other licensed feeds already enabled by the account.
- ESPN remains a runtime schedule/score fallback, but its failures must not remove cached or warehouse data from the application.

The app never reports credentials or raw secret values. A provider can be enabled with:

\`\`\`env
ENABLED_PROVIDERS=nflverse,sportsdataio
SPORTSDATAIO_API_KEY=...
\`\`\`

## Coverage endpoint

\`\`\`text
GET /api/data/coverage?season=2026
\`\`\`

The response reports:

- canonical 32-team coverage and missing abbreviations;
- roster, injury, depth-chart, snap-count, team-stat, player-stat, and transaction record coverage;
- registered source freshness and raw-version counts;
- the active primary/fallback provider chain;
- the latest sync result without exposing payloads or secrets.

The endpoint is intentionally read-only. Existing admin sync endpoints remain the only path that starts an ingestion job.
