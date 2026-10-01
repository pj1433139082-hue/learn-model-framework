"""A failed index write leaves completed facts intact, without stray temp files."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from model_lab.cli import main


class SnapshotWriteTests(unittest.TestCase):
    def test_index_stream_write_failure_cleans_both_temporary_files(self):
        with tempfile.TemporaryDirectory(prefix="model-lab-snapshot-stream-") as directory:
            models_root = Path(directory) / "private-models"
            original_temporary = tempfile.NamedTemporaryFile
            attempts = []

            class FailingIndexStream:
                def __init__(self, stream):
                    self.stream = stream
                    self.name = stream.name

                def __enter__(self):
                    self.stream.__enter__()
                    return self

                def write(self, contents):
                    # A partially written stream models a full disk / I/O error.
                    self.stream.write(contents[:10])
                    raise OSError("synthetic index stream I/O failure")

                def __exit__(self, *arguments):
                    return self.stream.__exit__(*arguments)

            def temporary_file(*arguments, **options):
                stream = original_temporary(*arguments, **options)
                if options.get("prefix") == ".workflow-state-":
                    attempts.append(stream.name)
                    return FailingIndexStream(stream)
                return stream

            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("tempfile.NamedTemporaryFile", side_effect=temporary_file), redirect_stdout(stdout), redirect_stderr(stderr):
                code = main([
                    "new", "stream-failure", "--name", "Synthetic Stream Failure",
                    "--owner", "example", "--models-root", str(models_root), "--apply", "--format", "json",
                ])
            result = json.loads(stdout.getvalue())
            workspace = Path(result["destination"])
            self.assertEqual(code, 2)
            self.assertEqual(len(attempts), 2)
            self.assertEqual(result["status"], "created")
            self.assertFalse(result["completion"]["repeat_operation"])
            self.assertEqual(len(list(models_root.rglob("model.yaml"))), 1)
            self.assertFalse((workspace / "workflow-state.json").exists())
            self.assertEqual(list(workspace.glob(".workflow-state-*.tmp")), [])
