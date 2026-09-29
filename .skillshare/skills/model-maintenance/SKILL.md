---
name: model-maintenance
description: Maintain a private released model asset without overwriting its history. Use for bug fixes, Python 3.11 migration, formula or threshold changes, new plant adaptation, repackaging, or retirement of an existing model.
---

# Model Maintenance

Inherit the task's interaction mode from `$model-lab-router`; do not ask for a
second mode choice or run the new-model questionnaire. Apply the maintenance row
of `docs/00-入口层/06-模型任务问答与交互档位.md`: inspect the parent first, carry
forward unchanged confirmed facts, and present only the baseline, actual
signature, or proposed-delta conflicts that require a business decision.

Locate the asset through the private model directory or its private generated catalog, then read only that
model's `model.yaml`, current release snapshot, parameter record, behavior baseline,
and relevant source. Keep the existing model directory, name, historical source,
Notebook, decisions, and release snapshots intact.

Before calling a Python migration a behavior-unchanged patch, compare the parent
Run's actual `service/MLmodel` input/output signature and Python version with the
archived manifest and deployment script. A manifest may omit evaluation-only
`*_target` fields that the old Run nevertheless marked required; a valid local
manifest or release gate does not prove the published signature matches it.
Record the Run ID and artifact digest as evidence. Do not silently add such targets
to new inference inputs, rewrite a released snapshot, or treat a point-code-array
request as compatible with an old numeric signature. If the actual online contract
changes, keep the patch as an archive and use a separate contract-upgrade version.
When the deployed Run is not identified, mark it unresolved; the latest or only
Run in an experiment is a candidate, not proof of what the platform serves.

For behavior-unchanged Python 3.11 work, use `model-lab maintain <released-model>
--version <next-patch>` to preview, then `--apply` inside the assigned local task.
It creates a separate versioned workspace under `models/_drafts/` while keeping
the released directory untouched. Verify its parent lineage with `status` and
the whole repository with `release-gate`; never edit the old `released` manifest
to `draft` or use ordinary `new` with the same model ID. The new workspace must
use an independently verified old behavior baseline before it can be promoted.
The current maintenance fork supports CPU-IoT assets; do not guess GPU/Chronos
runtime requirements.

If the platform input changes from numeric readings to point-code arrays,
do not retrofit the patch. Use `model-lab maintain <released-model> --version
<next-minor>.0 --change-kind contract-upgrade` for a separate draft. One verified
patch and one verified contract-upgrade draft may coexist with the released
parent. A contract-upgrade draft must explicitly define point bindings, time
window, timezone, and `serving.feature_adapter: source-history-v1`; its
`prepare_features(raw, request)` must reproduce the old resampling/rolling
behavior from the complete IoT window. Preserve the parent baseline as archive
evidence, and create a separate baseline for the changed online contract.
Never mark model-specific point/window decisions confirmed from a generic
example. Group currently independent model-specific questions in one review
round and record the full answer once; do not request another confirmation for
the same unchanged answer after `maintain --apply` or `advance --apply`.

First classify the work:

- Deployment-only repair: runtime or packaging changes with fixed behavior. Prove the
  existing baseline still matches.
- Behavior change: formula, threshold, input/output contract, measurement mapping, or
  anomaly policy changes. Open a new semantic model version and use the applicable
  requirement, data, build, and validation skills.
- Retirement: preserve the final released snapshot and require explicit confirmation.

Do not retrofit old parameter records into `decisions.yaml`. Create a decision record
for the new repair or version only. Mark replaced decisions `superseded` and point them
to the new decision; never delete or silently overwrite the old answer.

Use Python 3.11 for every new repair version. Do not execute historical Python 3.12
deployment entrypoints. Real publication, Git remote changes, and merges require an
explicit instruction covering the specific target and content; push alone does not
imply merge. These operations remain subject to the repository's disabled publishing
backend. Local repair work within the user's stated model task does not need per-step
authorization.
