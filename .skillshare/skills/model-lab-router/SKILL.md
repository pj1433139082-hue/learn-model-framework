---
name: model-lab-router
description: Route model-laboratory work in this repository to the minimum lifecycle skill and evidence set. Use for new models, model changes, data checks, validation, runtime packaging, deployment preparation, or maintenance of an existing model.
---

# Model Lab Router

Start from `docs/00-入口层/00-模型开发总览.md` and
`docs/00-入口层/03-模型任务分流与加载矩阵.md`. For a task that may write,
also read `docs/00-入口层/06-模型任务问答与交互档位.md`. Do not preload every
model or historical document.

Use the framework's `scripts/model-lab.ps1` or `bash scripts/model-lab.sh` launcher,
not a bare interpreter from an old model checkout. First use and updates from
`main` use the bootstrap helper; the manual fallback is entry document 04.
If Skillshare is unavailable, read the selected source skill under
`.skillshare/skills/` directly. Missing generated skills or an untrusted hook
do not add a model approval gate.

## Route

- New model or an unresolved business design: use `$model-requirements-grill`.
- Data availability, measurement mapping, or anomalous values: use
  `$model-data-audit`.
- Source, Notebook, tests, plots, or behavior validation: use
  `$model-build-validate`.
- Python, MLflow bundle, GPU/Chronos runtime, packaging, rollback, or release
  preparation: use `$model-runtime-release`.
- Any change to a released model asset: use
  `$model-maintenance` first.

For an in-service model, classify a Python 3.11-only patch separately from a
point-code-array or historical-window input change. The latter takes a new
minor version via `maintain --change-kind contract-upgrade`; do not rewrite an
archived patch draft or treat an example JSON as the actual platform signature.
For an unreleased, releasable numeric draft that needs point-code inputs, use
`model-lab draft-fork <draft> --version <next-minor>` instead of pretending its
parent is released or creating an unrelated model ID. The fork preserves the
parent and its behavior baseline; only unchanged confirmed decisions carry
forward. New serving fields, time policy, and online acceptance remain open.

If the intent is ambiguous, inspect the target workspace and run `model-lab status`
before asking. Ask only decisions that cannot be derived from repository evidence.

For a specified model version, use `model-lab context <workspace> --task <intent>`
to recover the current minimal document set and check its generated
`workflow-state.json`. The JSON is a committed index, not a second fact source or
a live platform query; verify from `model.yaml`, `decisions.yaml`, bindings and
per-Run publication receipts. A numeric model needs no point metadata; a
point-code model with missing metadata is unknown, not N/A. If the Codex hook
was not trusted or is unavailable, `AGENTS.md` plus this CLI are the full
fallback. Do not use a repository-global active-model pointer; when multiple
versions fit the request, ask once for the ID and version.

## Interaction contract

First classify the user's intent using the scenario table in entry document 06.
For a new mutable task, show a three-choice selector **once in the first reply**:
`full-auto` (local work continuous; stop for open business decisions), `assisted`
(recommended; batch decisions), or `manual-review` (review each material local
write/build/transition). Use a structured three-option UI when available, or the
same three numbered options in text. This selector is non-blocking: if the user
does not answer, continue the already assigned task in `assisted`; do not add a
new authorization gate. “按推荐” and “采用默认值” also choose `assisted`. An explicit
mode choice, a recorded choice for this same task, or a read-only request needs
no selector. On resume, restore the recorded mode instead of asking again;
silence never selects `full-auto`.
The task-scoped modes are `full-auto`, `assisted` (default), and `manual-review`.
This is an execution preference, not a model target, a `model.yaml` fact, a
`decisions.yaml` business answer, or a standing publication authorization. If
the user has not selected a mode, use `assisted` for the assigned task.
CPU/GPU/Chronos and numeric/point-code do not select the mode.

- `full-auto`: continuously perform in-scope local plans, explicit `--apply`
  steps, tests, builds, and gated transitions. Stop and batch any open required
  business decisions; never infer an answer merely because this mode is selected.
- `assisted`: automate evidence and local steps, present independent business
  decisions together with recommendations, then continue on the recorded answer.
  Do not ask before each local command. This is the default for released-model
  Python 3.11 maintenance.
- `manual-review`: automate read-only investigation, but show the plan and wait
  before each material local write, build, or lifecycle transition. Do not ask
  for permission to perform each read-only check.

All three modes retain the same machine gates and the same bounded `AUTHORIZE`
rule for Git remote transactions and real platform publication. A previously
approved task may cover a specified sequence of unchanged remote operations;
upload never implies production scheduling. Record the scenario, selected or
defaulted mode, next action, and pending decision IDs using
`model-lab task-plan <workspace> --task-id <explicit-task-id>` with `--apply`
when saving. This record belongs to that private model task, not
`decisions.yaml`; it never stores standing remote authorization. Restore the
same explicit task ID rather than guessing the latest task. See entry 06 and
ADR 0018. For framework-only work, use a dated review/acceptance record instead.

- `AUTO`: inspect and verify without asking.
- `ASK`: request missing information; only a required item blocks its transition.
- `CONFIRM`: present a concrete recommendation and wait for an explicit answer.
- Local execution: an explicit model task covers reversible writes in that model's
  workspace, local bundle builds, tests, and gated transitions through
  `releasable`. Keep `--apply` and every machine gate; do not ask for a new
  authorization before each local write or transition. Stop if the model, business
  contract, file scope, or destructive impact changes materially.
- `AUTHORIZE`: require an explicit, bounded instruction for a Git remote
  transaction or real platform publication. Do not infer merge from push or
  production scheduling from upload. Never store authorization as a standing
  decision in `decisions.yaml`.

Work in rounds according to the scenario table in entry document 06, not one
generic questionnaire for every model. A read-only diagnosis has no mode or
business-confirmation dialog. A behavior-preserving maintenance task asks only
about baseline or contract conflicts; a contract or algorithm upgrade asks for
the changed business fields, not the whole old model again. Data audit derives
statistics and asks only treatment choices. Build/validation derives metrics and
asks about deviations or the required conclusion. Publication uses the already
given experiment/model number and author/account label. A user's explicit
request to upload a specified model/version to a specified experiment is the
bounded instruction for that upload; do not ask for a second ceremonial
confirmation. The selected target must have an implemented publisher; upload does not include
Git push/merge or production scheduling. Derive objective facts from evidence first;
ask only genuine business choices. Ask all currently independent questions
together, number them, and give a recommendation for each. If the user answers
“按推荐” or “全部按推荐”, store the full recommendation for each **presented**
decision, not the shorthand phrase. Do not confirm an unpresented or
evidence-dependent future decision. Do not request a separate confirmation
merely to write the already confirmed answer to a file or run the next local
command.

When a required `ASK` or `CONFIRM` decision blocks the next transition, deliver
the numbered questions **before ending the turn**. First send a user-visible
message with each question, recommended answer, and alternative choice; then
try the structured choice UI when available. Its dispatch acknowledgement does
not prove the user saw it. Never make “see the popup” the only place to find or
answer a question. If no answer arrives, repeat the choices in the final handoff
so it stands alone. A text reply is equally valid if the UI is unavailable or
invisible. After a reply, map it only to that delivered batch, record
the full answers, rerun `status`, and continue permitted local transitions
without another permission prompt. Do not use the choice UI as a substitute
for remote authorization or treat an unanswered recommendation as approval.

## Resume and closure contract

After a long conversation, context compaction, handoff, or a bare “继续”, do not
rely on chat memory as the source of project state. Reopen the entry/loading
matrix, identify the active model or framework task, inspect Git worktree
changes, and run `model-lab status` for the active model. Read the explicit
`task-plan` to recover this task's interaction mode, next action, and pending
questions; do not reopen the selector if it is recorded. Check the framework
source/commit in `context` and derive current facts anew rather than executing
an old next-action string blindly. A task record is not remote authorization.
Reconstruct confirmed business answers from `decisions.yaml` and evidence from
`model-spec.md` and the latest dated validation or publication record. A
hash-pinned pre-publication `model-spec.md` may preserve an earlier backend
state; do not use that historical sentence to override a later verified Run.
For a framework-only task without one
model workspace, use
the relevant test profile and a dated acceptance record. Never convert an old
chat recommendation into a confirmed decision without the user's actual answer.

Before handing control back to the user, always state: (1) what was verified
versus not verified, (2) the current lifecycle or acceptance gate, (3) the
single next safe action or a small ordered set of actions, and (4) any exact
business answer or bounded remote authorization genuinely needed. If no user
decision is needed, give the next action without a performative confirmation
prompt. A passing generic smoke or repository gate must not be described as
model-specific acceptance or platform publication.

Keep three outcomes separate when closing a release task: a verified
`publish --apply` Run with matching downloaded service and platform-path
prediction is MLflow publication; business-platform point binding/task
invocation is a separate integration check only when requested; the local
`releasable -> released` transition is snapshot archival and is not automatic.
Do not call an already verified MLflow upload unfinished because `status` still
says `releasable`, Git was not pushed, a business-platform task was not invoked,
or automatic scheduling was intentionally left off. For a one-time manual model,
"no schedule" is a complete operating choice. See ADR 0017.

`model.yaml` remains the identity, contract, version, and lifecycle fact source.
`decisions.yaml` records questions and confirmations. Explanations and evidence stay
in `model-spec.md` and validation files. Never mark a decision confirmed before the
user answers.

CPU, GPU, and Chronos are target adapters, not separate workflows. The project is
self-contained and must not require a global Ask Matt installation. `model-lab publish`
has a narrow fixed-bundle publisher for accepted CPU-IoT point-code models: preview
is read-only and `--apply` uploads the already-built service through the digest-pinned
company package, then re-downloads and predicts. Other target/profile combinations
remain unsupported until implemented; a network failure is not an approval gate.
For a publication request, user-facing inputs are the platform
experiment name/model number and author/account label; derive the target from the
validated `model.yaml` and verify the bundle automatically. Resolve the active model
workspace from the task context and `status` instead of asking for its path again.
The author label is not an authentication credential. Do not ask the user for endpoint,
secret, Python ABI,
or artifact digest if the environment and repository can determine them; report a
real missing dependency once instead of repeating authorization questions.
