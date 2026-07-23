# NFL Analytics Hub v3.2

v3.2 delivers real-time data transport, authenticated personalization, advanced discovery, model operations, a mobile workspace, observability, and grounded reports.

## Integrated release scope

### Real-time delivery

- Server-Sent Events with typed envelopes and heartbeat behavior
- Bounded provider-neutral event broker
- Replayable IDs and `Last-Event-ID` reconnect support
- Topic subscriptions for scores, odds, injuries, model updates, and system events
- Guarded `POST /api/v3.2/events/publish` integration endpoint

### Personalization and discovery

- Validated dashboard preferences and module ordering
- Authenticated profile persistence for layouts, preferences, watchlists, and saved filters
- Atomic file-backed storage configurable through `V32_PROFILE_STORE`
- Cross-entity search for games, teams, players, and props
- Stable saved-filter contracts

### Model operations

- Calibration buckets, Brier score, log loss, and accuracy
- Backtesting with calibration and optional betting ROI
- Standardized drift diagnostics and severity classification
- Deterministic, dependency-light analytics contracts

### Mobile experience

- Touch-first responsive v3.2 workspace at `/static/v32.html`
- Live event stream, provider health, model status, saved workspace controls, and report studio
- Compact mobile layouts and reduced visual density

### Observability

- Endpoint request, success, and error counters
- Average and p95 latency snapshots
- Provider freshness tracking and user-facing degradation data
- `GET /api/v3.2/observability`

### Generated reports

- Grounded game previews, recaps, and weekly reports
- Explicit source-field metadata and grounded status
- `POST /api/v3.2/reports/generate`

## Key endpoints

- `GET /api/v3.2/capabilities`
- `GET /api/v3.2/events`
- `POST /api/v3.2/events/publish`
- `POST /api/v3.2/search`
- `GET|PUT /api/v3.2/profile`
- `POST /api/v3.2/models/calibration`
- `POST /api/v3.2/models/backtest`
- `POST /api/v3.2/models/drift`
- `POST /api/v3.2/providers/freshness`
- `GET /api/v3.2/observability`
- `POST /api/v3.2/reports/generate`

## Deployment configuration

- Set `V32_PUBLISH_TOKEN` before connecting external publishers.
- Set `V32_PROFILE_STORE` to a persistent mounted path in production. It defaults to `DATA_DIR/v32_profiles.json`.
- For horizontally scaled deployments, replace the in-process event broker with Redis pub/sub while preserving the SSE contract.

## Validation

Deterministic unit coverage includes preferences, SSE framing, broker replay, subscriptions, discovery, profile persistence, calibration, backtesting, drift diagnostics, observability, and report generation.
