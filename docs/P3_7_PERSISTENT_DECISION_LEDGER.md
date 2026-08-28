# P3.7 — Persistent Decision Ledger & Auto-Grading

P3.7 closes the history/verification gap left after P3.6 made live market prices actionable.

## Why this phase exists

The existing Tracker already supported saved picks, automatic grading, closing-line capture, CLV, ROI, live pace, and bankroll settings. Its canonical state, however, lived in `data/daily_tracker.json` and `data/model_adjustments.json`. Fly Machines use ephemeral root filesystems, so a deploy or machine replacement could erase tracked history.

P3.7 makes PostgreSQL the canonical Tracker backend and adds immutable release receipts for confirmed model decisions.

## Persistent Tracker

The existing Tracker API and UI continue to use the same legacy `{date: {entries: [...]}}` shape. Internally:

- `tracker_day_snapshots` stores one durable JSON day snapshot per event date.
- `tracker_settings_snapshots` stores bankroll/Kelly settings.
- legacy JSON files are best-effort local-development mirrors/bootstrap sources only.
- database writes synchronize the canonical state transactionally.

This preserves grading, closing-capture, live status, and performance behavior without forcing a second Tracker implementation.

## Immutable decision receipts

When a P3.4–P3.6 model selection is confirmed from the bet slip, P3.7 creates a first-publication receipt in `decision_ledger_receipts`.

The release payload includes, when available:

- game/player/market/line/side identity
- model mean and selected-side probability
- consensus and simulation probability
- simulation agreement
- evidence confidence and matchup grade
- Strong Play / Play / Lean grade and decision score
- release-time price/book
- quote status and price status
- fair/implied/reference probability
- edge, EV, Kelly, book coverage
- actionability state
- model/evidence versions

The receipt uses a deterministic release key. Re-saving the same selection does not rewrite the original model/price evidence. Outcome grading is stored separately from the immutable release payload.

## Auto-grading and verification metrics

The existing Tracker grade loop now grades both user-tracked picks and immutable decision receipts when final player statistics become available.

The publication ledger reports:

- W/L/push record
- hit rate
- Brier score
- expected calibration error (ECE)
- calibration sample size
- one-unit priced profit and ROI
- performance by decision grade
- performance by player-prop market

The normal Tracker keeps CLV as its primary KPI and now also reports Brier/ECE for graded tracked picks.

## API additions

- `GET /api/tracker/persistence`
- `GET /api/tracker/ledger`
- `GET /api/tracker/ledger/performance`
- `POST /api/tracker/ledger/grade`

`POST /api/tracker/grade` keeps the existing `graded` response field while grading both Tracker picks and ledger receipts.

## Safety

- P3.7 production verification is read-only.
- It performs no Odds API calls and consumes no provider credits.
- It does not alter identity reconciliation or warehouse retention.
- Release-time ledger payloads are immutable; result fields are stored separately.
- Zero receipts is a valid preseason state. The production gate validates schema/persistence and the deterministic receipt contract without inserting synthetic production data.

## Production exit gate

After merge/deploy, run **Actions → P3.7 Decision Ledger Verification → `RUN_DECISION_LEDGER_VERIFY`**.

P3.7 closes only when:

- all three P3.7 tables exist,
- Tracker persistence reports database/available,
- publication ledger reports database/available,
- deterministic receipt identity and release fingerprints pass,
- any existing persisted receipt fingerprints remain valid,
- Brier/ECE are null or bounded to `[0,1]`, and
- inherited P2/P3 safety verification remains green.
