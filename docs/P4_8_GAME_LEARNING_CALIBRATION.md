# P4.8 — Game Outcome Learning & Calibration

P4.8 turns the immutable, automatically graded P4.4 game-decision ledger into a transparent learning monitor for moneyline, spread, and total recommendations.

## Product contract

- `GET /api/game-learning/report` is read-only and consumes zero sportsbook/provider credits.
- Only graded win/loss receipts contribute calibration samples. Pushes and receipts without a release-time model probability are excluded.
- The monitor reports hit rate, average predicted probability, calibration gap, Brier score, ECE, one-unit profit/ROI, and sample counts.
- Learning is segmented by market, decision grade, season type, and selected side.
- When a release contains a same-book de-vigged `fairMarketProbability`, P4.8 also computes a paired market Brier benchmark and `brierSkillVsMarket`. Positive skill means the model beat the market benchmark on lower Brier error; negative skill means the market benchmark was better on that sample.

## Protected learning policy

Default review thresholds:

- 50 graded game outcomes before the overall learning state can leave `collecting`;
- 20 graded outcomes before a segment can emit a learning signal;
- 5 percentage points of calibration gap for over/under-confidence alerts;
- 0.08 ECE alert threshold;
- -0.01 or worse Brier skill versus market for a market-skill review signal.

All thresholds are environment-configurable within bounded safety ranges.

## States

- `collecting` — insufficient graded outcomes; collect more results.
- `review` — enough samples exist and at least one calibration/market-skill signal needs human review.
- `stable` — enough samples exist without a protected review signal; hold the current game model.
- `unavailable` — P4.4 ledger persistence is unavailable.

## Safety

P4.8 never automatically changes:

- P4.0 model probabilities;
- P4.1 edge/EV/actionability thresholds;
- P4.5 refresh behavior;
- P4.6 bankroll/Kelly policy;
- P4.7 Tracker records;
- any sportsbook wager or external provider state.

Every suggested calibration direction remains advisory and requires a later explicitly reviewed challenger/promotion phase before production model behavior can change.

## Verification

Run **P4.8 Game Learning Verification** with `RUN_GAME_LEARNING_VERIFY` after merge/deployment. The verifier reads production P4.4 ledger state, proves that the read leaves the ledger unchanged, checks the zero-credit/no-auto-apply safety contract, exercises deterministic synthetic overconfidence and market-benchmark cases, and reruns the inherited P2/P3 safety suite.
