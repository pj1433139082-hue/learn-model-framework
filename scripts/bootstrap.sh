#!/usr/bin/env bash
set -euo pipefail

FRAMEWORK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
VENV_DIRECTORY="$FRAMEWORK_ROOT/.venv-311"
PYPROJECT="$FRAMEWORK_ROOT/pyproject.toml"
MODELS_ROOT=""
UPDATE=0

usage() {
  printf '%s\n' "Usage: bash scripts/bootstrap.sh [--models-root PATH] [--update]"
}

while (($#)); do
  case "$1" in
    --models-root)
      (($# >= 2)) || { usage >&2; exit 2; }
      MODELS_ROOT="$2"
      shift 2
      ;;
    --update|-Update)
      UPDATE=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

printf '%s\n' 'Local setup plan: verify/create Python 3.11; install framework/test dependencies; save ignored settings; synchronize project skills.'
printf 'Framework: %s | Update: %s | No model writes or platform upload.\n' "$FRAMEWORK_ROOT" "$UPDATE"

normalize_windows_path() {
  local candidate="$1"
  if command -v cygpath >/dev/null 2>&1 && [[ "$candidate" =~ ^[A-Za-z]:\\ ]]; then
    cygpath -u "$candidate"
  else
    printf '%s' "$candidate"
  fi
}

is_python311() {
  local candidate="$1"
  [[ -x "$candidate" ]] || return 1
  "$candidate" -I -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)' >/dev/null 2>&1
}

find_python311() {
  local candidate=""
  if command -v uv >/dev/null 2>&1; then
    candidate="$(uv python find 3.11 2>/dev/null | tail -n 1 || true)"
    candidate="$(normalize_windows_path "$candidate")"
    if is_python311 "$candidate"; then
      printf '%s' "$candidate"
      return 0
    fi
  fi

  if command -v py.exe >/dev/null 2>&1 || command -v py >/dev/null 2>&1; then
    local py_launcher
    py_launcher="$(command -v py.exe || command -v py)"
    candidate="$("$py_launcher" -3.11 -c 'import sys; print(sys.executable)' 2>/dev/null | tail -n 1 || true)"
    candidate="$(normalize_windows_path "$candidate")"
    if is_python311 "$candidate"; then
      printf '%s' "$candidate"
      return 0
    fi
  fi

  for name in python3.11 python3 python; do
    if command -v "$name" >/dev/null 2>&1; then
      candidate="$(command -v "$name")"
      if is_python311 "$candidate"; then
        printf '%s' "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

if [[ -f "$VENV_DIRECTORY/Scripts/python.exe" ]]; then
  VENV_PYTHON="$VENV_DIRECTORY/Scripts/python.exe"
elif [[ -f "$VENV_DIRECTORY/bin/python" ]]; then
  VENV_PYTHON="$VENV_DIRECTORY/bin/python"
else
  VENV_PYTHON=""
fi

if [[ -L "$VENV_DIRECTORY" ]]; then
  printf '%s\n' ".venv-311 is a symbolic link; inspect it and choose a regular local environment." >&2
  exit 1
fi

if [[ -n "$VENV_PYTHON" ]]; then
  if ! is_python311 "$VENV_PYTHON"; then
    printf '%s\n' "Existing .venv-311 is not Python 3.11. It was left unchanged." >&2
    exit 1
  fi
elif [[ -e "$VENV_DIRECTORY" ]]; then
  printf '%s\n' "A .venv-311 directory exists without its Python interpreter; inspect it before retrying." >&2
  exit 1
else
  BASE_PYTHON="$(find_python311 || true)"
  [[ -n "$BASE_PYTHON" ]] || {
    printf '%s\n' "Python 3.11 was not found through uv or the system Python launchers." >&2
    exit 1
  }
  if command -v uv >/dev/null 2>&1; then
    (cd -- "$FRAMEWORK_ROOT" && uv venv --python "$BASE_PYTHON" .venv-311)
  else
    (cd -- "$FRAMEWORK_ROOT" && "$BASE_PYTHON" -m venv .venv-311)
  fi
  if [[ -f "$VENV_DIRECTORY/Scripts/python.exe" ]]; then
    VENV_PYTHON="$VENV_DIRECTORY/Scripts/python.exe"
  else
    VENV_PYTHON="$VENV_DIRECTORY/bin/python"
  fi
  is_python311 "$VENV_PYTHON" || {
    printf '%s\n' "The created .venv-311 interpreter did not verify as Python 3.11." >&2
    exit 1
  }
fi

cd -- "$FRAMEWORK_ROOT"
HAS_UPDATE_EXTRA="$("$VENV_PYTHON" -I -c 'import sys, tomllib; data=tomllib.load(open(sys.argv[1], "rb")); print("yes" if "update" in data.get("project", {}).get("optional-dependencies", {}) else "no")' "$PYPROJECT")"
if [[ "$HAS_UPDATE_EXTRA" == "yes" ]]; then
  PACKAGE_SPEC='.[test,update]'
else
  PACKAGE_SPEC='.[test]'
fi

UPGRADE_ARGS=()
if ((UPDATE)); then UPGRADE_ARGS+=(--upgrade); fi
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$VENV_PYTHON" "${UPGRADE_ARGS[@]}" --editable "$PACKAGE_SPEC"
else
  "$VENV_PYTHON" -m pip install "${UPGRADE_ARGS[@]}" --editable "$PACKAGE_SPEC"
fi

SETUP_ARGS=(-I -m model_lab setup)
if [[ -n "$MODELS_ROOT" ]]; then
  SETUP_ARGS+=(--models-root "$MODELS_ROOT")
fi
SETUP_ARGS+=(--apply)
if ((UPDATE)); then SETUP_ARGS+=(--update); fi
"$VENV_PYTHON" "${SETUP_ARGS[@]}"
