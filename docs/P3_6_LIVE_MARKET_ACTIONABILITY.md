# P3.6 — Live Price & Market Actionability

P3.6 closes the gap between a strong model pick and a sportsbook bet that can actually be taken.

## Separation of responsibilities

- **P3.4** decides model quality: `Strong Play`, `Play`, `Lean`, or `Pass`.
- **P3.5** delivers those decisions reliably to Quick Props and My Hub.
- **P3.6** evaluates the live sportsbook market and decides whether a model pick is currently actionable at a verified price.

A good model pick is not automatically a good bet. A stale quote, missing quote, or negative-value price must fail closed.

## Quote contract

Every priced player-prop row now carries:

- bookmaker name/key
- offered line and American price
- provider/book/market update timestamp when available
- local snapshot fetch time
- quote age
- quote freshness (`fresh`, `stale`, `expired`, or unavailable)
- expiry time for actionability
- quoted-book count and fresh-book count
- same-book two-way de-vig fair-market probability when both sides are available
- model-vs-market edge
- expected value and Kelly sizing

The default actionability policy is:

- quote age <= 15 minutes
- model-market edge >= 1.5 percentage points
- EV >= 2%
- model decision grade must be `Strong Play` or `Play`

These thresholds are configurable through `NFL_MARKET_*` environment variables, but P3.6 regression coverage prevents stale quotes from ever becoming actionable.

## Cache vs provider spend

The persisted Odds API snapshot remains the normal protection against repeated credit spend. P3.6 introduces explicit cache-only reads so production verification and degraded product paths can inspect prices without any provider request.

`GET /api/quick-props/week?pricing=cache` and the P3.6 cache-only verification never call the Odds API.

`POST /api/market-pricing/refresh` is an authenticated admin/owner action. It refreshes only the games represented by the highest-ranked model picks, capped at four games per request. This endpoint can consume Odds API credits and therefore requires an explicit mutating request/CSRF token.

## Product behavior

A row can now be:

- **Unpriced** — model pick only; no sportsbook quote.
- **Stale** — a quote can be shown for context but cannot be acted on.
- **Fresh / no value** — valid quote, but price does not support the model.
- **Fresh / thin value** — positive EV below the protected actionability thresholds.
- **Fresh / positive value** — price clears P3.6 value gates.
- **Actionable** — `Strong Play`/`Play` plus fresh positive-value pricing.

`Lean` can never become actionable even when price value is positive. It remains a model lean/watch candidate.

## APIs

- `GET /api/market-pricing/status` — sanitized cache/provider/policy state.
- `POST /api/market-pricing/refresh` — explicit targeted top-pick price refresh; may spend provider credits.
- `GET /api/props/game/<game_id>?pricing=auto|cache|off`
- `GET /api/props/board?pricing=auto|cache|off`
- `GET /api/quick-props/week?pricing=auto|cache|off`
- `GET /api/edges/week` — now only returns fresh positive-value priced rows.

## Production verification

Run **Actions → P3.6 Market Pricing Verification** after deployment.

Two explicit modes are available:

1. `RUN_CACHE_ONLY_VERIFY` — zero provider calls/zero pricing credit spend. Use this first when a recent price snapshot already exists.
2. `RUN_ONE_EVENT_PRICE_REFRESH_VERIFY` — explicitly refreshes exactly one top-pick game before running the cache-only checks. This option can consume Odds API credits and should only be selected intentionally.

The P3.6 gate checks quote timestamp integrity, stale-quote fail-closed behavior, model/price actionability separation, decision volume, priced/fresh quote pools, fresh-book coverage, zero game errors, bounded execution time, and then re-runs the inherited P2/P3 safety verification.
