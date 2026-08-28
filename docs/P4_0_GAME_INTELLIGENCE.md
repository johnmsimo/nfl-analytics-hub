# P4.0 — Game Prediction Intelligence Foundation

P4.0 begins the game-level prediction track after the P3.x player-prop stack.
The first contract is a transparent, warehouse-backed **moneyline model** that
produces useful model decisions without pretending sportsbook prices are model
evidence.

## Why this phase exists

The app already had game lines and market consensus, but those surfaces were
market-relative. P4.0 adds an independent game model so the platform can answer:

- which team the model favors
- the estimated win probability for both teams
- the expected home margin
- how strong the evidence is
- whether distribution confirmation agrees
- why the model prefers the selected side
- what the principal uncertainty/risk is

## Evidence model

Team strength is calculated from normalized warehouse facts:

- point differential per game
- win percentage
- offensive EPA per play when available
- defensive EPA per play when available
- offensive/defensive success rates when available
- explicit home-field adjustment

During preseason and the first weeks of a new regular season, the model uses the
prior regular season until the current season reaches the configured evidence
floor. The response always exposes the exact evidence season and mode.

## Probability contract

P4.0 preserves multiple inspectable quantities:

1. raw logistic home-win probability from model margin
2. evidence-aware probability shrinkage toward 50% when support is weaker
3. deterministic normal-margin distribution confirmation
4. a model-dominant consensus probability
5. simulation agreement and evidence-quality scores

Home and away probabilities always complement to 1.0.

## Decision contract

Every modeled game receives one grade:

- `Strong Play`
- `Play`
- `Lean`
- `Pass`

The chosen side/team, selected probability, confidence score/grade, reasons and
risks are returned with the decision.

### Critical separation from sportsbook actionability

P4.0 is deliberately **model-only**:

- `actionable=false`
- `priceStatus=model-only`
- no Odds API request is made by the P4.0 endpoint or verifier
- no edge, EV, or Kelly claim is made without a verified sportsbook quote

A later P4.x increment can join this game model to live moneyline/spread/total
pricing using the same price/actionability discipline established in P3.6.

## API

`GET /api/game-decisions/week?season=2026&week=1&type=REG`

The response includes the complete weekly model decision board, ordered by:

1. decision grade
2. confidence score
3. selected win probability
4. deterministic game ID tie-break

## Default policy

- current-season evidence floor: `4` completed regular-season games
- home-field adjustment: `1.5` points
- logistic scale: `6.5`
- margin distribution standard deviation: `13.5`

Optional environment overrides:

- `P40_CURRENT_SEASON_GAME_FLOOR`
- `P40_HOME_FIELD_POINTS`
- `P40_LOGISTIC_SCALE`
- `P40_MARGIN_SD`

## Production gate

Run **P4.0 Game Intelligence Verification** with:

`RUN_GAME_INTELLIGENCE_VERIFY`

The verifier is read-only and targets the 2026 regular-season Week 1 slate. It
requires:

- all 16 games present
- a model decision for all 16 games
- no missing team evidence
- valid complementary probabilities
- valid decision/selection contracts
- at least four Lean-or-better model selections
- zero actionable selections in this model-only phase
- explicit evidence provenance
- synthetic proof that weak evidence shrinks probability toward 50%
- inherited P2/P3 safety verification remains green

The workflow makes no Odds API calls and performs no production writes.
