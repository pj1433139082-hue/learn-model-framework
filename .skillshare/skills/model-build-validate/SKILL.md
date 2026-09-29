---
name: model-build-validate
description: Build and validate a model after its contract is confirmed, producing reproducible source, Notebook, tests, plots, artifacts, and behavior evidence. Use for implementation, model analysis, test work, visualization review, or behavior-baseline validation.
---

# Model Build and Validate

Inherit the task's interaction mode and scenario from `$model-lab-router`;
do not ask for a second mode choice. Use the build/validation row in
`docs/00-入口层/06-模型任务问答与交互档位.md`.

Begin with `model-lab status`. Do not implement while required `specified` decisions
remain open. Treat `model.yaml` as the contract and preserve the Python 3.11 runtime
profile.

Implement the smallest reproducible model slice, then run the Notebook from the start,
execute tests, record artifact digests, and create validation evidence. Automatically
calculate metrics and generate plots; do not ask the user to verify facts that the
environment can prove.

Ask for confirmation when a result changes the agreed acceptance interpretation,
selects between meaningful tradeoffs, accepts a deviation, or freezes the validation
conclusion. Present metrics, plots, baseline differences, risks, and a recommended
answer. Batch all currently independent acceptance decisions; do not ask the
user to approve each test, plot, or local build step. After the user confirms,
write the full answer to the relevant
`decisions.yaml` entry and update `validation/validation-report.md` or
`model-spec.md`.

Framework-only migration of an existing model must preserve its fixed-input behavior
baseline. A formula, threshold, output, or anomaly-policy change is a new model version
and must be reviewed as behavior change. Never treat passing tests or a dry run as a
real release.

For framework workflow changes, use `docs/30-触发层/31-从零使用验收流程.md` in
addition to automated tests. A no-context novice follows the entry layer and
uses only a temporary workspace; the main agent verifies target-runtime test
profiles and records commands, exits, and remaining gates. Distinguish a dirty
working-tree trial from a clean-clone acceptance. If the clean-clone condition
is not met, report conditional/local acceptance and its exact next step rather
than silently freezing a final delivery conclusion. See the router's resume
and closure contract before ending the task.
