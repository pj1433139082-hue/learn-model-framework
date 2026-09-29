from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MINIMAL_MANIFEST = REPO_ROOT / "tests" / "fixtures" / "minimal-model" / "model.yaml"
INVALID_MANIFEST = REPO_ROOT / "tests" / "fixtures" / "invalid.yaml"
INVALID_STATE_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "invalid-state" / "model.yaml"
)
UNSUPPORTED_SCHEMA_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "unsupported-schema" / "model.yaml"
)
UNKNOWN_FIELDS_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "unknown-fields" / "model.yaml"
)
MISSING_FIELDS_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "missing-fields" / "model.yaml"
)
INVALID_CONTRACT_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "invalid-contract" / "model.yaml"
)
INVALID_DIGEST_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "invalid-digest" / "model.yaml"
)
UNSUPPORTED_TARGET_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "unsupported-target" / "model.yaml"
)
INCOMPATIBLE_VERSION_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "incompatible-version" / "model.yaml"
)
MALFORMED_SHAPES_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "malformed-shapes" / "model.yaml"
)
NON_OBJECT_MANIFEST = REPO_ROOT / "tests" / "fixtures" / "non-object.yaml"
INVALID_VALUES_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "invalid-values" / "model.yaml"
)
DUPLICATE_KEYS_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "duplicate-keys" / "model.yaml"
)
MIXED_KEYS_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "mixed-keys" / "model.yaml"
)
UNICODE_VERSION_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "unicode-version" / "model.yaml"
)


def run_model_lab(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "model_lab", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


class InspectCommandTests(unittest.TestCase):
    def test_user_can_inspect_valid_model_asset(self) -> None:
        result = run_model_lab("inspect", str(MINIMAL_MANIFEST))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("minimal-reference — 最小参考模型", result.stdout)
        self.assertIn("Lifecycle state: draft", result.stdout)
        self.assertIn("Model version: 0.1.0", result.stdout)
        self.assertIn("Target: cpu-iot", result.stdout)
        self.assertIn("Inputs: flow_m3h (number, m3/h)", result.stdout)
        self.assertIn("Outputs: result (number, dimensionless)", result.stdout)

    def test_user_can_inspect_valid_model_asset_as_json(self) -> None:
        result = run_model_lab(
            "inspect", str(MINIMAL_MANIFEST), "--format", "json"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "inputs": [
                    {"name": "flow_m3h", "type": "number", "unit": "m3/h"}
                ],
                "lifecycle_state": "draft",
                "model_id": "minimal-reference",
                "model_version": "0.1.0",
                "name": "最小参考模型",
                "outputs": [
                    {
                        "name": "result",
                        "type": "number",
                        "unit": "dimensionless",
                    }
                ],
                "target": "cpu-iot",
            },
        )

    def test_missing_manifest_returns_an_actionable_error(self) -> None:
        missing = REPO_ROOT / "tests" / "fixtures" / "does-not-exist.yaml"

        result = run_model_lab("inspect", str(missing))

        self.assertEqual(result.returncode, 2)
        self.assertIn("manifest not found", result.stderr)
        self.assertIn(str(missing), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_machine_readable_errors_are_json(self) -> None:
        missing = REPO_ROOT / "tests" / "fixtures" / "does-not-exist.yaml"

        result = run_model_lab("inspect", str(missing), "--format", "json")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            json.loads(result.stderr),
            {
                "error": {
                    "code": "manifest_not_found",
                    "message": f"manifest not found: {missing}",
                }
            },
        )

    def test_invalid_yaml_returns_an_actionable_error(self) -> None:
        result = run_model_lab("inspect", str(INVALID_MANIFEST))

        self.assertEqual(result.returncode, 2)
        self.assertIn("manifest is not valid YAML", result.stderr)
        self.assertIn(str(INVALID_MANIFEST), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_non_file_manifest_path_returns_an_actionable_error(self) -> None:
        directory = MINIMAL_MANIFEST.parent

        result = run_model_lab("inspect", str(directory))

        self.assertEqual(result.returncode, 2)
        self.assertIn("manifest cannot be read", result.stderr)
        self.assertIn(str(directory), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_non_utf8_manifest_returns_an_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "model.yaml"
            manifest.write_bytes(b"model: \xff\xfe")

            result = run_model_lab("inspect", str(manifest))

        self.assertEqual(result.returncode, 2)
        self.assertIn("manifest is not UTF-8", result.stderr)
        self.assertIn(str(manifest), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_lifecycle_state_is_rejected(self) -> None:
        result = run_model_lab("inspect", str(INVALID_STATE_MANIFEST))

        self.assertEqual(result.returncode, 2)
        self.assertIn("unsupported lifecycle state: blocked", result.stderr)
        self.assertIn("draft, specified, built, validated", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class ValidateCommandTests(unittest.TestCase):
    def test_user_can_validate_a_valid_model_asset(self) -> None:
        result = run_model_lab("validate", str(MINIMAL_MANIFEST))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("minimal-reference: VALID", result.stdout)
        self.assertIn("Lifecycle state: draft", result.stdout)
        self.assertIn("Contract issues: 0", result.stdout)

    def test_user_can_validate_a_valid_model_asset_as_json(self) -> None:
        result = run_model_lab(
            "validate", str(MINIMAL_MANIFEST), "--format", "json"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "issues": [],
                "lifecycle_state": "draft",
                "model_id": "minimal-reference",
                "valid": True,
            },
        )

    def test_unsupported_schema_version_is_rejected_with_upgrade_guidance(self) -> None:
        result = run_model_lab("validate", str(UNSUPPORTED_SCHEMA_MANIFEST))

        self.assertEqual(result.returncode, 3)
        self.assertIn("minimal-reference: INVALID", result.stdout)
        self.assertIn("schema_version", result.stdout)
        self.assertIn("unsupported_schema_version", result.stdout)
        self.assertIn('use supported schema version "1.0"', result.stdout)
        self.assertIn("Lifecycle state: draft", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_unknown_fields_are_rejected_at_their_exact_paths(self) -> None:
        result = run_model_lab(
            "validate", str(UNKNOWN_FIELDS_MANIFEST), "--format", "json"
        )

        self.assertEqual(result.returncode, 3)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["valid"])
        self.assertEqual(
            {(issue["code"], issue["path"]) for issue in payload["issues"]},
            {
                ("unknown_field", "experimental_flag"),
                ("unknown_field", "model.alias"),
            },
        )
        self.assertTrue(
            all("remove unknown field" in issue["suggestion"] for issue in payload["issues"])
        )

    def test_missing_fields_report_exact_paths_and_fix_directions(self) -> None:
        result = run_model_lab(
            "validate", str(MISSING_FIELDS_MANIFEST), "--format", "json"
        )

        self.assertEqual(result.returncode, 3)
        issues = json.loads(result.stdout)["issues"]
        missing = {
            issue["path"]: issue
            for issue in issues
            if issue["code"] == "missing_required_field"
        }
        self.assertEqual(set(missing), {"model.owner", "artifacts[0].sha256"})
        self.assertEqual(missing["model.owner"]["suggestion"], "add model.owner")
        self.assertEqual(
            missing["artifacts[0].sha256"]["suggestion"],
            "add artifacts[0].sha256",
        )

    def test_invalid_lifecycle_is_a_contract_issue_not_a_lifecycle_state(self) -> None:
        result = run_model_lab(
            "validate", str(INVALID_STATE_MANIFEST), "--format", "json"
        )

        self.assertEqual(result.returncode, 3)
        payload = json.loads(result.stdout)
        self.assertIsNone(payload["lifecycle_state"])
        self.assertEqual(
            [issue["code"] for issue in payload["issues"]],
            ["invalid_lifecycle_state"],
        )
        self.assertEqual(payload["issues"][0]["path"], "model.lifecycle_state")
        self.assertIn("draft, specified, built", payload["issues"][0]["suggestion"])

    def test_input_and_output_contracts_reject_invalid_types_and_duplicate_names(self) -> None:
        result = run_model_lab(
            "validate", str(INVALID_CONTRACT_MANIFEST), "--format", "json"
        )

        self.assertEqual(result.returncode, 3)
        issues = json.loads(result.stdout)["issues"]
        self.assertEqual(
            {(issue["code"], issue["path"]) for issue in issues},
            {
                ("invalid_contract_field_type", "contract.inputs[0].type"),
                ("duplicate_contract_field", "contract.outputs[1].name"),
            },
        )
        self.assertTrue(
            any("number, integer, string, boolean, datetime" in issue["suggestion"] for issue in issues)
        )

    def test_artifact_digest_must_be_a_canonical_sha256(self) -> None:
        result = run_model_lab(
            "validate", str(INVALID_DIGEST_MANIFEST), "--format", "json"
        )

        self.assertEqual(result.returncode, 3)
        issues = json.loads(result.stdout)["issues"]
        self.assertEqual(
            [(issue["code"], issue["path"]) for issue in issues],
            [("invalid_artifact_digest", "artifacts[0].sha256")],
        )
        self.assertIn("64 lowercase hexadecimal", issues[0]["suggestion"])

    def test_target_must_name_a_supported_adapter_capability(self) -> None:
        result = run_model_lab(
            "validate", str(UNSUPPORTED_TARGET_MANIFEST), "--format", "json"
        )

        self.assertEqual(result.returncode, 3)
        issues = json.loads(result.stdout)["issues"]
        self.assertEqual(
            [(issue["code"], issue["path"]) for issue in issues],
            [("unsupported_target", "target.kind")],
        )
        self.assertIn("cpu-iot, gpu, chronos", issues[0]["suggestion"])

    def test_model_and_runtime_version_relationships_are_enforced(self) -> None:
        result = run_model_lab(
            "validate", str(INCOMPATIBLE_VERSION_MANIFEST), "--format", "json"
        )

        self.assertEqual(result.returncode, 3)
        issues = json.loads(result.stdout)["issues"]
        self.assertEqual(
            {(issue["code"], issue["path"]) for issue in issues},
            {
                ("invalid_model_version", "model.version"),
                ("incompatible_model_lab_version", "compatibility.model_lab"),
            },
        )
        self.assertTrue(
            any("0.1.0" in issue["message"] for issue in issues)
        )

    def test_semantic_version_rejects_non_ascii_digits(self) -> None:
        result = run_model_lab(
            "validate", str(UNICODE_VERSION_MANIFEST), "--format", "json"
        )

        self.assertEqual(result.returncode, 3)
        issues = json.loads(result.stdout)["issues"]
        self.assertEqual(
            [(issue["code"], issue["path"]) for issue in issues],
            [("invalid_model_version", "model.version")],
        )

    def test_malformed_sections_are_reported_without_a_traceback(self) -> None:
        result = run_model_lab(
            "validate", str(MALFORMED_SHAPES_MANIFEST), "--format", "json"
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stderr, "")
        issues = json.loads(result.stdout)["issues"]
        self.assertEqual(
            {
                (issue["code"], issue["path"])
                for issue in issues
                if issue["code"] == "invalid_field_shape"
            },
            {
                ("invalid_field_shape", "compatibility"),
                ("invalid_field_shape", "target"),
                ("invalid_field_shape", "contract.inputs"),
                ("invalid_field_shape", "contract.outputs[0]"),
                ("invalid_field_shape", "artifacts"),
            },
        )

    def test_manifest_root_must_be_an_object(self) -> None:
        result = run_model_lab(
            "validate", str(NON_OBJECT_MANIFEST), "--format", "json"
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["model_id"], "<unknown>")
        self.assertEqual(
            [(issue["code"], issue["path"]) for issue in payload["issues"]],
            [("invalid_field_shape", "$")],
        )

    def test_human_and_json_validation_report_the_same_conclusion(self) -> None:
        human = run_model_lab("validate", str(UNSUPPORTED_SCHEMA_MANIFEST))
        machine = run_model_lab(
            "validate", str(UNSUPPORTED_SCHEMA_MANIFEST), "--format", "json"
        )

        self.assertEqual((human.returncode, machine.returncode), (3, 3))
        payload = json.loads(machine.stdout)
        self.assertIn(f"{payload['model_id']}: INVALID", human.stdout)
        self.assertIn(
            f"Lifecycle state: {payload['lifecycle_state']}", human.stdout
        )
        self.assertEqual(
            human.stdout.count("- ["),
            len(payload["issues"]),
        )

    def test_null_collections_and_non_string_scalars_are_rejected(self) -> None:
        result = run_model_lab(
            "validate", str(INVALID_VALUES_MANIFEST), "--format", "json"
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["model_id"], "<unknown>")
        self.assertEqual(
            {
                (issue["code"], issue["path"])
                for issue in payload["issues"]
            },
            {
                ("invalid_field_value", "model.id"),
                ("invalid_field_value", "model.name"),
                ("invalid_field_value", "model.owner"),
                ("invalid_field_value", "contract.inputs[0].name"),
                ("invalid_field_value", "contract.inputs[0].unit"),
                ("invalid_field_shape", "contract.outputs"),
                ("invalid_field_shape", "artifacts"),
            },
        )

    def test_duplicate_yaml_keys_are_rejected_as_an_ambiguous_manifest(self) -> None:
        result = run_model_lab("validate", str(DUPLICATE_KEYS_MANIFEST))

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("duplicate YAML key", result.stderr)
        self.assertIn("version", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_non_string_and_string_unknown_keys_never_raise_a_traceback(self) -> None:
        result = run_model_lab(
            "validate", str(MIXED_KEYS_MANIFEST), "--format", "json"
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stderr, "")
        issues = json.loads(result.stdout)["issues"]
        self.assertEqual(
            {(issue["code"], issue["path"]) for issue in issues},
            {
                ("invalid_field_name", "model[1]"),
                ("unknown_field", "model.alias"),
            },
        )


if __name__ == "__main__":
    unittest.main()
