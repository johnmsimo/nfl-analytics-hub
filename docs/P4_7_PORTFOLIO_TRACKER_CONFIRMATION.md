# P4.7 — Confirmed Portfolio Tracking

P4.7 closes the gap between the P4.6 advisory bankroll portfolio and the existing persistent Tracker.

## Product contract

- `GET /api/game-portfolio/tracking/week` is read-only and reports which current P4.6 allocations are already saved in Tracker.
- `POST /api/game-portfolio/track` saves only rows that are still present in the current P4.6 portfolio.
- Every write requires `confirmed: true` from an explicit user action.
- `selectionKeys` may save a subset; unknown or stale keys fail closed before any write.
- repeated confirmation is idempotent through the Tracker's existing immutable first-save key.

## Mapping

P4.6 `moneyline` maps to Tracker `h2h`; spread and total preserve their market names. Best sportsbook, price, line, model probability, fair-market probability, edge, EV, reasons, risks, and P4.6 recommended dollars/units are copied into the Tracker receipt.

## Safety

P4.7 does not:

- request sportsbook/provider data;
- refresh odds;
- upgrade WATCH/REFRESH/MODEL/PASS opportunities;
- save non-portfolio rows;
- place or submit sportsbook bets;
- write during GET/status or production verification.

The Games page presents a **Confirm & Track Portfolio** control only for untracked P4.6 portfolio rows. The browser confirmation text explicitly states that the action records picks in Tracker and does not place bets.

## Verification

Run **P4.7 Portfolio Tracker Verification** with `RUN_PORTFOLIO_TRACK_VERIFY` after merge/deployment. The workflow is intentionally zero-credit and zero-write: it checks the real production portfolio/tracking status, then exercises the confirmation path with a synthetic `persist=False` dry run before rerunning the inherited P2/P3 safety suite.
