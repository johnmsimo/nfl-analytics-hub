# P5.3 — Calibration Operations Console

P5.3 turns the P4.9/P5.0/P5.1/P5.2 calibration governance stack into a usable human-review workflow inside **Model Operations**.

P5.2 already provides the canonical read-only control-plane decision. P5.3 does not create a second policy engine. Instead, it renders the P5.2 decision, displays the supporting state from P4.9/P5.0/P5.1, and exposes the existing P5.0 owner-only promotion and rollback endpoints behind deliberate human confirmation controls.

## Operator view

The Model Operations page now shows:

- canonical P5.2 operating state;
- recommended action;
- active champion or baseline state;
- challenger graded sample count;
- post-promotion guard graded sample count;
- P4.9 challenger state and eligibility;
- P5.0 production promotion eligibility;
- P5.1 guard state and rollback recommendation;
- active blockers;
- visible safety invariants.

The console uses `GET /api/game-calibration/control-plane` as its single canonical governance source.

## Promotion UX

The **Promote challenger** button starts disabled and can only become enabled when all of these are true:

1. the signed-in session role is `owner`;
2. P5.2 returns `promoteReady=true`;
3. the operator types the exact confirmation token `PROMOTE_GAME_CALIBRATION`;
4. the operator then accepts a second browser confirmation that explicitly states production moneyline probabilities will change.

The candidate ID comes from P5.2. It is not typed or invented by the UI.

The actual write still goes through the existing P5.0 owner-only route. Server-side candidate matching, promotion gates, CSRF, authentication, role enforcement, rate limiting, and append-only promotion history remain authoritative.

## Rollback UX

The **Rollback to baseline** button also starts disabled. The P5.3 console deliberately activates it only when:

1. the signed-in role is `owner`;
2. P5.2 returns `rollbackReady=true`, meaning P5.1 recommends rollback review;
3. the operator types `ROLLBACK_GAME_CALIBRATION` exactly;
4. the operator accepts a second browser confirmation.

P5.0's server API still allows an owner to perform an explicit manual rollback whenever a promoted champion is active. P5.3 intentionally makes the normal UI stricter: routine rollback controls appear actionable only when post-promotion evidence recommends review.

## Refresh behavior

Page load and **Refresh governance** are read-only. They fetch:

- `/api/game-calibration/control-plane`;
- `/api/auth/session`;
- the existing v4.3 operations status used by the legacy Model Operations panels.

No promotion or rollback request executes during page initialization or refresh. Mutation functions are bound only to explicit button click handlers.

## Existing Model Operations compatibility

P5.3 keeps the existing v4.3.3 registry, approvals, health, and audit panels intact. The calibration console is added above those lifecycle tools rather than replacing them.

## Safety contract

P5.3 itself:

- performs zero sportsbook/provider calls;
- performs no automatic promotion;
- performs no automatic rollback;
- does not change model probabilities except through the explicit existing P5.0 owner-confirmed promotion action;
- does not alter actionability thresholds;
- does not alter bankroll/Kelly policy;
- does not place wagers;
- does not mutate anything on load or refresh;
- requires both exact typed confirmation and a second human confirmation before sending a promotion or rollback request.

Server-side P5.0 remains the final authority for every mutation.

## Production verification

After merge/deployment, run **P5.3 Calibration Operations Console Verification** with:

`RUN_CALIBRATION_OPERATIONS_CONSOLE_VERIFY`

The verifier is zero-credit and zero-write. It validates the deployed UI safety contract, reads the live P5.2 control plane, proves game-decision receipts and promotion history remain unchanged, confirms automatic promotion/rollback remain disabled, and reruns inherited P2/P3 safety verification.
