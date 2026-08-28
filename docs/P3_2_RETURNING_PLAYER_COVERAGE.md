# P3.2 Returning-player projection coverage

The 2026 preseason roster warehouse contains both returning NFL players and cold-start players such as rookies and camp additions. A player with no 2025 NFL games cannot satisfy a three-game historical projection requirement, so measuring readiness against every current skill player understates the quality of the historical evidence that actually exists.

P3.2 therefore keeps two separate metrics:

- `projection_ready_skill_coverage`: projection-ready players divided by all current roster-verified skill players. This remains a diagnostic of total roster coverage.
- `projection_ready_returning_skill_coverage`: projection-ready players divided by current roster-verified skill players with at least one historical evidence game. This is the blocking readiness metric.

The minimum coverage threshold remains unchanged. P3.2 also retains the absolute minimum projection-ready player count, so this change does not allow a tiny historical sample to pass. Cold-start players are reported explicitly and remain candidates for later rookie/role/depth-chart modeling rather than being misclassified as missing historical data.
