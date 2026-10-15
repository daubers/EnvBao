"""Validate a git tag as SemVer 2.0.0 and stamp it into ``src/envbao/_version.py``.

Usage: ``python scripts/version_from_tag.py v1.2.3`` - prints the bare version.
Exits non-zero (and writes nothing) if the tag is not ``v`` + a valid SemVer string.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Official semver.org regex, prefixed with the literal "v".
SEMVER_TAG = re.compile(
    r"^v(?P<version>(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*)?"
    r"(?:\+[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*)?)$"
)
VERSION_FILE = Path(__file__).resolve().parent.parent / "src" / "envbao" / "_version.py"


def parse_tag(tag: str) -> str:
    """Return the version for a valid ``vX.Y.Z[-pre][+build]`` tag, else raise ValueError."""
    match = SEMVER_TAG.fullmatch(tag)
    if not match:
        raise ValueError(f"{tag!r} is not a valid semver tag (expected vMAJOR.MINOR.PATCH)")
    return match["version"]


def write_version(version: str, path: Path | None = None) -> None:
    """Overwrite the version module (default: the package's) with ``version``."""
    (path or VERSION_FILE).write_text(
        '"""Package version. Overwritten from the git tag by the release pipeline."""\n\n'
        f'__version__ = "{version}"\n',
        encoding="utf-8",
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: version_from_tag.py <tag>\n")
        return 2
    try:
        version = parse_tag(argv[1])
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    write_version(version)
    sys.stdout.write(version + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
