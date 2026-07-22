# NFL Analytics Hub v3.0 — Phases 3–6

## Phase 3: quality and security

- Ruff linting and formatting checks are configured in `pyproject.toml`.
- Coverage is enforced at 75% for the complete suite and 85% for the isolated analytics package.
- Mypy checks the new typed analytics boundary.
- Bandit and pip-audit run in the quality workflow.
- The existing PostgreSQL/Redis CI remains the integration gate.

## Phase 4: analytics

`analytics_engine` provides deterministic, explainable primitives for live win probability, EPA, drive success, Monte Carlo simulation, power ratings, injury impact, player similarity, and matchup intelligence. The API adapter is available under `/api/v3/analytics`.

The initial models are transparent baselines, not black-box claims. Each response includes components or reasons that can be surfaced in the UI and audited during model review.

## Phase 5: UI/UX

The shared theme includes a responsive dashboard foundation, premium dark surfaces, visible focus states, reduced-motion handling, and standardized loading, empty, and error presentation classes.

## Phase 6: release readiness

Before merge:

1. Run database migrations on a disposable PostgreSQL database.
2. Run all unit and integration tests with coverage.
3. Run Ruff, mypy, Bandit, and pip-audit.
4. Build the production Docker image.
5. Execute browser smoke tests against the built image.
6. Load-test `/health`, `/ready`, and high-volume analytics endpoints.
7. Review secrets, CORS, authentication, rate limiting, and provider fallbacks.
8. Confirm rollback and backup procedures.
9. Require a completely green CI run and explicit owner approval.
