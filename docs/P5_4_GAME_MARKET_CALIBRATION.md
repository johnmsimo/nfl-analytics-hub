# P5.4 — Market-Specific Spread & Total Calibration

P5.4 extends the controlled calibration system beyond moneyline while preserving the safety model established by P4.9 through P5.3.

P5.0 applies an explicitly owner-promoted calibration champion to P4.0 moneyline probabilities. P5.4 adds independent calibration challengers and append-only promotion registries for **spread** and **total** probabilities generated in P4.1.

## Why market-specific calibration

Moneyline, spread, and total probabilities are generated from different model transformations and exhibit different calibration error patterns. A single calibration transform should not be allowed to use evidence from one market to alter another market.

P5.4 therefore trains and validates spread and total independently. Every candidate is fit only from immutable P4.4 receipts for its own market.

## Challenger policy

For each of `spread` and `total`, the default policy requires:

- 50 graded receipts for that market;
- 15 forward-holdout validation receipts;
- 10 paired release-time fair-market benchmark receipts;
- chronological 70/30 train/validation split;
- no publication-batch or game leakage across the split;
- minimum Brier improvement of `0.003`;
- maximum ECE regression of `0.015`;
- maximum market-skill regression of `0.01`;
- a non-identity logit-affine candidate.

These thresholds are bounded through `P54_*` environment variables.

A candidate that clears all gates enters `review`. It is never automatically applied.

## Promotion boundary

Spread and total promotions use a dedicated append-only table:

`game_market_calibration_promotion_events`

Each event records the market, candidate ID, slope/intercept, actor, governance fingerprint, governance payload, and timestamp.

Promotion requires:

- market is exactly `spread` or `total`;
- the current market challenger is in `review`;
- every market-specific promotion gate is eligible;
- submitted candidate ID exactly matches the current candidate;
- authenticated role is `owner`;
- exact confirmation token `PROMOTE_GAME_MARKET_CALIBRATION`.

Rollback is also append-only and requires `ROLLBACK_GAME_MARKET_CALIBRATION`.

## Production application

P4.1 continues to derive the raw spread and total probabilities exactly as before. P5.4 calibration is applied **after the selected side has already been chosen**.

That ordering is deliberate: calibration may change the confidence of the selected side, but it cannot flip the selected team or selected over/under direction.

The final selected-side probability remains bounded to `[0.5, 0.999]`.

If the P5.4 registry is unavailable, invalid, or has no promoted champion, P4.1 returns the unchanged raw probability.

## Provenance

For spread and total, P4.1 now emits:

- `prePromotionProbability`;
- effective `modelProbability`;
- `marketCalibration`;
- effective `marketModelVersion` containing the promoted P5.4 candidate ID when active.

P4.3 carries that market-specific model version and calibration evidence forward into the canonical delivery layer, so P4.4 receipt keys remain attributable to the exact production champion that generated the recommendation.

Moneyline remains owned by P5.0/P4.0 and is unchanged by P5.4.

## API

Read-only status:

- `GET /api/game-market-calibration/status`
- `GET /api/tracker/game-market-calibration`

Owner-only promotion:

- `POST /api/game-market-calibration/promote`
- `POST /api/tracker/game-market-calibration-promote`
- JSON: `{"market":"spread|total","candidateId":"<current candidate>","confirmation":"PROMOTE_GAME_MARKET_CALIBRATION"}`

Owner-only rollback:

- `POST /api/game-market-calibration/rollback`
- `POST /api/tracker/game-market-calibration-rollback`
- JSON: `{"market":"spread|total","confirmation":"ROLLBACK_GAME_MARKET_CALIBRATION"}`

## Safety contract

P5.4:

- uses only immutable P4.4 graded receipts for challenger training;
- never mixes spread and total training evidence;
- performs zero provider requests during challenger evaluation or promotion;
- never automatically promotes or rolls back a candidate;
- never changes the already-selected side;
- never lowers P4.1 edge/EV/freshness/actionability thresholds;
- never changes P4.6 bankroll/Kelly policy;
- never automatically writes Tracker picks;
- never places wagers.

## Production verification

After merge and deployment, run **P5.4 Game Market Calibration Verification** with:

`RUN_GAME_MARKET_CALIBRATION_VERIFY`

The verifier is zero-credit and zero-write. It checks the live spread and total governance states, validates the append-only registries, proves synthetic spread and total challengers can clear market-isolated forward holdouts, dry-runs both promotions with `persist=False`, rejects an invalid confirmation, proves selected-side probability bounds, proves P4.4 receipts and P5.4 event history remain unchanged, and reruns inherited P2/P3 safety verification.
