# v3.0 release checklist

## Code quality
- [ ] Ruff check passes
- [ ] Ruff formatting check passes
- [ ] Mypy passes for typed boundaries
- [ ] Coverage threshold passes
- [ ] No high-severity Bandit findings
- [ ] No unresolved dependency advisories

## Data and analytics
- [ ] Migrations upgrade from the current production revision
- [ ] Migration rollback tested on a disposable database
- [ ] Win-probability calibration reviewed against held-out games
- [ ] Monte Carlo output is reproducible with fixed seeds
- [ ] Injury and similarity inputs are documented
- [ ] Matchup explanations match calculated components

## Product quality
- [ ] Desktop and mobile navigation smoke-tested
- [ ] Loading, empty, and error states verified
- [ ] Keyboard navigation and visible focus verified
- [ ] Reduced-motion behavior verified
- [ ] Primary pages meet accessibility contrast expectations

## Operations
- [ ] Docker image builds
- [ ] Readiness fails safely when the database is unavailable
- [ ] Redis fallback behavior tested
- [ ] Scheduler remains disabled in web workers unless explicitly enabled
- [ ] Load test completed with documented limits
- [ ] Rollback procedure rehearsed
- [ ] Documentation audited
- [ ] Full CI green
- [ ] Owner approval recorded
