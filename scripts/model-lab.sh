#!/usr/bin/env bash
set -euo pipefail

FRAMEWORK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ -f "$FRAMEWORK_ROOT/.venv-311/Scripts/python.exe" ]]; then
  PYTHON="$FRAMEWORK_ROOT/.venv-311/Scripts/python.exe"
else
  PYTHON="$FRAMEWORK_ROOT/.venv-311/bin/python"
fi

if [[ ! -x "$PYTHON" ]]; then
  printf '%s\n' "model-lab: run bash scripts/bootstrap.sh first to initialize .venv-311" >&2
  exit 2
fi

exec "$PYTHON" -I -m model_lab "$@"
