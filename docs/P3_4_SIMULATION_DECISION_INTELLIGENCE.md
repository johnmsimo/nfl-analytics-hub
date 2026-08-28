# P3.4 — Simulation & Decision Intelligence

P3.4 converts P3.3 projection intelligence into one canonical decision contract for Quick Props, per-game Props, analytics, and downstream tracking.

## Contract

P3.4 keeps the P3.3 evidence-calibrated probability as the statistical model. It then samples that same projection distribution with deterministic Monte Carlo to confirm line/tail behavior and quantify simulation agreement.

Simulation is **not** treated as an independent model vote. The consensus probability is deliberately dominated by the calibrated P3.3 model, with simulation used as a minority confirmation layer.

Each decision exposes:

- model probability for the selected side
- simulation probability for the selected side
- consensus probability
- simulation agreement
- deterministic simulation seed and P10/P50/P90 distribution
- evidence confidence and matchup quality inherited from P3.3
- explicit decision reasons and risks
- one model decision grade: `Strong Play`, `Play`, `Lean`, or `Pass`
- separate price status and `actionable` state when a real sportsbook price supports the model

## Model decision vs priced actionability

A model decision and a tradable bet are intentionally different states.

An unpriced row can be a `Strong Play` or `Play` as a model pick, but it remains `actionable=false` until a verified sportsbook price has positive model/price value. This prevents synthetic/reference lines from being presented as tradable sportsbook offers.

## Product behavior

- `/api/props/game/<game_id>` returns P3.4 decisions for each projectable player market.
- `/api/props/board` ranks by P3.4 decision quality first, then available price value.
- `/api/decisions/week` returns `Lean` or better model picks, including explicitly unpriced rows.
- `/api/edges/week` remains the positive-EV priced feed and now includes P3.4 decision metadata.
- `/api/analytics` exposes the same P3.4 decision fields and ranks top signals by decision score.

## Guardrails

- Confidence remains evidence quality, not outcome certainty.
- Monte Carlo is deterministic for the same decision identity.
- Probability bounds are enforced.
- Thin samples, volatility, roster uncertainty, simulation/model gaps, and unsupported prices remain visible risks.
- `Pass` remains available when evidence does not meet the model-decision threshold.
- P3.4 does not relax P3.3 calibration gates.

## Production verification

Run **Actions → P3.4 Simulation Decision Verification** with `RUN_SIMULATION_DECISION_VERIFY` after deployment.

The verification is read-only and does not call the Odds API or commercial providers. It checks decision volume, eligible-player volume, `Lean`/`Play` pools, simulation coverage, probability bounds, and average simulation agreement, then re-runs the inherited sanitized P2/P3 safety verification.
