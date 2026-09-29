#!/bin/sh
set -eu
root=$(git rev-parse --show-toplevel)
if [ -x "$root/.venv-linux311/bin/python" ]; then
  exec "$root/.venv-linux311/bin/python" "$root/.codex/hooks/model_context.py"
fi
if [ -x "$root/.venv-311/bin/python" ]; then
  exec "$root/.venv-311/bin/python" "$root/.codex/hooks/model_context.py"
fi
exit 0
