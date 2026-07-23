# NFL Analytics Hub v4.3 — Model Lifecycle and Governance

v4.3 turns the repository's existing model metadata, evaluation utilities, and distributed
execution platform into a controlled model lifecycle. Every model version remains
reproducible, inspectable, evidence-gated, and reversible without weakening the model-honesty
rules established by earlier releases.

## Delivery phases

1. **v4.3.0 Registry foundation** — deterministic model/version identities, versioned feature
   schemas, artifact integrity, training provenance, conflict-safe registration, explicit
   lifecycle transitions, and promotion-policy contracts.
2. **v4.3.1 Automated evaluation** — held-out evaluation records, metric suites, integrity and
   compatibility checks, champion/challenger comparisons, and evidence-backed promotion
   decisions.
3. **v4.3.2 Retraining and rollout controls** — drift/performance triggers, retraining requests,
   shadow and canary rollout plans, rollback targets, and distributed execution integration.
4. **v4.3.3 Lifecycle operations** — persistent registry adapters, audit history, lifecycle
   health and alerts, approval controls, and an operator-facing model workspace.

## v4.3.0 endpoints

- `GET /api/v4.3/capabilities`
- `POST /api/v4.3/models/versions/normalize`
- `POST /api/v4.3/models/transitions/validate`
- `POST /api/v4.3/models/promotion-policies/normalize`

## v4.3.1 endpoints

- `GET /api/v4.3/models/evaluations/metrics`
- `POST /api/v4.3/models/evaluations/run`
- `POST /api/v4.3/models/champion-challenger/select`

## v4.3.0 Registry foundation

- Deterministic model and model-version identities derived from caller-supplied keys
- Content fingerprints for conflict detection without including registration timestamps
- Bounded, order-independent feature schemas with explicit types, sources, requirements, and
  optional defaults
- Optional artifact metadata with required SHA-256 integrity when an artifact is supplied
- Training dataset digest, code version, parameters, and start/finish provenance
- Registered, candidate, champion, retired, and archived lifecycle states
- Explicit transition rules with actor, reason, time, deterministic event identity, and bounded
  history
- Champion transitions require a passing policy/evaluation/evidence decision supplied by the
  evaluation layer
- Deterministic promotion policies with metric direction, thresholds, minimum improvement,
  minimum sample size, freshness, and required integrity checks
- A bounded in-memory reference registry for deterministic tests and local development

## v4.3.1 Automated evaluation and champion/challenger selection

- Deterministic held-out evaluation records over caller-supplied observations and dataset
  integrity metadata
- Six allowlisted metrics: MAE, RMSE, Brier score, log loss, binary accuracy, and ten-bin
  calibration error
- Candidate/champion metric comparisons using the exact threshold, direction, minimum
  improvement, sample-size, and all/any rules from the normalized promotion policy
- Observed artifact-digest verification and serving/feature-schema compatibility gates
- Bounded caller-supplied evidence for additional policy checks
- Evidence digests and evaluation identities that detect post-evaluation mutation
- Freshness-aware champion/challenger selection with explicit promote, retain, or no-selection
  outcomes
- Promotion-decision envelopes that are accepted directly by the v4.3.0 candidate-to-champion
  transition contract

## Guardrails

- Existing v3.x, v4.0, v4.1, and v4.2 contracts remain unchanged.
- v4.3.0 defines provider-neutral lifecycle contracts; it does not silently mutate the existing
  SQL model registry or claim production persistence.
- The registry records only caller-supplied model, feature, artifact, and training metadata.
- Artifact and dataset integrity use SHA-256 digests; artifact credentials and bytes are not
  stored in registry records.
- Reusing a model key and version with different metadata is a visible conflict.
- Feature names, types, sources, defaults, tags, metadata size, policy metrics, required checks,
  and lifecycle history are bounded.
- Model versions cannot skip directly from registered to champion.
- Champion promotion requires an explicit passing decision containing policy, evaluation, and
  evidence identities; v4.3.1 calculates that evidence only from supplied observations and
  verification metadata.
- Lifecycle events require an actor, reason, and monotonic timestamp.
- Archived model versions are terminal.
- Evaluation never mutates lifecycle state or silently promotes a model.
- Unsupported policy metrics, altered evidence records, stale evaluations, schema mismatches,
  and missing verification evidence fail visibly.
- Retraining, canary rollout, rollback, durable registry storage, and operator approvals remain
  later increments.

## Next increment

v4.3.2 should add drift and performance retraining triggers, distributed retraining requests,
shadow/canary rollout plans, and explicit rollback targets.
