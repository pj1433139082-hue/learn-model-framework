"""Install and exercise the synthetic private extension in a temporary venv."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "private-extension"


@unittest.skipUnless(sys.version_info[:2] == (3, 11), "the framework runtime contract is Python 3.11")
class InstalledExtensionExampleTests(unittest.TestCase):
    def test_setuptools_entry_point_and_private_module_in_temporary_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="private-extension-example-") as temp_dir:
            root = Path(temp_dir)
            self._uv_cache_dir = root / "uv-cache"
            package_source = root / "private-extension"
            shutil.copytree(FIXTURE_ROOT, package_source)

            environment = root / "venv"
            self._run([sys.executable, "-I", "-m", "venv", str(environment)])
            python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            uv = shutil.which("uv")
            if uv:
                install_command = [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--no-index",
                    "--no-deps",
                    "--no-build-isolation",
                    str(package_source),
                ]
            else:
                install_command = [
                    str(python),
                    "-I",
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--no-deps",
                    "--no-build-isolation",
                    str(package_source),
                ]
            self._run(install_command)

            site_packages = self._run(
                [
                    str(python),
                    "-I",
                    "-c",
                    "import sysconfig; print(sysconfig.get_path('purelib'))",
                ],
                capture_output=True,
            ).stdout.strip()
            framework_package = Path(site_packages) / "model_lab"
            framework_package.mkdir()
            shutil.copy2(REPOSITORY_ROOT / "model_lab" / "__init__.py", framework_package)
            shutil.copy2(REPOSITORY_ROOT / "model_lab" / "baseline.py", framework_package)

            self._run(
                [
                    str(python),
                    "-I",
                    "-m",
                    "unittest",
                    "synthetic_private_baseline.tests",
                ]
            )

    def _run(
        self,
        command: list[str],
        *,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        uv_cache_dir = getattr(self, "_uv_cache_dir", None)
        if uv_cache_dir is not None:
            environment["UV_CACHE_DIR"] = str(uv_cache_dir)
        environment.pop("PYTHONPATH", None)
        environment["PIP_NO_INDEX"] = "1"
        environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        result = subprocess.run(
            command,
            check=False,
            capture_output=capture_output,
            text=True,
            env=environment,
            timeout=120,
        )
        if result.returncode != 0:
            details = result.stderr or result.stdout
            self.fail(f"command failed ({result.returncode}): {command!r}\n{details}")
        return result


if __name__ == "__main__":
    unittest.main()
