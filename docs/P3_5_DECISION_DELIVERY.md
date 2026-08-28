# P3.5 — Quick Props & My Hub Decision Delivery

P3.5 is the product-delivery layer above the completed P3.4 simulation decision engine.

## Goal

A calibrated decision is not useful if the dashboard keeps sorting by sportsbook edge, waits indefinitely in a ranking state, or collapses every non-actionable row into a generic skip. P3.5 makes the product consume the canonical P3.4 decision grade directly and guarantees a terminal delivery state.

## Canonical states

Every Quick Props delivery resolves to exactly one of:

- `ready` — Lean-or-better model picks are available
- `partial` — picks or watchlist rows exist, but one or more games degraded
- `watchlist` — projections exist, but no row cleared the Lean threshold; Pass rows are shown as watchlist only
- `empty` — no projectable rows exist for the selection
- `degraded` — evaluation failed and produced no usable rows

`terminal=true` is present on every delivery contract. The browser also terminates a My Hub request after 15 seconds and renders an explicit timeout message instead of leaving a ranking/loading placeholder indefinitely.

## Pick integrity

P3.5 never upgrades a `Pass` into a pick. Quick Props contains only `Strong Play`, `Play`, or `Lean` rows. When none exist, the strongest `Pass` rows may appear only under the explicit `watchlist` state.

Decision ranking is:

1. P3.4 decision grade
2. P3.4 decision score
3. available sportsbook price value

This removes the previous dashboard behavior that sorted player recommendations primarily by sportsbook edge.

## Model pick vs actionable bet

Unpriced model picks remain visible. They are not actionable wagers. `actionable=true` is valid only when P3.4 has `priceStatus=positive_value` for a verified sportsbook quote.

## Endpoints

- `GET /api/quick-props/week` — terminal Quick Props contract
- `GET /api/props/board` — full P3.5 board with delivery metadata
- `GET /api/props/game/<game_id>` — game decisions with delivery metadata
- `GET /api/my-hub` — canonical My Hub payload using the same delivery contract as the dashboard

`pricing=off` is supported by the Quick Props path used for protected production verification so the delivery model can be tested without sportsbook-provider calls.

## Production verification

Run **Actions → P3.5 Decision Delivery Verification** with `RUN_DECISION_DELIVERY_VERIFY` after deployment.

The read-only gate requires:

- a terminal `ready` delivery state
- no Pass rows in delivered picks
- no invalid watchlist promotion
- correct decision ordering
- correct price/actionability separation
- at least one current game and at least 50 decision rows
- at least three Lean-or-better rows and three delivered picks
- zero game-evaluation errors
- decision construction within 20 seconds

The workflow disables pricing/provider calls and then re-runs the inherited P2/P3 safety verification.
