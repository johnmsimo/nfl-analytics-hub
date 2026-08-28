# P4.5 — Smart Market Refresh & Opportunity Continuity

P4.5 fixes the production behavior exposed by the P4.4 verification: the game
pricing engine can be correct while the persisted sportsbook snapshot ages past
the actionability TTL, causing the board to collapse to `priced-no-play` even
though the underlying model still has useful game opinions.

## Goals

P4.5 solves two different problems without weakening either safety contract:

1. **Freshness continuity** — refresh the next upcoming NFL slate automatically
   when it is close enough to kickoff and the P4.2 cache is actually due.
2. **Recommendation continuity** — keep strong model opportunities visible when
   a sportsbook quote is stale or unavailable, while clearly preventing those
   rows from becoming actionable bets.

## Smart refresh lease

The worker scheduler runs a lightweight P4.5 check every five minutes. Most
checks make **zero provider requests**.

The next future schedule week is selected from canonical kickoff timestamps,
not only from `current_week`. This matters after the final preseason week, when
the next real market slate is REG Week 1.

Default refresh policy:

- outside 7 days: standby, no automatic provider spend
- 72–168 hours: refresh at most every 120 minutes
- 24–72 hours: refresh at most every 30 minutes
- 6–24 hours: refresh at most every 10 minutes
- inside 6 hours: refresh at most every 5 minutes

Each hydration remains bounded by P4.2:

- one economical bulk game-market request first
- event catalog only when needed
- at most two targeted fallback requests by default
- maximum targeted fallback cap is four

Environment controls:

- `ENABLE_GAME_MARKET_REFRESH`
- `P45_SCHEDULER_TICK_MINUTES`
- `P45_REFRESH_HORIZON_HOURS`
- `P45_FAR_REFRESH_MINUTES`
- `P45_MEDIUM_REFRESH_MINUTES`
- `P45_NEAR_REFRESH_MINUTES`
- `P45_IMMINENT_REFRESH_MINUTES`
- `P45_MAX_TARGETED_REQUESTS`

Production enables the feature, but the horizon gate prevents needless Week 1
credit consumption while games are still far away.

## Opportunity states

P4.5 adds a continuity layer over P4.3/P4.4. It **does not recalculate** P4.1
actionability.

- `ACTIONABLE` — upstream P4.1/P4.2 already approved the market; safe to publish
  and eligible for the P4.4 immutable ledger.
- `WATCH` — fresh quote and positive price signal, but one or more model/price
  gates still block action.
- `REFRESH` — model + previous price signal remain interesting, but the quote is
  stale and must be refreshed before action.
- `MODEL` — Lean-or-better model opinion remains visible without pretending a
  current sportsbook price exists.
- `PASS` — below the model-quality floor.

Every non-actionable opportunity carries explicit blockers such as:

- `quote_not_fresh`
- `paired_fair_market_missing`
- `model_grade_below_actionable`
- `edge_below_threshold`
- `ev_below_threshold`

Stale/unpriced rows can never be upgraded to `ACTIONABLE` by P4.5.

## Product API

`GET /api/game-opportunities/week?season=2026&week=1&type=REG`

This endpoint is provider-I/O free. It preserves P4.4 first-publication receipt
behavior for upstream actionable picks, then adds P4.5 continuity metadata.

`GET /api/game-market-refresh/status`

This is also zero-credit and exposes:

- next upcoming slate
- hours to first kickoff
- cache age
- current refresh cadence
- whether a refresh is due
- standby/fresh/due state

The Games page now consumes the P4.5 opportunity endpoint so users see the best
available model opportunity even when the market needs a refresh, instead of a
blank/skip-only card.

## Safety invariants

P4.5 never changes:

- P4.0 model probabilities
- P4.1 minimum edge or EV
- P4.1 Strong Play / Play requirement for actionability
- P4.2 quote freshness requirements
- P4.4 immutable publication receipts

Product GET requests never trigger provider spend. Only the explicitly enabled
scheduler worker can call the bounded live hydration path.

## Production exit gate

Run **P4.5 Smart Market Refresh Verification** with:

`RUN_SMART_MARKET_VERIFY`

The verifier itself is zero-credit and requires:

- P4.5 refresh policy enabled in production
- next upcoming slate correctly resolves to 2026 REG Week 1
- scheduler refresh job registered/enabled
- useful Lean-or-better opportunity pool remains visible
- stale positive-value synthetic play becomes `REFRESH`, never actionable
- unpriced Play becomes `MODEL`, never actionable
- P4.5 actionability audit passes
- product reads remain provider-free
- inherited P2/P3 safety verification remains green
