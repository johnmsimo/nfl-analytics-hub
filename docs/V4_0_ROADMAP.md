# NFL Analytics Hub v4.0 — AI Decision Intelligence

v4.0 evolves the deployed analytics platform into a decision-support system that combines multiple models, explains uncertainty, evaluates scenarios, and surfaces actionable intelligence.

## Delivery phases

1. **Decision foundation** — reliability-weighted ensembles, automatic primary-model selection, model disagreement, confidence, scenario adjustments, and grounded decision briefs.
2. **Simulation laboratory** — distribution-based game simulations, injury/weather/lineup overrides, sensitivity analysis, and reusable scenario presets.
3. **AI insight layer** — structured driver analysis, prediction-change explanations, upset alerts, confidence reasoning, and evidence-linked recommendations.
4. **Advanced scouting** — player similarity, team-style clustering, personnel and formation tendencies, matchup strengths, and exploitable weaknesses.
5. **Distributed intelligence platform** — Redis streams/pub-sub, background model workers, distributed caching, idempotent jobs, and horizontal scaling.
6. **Model lifecycle** — model registry, champion/challenger selection, feature/version metadata, automated evaluation, retraining triggers, and rollback controls.
7. **Enterprise access** — organizations, roles, shared workspaces, audit trails, API keys, quotas, and public decision APIs.
8. **Decision workspace** — scenario builder, ensemble comparison, evidence drawer, decision history, alerts, and mobile-first review flows.

## First increment

### Endpoints

- `GET /api/v4/capabilities`
- `POST /api/v4/decisions/ensemble`
- `POST /api/v4/decisions/scenario`
- `POST /api/v4/decisions/brief`

### Capabilities

- Reliability weighting based on calibration, recency, and sample size
- Automatic primary-model identification
- Ensemble probability and side selection
- Model-disagreement and confidence measurements
- Scenario probability adjustments with bounded impacts
- Ranked decision drivers
- Low/moderate/high decision-risk classification
- Deterministic, framework-independent tests

## Guardrails

- v3.x API contracts remain unchanged.
- Decisions are grounded in supplied structured model outputs; the engine does not invent source data.
- All scenario changes are explicit and returned with their reasons.
- Reliability weights and model contributions remain inspectable.
- The initial engine is dependency-light so it can later move into background workers without changing the public contract.

## Next increment

The next v4.0 increment should add a distribution-based simulation laboratory with reusable injury, weather, pace, turnover, and market scenarios, plus sensitivity analysis across the most important assumptions.
