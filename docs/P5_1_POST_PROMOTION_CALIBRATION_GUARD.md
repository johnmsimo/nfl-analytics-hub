# P5.1 — Post-Promotion Calibration Champion Guard

P5.1 monitors any P5.0-promoted game-calibration champion after it begins producing real graded moneyline decisions. It compares the promoted probability against the exact pre-promotion P4.0 probability reconstructed from immutable P4.4 release receipts and the append-only P5.0 promotion registry.

P5.1 is observational governance only. It can recommend rollback review, but it never performs rollback automatically.

## Why this phase exists

P4.9 proves a challenger on a forward holdout before promotion. P5.0 adds an explicit owner-controlled production application boundary. P5.1 adds the post-promotion safety check: once new games are graded, did the promoted calibration continue to outperform or at least remain within bounded regression limits relative to the uncalibrated P4.0 shadow baseline?

## Evidence source

P5.1 uses only existing production evidence:

- immutable P4.4 game-decision receipts;
- each receipt's effective `sourceModelVersion`, which includes the promoted P4.9 candidate ID;
- the P5.0 append-only promotion events that preserve the candidate slope/intercept;
- release-time `modelProbability` and `fairMarketProbability`;
- final P4.4 win/loss grades.

No historical receipt is rewritten or backfilled.

## Shadow baseline reconstruction

P5.0 applies a logit-affine transform to the selected-side P4.0 moneyline probability. P5.1 mathematically inverts that transform using the exact candidate parameters stored in the promotion event.

This creates two probabilities for the same immutable graded release:

- **promoted champion** — the probability actually released to production;
- **shadow baseline** — the pre-promotion P4.0 selected-side probability that would have been used without calibration.

If a P5.0 probability hit the protected `0.5` floor, the original probability is not uniquely reconstructable, so that receipt is excluded from shadow comparison rather than guessed.

## Default guard policy

- minimum graded promoted moneyline samples: `20`;
- minimum paired release-time market samples: `12`;
- maximum promoted Brier regression vs shadow: `0.02`;
- maximum promoted ECE regression vs shadow: `0.03`;
- maximum market-skill regression vs shadow: `0.02`;
- receipt window: `2000`;
- promotion-event window: `100`.

Environment overrides are bounded through `P51_*` variables.

## States

- `baseline` — no promoted P5.0 champion is active;
- `collecting` — a champion is active but fewer than the protected number of graded receipts exist;
- `healthy` — the promoted champion remains inside post-promotion guardrails;
- `rollback-review` — enough evidence exists and one or more protected performance guardrails materially regress.

A `rollback-review` state returns `REVIEW_ROLLBACK_TO_BASELINE`, but `automaticRollback=false` remains invariant. The existing P5.0 owner-only rollback flow is still the only way to return production to the baseline champion.

## API

Read-only status:

- `GET /api/game-calibration/guard`
- `GET /api/tracker/game-calibration-guard`

The response includes champion identity, graded sample count, promoted-vs-shadow Brier/ECE deltas, market benchmark skill, guard checks, failed checks, and the human-review recommendation.

## Safety contract

P5.1:

- performs zero Odds API/provider requests;
- writes zero game-decision receipts;
- writes zero promotion events;
- does not change model probabilities;
- does not change actionability thresholds;
- does not change bankroll/Kelly policy;
- does not automatically roll back a champion;
- does not place wagers.

## Production verification

After merge/deployment, run **P5.1 Game Calibration Guard Verification** with:

`RUN_GAME_CALIBRATION_GUARD_VERIFY`

The verifier is zero-credit and zero-write. It accepts the current legitimate production states (`baseline`, `collecting`, `healthy`, or `rollback-review`), proves P4.4 receipts and P5.0 promotion history remain unchanged, proves a synthetic healthy champion stays active, proves a synthetic materially regressed champion triggers human rollback review, proves low sample counts remain collecting, and reruns inherited P2/P3 safety verification.
