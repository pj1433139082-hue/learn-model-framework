---
name: model-lab-setup
description: Scaffold and verify the developer environment, agent skills, and deployment capabilities for the model laboratory. Use when setting up a new machine, starting in an unverified environment, checking Python 3.11/WSL/Skillshare readiness, or preparing deployment packages.
---

> contract: workflow=none steps=none

# Setup Model Lab Environment & Skills

Scaffold and verify the four-dimensional readiness required for the model laboratory:

- **Control-plane Python**: Python `>=3.11,<3.12`, virtualenv (`.venv-311`), and CLI health.
- **Agent skills**: Project skills under `.skillshare/skills/` synchronized to `.agents/skills/`.
- **Target build environment**: Linux x86_64 target (WSL or Linux container) for packaging MLflow bundles.
- **Company deployment bundle**: Pinned `infra_model_deploy.zip` supplied by the user, SHA-256 verified, and persisted once.

This is a prompt-driven skill inspired by `setup-matt-pocock-skills`. Explore, present findings, confirm with the user, then guide or execute.

## Hard rule: the company bundle is user-supplied, never shipped

The public framework checkout does **not** contain `infra_model_deploy.zip` and does **not**
contain the company MLflow / MinIO credentials. The ZIP is the on-machine carrier of those
credentials plus the pyarmor runtime that gets baked into point-code release bundles.
Therefore:

- **Ask the user for the ZIP path during setup** (do not download it, do not generate it,
  do not continue pretending the environment is ready without it).
- **Never copy the ZIP into the repository or into a model workspace.** The security scan
  classifies archives under the repository as `git_history_archive`; the model workspace
  forbids archives and oversized files. Keep it in a user directory such as
  `%USERPROFILE%\Downloads` and only record its path.
- **Never write credentials into tracked files, skills, or `model.yaml`.** The package
  restores environment variables at import time; nothing else needs to be stored.
- Point-code packaging, publish, verify-publication and online acceptance are **blocked**
  while the package is missing. Report that as `BLOCKED`, not as "mostly ready".

## Process

### 1. Explore (Do not assume)

Inspect the current workspace and host system to gather facts:

1. **Python Control Plane**:
   - Check `python --version` and `.venv-311/Scripts/python.exe --version` (or Unix equivalent).
   - Verify that Python is `>=3.11,<3.12`. Flag any legacy Python 3.12 environments.
   - Run `model-lab doctor . --format json` (or equivalent) to verify control-plane integrity.
2. **Agent Skills**:
   - Check if `skillshare` CLI is available (Windows install path may be
     `%LOCALAPPDATA%\Programs\skillshare\skillshare.exe`).
   - Run `skillshare status --json` and `skillshare sync --dry-run --json`; compare
     `.skillshare/skills/` against `.agents/skills/`.
3. **Build & Deployment Environment**:
   - On Windows: check if WSL is installed (`wsl -l -v`) and inspect Linux Python 3.11
     availability (e.g. `hermes` distribution or `.venv-linux311`).
   - Check if the environment can execute Linux x86_64 target builds.
4. **Company Deployment Archive**:
   - Run `.venv-311\Scripts\python.exe -B -m model_lab setup --format json` and read the
     `company_zip` object (`path`, `sha256`, `approved_sha256`, `reviewed`) plus
     `company_iot_zip`; also check `$env:MODEL_LAB_COMPANY_IOT_ZIP` and the ignored
     `.model-lab.local.json`. Resolution order is explicit flag > environment variable >
     local settings.
   - On a machine with no configured models root yet, the first
     `setup --format json` run exits 2 with `status: models_root_required` (`valid: false`;
     human form `setup: models_root_required`, next hint `next: Choose an external local
     models directory`). That is the expected answer for "not configured yet", not a
     broken environment: keep going with the rest of the report and the persistence step
     below.
   - If no path is configured, look in `~/Downloads/` and the current directory, then **ask
     the user for the path** if nothing is found.
   - `setup` already computes the SHA-256 and reports `reviewed: true|false` against the
     approved digest `06f08382f652c303d451fb1770114a546babba5388247185729e46fd2e793942`;
     cross-check with `Get-FileHash -Algorithm SHA256` when reporting to the user. A mismatch
     means "wrong or tampered package": stop and ask for the official one.
   - Check whether `framework/` native runtimes are installed.

### 2. Present Findings

Summarise what is `[READY]`, `[MISSING]` or `[OPTIONAL]`:

- **Control-Plane Python**: Status, version, interpreter path, doctor verdict.
- **Project Skills**: Synchronized count, missing skills, sync command recommendation.
- **Packaging/WSL Environment**: WSL status, Linux Python 3.11 path.
- **Company Archive**: Found path, `sha256` vs the approved digest, whether the path is
  already persisted for later commands, and which capabilities are blocked without it.
- **Hooks**: `setup --format json` reports `hooks.clients`; quote `codex.state` and
  `clients.dsh.{guard_enabled,guard_rules,context_enabled}` instead of guessing whether
  hooks are active.

### 3. Confirm and Guide

For each missing or unconfigured item:

1. **Missing Python 3.11 Environment**:
   - Propose: `uv venv .venv-311 --python 3.11 && uv pip install --python .venv-311/Scripts/python.exe -e .`
2. **Unsynced Skills**:
   - Propose: `skillshare sync --json` to synchronize `.skillshare/skills/` to `.agents/skills/`.
3. **Missing Linux Build Target** (if deployment is needed):
   - Propose WSL `hermes` or Docker container with Python 3.11.
4. **Missing Company Capability Package** (required for point-code packaging, publish,
   verify-publication and online acceptance):
   - Ask the user to provide the ZIP path, then verify the SHA-256 as in step 1.4.
   - Persist it once so later commands need no flag:
     `python -m model_lab setup --models-root models --company-iot-zip <ZIP> --apply`
     which writes the path into the Git-ignored `.model-lab.local.json`. When the private
     models root is not used on this machine, set the environment variable instead:
     `setx MODEL_LAB_COMPANY_IOT_ZIP "<ZIP path>"` (new terminals pick it up).
     The models root must be this repository's `models` directory (ADR 0050): a path outside
     the checkout fails with `models_root_outside_framework`, and any other directory name
     (including a nested `models/_drafts`) fails with `models_root_not_models_directory`.
   - `setup --apply` refuses an unreviewed ZIP: it reports `company_zip_unreviewed`, marks
     the run invalid and writes nothing, so a wrong path never gets persisted.
   - After persistence, `publish`, `verify-publication`, `bundle-build --iot-package-zip`
     and `test-profile` resolve the ZIP automatically; the SHA-256 gate still runs on every
     release, so persisting the path never bypasses a check.
   - Install the native runtime when needed:
     `python scripts/验收/install_company_runtime311.py <zip-path>`.
   - If the user only does non-release work (data audit, offline scoring), mark the item
     `[OPTIONAL]` and state explicitly that the point-code release path stays unavailable.

### 4. Transition

Once setup is complete or verified:
- If the company bundle is still missing while the task needs packaging or publishing,
  report `BLOCKED` with the missing capability and stop; do not let later steps assume it.
- Otherwise route to `$model-lab-router` to begin model requirements, building, or maintenance.
