# P4.4 — Game Decision Ledger & Automatic Grading

P4.4 closes the accountability loop for the P4.0–P4.3 game-intelligence stack.
When an actionable moneyline, spread, or total recommendation is first delivered
to a user-facing game board, the exact model-and-price evidence is stored as an
immutable publication receipt.

## Why this phase exists

P4.0 created independent game probabilities. P4.1 priced them. P4.2 hydrated and
persisted real sportsbook markets. P4.3 surfaced the strongest decisions in My
Hub and the Games page. P4.4 ensures those published game recommendations cannot
be silently rewritten after prices, model outputs, or outcomes change.

## Publication boundary

Both game-delivery surfaces are covered:

- `GET /api/game-decision-board/week` — canonical P4.3 delivery
- `GET /api/game-market-board/week` — the P4.2 board consumed by My Hub

These reads remain **zero-credit with respect to The Odds API**. P4.4 only writes
a local PostgreSQL receipt for a recommendation that was already marked:

- `actionable=true`
- `quoteStatus=fresh`
- market in `moneyline`, `spread`, or `total`

The publication write never changes upstream actionability.

## Immutable release evidence

Each first-publication receipt preserves:

- season / week / season type / game ID / kickoff
- home and away teams
- market, selected side/team, and line
- model probability and confidence
- decision grade
- de-vig fair-market probability and reference probability
- edge, EV, and Kelly diagnostic
- best sportsbook and best price
- quote timestamp and age
- fresh and paired-fair book counts
- reasons and risks
- source model version and P4.4 publication version

Later price changes alter the candidate fingerprint but do not replace the first
stored release for the same decision identity.

## Dedicated ledger isolation

Game receipts use `game_decision_ledger_receipts`, separate from the P3.7
`decision_ledger_receipts` table. This prevents moneyline/spread/total outcomes
from contaminating the P3.8/P3.9 player-prop calibration and challenger data.

## Automatic outcome grading

P4.4 grades receipts only after the canonical NFL schedule marks a game final.
The original release payload is never changed.

- **Moneyline:** selected team/side versus final winner
- **Spread:** selected team's final score plus its published line versus opponent
- **Total:** final combined score versus published over/under line
- exact ties are graded `push`

Result payloads include final score, grade, release-time model probability, Brier
score when binary, and one-unit profit from the exact published sportsbook price.

A scheduler job named `game-decision-grading` runs every 30 minutes when the app
scheduler is enabled. Manual grading is also available through the tracker API.

## APIs

- `GET /api/tracker/game-ledger`
- `GET /api/tracker/game-ledger/performance`
- `POST /api/tracker/game-ledger/grade`
- `POST /api/tracker/grade` also includes game-ledger grading

Performance includes record, hit rate, Brier score, one-unit profit/ROI, pending
count, and per-market results.

## Production exit gate

Run **P4.4 Game Decision Ledger Verification** with:

`RUN_GAME_LEDGER_VERIFY`

The verification makes **zero Odds API requests**. It may persist the legitimate
first-publication receipts for currently actionable cached game picks, then
immediately repeats publication to prove idempotency. It also verifies:

- dedicated PostgreSQL ledger availability
- P3.7 player-prop receipt count is unchanged
- every current P4.3 pick is fully accounted for
- repeat publication inserts nothing
- moneyline, spread, total, and push grading contracts
- performance API readiness
- inherited P2/P3 production safety verification
