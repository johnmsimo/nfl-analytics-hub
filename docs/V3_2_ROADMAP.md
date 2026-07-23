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

## First increment

- `GET /api/v3.2/capabilities`
- `GET /api/v3.2/events` using Server-Sent Events
- `POST /api/v3.2/preferences/normalize`
- Deterministic tests for event framing, heartbeat behavior, and preference validation

SSE begins with heartbeat events and establishes the production API contract. Live score and model-update publishers will be connected in the next increment.
