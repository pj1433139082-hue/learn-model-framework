---
name: model-runtime-release
description: Prepare and verify Python 3.11 model runtime and MLflow release artifacts without assuming publication authority. Use for environment diagnosis, CPU/GPU/Chronos runtime contracts, bundle preflight, deployment planning, rollback planning, or release authorization.
---

# Model Runtime and Release

The active training, validation, packaging, and deployment contract is Python
`>=3.11,<3.12`. Run `model-lab doctor` before build work and `model-lab
bundle-preflight` against the single-model MLflow bundle before release preparation.
Do not package a local interpreter or compress the whole source checkout.

Follow the task-scoped interaction mode selected by `model-lab-router`
(`full-auto`, default `assisted`, or `manual-review`). The mode changes when to
pause, never the runtime, behavior-baseline, or publication gate. For a batch
of models, report per-model readiness and blockers together; do not ask for a
new authorization for each local command or repeat an unchanged platform
question. Use the publication row in
`docs/00-入口层/06-模型任务问答与交互档位.md`; do not reopen
the mode selector here. A missing required business decision still blocks
`full-auto`.

Inspect target OS, architecture, Python ABI, MLflow metadata, dependency locks, and
native runtime compatibility as facts. For GPU or Chronos, also verify actual compute
capability, memory, CUDA, framework version, and artifact format. Never guess missing
platform parameters or reuse a Python 3.12 PyArmor runtime for Python 3.11.

From a Git checkout, install the optional `.[test]` dependency group into the
active project Python 3.11 environment before the full regression. A base
installation is enough for `doctor/new/status`, not the MLflow-heavy test suite.
For real S3/MinIO publication, install `.[test,publish]` in the same target Linux
3.11 environment: `publish` adds the `boto3` client used by MLflow's artifact
store. It is a publisher dependency, not a model-service lock dependency.
Use `model-lab test-profile local` for the ordinary offline regression, then run
`test-profile linux-offline` on target Linux Python 3.11 with the reviewed company
311 ZIP. Run `test-profile linux-online` only with explicit company-network opt-in
and a recent start time; it verifies only the configured model's network probe, not
every model's historical window. Report each profile separately. `PARTIAL`, `INCOMPLETE`, or
`BLOCKED` is not target-runtime acceptance; the optional `windows-network`
diagnostic cannot substitute for Linux evidence.

For a historical point-code model, use `serving.feature_adapter:
source-history-v1` and implement `prepare_features(raw, request)` in the single
model source. The service queries the declared time window in bounded chunks,
passes all in-window readings to this function, and requires one numeric feature
row with the declared fields. Empty chunks, missing points, and non-finite
features fail closed. Offline build success does not prove company IoT access or
old/new behavior parity; retain both online and fixed-window acceptance gates.

Require confirmation of deployment and rollback before advancing to
`releasable`; derive the intended operating mode from the already confirmed
requirements, and ask only if it is absent or conflicts with new evidence.
A confirmed one-time manual mode with no
automatic schedule is a complete operations plan; do not ask for a scheduling
frequency, production monitoring, or alert ownership that this task does not
have. Do not create a second `operations-plan` confirmation for information
already answered in `requirements-context`.
For an actual scheduled or production task, confirm its monitoring, alerting,
and maintenance plan before enabling those operations. Present all currently
independent plans together with separate recommendations so one explicit reply
can answer that round; do not ask again when the same answers are already
recorded. Evidence-dependent plans remain open until their evidence exists.
Record full recommendations and answers in `decisions.yaml`; the status decision
summary provides the digest that a future immutable release snapshot must bind.

An explicit model-development task covers local `new --apply`, `bundle-build --apply`,
and gated `advance --apply` through `releasable`; do not ask for a fresh authorization
for each local step. Git remote changes and real publication require a bounded,
explicit task instruction. A user's clear request to upload one specified
model/version to one experiment is that transaction's instruction; do not ask
for a second confirmation of the same unchanged upload. A push does not
authorize a merge unless the instruction
clearly includes it; changed target or content requires renewed direction. Do not
store `AUTHORIZE` as a standing decision.

For publication, ask the user only for the platform experiment name/model number and
the author/account label if they are not already known. Resolve the target from the
validated `model.yaml`; check Python, bundle digest, and platform prerequisites in
the framework. An author label is not a login credential. For an accepted CPU-IoT
point-code model, `model-lab publish` now has a fixed-bundle path: use `--apply` only
under the existing bounded user instruction, on the declared Linux Python 3.11
runtime, with the already verified company ZIP. It checks the bundle and online
receipt before connection, uses the company's `service` artifact convention, then
re-downloads the same bytes and runs a platform-path prediction. Never run historical
`deploy_*.py` files or claim a preview, local bundle, experiment ID, or failed Run
is a completed release. If the tracking host is unreachable, report the network
blocker and `remote_writes=false`; do not ask the user to authorize the same upload
again. Other target/profile combinations remain unsupported until implemented.

After a successful fixed-bundle upload, report the verified Run, matching
downloaded service digest, and platform-path prediction as MLflow publication
success. Do not require a company business-platform manual task call, production
scheduling, Git push, or local `released` status to make that claim. Report any
unverified business-platform integration separately, only as relevant to the
user's scope. `model-lab status` is a local lifecycle check; its manual
`releasable -> released` transition still needs a release snapshot but does not
invalidate a completed MLflow Run. See ADR 0017.
