# P3.8 — Outcome Learning & Calibration Monitor

P3.8 closes the loop created by P3.7. The application now has immutable
first-publication receipts and automatic grading, so it can measure how released
probabilities behave in production without rewriting history.

## Scope

P3.8 is deliberately **read-only learning governance**.

It adds:

- calibration monitoring from immutable P3.7 receipts
- Brier score, ECE, hit rate, average released probability, and calibration gap
- one-unit priced ROI as a secondary outcome diagnostic
- segmentation by market, decision grade, confidence grade, and side
- explicit overconfidence, underconfidence, and high-calibration-error signals
- a protected sample gate before any model-change review is considered
- `GET /api/tracker/learning`
- a protected production verification workflow

## Safety contract

P3.8 never automatically changes:

- projection means
- calibrated probabilities
- confidence scores
- decision-grade thresholds
- market-pricing thresholds
- sportsbook actionability rules

The report always returns `autoApply=false`. Even after enough graded samples
exist, the only possible state transition is from `collecting` to `stable` or
`review`. A future phase may evaluate a versioned calibration candidate, but
production promotion must remain explicit and independently validated.

## Default sample policy

- minimum graded calibration samples: `50`
- minimum samples per segment: `20`
- calibration-gap alert: `0.05`
- maximum monitored ECE before review: `0.08`
- maximum ledger window: `2000` receipts

Environment overrides:

- `P38_MIN_GRADED_SAMPLES`
- `P38_MIN_SEGMENT_SAMPLES`
- `P38_CALIBRATION_ALERT`
- `P38_MAX_ECE`

## API

`GET /api/tracker/learning`

Returns:

- current learning state: `collecting`, `stable`, `review`, or `unavailable`
- overall calibration metrics
- per-market / per-grade / per-confidence / per-side diagnostics
- review signals and direction
- sample policy and promotion gate
- sanitized P3.7 ledger status

## Production gate

Run **P3.8 Learning Calibration Verification** with:

`RUN_LEARNING_VERIFY`

The workflow is read-only, consumes no Odds API credits, performs no provider
sync, and makes no production model changes. It verifies:

- the P3.7 ledger is available
- zero-sample preseason state remains valid
- metrics are bounded
- segmentation accounts for every graded calibration sample
- known synthetic overconfidence is detected
- auto-apply remains disabled
- inherited P2/P3 safety verification remains green
