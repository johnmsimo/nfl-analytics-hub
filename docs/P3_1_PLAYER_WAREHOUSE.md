# P3.1 — Production Player Warehouse

P3.1 converts the P2 exit advisory (`players=0`, `player_identities=0`) into a protected production population and verification step.

## Scope

- synchronize the 2026 nflverse weekly roster dataset;
- preserve raw roster provenance through the existing source registry;
- resolve roster rows through the source-scoped canonical player identity service;
- hydrate canonical player name, position, birth date, height, weight, and college fields when available;
- preserve 2026 player/team membership rows;
- verify aggregate player, identity, team, and position coverage before the workflow succeeds.

P3.1 intentionally does **not** enable continuous external-provider automation and does not call commercial providers or the Odds API. Player statistics, depth charts, injuries, snaps, and projection features remain later P3 work.

## Production run

After the P3.1 PR is merged and deployed:

1. Open **Actions → P3.1 Player Warehouse Sync**.
2. Select `main`.
3. Select `RUN_PLAYER_WAREHOUSE_SYNC`.
4. Run the workflow once.
5. Review the sanitized aggregate JSON from **Populate and verify player warehouse**.

The workflow runs only against season 2026 and uses the protected `production` environment.

## Exit gate

The default production gate requires:

- at least 1,000 distinct 2026 rostered players;
- all 32 NFL teams represented by 2026 player/team membership;
- at least 95% of rostered players linked to one source-scoped external identity;
- at least 90% linked specifically to an nflverse/GSIS identity;
- at least 90% with a normalized position.

Thresholds are explicit environment-configurable controls (`P31_MIN_*`) for operational recovery, but the repository defaults above are the P3.1 acceptance standard.

The workflow then re-runs the sanitized P2 exit verification. P3.1 is complete only after both the player warehouse gate and the inherited P2 safety gate pass.

## Safety

This phase performs a controlled production data mutation because its purpose is to populate the empty player warehouse. It does not delete warehouse rows, apply retention, run player-identity bulk reconciliation, call the Odds API, or enable commercial sync. Existing source-scoped identity uniqueness remains the guard against cross-provider ID collisions.
