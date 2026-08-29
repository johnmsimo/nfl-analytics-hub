# P5.8 — Unified All-Market Calibration Portfolio Control Plane

P5.8 unifies the two existing canonical calibration control planes into one read-only portfolio decision across **moneyline, spread, and total**.

- **P5.2** remains the source of truth for moneyline challenger/promotion/guard governance.
- **P5.6** remains the source of truth for independent spread and total challenger/promotion/guard governance.
- **P5.8** does not duplicate or weaken those gates. It only composes their already-governed outputs into one operator-level priority decision.

## Why this phase exists

After P5.7, Model Operations has safe owner-reviewed surfaces for moneyline plus spread/total, but the underlying governance is still split across two canonical control planes. P5.8 creates one backend portfolio contract that future dashboards, alerts, audits, and operations tooling can consume without reinterpreting market-specific rules.

The portfolio never manufactures promotion or rollback readiness. `promoteReady` and `rollbackReady` are copied from the existing canonical control planes.

## Portfolio markets

The response always contains three market views:

- `moneyline` — delegated to P5.2;
- `spread` — delegated to P5.6;
- `total` — delegated to P5.6.

Each market view includes:

- current governance state;
- recommended action;
- candidate ID;
- active champion ID;
- promotion readiness;
- rollback readiness;
- blockers;
- challenger/guard evidence counts and state;
- the existing command contract;
- the governance source that made the decision.

## Priority contract

P5.8 resolves one aggregate state using a fail-closed priority order:

1. `rollback-review`
2. `promotion-review`
3. `collecting`
4. `champions-healthy`
5. `champions-monitor`
6. `degraded-monitor`
7. `baseline-monitor`

Rollback review intentionally outranks promotion review. If, for example, moneyline has a promotion-ready challenger while spread has a promoted champion that crossed P5.5 guardrails, P5.8 returns `rollback-review` while still preserving moneyline in `promotionReviewMarkets`.

## Degraded behavior

If any delegated market governance source is unavailable, P5.8 reports that market in `unavailableMarkets` and the portfolio becomes `degraded-monitor` unless a higher-priority rollback/promotion/collection condition already exists.

Unavailable governance never creates mutation readiness.

## API

Read-only status:

- `GET /api/game-calibration/portfolio-control-plane`
- `GET /api/tracker/game-calibration-portfolio-control-plane`

Important fields:

- `state`
- `recommendedAction`
- `markets.moneyline`
- `markets.spread`
- `markets.total`
- `promotionReviewMarkets`
- `rollbackReviewMarkets`
- `activeChampionMarkets`
- `collectingMarkets`
- `healthyChampionMarkets`
- `unavailableMarkets`
- `commands`
- `priorityContract`
- `safetyContract`

## Mutation boundaries remain unchanged

P5.8 performs no writes.

Moneyline mutations remain exclusively behind the P5.0 owner-confirmed endpoints supplied through P5.2. Spread/total mutations remain exclusively behind the P5.4 owner-confirmed endpoints supplied through P5.6.

P5.8 does not create any new promotion or rollback endpoint.

## Safety contract

P5.8:

- is read-only;
- performs zero provider/Odds API requests;
- writes neither the P5.0 moneyline registry nor the P5.4 market registry;
- delegates moneyline gates unchanged to P5.2;
- delegates spread/total gates unchanged to P5.6;
- never automatically promotes;
- never automatically rolls back;
- requires the existing owner-confirmed mutation boundaries;
- changes no model probability;
- changes no selected side;
- changes no actionability threshold;
- changes no bankroll/Kelly policy;
- places no wagers.

## Production verification

After merge/deployment, run **P5.8 Calibration Portfolio Control Plane Verification** with:

`RUN_CALIBRATION_PORTFOLIO_VERIFY`

The verifier is zero-credit and zero-write. It validates the live three-market portfolio, proves rollback priority over simultaneous promotion opportunities, proves promotion aggregation, proves collecting and healthy portfolio states, proves P4.4 receipts plus P5.0/P5.4 promotion histories remain unchanged, and reruns inherited P2/P3 safety verification.
