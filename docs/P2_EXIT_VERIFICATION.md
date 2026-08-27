# P2 Hardening Exit Verification

P2 is complete only after the protected production verification workflow succeeds on the deployed `main` commit.

## Run

After this change is merged and the normal Fly deployment succeeds:

1. Open **Actions → P2 Exit Verification → Run workflow**.
2. Select branch `main`.
3. Select `RUN_P2_EXIT_VERIFICATION`.
4. Run the workflow once and review the single sanitized production JSON report.

The workflow is manual, uses the protected `production` environment, and is strictly read-only. It does not invoke provider synchronization, commercial synchronization, Odds API smoke traffic, identity apply operations, or warehouse-retention apply operations.

## Blocking P2 exit gates

The run fails if any of these conditions is false:

- production `/ready` reports a usable database and complete 2026 schedule;
- the 2026 schedule still contains at least 49 preseason, 272 regular-season, and 13 postseason records;
- the current 2026 schedule period resolves and contains at least one game in the merged schedule source;
- the P2.1 identity and retention paths remain dry-run during verification, with zero merges, links, or deletions;
- warehouse retention remains disabled;
- the latest local cached-data sync is `completed` with no sanitized error category/fingerprint;
- the expected scheduler is healthy (`ready` or `pending`);
- the configured role/MFA policy validates, including mandatory MFA when access expands beyond one account or `REQUIRE_MFA=true`;
- production serves a script CSP without `unsafe-inline` or `unsafe-eval`, with the P2.3 structural directives intact;
- unauthenticated access to the canonical API is rejected;
- every `/api/current` compatibility alias remains registered to the exact historical source view function;
- P2.5 repository coverage remains at least 68%, focused analytics coverage remains at least 90%, repository-wide correctness lint remains enabled, and the canonical API surfaces remain in strict lint/format scope.

## Advisories carried beyond P2

These conditions are reported prominently but do not fail the P2 hardening gate by themselves:

- schedule freshness is stale even though the complete bundled schedule is usable;
- persistent provider freshness is stale while provider automation remains intentionally gated;
- the production player/player-identity warehouse is still empty.

An advisory is not ignored. It becomes explicit input to the next roadmap phase, especially data coverage, automated freshness, player projections, and props intelligence.

## Exit decision

`ok=true` with an empty `blocking_failures` array closes P2. Any item in `advisories` is recorded in the P3 backlog. A non-empty `blocking_failures` array keeps P2 open until that production condition is repaired and the protected exit workflow is rerun successfully.
