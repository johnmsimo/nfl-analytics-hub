# P5.6 — Game Market Calibration Control Plane

P5.6 unifies the independent spread and total calibration governance stack into one canonical, read-only operating decision.

P5.4 owns market-specific challengers plus explicit owner-confirmed promotion and rollback. P5.5 monitors promoted spread and total champions after real graded results arrive. P5.6 combines those two layers so future operator surfaces do not have to independently reinterpret promotion readiness, champion health, or rollback readiness.

## Per-market operating states

Each of `spread` and `total` resolves to exactly one state:

- `challenger-collecting` — more graded market evidence is required;
- `baseline-monitor` — baseline remains active and no candidate clears every P5.4 gate;
- `promotion-review` — the current P5.4 challenger clears every promotion gate;
- `champion-collecting` — a promoted market champion is active but P5.5 needs more post-promotion evidence;
- `champion-healthy` — the promoted market champion remains inside P5.5 guardrails;
- `champion-monitor` — a champion is active but the guard is not in a terminal healthy/rollback-review state;
- `rollback-review` — P5.5 detects material post-promotion regression for that market.

## Promotion readiness

For one market, `promoteReady=true` only when all of the following are true:

- no P5.4 champion is currently active for that market;
- a current market candidate ID exists;
- the P5.4 challenger state is `review`;
- the P5.4 promotion gate is eligible.

P5.6 exposes the exact P5.4 mutation endpoint, market, candidate ID, and required confirmation token as metadata only. It performs no write itself.

## Rollback readiness

For one market, `rollbackReady=true` only when:

- a P5.4 champion is active for that market; and
- P5.5 explicitly recommends rollback review for that same market.

The control plane distinguishes rollback **allowed** from rollback **recommended**. The existing P5.4 owner-only rollback API remains the only mutation path.

## Aggregate operating state

The aggregate report gives rollback review the highest priority, then promotion review, then evidence collection, then healthy/monitoring states. This makes operator urgency explicit without contaminating one market with another market's evidence.

Important aggregate fields include:

- `promotionReviewMarkets`;
- `rollbackReviewMarkets`;
- `activeChampionMarkets`;
- `collectingMarkets`;
- `healthyChampionMarkets`.

A healthy spread champion can remain healthy while total is promotion-ready or rollback-ready, and vice versa.

## API

Read-only status:

- `GET /api/game-market-calibration/control-plane`
- `GET /api/tracker/game-market-calibration-control-plane`

Each market payload includes:

- current state and recommended action;
- candidate ID and active champion candidate ID;
- `promoteReady` and `rollbackReady`;
- blockers;
- challenger/guard evidence;
- exact owner-only P5.4 promotion and rollback command metadata.

## Safety contract

P5.6:

- is read-only;
- keeps spread and total governance isolated;
- performs zero sportsbook/provider requests;
- writes zero P5.4 promotion events;
- never automatically promotes a candidate;
- never automatically rolls back a champion;
- requires the existing P5.4 owner-confirmed endpoints for mutations;
- changes no model probability;
- changes no selected side;
- changes no actionability threshold;
- changes no bankroll/Kelly policy;
- places no wagers.

## Production verification

After merge/deployment, run **P5.6 Game Market Calibration Control Plane Verification** with:

`RUN_GAME_MARKET_CALIBRATION_CONTROL_PLANE_VERIFY`

The verifier is zero-credit and zero-write. It accepts all legitimate current production states, proves synthetic market promotion-review, healthy-champion, rollback-review, and collecting decisions resolve correctly, proves rollback review in one market does not erase promotion readiness in the other, proves P4.4 receipts and P5.4 promotion history remain unchanged, and reruns inherited P2/P3 safety verification.
