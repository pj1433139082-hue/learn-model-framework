# Shared model-laboratory framework

Start every model task at `docs/00-入口层/00-模型开发总览.md`, then route through
`model-lab-router` and the minimum task-specific documents. Do not preload private
model inventories. The task's `model.yaml`, decisions, bindings, and dated evidence
remain the facts for that model.

This is the maintained framework checkout; colleagues normally clone/pull `main`.
First use: `scripts/bootstrap.ps1 -ModelsRoot <private-dir>` on Windows, or
`bash scripts/bootstrap.sh --models-root <private-dir>` on Linux. After `git pull --ff-only`,
run bootstrap with `-Update` / `--update` to refresh dependencies and skills.
Use `scripts/model-lab.ps1` / `bash scripts/model-lab.sh` as the CLI launcher;
it isolates imports from obsolete packages in the caller's working directory.
Manual instructions are in `docs/00-入口层/04-人工操作快速开始.md`.

This checkout is framework-only. Real models, point codes, plant data, notebooks,
company archives, credentials, and publication receipts live in an external private
models directory. Set `MODEL_LAB_MODELS_ROOT` to that directory or pass
`--models-root` where supported; never create a `models/` directory here. The
Codex hook is read-only and optional; `AGENTS.md` plus `model-lab context/status`
remain the fallback. Do not infer a model version when several match.
The ignored `.model-lab.local.json` stores the local models root after setup.
For a model task, restore its explicit task ID with `model-lab task-plan`; reuse
the recorded interaction mode and derive current facts with context/status.

The Python contract is 3.11. Before sharing changes, run `model-lab doctor`,
`model-lab test-profile local`, and `model-lab release-gate`. A generic passing
suite is not a private model's acceptance or platform publication.

Project skill sources are `.skillshare/skills/`. Generated `.agents/skills/` files
are local only; update sources, then run Skillshare sync and audit. Publication and
Git remote changes stay within the user's explicit bounded request.
If Skillshare is unavailable, read `.skillshare/skills/model-lab-router/SKILL.md`
and its selected source skill directly; do not invent a missing-skill approval gate.
