from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import yaml

from model_lab.publication_import import import_group_publication
from model_lab.workflow_state import _publication_state


class PublicationImportTests(unittest.TestCase):
    def test_group_receipt_is_recorded_and_recognized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "model"
            workspace.mkdir()
            (workspace / "model.yaml").write_text(
                yaml.safe_dump(
                    {
                        "model": {
                            "id": "demo",
                            "version": "1.0.0",
                            "lifecycle_state": "validated",
                        }
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            package = Path(temporary) / "infra_model_deploy.zip"
            package.write_bytes(b"zip")
            kit = Path(temporary) / "kit"
            kit.mkdir()
            (kit / "deploy.py").write_text("print('kit')\n", encoding="utf-8")

            result = import_group_publication(
                workspace,
                run_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                experiment="demo-experiment",
                company_package=package,
                deploy_kit=kit,
            )
            receipt = json.loads((workspace / "validation" / "publications" / "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json").read_text(encoding="utf-8"))
            again = import_group_publication(
                workspace,
                run_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                experiment="demo-experiment",
                company_package=package,
                deploy_kit=kit,
            )
            publication = _publication_state(
                workspace,
                {"artifacts": []},
                {"id": "demo", "version": "1.0.0"},
            )

        self.assertEqual(result["status"], "imported")
        self.assertEqual(receipt["schema_version"], "1.1")
        self.assertEqual(receipt["channel"], "group")
        self.assertEqual(len(receipt["company_package_sha256"]), 64)
        self.assertEqual(publication["status"], "verified_group_receipt")
        self.assertEqual(publication["run_id"], "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertEqual(again["status"], "already_present")


if __name__ == "__main__":
    unittest.main()
