import os
from pathlib import Path

import pytest

from envbao.errors import EXIT_CONFIG, PermissionPolicyError
from envbao.security import check_config_dir, check_path


def test_safe_directory_passes(config_dir: Path) -> None:
    (config_dir / "config.toml").write_text("x")
    (config_dir / "config.toml").chmod(0o600)
    check_config_dir(config_dir)


def test_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(PermissionPolicyError, match="does not exist"):
        check_config_dir(tmp_path / "nope")


@pytest.mark.parametrize("mode", [0o755, 0o770, 0o701, 0o750])
def test_directory_too_open(config_dir: Path, mode: int) -> None:
    config_dir.chmod(mode)
    with pytest.raises(PermissionPolicyError, match="chmod 0700"):
        check_config_dir(config_dir)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o602, 0o660])
def test_file_too_open(config_dir: Path, mode: int) -> None:
    f = config_dir / "config.toml"
    f.write_text("x")
    f.chmod(mode)
    with pytest.raises(PermissionPolicyError, match="chmod 0600"):
        check_config_dir(config_dir)


def test_nested_file_checked(config_dir: Path) -> None:
    sub = config_dir / "sub"
    sub.mkdir(mode=0o700)
    bad = sub / "token"
    bad.write_text("x")
    bad.chmod(0o666)
    with pytest.raises(PermissionPolicyError):
        check_config_dir(config_dir)


def test_symlink_rejected(config_dir: Path, tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.write_text("x")
    target.chmod(0o600)
    (config_dir / "link").symlink_to(target)
    with pytest.raises(PermissionPolicyError, match="symbolic link"):
        check_config_dir(config_dir)


def test_symlinked_directory_rejected(tmp_path: Path, config_dir: Path) -> None:
    link = tmp_path / "linkdir"
    link.symlink_to(config_dir)
    with pytest.raises(PermissionPolicyError, match="symbolic link"):
        check_config_dir(link)


def test_directory_given_as_file(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_text("x")
    f.chmod(0o600)
    with pytest.raises(PermissionPolicyError, match="not a directory"):
        check_path(f, expect_dir=True)


def test_other_owner_rejected(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: real + 1)
    with pytest.raises(PermissionPolicyError, match="not owned"):
        check_path(config_dir, expect_dir=True)


def test_special_file_rejected(config_dir: Path) -> None:
    fifo = config_dir / "pipe"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(PermissionPolicyError, match="not a regular"):
        check_config_dir(config_dir)


def test_violation_is_logged_critical(config_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    config_dir.chmod(0o755)
    with pytest.raises(PermissionPolicyError):
        check_config_dir(config_dir)
    assert any(r.levelname == "CRITICAL" for r in caplog.records)


def test_exit_code() -> None:
    assert PermissionPolicyError.exit_code == EXIT_CONFIG
