# Changelog

EmbodiScope follows semantic versioning for assessment releases. This file records deliverable-level changes and does not claim a Git commit history.

## 2.3.0 - 2026-07-23

### Added

- RecoveryLab paired Failure/Recovered experiments with fixed environment, seed, fault, camera, and horizon.
- Online recovery triggers for collision, gripper command/response mismatch, and grasp-state transitions.
- RecoveryBench across three fault scenarios and multiple admitted seeds, including Wilson 95% confidence intervals.
- Independent task-recovery, full-episode safety, and post-intervention safety conclusions.
- Task graph, grounded predicates, first-failed-predicate explanations, and local recovery operators.
- Assessment deck, 15-minute talk track, 3-minute live demo route, Q&A, backup video, and automated preflight.

### Changed

- Promoted the assessment workflow to the first section of the README.
- Locked formal benchmark evidence to versioned JSON artifacts and explicit seed admission rules.
- Expanded third-party notices to distinguish runtime dependencies, method references, and original implementation.

### Validated

- FaultBench Macro F1: 98.4%.
- RepairBench repair-action success: 100.0%, with all 6 quality gates passing.
- RecoveryBench strict task recovery: 8/9 (88.9%), Wilson 95% CI 56.5%-98.0%.
- Full-episode safety: 66.7%; post-intervention safety: 100.0%.
- Online-trigger coverage and pair integrity: 100.0%; recovery latency p95: 3.93 s.
- Automated tests: 58/58, plus JavaScript syntax validation.

### Known Limits

- RecoveryBench currently uses `PickCube-v1`, Franka Panda, and three controlled fault scenarios.
- Results do not establish multi-task, cross-robot, sim-to-real, or certified functional-safety generalization.
- The diagnostic root-cause layer is explainable and rule-based; open-vocabulary video semantics are future work.

## Previous Milestones

- `v1.7`: RepairBench and dataset-level repair delivery with traceable artifacts.
- `v1.6`: Conservative, reversible cleaning plans generated from diagnostic evidence.
- Earlier versions: unified multimodal ingestion, diagnostic scoring, synchronized replay, and FaultBench.
