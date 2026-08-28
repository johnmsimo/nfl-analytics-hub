# P4.6 — Bankroll-Aware Game Portfolio

P4.6 turns the P4.5 opportunity board into a disciplined staking plan without
weakening any upstream actionability gate.

## Why this phase exists

By P4.5 the platform can preserve useful game intelligence through changing
market freshness states. That still leaves a practical question unanswered:
when several game markets are simultaneously actionable, how much bankroll
should be allocated to each one without concentrating too much risk in one bet,
one game, or one slate?

P4.6 answers that question with a transparent, bounded portfolio layer.

## Inputs

P4.6 consumes only the cache-only P4.5 opportunity board and the existing
Tracker bankroll settings:

- bankroll
- fractional Kelly multiplier
- maximum bet percentage
- unit percentage

It does not query sportsbooks or refresh odds.

## Eligibility

A market can receive a non-zero recommended stake only when P4.5 already marks
it `ACTIONABLE` and all of the following remain true:

- fresh sportsbook quote
- `Strong Play` or `Play` decision grade
- real best sportsbook and price
- de-vig fair-market probability present
- upstream actionability remains true

`WATCH`, `REFRESH`, `MODEL`, and `PASS` opportunities always receive a zero
recommended stake.

## Allocation

The raw input is the upstream Kelly stake percentage. P4.6 applies the user's
fractional-Kelly setting and then enforces three independent risk caps:

1. per-bet cap from Tracker `max_bet_pct`
2. per-game exposure cap
3. total slate exposure cap

Candidates are ranked transparently using decision grade, EV, edge, confidence,
and market priority. Exposure is allocated in that order until the caps are
reached.

## Default policy

- maximum slate exposure: `15%` of bankroll
- maximum exposure to one game: `7.5%` of bankroll
- minimum recommended stake: `0.25%` of bankroll
- maximum portfolio picks: `8`
- Strong Play Kelly multiplier: `1.10`
- Play Kelly multiplier: `1.00`

Optional environment overrides:

- `P46_MAX_SLATE_EXPOSURE_PCT`
- `P46_MAX_GAME_EXPOSURE_PCT`
- `P46_MIN_STAKE_PCT`
- `P46_MAX_PORTFOLIO_PICKS`
- `P46_STRONG_PLAY_MULTIPLIER`
- `P46_PLAY_MULTIPLIER`

## Safety contract

P4.6 never:

- upgrades a non-actionable market into a bet
- invents a sportsbook, price, fair probability, edge, EV, or Kelly value
- calls an odds provider
- places a bet
- writes a Tracker pick automatically

It is advisory staking intelligence only.

## API

`GET /api/game-portfolio/week?season=2026&week=1&type=REG`

The response includes:

- portfolio state
- sanitized bankroll/allocation settings
- per-bet, per-game, and per-slate caps
- recommended stake dollars, bankroll percentage, and units
- ranked portfolio picks
- actionable alternates blocked by allocation limits
- non-actionable zero-stake context

## Production gate

Run **P4.6 Game Portfolio Verification** with:

`RUN_GAME_PORTFOLIO_VERIFY`

The verifier is zero-credit. It validates the next live slate, confirms the
production portfolio never upgrades actionability, confirms all non-actionable
rows remain zero-stake, and runs synthetic cases proving per-bet, per-game, and
per-slate caps.

A production slate with zero actionable markets is valid. In that case P4.6
must return a zero-stake portfolio rather than manufacture a bet.
