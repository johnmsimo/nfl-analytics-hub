# P4.9 — Game Calibration Challenger & Promotion Governance

P4.9 converts P4.8 learning evidence into a deterministic challenger calibration that can be evaluated against the live game-model champion without silently changing production behavior.

## Challenger design

- Uses immutable P4.4 graded game receipts.
- Accepts win/loss outcomes with release-time model probability.
- Sorts samples chronologically by release time and receipt ID.
- Fits a bounded logit-affine calibration (`slope`, `intercept`) on the older training partition.
- Evaluates the untouched newer partition as a forward holdout.
- Emits a deterministic candidate ID tied to training receipts, parameters, model name, and version.

## Promotion governance

Default gates require:

- at least 80 graded game outcomes;
- at least 24 forward validation outcomes;
- at least 12 validation outcomes with release-time fair-market probability;
- a non-identity challenger;
- at least 0.005 validation Brier improvement over the champion;
- no more than 0.01 ECE regression;
- no more than 0.005 regression in Brier skill versus the release-time fair-market benchmark.

A challenger must clear every gate to enter `review`. Otherwise it remains `collecting` or `rejected`.

## Market-skill guard

P4.9 evaluates champion and challenger on the exact same validation receipts that contain P4.1/P4.2 release-time `fairMarketProbability`. The market benchmark itself is unchanged. `validationMarketSkillDelta` measures challenger Brier skill versus market minus champion Brier skill versus market. A materially negative delta blocks promotion even when the challenger improves overall Brier score.

## Safety contract

P4.9 is advisory governance only. It never:

- requests sportsbook/provider data;
- mutates P4.4 receipts;
- writes Tracker picks;
- changes P4.0 probabilities;
- changes P4.1 edge/EV/actionability thresholds;
- changes P4.5 refresh behavior;
- changes P4.6 bankroll/Kelly policy;
- automatically promotes or applies a challenger;
- places a sportsbook wager.

An eligible challenger still requires explicit human review and a separate future production-application phase before any live game probability can change.

## API

- `GET /api/game-calibration/challenger`
- `GET /api/tracker/game-calibration-challenger`

Both are zero-credit, read-only views of the same P4.9 governance report.

## Production verification

Run **P4.9 Game Calibration Challenger Verification** with `RUN_GAME_CALIBRATION_VERIFY` after merge/deployment. The workflow verifies production readiness, immutable ledger reads, zero-credit/no-auto-apply controls, a synthetic eligible challenger, a synthetic market-skill-regression rejection, and the inherited P2/P3 safety suite.
