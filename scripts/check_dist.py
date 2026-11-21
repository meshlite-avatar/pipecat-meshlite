"""Fail unless the built wheel holds only the package, and nothing looks like a secret.

Usage: python scripts/check_dist.py dist [--tag v0.1.0]
"""

from __future__ import annotations

import re
import sys
import tarfile
import zipfile
from pathlib import Path

PACKAGE = r"pipecat_meshlite/(__init__|client|languages|processor|version)\.py|pipecat_meshlite/py\.typed"
WHEEL_OK = re.compile(
    rf"^({PACKAGE}|pipecat_meshlite-[^/]+\.dist-info/(METADATA|WHEEL|RECORD|licenses/LICENSE))$"
)
SECRET = re.compile(
    rb"ml_(sk|pk)_(live|test)_[A-Za-z0-9]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}"
    rb"|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|pypi-[A-Za-z0-9_-]{20,}|[0-9a-f]{32}|[A-Za-z0-9]{84}"
)


def version() -> str:
    text = Path("src/pipecat_meshlite/version.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def main() -> int:
    args = sys.argv[1:]
    dist = Path(args[0] if args else "dist")
    tag = args[args.index("--tag") + 1] if "--tag" in args else None
    errors: list[str] = []
    if tag is not None and tag != f"v{version()}":
        errors.append(f"tag {tag} does not match the package version v{version()}")
    for wheel in dist.glob("*.whl"):
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                if not WHEEL_OK.match(name):
                    errors.append(f"{wheel.name}: unexpected file {name}")
                if SECRET.search(archive.read(name)):
                    errors.append(f"{wheel.name}: {name} looks like it holds a secret")
    for sdist in dist.glob("*.tar.gz"):
        with tarfile.open(sdist) as archive:
            for member in archive.getmembers():
                if member.name.endswith("/.env"):
                    errors.append(f"{sdist.name}: {member.name} must not ship")
                if member.isfile() and SECRET.search(archive.extractfile(member).read()):
                    errors.append(f"{sdist.name}: {member.name} looks like it holds a secret")
    if not list(dist.glob("*.whl")) or not list(dist.glob("*.tar.gz")):
        errors.append("dist must hold one wheel and one sdist")
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
