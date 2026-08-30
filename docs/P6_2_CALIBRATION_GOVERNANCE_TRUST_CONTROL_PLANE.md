# P6.2 — Calibration Governance Trust Control Plane

P6.2 combines the two Phase 6 governance-hardening layers into one canonical, read-only trust posture:

- **P6.0** — is the all-market calibration governance history internally consistent and aligned with the live champions?
- **P6.1** — has that exact healthy audit snapshot been owner-attested, and does the attestation hash chain remain intact?

P6.2 does not replace either layer. It centralizes their interpretation so operator surfaces and future automation cannot independently invent looser trust rules.

## Canonical states

P6.2 returns exactly one operating state:

- `trusted` — P6.0 is audit-ready and the latest valid P6.1 owner attestation exactly matches the current P6.0 digest and event count;
- `attestation-required` — P6.0 is healthy, but the current audit is either unattested or newer than the latest checkpoint;
- `audit-degraded` — P6.0 integrity is not healthy or its portfolio digest is malformed;
- `attestation-chain-degraded` — the P6.1 hash chain cannot be trusted;
- `unavailable` — the P6.0 audit itself is unavailable;
- `review` — a cross-layer inconsistency is detected, such as a status claiming `attested-current` while its digest/event count does not match P6.0.

## Trust rule

`trusted=true` requires all of the following at the same time:

- P6.0 is available;
- P6.0 state is `audit-ready`;
- P6.0 `ok=true`;
- the P6.0 `portfolioDigest` is a 64-character digest;
- the P6.1 attestation chain verifies cleanly;
- P6.1 reports a current attestation;
- the latest attestation portfolio digest exactly matches the current P6.0 digest;
- the latest attestation event count exactly matches the current P6.0 event count.

If any one of those conditions fails, P6.2 fails closed and does not report trusted governance.

## Recommended mutation posture

P6.2 exposes advisory `recommendedMutationPosture` metadata:

- `normal` only when governance is fully trusted;
- `hold` for every degraded, stale, unattested, unavailable, or ambiguous state.

This is advisory in P6.2. It does not silently change or replace the existing P5.0/P5.4 owner-confirmed mutation boundaries.

## Attestation command metadata

When state is `attestation-required`, P6.2 exposes the existing P6.1 command metadata:

- endpoint: `/api/game-calibration/audit-attest`
- exact confirmation: `ATTEST_CALIBRATION_GOVERNANCE`
- owner role required

P6.2 never invokes that command itself.

## API

Read-only endpoints:

- `GET /api/game-calibration/governance-trust`
- `GET /api/tracker/game-calibration-governance-trust`

Important fields:

- `state`
- `trustLevel`
- `trusted`
- `recommendedAction`
- `recommendedMutationPosture`
- `blockers`
- `audit`
- `attestation`
- `command.attest`
- `safetyContract`

## Safety contract

P6.2:

- is read-only;
- performs zero sportsbook/provider requests;
- writes neither P5.0 nor P5.4 promotion registries;
- writes no P6.1 attestation rows;
- creates no mutation endpoint;
- never automatically attests;
- never automatically promotes;
- never automatically rolls back;
- changes no model probability;
- changes no selected side;
- changes no actionability threshold;
- changes no bankroll/Kelly policy;
- places no wagers.

## Production verification

After merge and deployment, run **P6.2 Calibration Governance Trust Verification** with:

`RUN_CALIBRATION_GOVERNANCE_TRUST_VERIFY`

The verifier is zero-credit and zero-write. It accepts every legitimate live trust state, proves a current matching attestation resolves to `trusted`, proves unattested/stale audits require owner attestation, proves degraded audits and broken chains block trust, proves cross-layer current-attestation mismatches fail closed, proves P5.0/P5.4/P6.1 histories remain unchanged, and reruns inherited P2/P3 safety verification.
