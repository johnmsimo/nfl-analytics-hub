# P4.2 — Live Market Hydration & Actionable Game Board

P4.1 proved the game pricing and actionability contract, but its production
verification exposed a real operational gap: the cached Week 1 market snapshot
contained no priced games. P4.2 fixes that gap by separating **explicit live
hydration** from ordinary **cache-only product reads**.

## Goals

P4.2 must make the P4.0 + P4.1 stack usable with real sportsbook markets:

- hydrate upcoming NFL game markets from the configured provider
- match provider events to the canonical 2026 schedule
- persist a durable weekly market snapshot in PostgreSQL-backed provider cache
- retain quote/provider timestamps and best-book provenance
- serve a cache-only weekly board with moneyline, spread, and total decisions
- preserve all P4.1 freshness, paired-fair-book, edge, EV, and actionability gates
- never spend provider credits during normal GET requests

## Hydration strategy

The live hydration command is intentionally bounded and economical.

1. **Bulk refresh** — one featured NFL game-odds request for h2h, spreads, totals.
2. **Event catalog fallback** — only if schedule games remain unmatched, fetch the
   provider event catalog to obtain event IDs.
3. **Targeted fallback** — request odds only for still-missing scheduled events,
   capped by a hard request budget.

The P4.2 production exit workflow caps targeted fallback at **4 events**. The
module itself has an absolute cap of **16**.

Every live hydration requires `allow_provider_spend=true`; without the explicit
flag, the function returns `state=blocked` and makes zero provider requests.

## Durable weekly cache

Hydrated markets are stored under the dedicated provider-cache namespace:

`p4.2-game-market-hydration`

The cache preserves:

- season / type / week
- hydration timestamp
- provider request count
- bulk and catalog event counts
- schedule-to-provider event IDs
- hydration source per game (`bulk` or `targeted`)
- provider event payloads with bookmaker/market timestamps
- missing-game diagnostics

This survives Fly machine replacement and deploys because the canonical cache is
stored in PostgreSQL.

## Product API

`GET /api/game-market-board/week?season=2026&week=1&type=REG`

This endpoint is always **cache-only**. It never calls The Odds API.

The board includes:

- all P4.0 game decisions
- real P4.1 moneyline / spread / total pricing when hydrated
- best price and sportsbook
- quote age / freshness / expiration
- same-book de-vig fair probability
- model probability
- edge / EV / Kelly diagnostics
- actionable status
- market coverage counts
- hydration age and missing-market diagnostics

Hydration status is available at:

`GET /api/game-market-hydration/status?season=2026&week=1&type=REG`

## Fail-closed behavior

P4.2 never invents a market.

If no durable hydration exists, the board still returns the full model decision
slate but every row is unpriced and non-actionable. If hydrated quotes become
stale, P4.1 freshness controls automatically remove actionability.

## Production exit gate

Run **P4.2 Live Market Hydration Verification** and select:

`RUN_LIVE_GAME_MARKET_HYDRATE`

Unlike the P4.1 verification, this workflow **does make live Odds API requests
and may consume provider credits**. The workflow is explicitly bounded to:

- one bulk game-odds refresh
- one catalog request only when needed
- at most four targeted event-odds requests

The phase closes only when production proves:

- hydration persisted successfully
- 16/16 Week 1 model decisions remain available
- at least one real priced game exists
- at least one real fresh priced game exists
- real market coverage is non-empty
- best-price sportsbook/timestamp provenance is present
- persisted cache-only board verification passes
- P4.1 actionability invariants remain green
- inherited P2/P3 safety verification remains green

This exit gate intentionally refuses to close P4.2 on synthetic market tests
alone. Real production market data must be present.
