# P5.9 — Calibration Portfolio Operations Console

P5.9 turns the P5.8 all-market calibration portfolio control plane into one operator-facing human-review console across **moneyline, spread, and total**.

The console does not create any new governance rules. It consumes P5.8 exactly as produced, which means moneyline remains governed by P5.2/P5.0/P5.1 and spread/total remain governed by P5.6/P5.4/P5.5.

## Console location

`/static/p59_calibration_portfolio_operations.html`

The page links back to the existing `/model-operations` surface and uses the same application theme/shell.

## Portfolio view

The console shows:

- canonical P5.8 portfolio state;
- recommended portfolio action;
- markets currently in promotion review;
- markets currently in rollback review;
- active champion markets;
- collecting, healthy, and unavailable market lists;
- P5.8 priority contract;
- signed-in operator role.

Each market card shows:

- delegated governance state and recommendation;
- candidate and champion identity;
- challenger and guard graded sample counts;
- promotion eligibility and rollback recommendation;
- active blockers;
- exact delegated promotion and rollback command contracts.

## Mutation safety

P5.9 adds **no new mutating API** and hard-codes no mutation endpoint.

Every action is taken only through the command object already supplied by P5.8 from the lower-layer control plane.

For any market mutation, all of the following must be true:

- signed-in role is `owner`;
- the exact market is `promoteReady` or `rollbackReady` as appropriate;
- the delegated command contract contains an endpoint and confirmation token;
- the operator types the exact confirmation token;
- the operator accepts a second browser confirmation prompt.

All mutation buttons are disabled by default. Page load and refresh perform read-only requests only.

## Market-specific request bodies

Moneyline continues to use the P5.0 contract:

- promotion: `candidateId` + `confirmation`;
- rollback: `confirmation`.

Spread and total continue to use the P5.4 contract:

- promotion: `market` + `candidateId` + `confirmation`;
- rollback: `market` + `confirmation`.

The portfolio console does not normalize those contracts into a looser shared mutation format.

## Portfolio priority

The UI preserves P5.8's fail-closed priority exactly:

1. rollback review;
2. promotion review;
3. evidence collection;
4. healthy champions;
5. champion monitoring;
6. degraded governance availability;
7. baseline monitoring.

A rollback issue in one market does not erase valid state in the other markets. Each market retains its own delegated readiness and blockers.

## CSP and page delivery

The page lives under `static/`, so the existing strict CSP scanner includes its inline application script hash automatically. Production verification checks that the deployed page is served with strict `script-src` policy and without blanket `unsafe-inline` script permission.

## Safety contract

P5.9:

- performs zero provider/Odds API calls on load or refresh;
- adds no promotion or rollback endpoint;
- writes no P4.4 receipt;
- writes no P5.0 or P5.4 promotion event during verification;
- never automatically promotes;
- never automatically rolls back;
- never changes a probability by itself;
- never changes a selected side;
- never lowers actionability thresholds;
- never changes bankroll/Kelly policy;
- never places wagers.

## Production verification

After merge/deployment, run **P5.9 Calibration Portfolio Operations Console Verification** with:

`RUN_CALIBRATION_PORTFOLIO_CONSOLE_VERIFY`

The verifier checks the deployed console, strict CSP, P5.8 production state, three-market delegation, disabled/default-safe controls, exact-token + second-confirmation requirements, lack of hard-coded mutation endpoints, P4.4 receipt immutability, P5.0/P5.4 promotion-history immutability, and inherited P2/P3 safety verification.
