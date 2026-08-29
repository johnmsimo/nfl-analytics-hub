# P5.0 — Controlled Game Calibration Promotion

P5.0 is the explicit production-application boundary for the P4.9 game calibration challenger. P4.9 remains advisory; P5.0 allows a challenger to affect live P4.0 moneyline probabilities only after every P4.9 gate passes, P5.0's moneyline-specific holdout guard passes, and an authenticated owner explicitly confirms the exact candidate ID.

## Production contract

The default state is the unchanged P4.0 identity/baseline champion. No P4.9 challenger is automatically applied.

A promotion requires all of the following:

- P4.9 state is `review`;
- P4.9 `promotionGate.eligible=true`;
- P4.9 still requires human review and has automatic apply disabled;
- the challenger uses a leakage-safe forward holdout;
- challenger slope/intercept remain inside the bounded P4.9 search family;
- at least 8 moneyline validation samples exist by default;
- moneyline Brier score does not regress versus the champion;
- moneyline ECE regression is no worse than the bounded P5.0 allowance;
- the submitted `candidateId` exactly matches the current eligible P4.9 candidate;
- the authenticated owner submits confirmation `PROMOTE_GAME_CALIBRATION`.

## Immutable promotion registry

`game_calibration_promotion_events` is append-only. Promotion and rollback each append an event containing the candidate, parameters, actor, governance fingerprint, governance snapshot, base model version, and timestamp. Historical events are never updated or deleted by P5.0.

The latest event determines the effective champion:

- latest event `promote` -> that candidate is active;
- latest event `rollback` or no events -> P4.0 baseline identity calibration is active.

Rollback requires the owner confirmation `ROLLBACK_GAME_CALIBRATION`.

## Probability application

P5.0 applies only to P4.0 selected-side **moneyline** probability. The underlying team-strength rating, home-field model, simulation margin, sportsbook price, P4.1 minimum edge/EV thresholds, P4.5 refresh policy, P4.6 bankroll policy, and bet execution behavior are unchanged.

The selected team is never flipped by calibration. A promoted probability is bounded to `[0.5, 0.999]`; registry failure safely returns the unchanged baseline probability.

P4.0 exposes both the pre-promotion and effective probabilities plus the active candidate ID. P4.3 preserves effective source-model/calibration provenance so later P4.4 publication receipts remain attributable to the production champion that generated them.

## API

Read-only status:

- `GET /api/game-calibration/champion`
- `GET /api/tracker/game-calibration-champion`

Owner-only promotion:

- `POST /api/game-calibration/promote`
- `POST /api/tracker/game-calibration-promote`
- JSON: `{"candidateId":"<current candidate>","confirmation":"PROMOTE_GAME_CALIBRATION"}`

Owner-only rollback:

- `POST /api/game-calibration/rollback`
- `POST /api/tracker/game-calibration-rollback`
- JSON: `{"confirmation":"ROLLBACK_GAME_CALIBRATION"}`

All mutating routes also inherit the application's authentication, owner-role, CSRF, and rate-limit controls.

## Safety contract

P5.0 never automatically promotes a challenger, never lowers actionability thresholds, never changes bankroll/Kelly policy, never calls an odds provider as part of promotion, never rewrites P4.4/P4.9 evidence, and never places a wager.

## Production verification

After merge/deployment, run **P5.0 Game Calibration Promotion Verification** with `RUN_GAME_CALIBRATION_PROMOTION_VERIFY`.

The verifier is zero-credit and zero-write. It reads the production registry/status, proves the registry remains unchanged, evaluates a synthetic owner-approved candidate using `persist=False`, rejects a bad confirmation, verifies the calibrated probability remains bounded, and reruns the inherited P2/P3 safety suite.
