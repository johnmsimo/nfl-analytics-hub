# P2.5 — Repository Quality Gates

P2.5 raises the minimum engineering quality required for every change without excluding production code from coverage to make the numbers look better.

## Coverage policy

The repository-wide branch coverage floor is centralized in `pyproject.toml` at **68%**. This replaces the historical workflow-only 60% threshold. The current baseline before P2.5 was approximately 69.39%, so the new floor is intentionally close enough to prevent material regression while retaining a small operating margin for normal development.

The focused analytics suite increases from **85% to 90%** coverage for the v3.1 analytics engine modules.

Coverage still includes production modules repository-wide and continues to omit only tests, migrations, and local virtual-environment files.

## Lint policy

Quality CI now runs two Ruff layers:

1. A repository-wide correctness pass using high-severity Python rules (`E9`, `F63`, `F7`, `F82`). This ensures every Python file is checked for syntax/compiler errors, invalid constructs, and undefined-name failures.
2. The configured strict Ruff rules (`E`, `F`, `I`, `B`, `UP`, `SIM`) on the actively maintained production quality baseline, including the canonical P2.4 API lifecycle and routing surfaces.

Formatting checks cover the same modern production baseline.

This staged model raises repository-wide protection immediately without forcing unrelated legacy cleanup into a single risky deployment. Future phases can widen the strict baseline as older modules are touched and modernized.

## Regression protection

`tests/test_quality_policy_p25.py` prevents later changes from silently lowering the repository coverage floor, restoring the former 60% CI override, removing repository-wide correctness lint, dropping the canonical API from strict lint, or lowering analytics coverage below 90%.

## Exit criteria

P2.5 is complete when:

- full repository pytest passes at the new 68% coverage floor;
- the production Docker image builds;
- repository-wide correctness lint passes;
- strict Ruff lint and formatting pass on the modern production baseline;
- MyPy, Bandit, and dependency audit remain green;
- the analytics suite passes at 90% or higher coverage.
