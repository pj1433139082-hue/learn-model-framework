"""Install only the reviewed Python 3.11 vendor capability from the company ZIP.

This does not import the encrypted module, use credentials, or contact the network.
The whole `framework/` directory is Git-ignored. This installer reproduces exactly
four things there from the digest-pinned ZIP: the encrypted module, the Linux and
Windows native runtimes, and the PyArmor initializer. Anything else a machine keeps
in that directory (locally rewritten helper scripts) is neither reproduced nor
covered by these checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from model_lab.path_safety import is_link_or_junction


REPO_ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_SHA256 = "06f08382f652c303d451fb1770114a546babba5388247185729e46fd2e793942"
PREFIX = "infra_model_deploy/resources/infra/311/"
FILES = {
    "mlops_deploy_iot_armor2_311.py":
        "framework/mlops_deploy_iot_armor2_311.py",
    "pyarmor_runtime_000000/linux_x86_64/pyarmor_runtime.so":
        "framework/pyarmor_runtime_000000/linux_x86_64/pyarmor_runtime.so",
    "pyarmor_runtime_000000/windows_x86_64/pyarmor_runtime.pyd":
        "framework/pyarmor_runtime_000000/windows_x86_64/pyarmor_runtime.pyd",
}
INITIALIZER = "pyarmor_runtime_000000/__init__.py"


def initializer_matches(installed: Path, reviewed: bytes) -> bool:
    """Compare by line rather than by byte.

    The reviewed package entry uses CRLF while this installer lands the file
    LF-normalized, so a byte comparison would reject a correct install. The trade
    is that the check is blind to line-ending drift in both directions; that is
    acceptable because Python ignores the difference and because this copy is never
    a build source -- `model_lab.provider_archive` reads the ZIP by byte digest.
    """
    try:
        current = installed.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return (
        current.rstrip().splitlines()
        == reviewed.decode("utf-8").rstrip().splitlines()
    )


def install(archive: Path) -> list[str]:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != ARCHIVE_SHA256:
        raise ValueError("Company ZIP hash differs from the reviewed package; review it before installing")

    with zipfile.ZipFile(archive) as bundle:
        data = {relative: bundle.read(PREFIX + relative) for relative in (*FILES, INITIALIZER)}

    initializer = REPO_ROOT / "framework" / INITIALIZER
    if is_link_or_junction(initializer):
        raise ValueError(f"Refusing to write the initializer through a link or junction: {initializer}")
    if initializer.is_file() and not initializer_matches(initializer, data[INITIALIZER]):
        raise ValueError("Installed PyArmor initializer does not match the reviewed company package")
    if not data["mlops_deploy_iot_armor2_311.py"].startswith(b"# Pyarmor 9.2.6"):
        raise ValueError("Unexpected encrypted module header")
    if not data["pyarmor_runtime_000000/linux_x86_64/pyarmor_runtime.so"].startswith(b"\x7fELF"):
        raise ValueError("Invalid Linux runtime signature")
    if not data["pyarmor_runtime_000000/windows_x86_64/pyarmor_runtime.pyd"].startswith(b"MZ"):
        raise ValueError("Invalid Windows runtime signature")

    # Validate all existing targets before writing any new file.
    for relative, destination in FILES.items():
        target = REPO_ROOT / destination
        if target.exists() and target.read_bytes() != data[relative]:
            raise FileExistsError(f"Existing vendor file differs: {target}")

    installed: list[str] = []
    for relative, destination in FILES.items():
        target = REPO_ROOT / destination
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output:
                output.write(data[relative])
        installed.append(str(target))
    if not initializer.is_file():
        initializer.parent.mkdir(parents=True, exist_ok=True)
        # Land it LF-normalized: that is the byte form already verified against the
        # reviewed package on Windows and WSL, and Python ignores the difference.
        initializer.write_bytes(data[INITIALIZER].replace(b"\r\n", b"\n"))
    installed.append(str(initializer))
    return installed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="Company infra_model_deploy.zip")
    args = parser.parse_args()
    print(json.dumps({"installed_or_verified": install(args.archive)}, ensure_ascii=False))
