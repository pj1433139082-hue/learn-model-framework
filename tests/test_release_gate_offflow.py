from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from model_lab.release_gate import off_flow_issues


class OffFlowGateTests(unittest.TestCase):
    def test_publication_receipt_is_allowed_after_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "validation" / "publications").mkdir(parents=True)
            self.assertTrue(off_flow_issues(workspace, "specified"))
            self.assertEqual(off_flow_issues(workspace, "validated"), ())


if __name__ == "__main__":
    unittest.main()
