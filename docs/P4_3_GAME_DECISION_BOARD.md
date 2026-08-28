# P4.3 — Game Decision Board Integration

P4.3 turns the verified P4.0–P4.2 game stack into a decision-first product
surface. The model, pricing logic, and live hydration already existed; this
phase makes those decisions visible where a user actually needs them.

## Product goal

The weekly game board should answer, immediately:

- What is the pick?
- Is it actionable or only a watchlist decision?
- What market is it: moneyline, spread, or total?
- What probability does the model assign?
- What is the de-vig fair-market probability?
- What are the edge and EV?
- Which sportsbook has the best verified price?
- Is that quote fresh?
- What are the main model reasons and risks?

## Canonical delivery contract

`p43_game_decision_delivery.py` reads the persisted P4.2 board and flattens the
P4.1 market contract into UI-ready decisions.

P4.3 **does not recalculate actionability**. A market appears in `picks` only
when P4.1/P4.2 already marked the source market actionable.

The delivery preserves:

- game ID, teams, kickoff, season and week
- market and selected side/team
- line
- model probability and confidence
- decision grade
- quote/price state
- fair-market and reference probability
- edge, EV and Kelly diagnostic
- best sportsbook and best price
- quote timestamp, age and expiration window
- model reasons and risks

## Surfaces

### My Hub

The top decision area now includes **Best Game Bets · P4.3** before the older
analytics panels. Verified actionable markets appear first. When there are no
actionable markets, the surface shows the strongest fresh watchlist rather than
inventing a play.

### Games

Every weekly game card now carries the best P4.3 decision for that matchup:

- `ACTIONABLE` when all upstream price/model gates cleared
- `WATCH` for the strongest fresh non-actionable decision
- no decision when the persisted contract does not support one

The card displays model probability, edge, EV, and best book/price.

## API

`GET /api/game-decision-board/week?season=2026&week=1&type=REG`

This endpoint is **cache-only**. It reads the durable P4.2 weekly snapshot and
performs no Odds API refresh.

## Safety contract

P4.3 inherits every P4.1/P4.2 safeguard:

- fresh quote required for actionability
- same-book paired de-vig market required
- positive edge and EV thresholds remain upstream
- Strong Play / Play actionability grade remains upstream
- stale or incomplete pricing fails closed
- ordinary product reads spend zero provider credits
- P4.3 never upgrades a watchlist or Pass market into an actionable pick

## Production verification

Run **P4.3 Game Decision Board Verification** with:

`RUN_GAME_DECISION_BOARD_VERIFY`

The production gate is read-only and zero-credit. It verifies:

- the complete 16-game Week 1 context remains available
- a persisted priced P4.2 board exists
- market decisions are delivered to P4.3
- production delivery does not upgrade source actionability
- actionable picks, when present, retain fresh best-price and fair-market data
- My Hub includes the P4.3 game-bet surface
- Games cards include the P4.3 decision surface
- a synthetic actionable market renders through the same delivery contract
- inherited P2/P3 safety verification remains green

The production gate does not require a current actionable pick because quotes
can legitimately age after P4.2 hydration. Stale prices must disappear from the
actionable set rather than making P4.3 fail or encouraging an expired play.
