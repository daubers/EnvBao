"""Version reporting.

Release binaries carry the version stamped into ``_version.py`` from the git tag. When
running from a source checkout (stamp is the placeholder) the version is derived from
``git describe`` so a development build never masquerades as a release, e.g.
``0.1.0.dev3+g19a5373`` (3 commits after ``v0.1.0``), with ``.dirty`` appended if the
working tree has uncommitted changes.

git is only consulted lazily (``--version``), never during normal shell start-up.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # noqa: S404
import sys
from pathlib import Path

from envbao._version import __version__ as STAMPED

PLACEHOLDER = "0.0.0.dev0"
_DESCRIBE_RE = re.compile(r"^v(?P<tag>.+?)-(?P<n>\d+)-g(?P<sha>[0-9a-f]+)(?P<dirty>-dirty)?$")


def format_describe(described: str) -> str | None:
    """Convert ``git describe --long --dirty`` output to a PEP 440 style version.

    ``v0.1.0-0-gabc1234`` -> ``0.1.0``;
    ``v0.1.0-3-gabc1234-dirty`` -> ``0.1.0.dev3+gabc1234.dirty``.
    Returns None if the text is not from a ``v*`` tag.
    """
    match = _DESCRIBE_RE.match(described.strip())
    if not match:
        return None
    tag, count, sha, dirty = match["tag"], int(match["n"]), match["sha"], bool(match["dirty"])
    if count == 0 and not dirty:
        return tag
    local = f"g{sha}.dirty" if dirty else f"g{sha}"
    return f"{tag}.dev{count}+{local}"


def _git_version(root: Path) -> str | None:
    git = shutil.which("git")
    if not git or not (root / ".git").exists():
        return None
    try:
        proc = subprocess.run(  # noqa: S603
            [git, "describe", "--tags", "--long", "--dirty", "--match", "v[0-9]*"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return format_describe(proc.stdout) if proc.returncode == 0 else None


def get_version() -> str:
    """Return the stamped release version, or a git-derived one for source checkouts."""
    if STAMPED != PLACEHOLDER or getattr(sys, "frozen", False):
        return STAMPED
    root = Path(__file__).resolve().parents[2]
    return _git_version(root) or STAMPED
