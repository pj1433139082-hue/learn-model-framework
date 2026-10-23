---
name: model-lab-router
description: Route model-laboratory work in this repository to the minimum lifecycle skill and evidence set. Use for new models, model changes, data checks, validation, runtime packaging, deployment preparation, or maintenance of an existing model.
---

> contract: workflow=none steps=none

# Model Lab Router

This skill carries only the rules that cannot live in the entry documents: opening a turn, classifying a task, resuming and closing one. Routing is the scenario
table in `docs/00-入口层/03-模型任务分流与加载矩阵.md`; interaction tiers and per-scenario rounds are in `docs/00-入口层/06-模型任务问答与交互档位.md`. Read them there, not here.

## Turn 1: read the injected block, not the entry documents

Every turn carries the hook block (`[模型实验室 · 每步核对]` plus `[开局]`): active workspaces, lifecycle stage, next missing deliverable, check command -- answer turn 1 from
that block alone. If the request does not name both a model and a task, ask exactly one question ("哪个模型 + 想做什么") and stop; do not grep the repository to guess
intent. Do not open the entry documents on turn 1, and do not preload a lifecycle skill's full text. After the answer run `model-lab context <工作区> --task status|read|new|maintain|build|publish`
and read only the documents it prints plus the one lifecycle skill this route selects.

## Launcher

Use `scripts/model-lab.ps1` or `bash scripts/model-lab.sh`, never a bare interpreter from an old model checkout; first use and updates from `main` use the
bootstrap helper, manual fallback `docs/00-入口层/04-人工操作快速开始.md`. If Skillshare is unavailable, read the selected source skill under `.skillshare/skills/` directly;
missing generated skills or an untrusted hook never add a model approval gate.

## Route

Per-intent skill, the column of the scenario table in `docs/00-入口层/03-模型任务分流与加载矩阵.md`: new model or unresolved design -> `$model-requirements-grill`; data
availability, mapping or anomalies -> `$model-data-audit`; source, Notebook, tests, plots or behavior validation -> `$model-build-validate`; Python, MLflow bundle,
GPU/Chronos, packaging, rollback or release preparation -> `$model-runtime-release`; any change to a released asset -> `$model-maintenance` first. Each skill's
`contract:` line declares the lifecycle steps it covers and must resolve against `model_lab/step_plan.py`; `model-lab doctor` reports that check.

In-service: a Python 3.11-only patch is not a point-code-array or historical-window input change, which takes a new minor via `maintain --change-kind contract-upgrade`
; never rewrite an archived patch draft or read an example JSON as the real platform signature. An unreleased releasable numeric draft needing point-code inputs
uses `model-lab draft-fork <draft> --version <next-minor>` , not a fake released parent or unrelated model ID: the fork keeps the parent and its behavior baseline,
only unchanged confirmed decisions carry forward, and new serving fields, time policy and online acceptance stay open.

Ambiguous intent: inspect the workspace and run `model-lab status <workspace>` before asking, and ask only decisions not derivable from repository evidence. For a
specified version, `model-lab context <workspace> --task <intent>` recovers the minimal document set and its `workflow-state.json` -- a committed index, not a
second fact source or live platform query; verify from `model.yaml`, `decisions.yaml`, bindings and per-Run publication receipts. A numeric model needs no point
metadata; a point-code model with missing metadata is unknown, not N/A. With an untrusted or unavailable hook, `AGENTS.md` plus this CLI are the full fallback;
never use a repository-global active-model pointer, and ask once for ID and version when several fit.

## Interaction and authorization

Tiers and rounds are owned by `docs/00-入口层/06-模型任务问答与交互档位.md`; `ASK`, `CONFIRM` and `AUTHORIZE` are not three tiers. Show the three-choice selector (`full-auto`,
`assisted` recommended, `manual-review`) once in the first reply, structured UI when available, else numbered text; it is non-blocking (no answer keeps `assisted`;
"按推荐" and "采用默认值" mean `assisted`), an explicit or recorded choice or a read-only request needs none, resume restores the recorded mode, and silence never selects
`full-auto`. The mode is an execution preference, not a `model.yaml` fact. All modes keep the same machine gates: an explicit model task covers reversible writes,
local builds, tests and gated transitions through `releasable` (keep `--apply`, no re-ask per local write, stop when model, business contract, file scope or
destructive impact changes), while `AUTHORIZE` needs an explicit bounded instruction for a Git remote transaction or a real platform publication -- never infer
merge from push or scheduling from upload, never store it in `decisions.yaml`, never use the choice UI as its substitute.

Record scenario, mode, next action and pending decision IDs with `model-lab task-plan <workspace> --task-id <explicit-task-id>` (`--apply` to save): it belongs to
the private model task, not `decisions.yaml`, never stores standing remote authorization, and is restored by its explicit task ID, not "the latest" (ADR 0018).
Derive facts from evidence, then ask all currently independent business questions together, numbered, each with a recommendation; an explicit request to upload a
specified model/version to a specified experiment is the bounded instruction for that upload (its target needs an implemented publisher; upload excludes Git
push/merge and production scheduling), so no second ceremonial confirmation and none merely to write a confirmed answer or run the next local command;
"按推荐"/"全部按推荐" stores each **presented** decision's full recommendation and never confirms an unpresented or evidence-dependent one.

When an `ASK`/`CONFIRM` decision blocks the next transition, deliver the numbered questions **before ending the turn**: a user-visible message with each question,
recommendation and alternative first, then the structured UI -- a dispatch acknowledgement proves nothing and "see the popup" must never be the only place to
answer. Without an answer, repeat the choices in the final handoff (a text reply is equally valid); map a reply only to that delivered batch, record the answers,
rerun `model-lab status <workspace>`, and continue permitted local transitions without another permission prompt. An unanswered recommendation is not approval.

## Resume and closure

After a long conversation, compaction, a handoff or a bare "继续", never rely on chat memory: reopen the loading matrix, identify the active model or framework task,
inspect Git worktree changes, run `model-lab status <workspace>`, and read the explicit `task-plan` for this task's mode, next action and pending questions rather
than reopening a recorded selector. Check the framework source/commit in `context` and derive facts anew instead of executing an old next-action string blindly --
a task record is not remote authorization. Reconstruct confirmed answers from `decisions.yaml` and evidence from `model-spec.md` plus the latest dated validation
or publication record; a hash-pinned pre-publication `model-spec.md` may preserve an earlier backend state, so its old sentence never overrides a later verified
Run. A framework-only task without a model workspace uses the relevant test profile and a dated acceptance record, and no old chat recommendation becomes a
confirmed decision without the user's answer.

Before handing control back, state (1) verified versus not verified, (2) the current lifecycle or acceptance gate, (3) the single next safe action or a small
ordered set, (4) any exact business answer or bounded remote authorization genuinely needed; with nothing needed, give the next action without a performative
confirmation prompt, and never describe a passing generic smoke or repository gate as model-specific acceptance or platform publication. Keep three outcomes
separate when closing a release task: a verified `publish --apply` Run with a matching downloaded service and platform-path prediction is MLflow publication; a
business-platform point binding or task invocation is a separate integration check only when requested; the local `releasable -> released` transition is snapshot
archival, not automatic. Do not call a verified MLflow upload unfinished because `status` still says `releasable`, Git was not pushed, a business-platform task was
not invoked, or scheduling was intentionally left off -- for a one-time manual model, "no schedule" is a complete operating choice (ADR 0017).

Per step the check is `step-check <workspace>`; record the outcome with `ledger set <workspace> --step <key> --status <state> --owner <label> --evidence file:<path>`
and re-read it with `ledger check <workspace>` (0 consistent, 1 drift, ledger only). The ledger's `status` is a claim: `gate.verdict` is recomputed by `step-check`, a
`ledger:<step>` advisory never changes the gate, `ok` or the exit code, and no ledger entry authorises `advance` or publication (ADR 0052).
`model.yaml` stays the identity, contract, version and lifecycle fact source; `decisions.yaml` records answers; explanations and evidence stay in `model-spec.md`.
CPU, GPU and Chronos are target adapters, not separate workflows; the project is self-contained (no global Ask Matt installation), and `model-lab publish` is the
narrow fixed-bundle publisher for accepted CPU-IoT point-code models, whose user-facing inputs are the platform experiment name/model number and the author/account
label -- derive the target from the validated `model.yaml`, verify the bundle automatically, and resolve the workspace from task context and `status`, not by
asking for the path again. The author label is not a credential; do not ask for endpoint, secret, Python ABI or artifact digest the environment can determine, and
report a real missing dependency once.
