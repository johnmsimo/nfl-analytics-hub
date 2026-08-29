# P5.2 — Game Calibration Control Plane

P5.2 unifies the three existing game-calibration governance layers into one canonical operational decision:

- **P4.9** — is the current challenger forward-holdout eligible for human review?
- **P5.0** — does the candidate also clear production moneyline promotion gates, and what champion is active?
- **P5.1** — if a promoted champion is active, is post-promotion performance healthy or does it warrant rollback review?

Before P5.2, clients or operators had to interpret those three payloads independently. P5.2 centralizes that logic so no UI, workflow, or operator script can accidentally invent a looser promotion or rollback rule.

## Canonical states

The control plane returns exactly one operating state:

- `challenger-collecting` — more graded evidence is required;
- `baseline-monitor` — baseline remains active and no candidate clears every promotion gate;
- `promotion-review` — P4.9 + P5.0 gates clear and an owner may explicitly review promotion;
- `champion-collecting` — a promoted champion is active but P5.1 needs more post-promotion evidence;
- `champion-healthy` — the active promoted champion remains within guardrails;
- `champion-monitor` — a champion is active but the guard is not in a terminal healthy/rollback-review state;
- `rollback-review` — P5.1 detects material post-promotion regression and owner rollback review is recommended.

## Promotion readiness

`promoteReady=true` requires all of the following at the same time:

- no promoted champion is currently active;
- P4.9 exposes a current candidate ID;
- P4.9 state is `review`;
- P4.9 promotion gate is eligible;
- P5.0 moneyline production promotion review is eligible.

The control plane exposes the exact P5.0 endpoint, candidate ID, and required confirmation token, but performs no write itself.

## Rollback readiness

`rollbackReady=true` requires:

- a promoted P5.0 champion is active; and
- P5.1 explicitly recommends rollback review.

An owner may still use P5.0's explicit rollback endpoint whenever a promoted champion is active, but P5.2 distinguishes **allowed** from **recommended** so an operator cannot confuse availability with model-health guidance.

## API

Read-only status:

- `GET /api/game-calibration/control-plane`
- `GET /api/tracker/game-calibration-control-plane`

Important fields:

- `state`
- `recommendedAction`
- `candidateId`
- `championCandidateId`
- `promoteReady`
- `rollbackReady`
- `blockers`
- `evidence`
- `commands.promotion`
- `commands.rollback`
- `safetyContract`

## Safety contract

P5.2:

- is read-only;
- performs zero sportsbook/provider requests;
- writes zero promotion events;
- never automatically promotes a candidate;
- never automatically rolls back a champion;
- requires the existing P5.0 owner-confirmed mutation endpoints for either action;
- changes no model probability;
- changes no actionability threshold;
- changes no bankroll/Kelly policy;
- places no wagers.

## Production verification

After merge/deployment, run **P5.2 Game Calibration Control Plane Verification** with:

`RUN_GAME_CALIBRATION_CONTROL_PLANE_VERIFY`

The verifier accepts every legitimate current production operating state, proves the safety contract remains zero-credit/read-only, proves synthetic promotion-review, healthy-champion, rollback-review, and collecting states resolve correctly, proves P4.4 receipts and P5.0 promotion history remain unchanged, and reruns inherited P2/P3 safety verification.
