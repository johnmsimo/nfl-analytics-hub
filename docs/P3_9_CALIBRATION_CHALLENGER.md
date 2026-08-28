# P3.9 — Calibration Challenger & Promotion Governance

P3.9 builds on the verified P3.8 learning monitor. P3.8 can identify calibration drift; P3.9 can now construct a **versioned challenger** and test it against the current production probability contract on a chronological forward holdout.

## Core contract

P3.9 is still **read-only** with respect to production decisions.

It may:

- read immutable, graded P3.7 publication receipts
- fit a deterministic challenger on older graded receipts
- evaluate the challenger on newer holdout receipts
- compare champion vs challenger Brier score and ECE
- mark a candidate eligible for human review

It may **not**:

- rewrite historical receipts
- alter production `modelProb` / `consensusProb`
- change P3.4 decision-grade thresholds
- change P3.6 edge/EV/actionability thresholds
- automatically promote a challenger
- automatically change sportsbook decisions or Quick Props output

Every report returns `autoApply=false`, `productionApplied=false`, and `promotionGate.automaticApply=false`.

## Challenger family

The candidate uses a transparent two-parameter logit-affine transform:

`p' = sigmoid(intercept + slope * logit(p))`

The grid search is deterministic and bounded. Identity (`slope=1`, `intercept=0`) remains the production champion.

## Validation design

Receipts are ordered chronologically. The older portion is training data; the newest portion is an untouched forward holdout. The challenger is selected using training data only and must improve the holdout before it can become review-eligible.

Default policy:

- minimum graded samples: `80`
- minimum forward validation samples: `24`
- training fraction: `0.70`
- minimum holdout Brier improvement: `0.005`
- maximum allowed ECE regression: `0.01`
- maximum ledger window: `2000` receipts

Optional environment overrides:

- `P39_MIN_GRADED_SAMPLES`
- `P39_MIN_VALIDATION_SAMPLES`
- `P39_TRAIN_FRACTION`
- `P39_MIN_BRIER_IMPROVEMENT`
- `P39_MAX_ECE_REGRESSION`

## API

`GET /api/tracker/calibration-challenger`

The response includes:

- `collecting`, `review`, `rejected`, or `unavailable` state
- deterministic candidate ID
- candidate slope/intercept
- champion/challenger train metrics
- champion/challenger holdout metrics
- holdout Brier improvement
- ECE regression
- explicit promotion governance

A zero-receipt or low-sample preseason state is valid and returns `collecting`.

## Production gate

Run **P3.9 Calibration Challenger Verification** with:

`RUN_CALIBRATION_CHALLENGER_VERIFY`

The workflow:

- makes no Odds API request
- performs no provider sync
- performs no model or ledger mutation
- confirms the production report remains read-only
- confirms probability metrics stay bounded
- proves a known synthetic overconfidence case creates a forward-validated challenger
- proves already-calibrated synthetic data is not promoted
- reruns the inherited P2/P3 safety verification

P3.9 closes when the production verification returns `ok=true`. A production state of `collecting` is expected until enough graded P3.7 receipts exist.
