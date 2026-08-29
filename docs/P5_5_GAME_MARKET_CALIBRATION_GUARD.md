# P5.5 — Spread & Total Post-Promotion Champion Guard

P5.5 adds the post-promotion safety layer for the independent spread and total calibration champions introduced in P5.4.

P5.4 can promote a market-specific calibration candidate after forward-holdout review and explicit owner confirmation. P5.5 answers the next production question: once that champion begins generating real graded recommendations, is it still behaving better than the uncalibrated P4.1 probability it replaced?

## Evidence source

P5.5 is read-only and uses only existing immutable evidence:

- P4.4 graded game-decision receipts;
- each receipt's market-specific effective `sourceModelVersion`;
- release-time `modelProbability` and `fairMarketProbability`;
- P5.4 append-only spread/total promotion events;
- final P4.4 win/loss grades.

Spread and total are evaluated independently. Evidence from one market is never allowed to influence the other market's guard state.

## Shadow baseline reconstruction

P5.4 applies a logit-affine transform only after P4.1 has already selected the spread side or total direction. P5.5 inverts that exact transform using the slope/intercept stored in the matching P5.4 promotion event.

For each graded promoted receipt, P5.5 therefore compares:

- **promoted champion** — the selected-side probability actually released to production;
- **shadow baseline** — the raw P4.1 selected-side probability before P5.4 calibration;
- **release-time market** — the de-vig fair-market probability, when present.

If the promoted probability hit P5.4's protected `0.5` floor, the original probability is not uniquely recoverable. That receipt is excluded from shadow comparison rather than guessed.

## Default per-market guardrails

For each active spread or total champion:

- minimum graded post-promotion samples: `20`;
- minimum release-time market benchmark samples: `12`;
- maximum Brier regression vs shadow: `0.02`;
- maximum ECE regression vs shadow: `0.03`;
- maximum market-skill regression vs shadow: `0.02`;
- receipt window: `2000`;
- P5.4 promotion-event window: `200`.

Environment overrides are bounded through `P55_*` variables.

## Per-market states

Each market returns one of:

- `baseline` — no P5.4 champion is currently promoted for that market;
- `collecting` — a champion is active but fewer than the protected number of graded receipts exist;
- `healthy` — the promoted market champion remains inside post-promotion guardrails;
- `rollback-review` — enough evidence exists and one or more protected performance guardrails materially regress versus the P4.1 shadow baseline.

A market in `rollback-review` returns `REVIEW_MARKET_ROLLBACK_TO_BASELINE`, but `automaticRollback=false` remains invariant. The existing P5.4 owner-only rollback endpoint is still the only way to return that market to baseline.

## Aggregate state

The P5.5 report also returns one aggregate state across spread and total:

- `rollback-review` if either market requires rollback review;
- otherwise `collecting` if either active champion is still collecting evidence;
- otherwise `healthy` when at least one promoted market champion is healthy and none is collecting/regressing;
- otherwise `baseline` when neither market has an active promoted champion.

`rollbackReviewMarkets` identifies exactly which market or markets require owner attention so a failing total champion cannot contaminate a healthy spread champion, or vice versa.

## API

Read-only status:

- `GET /api/game-market-calibration/guard`
- `GET /api/tracker/game-market-calibration-guard`

The response includes per-market champion identity, graded sample counts, promoted-vs-shadow Brier/ECE deltas, release-time market benchmark skill, guard checks, failed checks, and human rollback-review recommendations.

## Safety contract

P5.5:

- performs zero sportsbook/provider requests;
- writes zero P4.4 receipts;
- writes zero P5.4 promotion events;
- never changes a model probability;
- never changes the selected spread side or total direction;
- never changes P4.1 actionability thresholds;
- never changes bankroll/Kelly policy;
- never automatically rolls back a champion;
- never places wagers.

## Production verification

After merge/deployment, run **P5.5 Game Market Calibration Guard Verification** with:

`RUN_GAME_MARKET_CALIBRATION_GUARD_VERIFY`

The verifier is zero-credit and zero-write. It accepts every legitimate current production state, proves P4.4 receipts and P5.4 event history remain unchanged, proves synthetic healthy spread and total champions remain active, proves a materially regressed market triggers human rollback review without affecting the other market, proves low sample counts remain collecting, and reruns inherited P2/P3 safety verification.
