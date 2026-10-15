"""Filesystem permission policy for the EnvBao configuration directory.

The directory holds an API token, so EnvBao refuses to run unless everything in it is
readable and writable by the current user only. Symlinks are rejected outright to avoid
being tricked into reading (or trusting the permissions of) a file elsewhere.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from envbao.errors import PermissionPolicyError

log = logging.getLogger(__name__)

_GROUP_OTHER_BITS = stat.S_IRWXG | stat.S_IRWXO


def _check(path: Path, *, expect_dir: bool) -> None:
    """Verify ``path`` is a non-symlink owned by the current user with no group/other access.

    Raises:
        PermissionPolicyError: if any check fails.
    """
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise PermissionPolicyError(f"{path} does not exist") from None
    except OSError as exc:
        raise PermissionPolicyError(f"cannot stat {path}: {exc.strerror}") from None

    if stat.S_ISLNK(st.st_mode):
        raise PermissionPolicyError(f"{path} is a symbolic link; refusing to follow it")
    if expect_dir and not stat.S_ISDIR(st.st_mode):
        raise PermissionPolicyError(f"{path} is not a directory")
    if not expect_dir and not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
        raise PermissionPolicyError(f"{path} is not a regular file or directory")
    if st.st_uid != os.geteuid():
        raise PermissionPolicyError(f"{path} is not owned by the current user")

    mode = stat.S_IMODE(st.st_mode)
    if mode & _GROUP_OTHER_BITS:
        want = "0700" if stat.S_ISDIR(st.st_mode) else "0600"
        raise PermissionPolicyError(
            f"{path} has mode {mode:04o}; it must be accessible by the owner only "
            f"(fix with: chmod {want} '{path}')"
        )


def check_path(path: Path, *, expect_dir: bool) -> None:
    """Check a single path against the policy, logging any violation at CRITICAL.

    Raises:
        PermissionPolicyError: if any check fails.
    """
    try:
        _check(path, expect_dir=expect_dir)
    except PermissionPolicyError as exc:
        log.critical("permission policy violation: %s", exc)
        raise


def check_config_dir(directory: Path) -> None:
    """Check ``directory`` and everything beneath it against the permission policy.

    Raises:
        PermissionPolicyError: on the first violation found. The violation is also logged
            at CRITICAL level for auditing.
    """
    try:
        _check(directory, expect_dir=True)
        for root, dirs, files in os.walk(directory, followlinks=False):
            for name in (*dirs, *files):
                _check(Path(root) / name, expect_dir=False)
    except PermissionPolicyError as exc:
        log.critical("permission policy violation: %s", exc)
        raise
