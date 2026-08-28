# P3.3 — Player Projection & Matchup Intelligence

P3.3 converts the normalized P3.2 player-stat warehouse into an evidence-aware projection layer for Props, Quick Props, and analytics surfaces.

## Model contract

The existing transparent distribution model remains the statistical core. P3.3 adds a second layer that measures evidence quality rather than replacing the projection mathematics.

Each projection now includes:

- season and recent-form means
- defense-vs-position matchup factor and matchup grade
- P10 / P50 / P90 uncertainty interval
- raw distribution probability at the offered/reference line
- evidence-calibrated probability shrunk toward 50% when confidence is weaker
- confidence score and grade
- confidence components for sample size, stability, trend agreement, matchup data, and roster verification
- explicit risk flags
- a deterministic rank score used by Quick Props

## Confidence is not outcome certainty

Confidence measures how trustworthy the *inputs and stability of the estimate* are. It is deliberately separate from the probability that a side wins. A 90% confidence score does not mean a prop has a 90% chance to hit.

## Probability calibration

P3.3 preserves the raw analytic probability for auditability, then shrinks it toward 50% according to evidence confidence. Thin samples, volatility, recent-form conflict, weak matchup history, and unverified roster evidence cannot produce the same probability extremity as a deep, stable sample.

## Quick Props ranking

The weekly/game boards rank by a model-first score made from confidence and calibrated signal strength. When real sportsbook prices are available, edge and positive EV add to the rank score. When prices are unavailable, the model can still rank the strongest evidence without pretending that a synthetic reference line is a tradable sportsbook offer.

## Validation

P3.3 includes a leave-forward history-only backtest using only prior games for each prediction. It reports Brier score, expected calibration error, sample count, and reliability buckets. The production verification also checks projection volume, eligible-player volume, probability bounds, confidence completeness, and DVP matchup coverage.

The backtest intentionally excludes matchup DVP in historical validation because a leakage-safe historical DVP snapshot does not yet exist. Matchup enrichment is therefore validated structurally in P3.3 and can receive a time-versioned historical calibration layer in a later phase.

## Production verification

Run **Actions → P3.3 Player Intelligence Verification** with `RUN_PLAYER_INTELLIGENCE_VERIFY` after deployment.

The workflow is read-only. It does not call the Odds API, commercial providers, warehouse retention, or identity reconciliation. It also re-runs the inherited sanitized P2/P3 safety verification before P3.3 can close.
