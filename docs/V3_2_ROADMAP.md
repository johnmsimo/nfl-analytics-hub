# NFL Analytics Hub v3.2

v3.2 focuses on real-time delivery, personalization, model calibration, mobile usability, and production observability.

## Delivery phases

1. **Real-time foundation** — SSE event transport, typed event envelopes, reconnect behavior, and live-game subscriptions.
2. **Personalization** — saved dashboard layouts, module ordering, density, refresh cadence, watchlist-driven views, and user preferences.
3. **Advanced discovery** — cross-entity search, filters, saved queries, and shareable views.
4. **Model operations** — calibration reports, version metadata, backtesting, drift checks, and confidence diagnostics.
5. **Mobile experience** — compact navigation, touch-first controls, responsive tables, and reduced-data live mode.
6. **Observability** — endpoint latency, stream health, provider freshness, model quality, and user-facing degradation states.
7. **Generated reports** — grounded previews, recaps, weekly reports, and export workflows.

## Completed increments

### Foundation

- `GET /api/v3.2/capabilities`
- `GET /api/v3.2/events` using Server-Sent Events
- `POST /api/v3.2/preferences/normalize`
- Deterministic tests for event framing, heartbeat behavior, and preference validation

### Live pipeline and discovery

- Bounded in-process event broker with replayable event IDs
- Topic subscriptions for scores, odds, injuries, model updates, and system events
- `Last-Event-ID` reconnection support
- Guarded `POST /api/v3.2/events/publish` provider integration endpoint
- `POST /api/v3.2/search` cross-entity search contract
- `POST /api/v3.2/filters/normalize` saved-filter contract
- Deterministic tests for broker replay, topic filtering, saved filters, and search ranking

The in-process broker is intentionally provider-neutral. A Redis pub/sub adapter can replace it for multi-instance deployment without changing the public SSE contract.

## Remaining work

- Persist layouts, filters, and preferences per authenticated user
- Connect score, odds, injury, and model providers to the guarded publish endpoint
- Add model calibration, backtesting, version registry, and drift diagnostics
- Build the mobile-first customizable dashboard
- Add stream, provider, endpoint, and model-quality observability
- Add generated previews, recaps, weekly reports, and export workflows
