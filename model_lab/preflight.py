from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from model_lab.runtime_contract import (
    BundleCheckReport,
    RuntimeCheckReport,
    check_build_environment,
    verify_mlflow_bundle,
)


@dataclass(frozen=True)
class BundlePreflightReport:
    build_environment: RuntimeCheckReport
    bundle: BundleCheckReport

    @property
    def valid(self) -> bool:
        return self.build_environment.valid and self.bundle.valid

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "build_environment": self.build_environment.as_dict(),
            "bundle": self.bundle.as_dict(),
            "safety": {
                "serialized_artifacts_loaded": False,
                "network_access": False,
                "remote_writes": False,
            },
        }

    def as_text(self) -> str:
        state = "PASS" if self.valid else "FAIL"
        return "\n".join(
            (
                f"bundle preflight: {state}",
                self.build_environment.as_text(),
                self.bundle.as_text(),
                "Safety: static inspection only; no artifact loading or remote writes",
            )
        )


def run_bundle_preflight(
    manifest_path: str | Path,
    bundle_path: str | Path | None = None,
) -> BundlePreflightReport:
    manifest = Path(manifest_path)
    bundle = (
        Path(bundle_path)
        if bundle_path is not None
        else manifest.parent / "release-bundle" / "service"
    )
    return BundlePreflightReport(
        build_environment=check_build_environment(manifest),
        bundle=verify_mlflow_bundle(manifest, bundle),
    )


__all__ = ["BundlePreflightReport", "run_bundle_preflight"]
