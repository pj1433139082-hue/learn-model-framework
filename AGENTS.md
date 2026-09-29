# Shared model-laboratory framework

Start every model task at `docs/00-入口层/00-模型开发总览.md`, then route through
`model-lab-router` and the minimum task-specific documents. Do not preload private
model inventories. The task's `model.yaml`, decisions, bindings, and dated evidence
remain the facts for that model.

This checkout is framework-only. Real models, point codes, plant data, notebooks,
company archives, credentials, and publication receipts live in an external private
models directory. Set `MODEL_LAB_MODELS_ROOT` to that directory or pass
`--models-root` where supported; never create a `models/` directory here. The
Codex hook is read-only and optional; `AGENTS.md` plus `model-lab context/status`
remain the fallback. Do not infer a model version when several match.

The Python contract is 3.11. Before sharing changes, run `model-lab doctor`,
`model-lab test-profile local`, and `model-lab release-gate`. A generic passing
suite is not a private model's acceptance or platform publication.

Project skill sources are `.skillshare/skills/`. Generated `.agents/skills/` files
are local only; update sources, then run Skillshare sync and audit. Publication and
Git remote changes stay within the user's explicit bounded request.
