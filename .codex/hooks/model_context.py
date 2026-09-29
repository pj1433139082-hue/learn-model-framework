"""Run the project hook without relying on the session's current directory."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from model_lab.codex_hook import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
