# P4.1 — Game Market Pricing & Actionability

P4.1 joins the verified P4.0 game probabilities to sportsbook moneyline, spread,
and total prices while preserving a strict separation between model probability
and market price.

## Scope

P4.1 adds moneyline pricing against the P4.0 win probability, spread cover
probabilities from the P4.0 model margin distribution, total over/under
probabilities from a transparent scoring baseline, same-book two-way de-vig fair
probability, multi-book best-price comparison at a common line, quote provenance
and freshness, edge/expected-value/Kelly diagnostics, and explicit market
actionability through `GET /api/game-market-decisions/week`.

## Actionability contract

A game market may be marked actionable only when all of the following are true:

1. the model decision is `Strong Play` or `Play`
2. the market quote is fresh
3. at least one same-book opposite-side pair exists for de-vigging
4. model edge is at least the configured P3.6 minimum
5. expected value is at least the configured P3.6 minimum

P4.1 reuses the established P3.6 market thresholds and freshness policy. Stale
quotes may be displayed as context but can never become actionable.

## Market details

Moneyline uses P4.0 home/away win probability directly. Spread decisions compare
books only at a common canonical home spread, while the model cover probability
comes from the P4.0 home-margin distribution. Total decisions use a transparent
expected-total baseline derived from team scoring and points-allowed evidence;
total confidence is deliberately discounted because direct pace/play-volume
modeling is not yet part of P4.0.

## Pricing modes

`GET /api/game-market-decisions/week?...&pricing=<mode>` supports `off`, `cache`,
`auto`, and `live`. `cache` is zero-credit. `auto` follows the normal provider
TTL. `live` is the only explicit force-refresh mode. Production verification
always uses `cache`.

## Production gate

Run **P4.1 Game Market Verification** with `RUN_GAME_MARKET_VERIFY`.

The gate requires the complete 16-game 2026 Week 1 P4.0 board, cache-only
pricing, valid production actionability invariants, mandatory fresh quotes and
same-book paired de-vig evidence, explicit live-refresh control, synthetic
moneyline/spread/total positive-value coverage, stale-quote fail-closed behavior,
timestamped best-price provenance, and the inherited P2/P3 safety verification.

Cached Week 1 sportsbook coverage is informative rather than blocking. If a
future market is unavailable or stale, P4.1 must remain safely unpriced rather
than inventing a price or edge.
