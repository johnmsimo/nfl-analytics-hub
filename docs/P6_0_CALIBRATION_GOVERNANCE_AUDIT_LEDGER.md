# P6.0 — Unified Calibration Governance Audit Ledger

P6.0 begins Phase 6 by adding one read-only audit boundary over the complete calibration governance history established in Phase 5.

Phase 5 intentionally keeps moneyline and spread/total mutation boundaries separate:

- P5.0 owns moneyline promotion/rollback history;
- P5.4 owns spread and total promotion/rollback history;
- P5.8/P5.9 provide the unified portfolio decision and operator console.

P6.0 does **not** replace those boundaries. It creates one canonical audit view over them.

## Why this phase exists

By P5.9, operators can safely review and execute owner-confirmed calibration changes across all three game markets. The next production requirement is independent verification that the append-only histories still agree with the champions actually serving production.

P6.0 answers:

- what calibration governance events happened across all markets and in what order?
- are event IDs unique?
- are governance fingerprints structurally valid?
- did each event stay inside the correct market/candidate lineage?
- are rollback state transitions valid?
- does the latest event history reconstruct the same champion that production currently reports?
- can the entire public governance history be represented by one deterministic digest?

## Source registries

P6.0 reads only the existing append-only registries:

- moneyline: `game_calibration_promotion_events` from P5.0;
- spread/total: `game_market_calibration_promotion_events` from P5.4.

No new governance table or mutation endpoint is introduced.

## Unified event stream

The two registries are normalized into one oldest-to-newest event stream. Every event includes:

- global `sequence`;
- `eventId`;
- `market` (`moneyline`, `spread`, or `total`);
- `action` (`promote` or `rollback`);
- `candidateId`;
- calibration family/parameters;
- base model version;
- approving actor;
- governance fingerprint;
- creation time;
- source registry;
- deterministic `eventDigest`.

The complete ordered event-digest sequence produces one `portfolioDigest` for the current audit snapshot.

The digest is read-only evidence. P6.0 does not persist it or use it to mutate production.

## Integrity gates

P6.0 evaluates the following checks:

- `eventIdsUnique`
- `timestampsValid`
- `actionsValid`
- `marketsValid`
- `governanceFingerprintsWellFormed`
- `candidateLineageValid`
- `stateTransitionsValid`
- `liveChampionsMatchHistory`

Candidate lineage is market-specific:

- moneyline promotions must reference a P4.9 candidate (`p49-*`);
- spread promotions must reference a P5.4 spread candidate (`p54-sp-*`);
- total promotions must reference a P5.4 total candidate (`p54-to-*`).

Rollback events must not carry a candidate ID.

## Champion reconstruction

P6.0 independently replays each market's ordered history:

- `promote` makes that candidate the derived active champion;
- `rollback` returns that market to baseline;
- a rollback without an active derived champion is an integrity failure.

The derived result is then compared to the live P5.0/P5.4 champion readout. Both active/baseline state and candidate ID must agree.

## States

- `audit-ready` — every integrity gate passes;
- `audit-degraded` — one or more integrity gates fail.

A degraded audit is diagnostic only. P6.0 never automatically rolls back or promotes a model.

## API

Read-only endpoints:

- `GET /api/game-calibration/audit-ledger`
- `GET /api/tracker/game-calibration-audit-ledger`

The response includes the unified event stream, portfolio digest, per-market reconstructed history, current-champion consistency, integrity checks, and safety contract.

## Safety contract

P6.0:

- is read-only;
- performs zero sportsbook/provider requests;
- writes neither P5.0 nor P5.4 registries;
- creates no new mutation endpoint;
- never automatically promotes;
- never automatically rolls back;
- changes no model probability;
- changes no selected side;
- changes no actionability threshold;
- changes no bankroll/Kelly policy;
- places no wagers.

## Production verification

After merge and deployment, run **P6.0 Calibration Governance Audit Verification** with:

`RUN_CALIBRATION_GOVERNANCE_AUDIT_VERIFY`

The verifier is zero-credit and zero-write. It validates the live audit contract and deterministic digest, proves a synthetic valid three-market governance history passes, proves malformed governance is detected, confirms both underlying promotion histories remain unchanged, and reruns inherited P2/P3 safety verification.
