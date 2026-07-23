# NFL Analytics Hub v4.1 — Advanced Scouting Intelligence

v4.1 builds a transparent scouting layer on the v4.0 decision, simulation, and insight foundations. It turns supplied player, team, personnel, formation, and play-level features into comparable and inspectable scouting outputs.

## Delivery phases

1. **v4.1.0 Scouting foundation** — player similarity, deterministic team-style clustering, personnel/formation tendencies, explainable feature differences, and versioned API contracts.
2. **v4.1.1 Matchup intelligence** — matchup strengths, exploitable weaknesses, tendency-vs-tendency comparisons, and evidence-ranked matchup briefs.
3. **v4.1.2 Scouting history** — time-aware tendency changes, roster and role transitions, opponent-adjusted splits, and season-over-season comparisons.
4. **v4.1.3 Scouting workspace** — player comparison, team-style maps, tendency explorers, matchup cards, saved reports, and mobile review flows.

## v4.1 endpoints

- `GET /api/v4.1/capabilities`
- `POST /api/v4.1/scouting/player-similarity`
- `POST /api/v4.1/scouting/team-styles/cluster`
- `POST /api/v4.1/scouting/tendencies`
- `POST /api/v4.1/scouting/matchups/compare`
- `POST /api/v4.1/scouting/matchups/tendencies`
- `POST /api/v4.1/scouting/matchups/brief`
- `POST /api/v4.1/scouting/history/tendencies`
- `POST /api/v4.1/scouting/history/roles`
- `POST /api/v4.1/scouting/history/opponent-adjusted`
- `POST /api/v4.1/scouting/history/seasons`
- `GET /api/v4.1/scouting/workspace`
- `POST /api/v4.1/scouting/workspace/reports/normalize`
- `POST /api/v4.1/scouting/workspace/reports/review`

## v4.1.0 scouting foundation

- Range-normalized player similarity across explicitly supplied metrics
- Ranked player matches with feature coverage and largest differences
- Deterministic k-means team-style clustering
- Inspectable cluster membership and style signatures
- Personnel, formation, and personnel/formation-combination usage
- Pass rate, success rate, explosive rate, and yards-per-play summaries
- Bounded result sizes, cluster counts, iteration counts, and sample thresholds

## v4.1.1 matchup intelligence

- Explicit offense-vs-defense metric rules with caller-supplied scales, weights, and direction
- Ranked offensive and defensive advantages with exact input values and normalized edges
- Exploitable-defensive-weakness and offensive-risk views
- Feature coverage, unavailable-metric reporting, and inspectable sample support
- Tendency-vs-tendency comparison by shared personnel or formation label
- Success-rate, explosive-rate, and yards-per-play comparison against defensive rates allowed
- Usage-aware opportunity and risk rankings
- Bounded, evidence-ranked matchup briefs with deterministic summaries and confidence

## v4.1.2 scouting history

- Earliest-to-latest tendency change analysis across caller-ordered periods
- Ranked usage, efficiency, explosiveness, and yards-per-play changes
- Observed team, role, and material snap-share transitions by player
- Current-roster snapshots derived from the latest supplied observation
- Opponent-adjusted splits using explicit caller-supplied baseline metrics
- Direction-aware adjustment for metrics where lower values are better
- Consecutive season comparisons with inspectable values and normalized changes
- Visible exclusions for small samples, missing evidence, and incomplete history

## v4.1.3 scouting workspace

- Responsive workspace at `/scouting` for player, team-style, tendency, matchup, and history analysis
- Caller-editable JSON requests that preserve the explicit v4.1 engine contracts
- Deterministic saved-report envelopes with bounded titles, tags, sources, and payload sizes
- Browser-local saved reports with explicit storage disclosure and no implied server persistence
- Pinned-first, recency-ordered mobile report review with compact evidence counts
- Shared navigation integration without changing any earlier scouting calculation

## Guardrails

- Existing v3.x, v4.0, v4.1.0, v4.1.1, and v4.1.2 contracts remain unchanged.
- Scouting and matchup results use only supplied structured inputs; the engine does not invent player, team, tracking, play, or opponent data.
- Matchup semantics are explicit: callers name the offense and defense metrics, normalization scale, direction, and optional weight.
- Defense tendency metrics are declared as rates allowed in the tendency response.
- Every matchup claim includes the exact compared values, normalized edge, sample context, and evidence score.
- Missing metrics reduce coverage and are reported instead of being silently imputed.
- Unknown sample support is reported as unknown and conservatively discounted in evidence ranking.
- History ordering uses explicit `sort_key`, season/week values, or stable input order.
- Opponent adjustments require an explicit baseline for every compared metric.
- Roster and role transitions report only observed changes between supplied snapshots.
- Season comparisons default to higher-is-better unless a metric is explicitly marked lower-is-better.
- Small samples remain visible through snap counts and caller-selected minimum thresholds.
- Ranking and tie-breaking are deterministic, and result sizes are bounded.
- Saved reports remain in the current browser unless the user exports them; the API normalizes reports but does not claim server persistence.
- The workspace engine remains framework-independent and dependency-light.

## Next increment

v4.1 is complete with the scouting workspace. Scope the next version from deployed usage feedback and keep it behind a new versioned contract.
