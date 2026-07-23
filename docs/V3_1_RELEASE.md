# NFL Analytics Hub v3.1

v3.1 introduces a versioned intelligence platform built on deterministic, explainable analytics.

## Integrated modules

- Game Intelligence with simulations, confidence, factors, injuries, weather, coaching and market disagreement
- Live Game Center win probability, leverage and alerts
- Player Intelligence projections, ranges, trends and similarity
- Team Intelligence power tiers, strengths, weaknesses and playoff probability
- Betting Intelligence edges, expected value, grades and conservative fractional-Kelly sizing
- Conversational Assistant intent classification and grounded-context responses
- Watchlist normalization for teams, players and games
- Premium consolidated dashboard at `/static/v31.html`

## API

All modules use the existing `/api/v3/analytics` namespace. Discover them with `GET /api/v3/analytics/capabilities`.

## Validation checklist

- Repository-wide Python syntax compilation
- Deterministic engine smoke tests
- Unit coverage for live, player, team, betting, assistant, and watchlist modules
- GitHub CI and quality/security workflows

## Dashboard

Open `/static/v31.html` to exercise every v3.1 module through the consolidated interface.

## Release inventory

The v3.1 branch contains the engine, Flask endpoints, deterministic tests, dashboard, and release documentation required for the planned upgrade scope.

## Safety

Betting outputs are analytical signals, not guarantees or financial advice. The assistant reports whether an answer is grounded in supplied context.