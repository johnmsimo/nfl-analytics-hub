# P2.4 — API Generation Consolidation

P2.4 establishes `/api/current` as the stable application API facade for the overlapping v2, v3/v3.1, v3.2, and v4.5 generations.

The consolidation is intentionally compatibility-first: canonical routes reuse the exact Flask view functions already backing the historical endpoints. Business logic is not copied into a new implementation, and existing clients are not broken during this phase.

## Canonical discovery

Use:

```text
GET /api/current/capabilities
```

The response declares contract `2026.1`, the canonical domains, the source contract behind each domain, and the legacy compatibility base.

Domain-specific discovery is also available:

```text
GET /api/current/intelligence/capabilities
GET /api/current/analytics/capabilities
GET /api/current/realtime/capabilities
GET /api/current/deliveries/capabilities
```

## Canonical domains

### Intelligence

Source contract: v2.0 warehouse-backed intelligence.

```text
GET /api/current/intelligence/platform/health
GET /api/current/intelligence/live
GET /api/current/intelligence/teams/{abbr}/intelligence
GET /api/current/intelligence/games/{game_id}/intelligence
```

### Analytics

Source contract: v3.1 dependency-light analytics.

```text
POST /api/current/analytics/win-probability
POST /api/current/analytics/epa
POST /api/current/analytics/drives
POST /api/current/analytics/simulate
POST /api/current/analytics/power-rating
POST /api/current/analytics/injury-impact
POST /api/current/analytics/player-similarity
POST /api/current/analytics/matchup
POST /api/current/analytics/game-intelligence
POST /api/current/analytics/live-center
POST /api/current/analytics/player-intelligence
POST /api/current/analytics/team-intelligence
POST /api/current/analytics/betting-intelligence
POST /api/current/analytics/assistant
POST /api/current/analytics/watchlist
```

### Realtime and discovery

Source contract: v3.2.

```text
GET  /api/current/realtime/events
POST /api/current/realtime/events/publish
POST /api/current/realtime/preferences/normalize
POST /api/current/realtime/filters/normalize
POST /api/current/realtime/search
```

### Profile, models, providers, observability, and reports

Source contract: v3.2 completion APIs.

```text
GET  /api/current/profile
PUT  /api/current/profile
POST /api/current/models/calibration
POST /api/current/models/backtest
POST /api/current/models/drift
POST /api/current/providers/freshness
GET  /api/current/observability
POST /api/current/reports/generate
```

### Delivery operations

Source contract: v4.5.3.

```text
POST /api/current/deliveries
GET  /api/current/deliveries
GET  /api/current/deliveries/{delivery_id}
GET  /api/current/deliveries/dead-letters
GET  /api/current/deliveries/metrics
POST /api/current/deliveries/{delivery_id}/replay
GET  /api/current/deliveries/health
```

The canonical delivery routes preserve the existing v4.4/v4.5 scoped API-key authorization model. An API key is not accepted on unrelated `/api/current` domains.

## Compatibility policy

P2.4 does not delete historical routes.

- `/api/v2/*` remains available as a legacy compatibility contract.
- `/api/v3/analytics/*` remains available as a legacy compatibility contract.
- `/api/v3.2/*` remains available as a legacy compatibility contract.
- `/api/v4.5/*` remains a stable enterprise compatibility contract while `/api/current/deliveries/*` becomes the canonical delivery namespace.

Responses from v2, v3, and v3.2 include compatibility metadata:

```text
Deprecation: true
X-API-Lifecycle: legacy-compatibility
X-API-Generation: <generation>
X-API-Contract: <source contract>
X-API-Canonical-Base: <canonical base>
Link: </api/current/capabilities>; rel="successor-version"
```

v4.5 responses advertise `X-API-Lifecycle: stable-compatibility` and the canonical delivery base but are not marked deprecated in P2.4.

No `Sunset` date is declared in this phase. Historical routes should only be removed after first-party callers are migrated, external consumers have an announced migration window, and route telemetry supports retirement.

## First-party migration

The legacy v3.2 workspace page is migrated in P2.4 to call `/api/current` for realtime events, profile persistence, observability, and report generation. This ensures the canonical facade is exercised by a real browser client immediately.

Future first-party code should use `/api/current` rather than introducing a new numeric API generation for an existing domain. A numeric contract should be added only when a genuinely incompatible public contract is required.

## Regression guarantees

P2.4 tests verify that:

1. every canonical compatibility route points to the exact same Flask view function object as its historical route;
2. representative old and canonical requests return the same payload and status;
3. v2/v3/v3.2 lifecycle headers identify the canonical successor;
4. v4.5 remains non-deprecated stable compatibility;
5. canonical delivery routes accept the existing enterprise API-key authentication path;
6. API keys are still rejected on unrelated canonical domains; and
7. the v3.2 browser workspace no longer calls `/api/v3.2/*`.

## Exit criterion

P2.4 is complete when `/api/current` is registered on production, required CI/security tests pass, first-party v3.2 browser traffic uses the canonical facade, and the historical generations remain compatibility aliases rather than independent implementations.
