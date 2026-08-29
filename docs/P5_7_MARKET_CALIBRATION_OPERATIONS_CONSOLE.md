# P5.7 — Spread & Total Calibration Operations Console

P5.7 brings the market-calibration governance stack built in P5.4, P5.5, and P5.6 into the existing **Model Operations** page as one owner-reviewed operational surface.

P5.4 owns market-specific challenger evaluation plus append-only owner-confirmed promotion/rollback. P5.5 monitors promoted spread and total champions against their raw P4.1 shadow probabilities. P5.6 resolves those signals into one canonical operating decision per market. P5.7 renders that canonical decision without weakening any server-side gate.

## Console layout

The existing P5.3 moneyline calibration console remains unchanged and active.

A second **P5.7 Spread & Total Calibration Operations Console** appears on the same Model Operations page and provides:

- aggregate P5.6 state and recommended action;
- markets currently eligible for promotion review;
- markets currently requiring rollback review;
- markets with active promoted champions;
- independent spread and total governance cards;
- challenger sample count and P5.5 guard sample count per market;
- current candidate/champion identity;
- P5.4 blockers and P5.5 rollback guidance;
- explicit promotion/rollback confirmation controls per market.

## Market isolation

Spread and total controls are independent by construction.

Each mutation request is built from the matching P5.6 market command contract and always carries the exact market name. A spread rollback recommendation cannot enable the total rollback button, and a total promotion-ready state cannot enable spread promotion.

The aggregate P5.6 state may prioritize rollback review, but the individual market cards continue to show the independent state of the other market.

## Promotion interaction

A market promotion button starts disabled and can become enabled only when all of the following are true:

- the signed-in role is `owner`;
- P5.6 reports `promoteReady=true` for that exact market;
- the operator types the exact confirmation token supplied by the P5.6 command contract;
- the operator accepts a second browser confirmation dialog.

The page does not hard-code a promotion endpoint. It submits only to the endpoint supplied by `commands.promotion.endpoint` in the P5.6 response.

The request remains market-scoped:

`{"market":"spread|total","candidateId":"<current candidate>","confirmation":"PROMOTE_GAME_MARKET_CALIBRATION"}`

## Rollback interaction

A market rollback button starts disabled and is intentionally stricter than simple rollback availability. P5.7 enables rollback only when:

- the signed-in role is `owner`;
- P5.6 reports `rollbackReady=true` for that exact market, which means P5.5 recommends rollback review;
- the exact confirmation token is typed;
- the operator accepts a second browser confirmation dialog.

The request remains market-scoped:

`{"market":"spread|total","confirmation":"ROLLBACK_GAME_MARKET_CALIBRATION"}`

The existing P5.4 owner-only server route remains the mutation boundary.

## No automatic mutations

Page load and refresh perform only read operations:

- `GET /api/game-market-calibration/control-plane`
- `GET /api/auth/session`

Promotion and rollback functions are bound only to explicit button click handlers. Refreshing the page or clicking **Refresh markets** can never promote or roll back a model.

## Safety contract

P5.7 preserves the full P5.6/P5.4 server safety model:

- zero provider/Odds API requests for governance rendering;
- market-isolated spread and total controls;
- no automatic promotion;
- no automatic rollback;
- owner role required;
- exact candidate binding on promotion;
- exact confirmation token required;
- second human confirmation required;
- no selected-side changes;
- no actionability-threshold changes;
- no bankroll/Kelly changes;
- no wager execution.

The existing P5.3 moneyline console remains available and uses its separate P5.2/P5.0 governance path.

## Production verification

After merge/deployment, run **P5.7 Market Calibration Operations Console Verification** with:

`RUN_MARKET_CALIBRATION_OPERATIONS_CONSOLE_VERIFY`

The verifier is zero-credit and zero-write. It validates the deployed HTML controls, proves mutation endpoints are not hard-coded into page-load logic, validates owner/readiness/confirmation gates, validates the current production P5.6 market states, proves P4.4 receipts and P5.4 promotion history remain unchanged, and reruns inherited P2/P3 safety verification.
