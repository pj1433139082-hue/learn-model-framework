from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import yaml

from model_lab.bundle_build import _portable_mlmodel


class BundlePortabilityTests(unittest.TestCase):
    def test_windows_artifact_paths_are_rewritten_to_posix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            (bundle / "MLmodel").write_text(
                yaml.safe_dump(
                    {
                        "flavors": {
                            "python_function": {
                                "artifacts": {
                                    "logic": {
                                        "path": "artifacts\\logic_bundle.pkl",
                                        "uri": "artifacts\\logic_bundle.pkl",
                                    }
                                }
                            }
                        }
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            _portable_mlmodel(bundle)
            document = yaml.safe_load((bundle / "MLmodel").read_text(encoding="utf-8"))
        artifact = document["flavors"]["python_function"]["artifacts"]["logic"]
        self.assertEqual(artifact["path"], "artifacts/logic_bundle.pkl")
        self.assertEqual(artifact["uri"], "artifacts/logic_bundle.pkl")


if __name__ == "__main__":
    unittest.main()
