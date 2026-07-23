# NFL Analytics Hub v4.1 — Advanced Scouting Intelligence

v4.1 builds a transparent scouting layer on the v4.0 decision, simulation, and insight foundations. It turns supplied player, team, personnel, formation, and play-level features into comparable and inspectable scouting outputs.

## Delivery phases

1. **v4.1.0 Scouting foundation** — player similarity, deterministic team-style clustering, personnel/formation tendencies, explainable feature differences, and versioned API contracts.
2. **v4.1.1 Matchup intelligence** — matchup strengths, exploitable weaknesses, tendency-vs-tendency comparisons, and evidence-ranked matchup briefs.
3. **v4.1.2 Scouting history** — time-aware tendency changes, roster and role transitions, opponent-adjusted splits, and season-over-season comparisons.
4. **v4.1.3 Scouting workspace** — player comparison, team-style maps, tendency explorers, matchup cards, saved reports, and mobile review flows.

## v4.1.0 endpoints

- `GET /api/v4.1/capabilities`
- `POST /api/v4.1/scouting/player-similarity`
- `POST /api/v4.1/scouting/team-styles/cluster`
- `POST /api/v4.1/scouting/tendencies`

## v4.1.0 capabilities

- Range-normalized player similarity across explicitly supplied metrics
- Ranked player matches with feature coverage and largest differences
- Deterministic k-means team-style clustering
- Inspectable cluster membership and style signatures
- Personnel, formation, and personnel/formation-combination usage
- Pass rate, success rate, explosive rate, and yards-per-play summaries
- Bounded result sizes, cluster counts, iteration counts, and sample thresholds

## Guardrails

- Existing v3.x and v4.0 contracts remain unchanged.
- Scouting results use only supplied structured inputs; the engine does not invent player, team, tracking, or play data.
- Similarity features, coverage, differences, cluster memberships, and tendency sample sizes remain inspectable.
- Clustering initialization and tie-breaking are deterministic.
- Missing numeric features reduce coverage instead of being silently treated as measured values.
- Small samples remain visible through snap counts and caller-selected minimum thresholds.
- The engine remains framework-independent and dependency-light.

## Next increment

v4.1.1 should compare offensive and defensive scouting profiles to identify matchup strengths and exploitable weaknesses, with every claim linked to supplied metrics and sample context.
