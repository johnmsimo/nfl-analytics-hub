# P6.1 — Calibration Governance Audit Attestations

P6.1 adds explicit owner-attested checkpoints on top of the read-only P6.0 calibration governance audit.

P6.0 computes one deterministic `portfolioDigest` across the append-only moneyline, spread, and total governance history and verifies that the reconstructed champions match production. P6.1 allows an owner to attest an **audit-ready** snapshot into a separate append-only attestation ledger.

The attestation is evidence only. It does not promote, roll back, recalibrate, price, grade, stake, or place anything.

## Why this phase exists

P6.0 can prove what the current calibration-governance state is. P6.1 adds a durable checkpoint that records that an owner reviewed and attested that exact audit state at a specific point in time.

This creates a clean separation between:

- **governance mutations** — still owned exclusively by P5.0 and P5.4;
- **audit computation** — owned by P6.0 and always read-only;
- **audit attestation** — owned by P6.1 and writes only to its dedicated checkpoint ledger after explicit owner confirmation.

## Attestation ledger

P6.1 creates:

`calibration_governance_attestations`

Each append-only row stores:

- `attestationId`;
- P6.0 `portfolioDigest`;
- governance event count;
- P6.0 model version/state;
- derived champion snapshot for moneyline, spread, and total;
- P6.0 integrity snapshot;
- previous attestation digest;
- current attestation digest;
- attesting owner;
- timestamp.

## Hash chain

Every attestation digest includes the previous attestation digest. This creates a deterministic append-only chain across checkpoints.

P6.1 verifies the chain on every status read. It detects:

- previous-digest mismatch;
- malformed timestamp;
- attestation payload/digest mismatch.

A broken chain produces `attestation-chain-degraded` and blocks further attestations until reviewed.

## Attestation gate

A new attestation is allowed only when:

- P6.0 state is exactly `audit-ready`;
- P6.0 `ok=true`;
- the existing P6.1 attestation chain verifies cleanly;
- the current P6.0 digest/event count is not already the latest attestation;
- the caller has the `owner` role;
- the exact confirmation token is supplied:

`ATTEST_CALIBRATION_GOVERNANCE`

Attesting the same current audit twice is idempotent and does not append a duplicate row.

## States

- `unattested` — P6.0 is healthy but no checkpoint exists;
- `attested-current` — the latest checkpoint exactly matches the current P6.0 digest and event count;
- `attestation-stale` — governance history changed after the last checkpoint and a new owner attestation is available;
- `audit-degraded` — P6.0 integrity is not healthy, so attestation is blocked;
- `attestation-chain-degraded` — the P6.1 checkpoint chain itself fails verification.

## APIs

Read-only status/history:

- `GET /api/game-calibration/audit-attestations`
- `GET /api/tracker/game-calibration-audit-attestations`

Owner-only attestation:

- `POST /api/game-calibration/audit-attest`
- `POST /api/tracker/game-calibration-audit-attest`
- JSON: `{"confirmation":"ATTEST_CALIBRATION_GOVERNANCE"}`

## Safety contract

P6.1:

- performs zero sportsbook/provider requests;
- never writes P5.0 or P5.4 promotion registries;
- writes only the dedicated P6.1 attestation ledger and only after explicit owner action;
- never automatically attests;
- never automatically promotes;
- never automatically rolls back;
- never changes probabilities or selected sides;
- never changes actionability thresholds;
- never changes bankroll/Kelly policy;
- never places wagers.

## Production verification

After merge and deployment, run **P6.1 Calibration Governance Attestation Verification** with:

`RUN_CALIBRATION_GOVERNANCE_ATTESTATION_VERIFY`

The verifier is intentionally zero-write. It validates the live P6.1 state and attestation chain, dry-runs a valid attestation with `persist=False`, proves an already-attested audit becomes current, proves a changed P6.0 digest becomes stale and ready for a new checkpoint, proves degraded audits and invalid confirmation are rejected, proves all three underlying histories remain unchanged, and reruns inherited P2/P3 safety verification.
