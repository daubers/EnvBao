import subprocess
from pathlib import Path

import pytest

from envbao import version


@pytest.mark.parametrize(
    "described, expected",
    [
        ("v0.1.0-0-g19a5373", "0.1.0"),
        ("v0.1.0-3-g19a5373", "0.1.0.dev3+g19a5373"),
        ("v1.2.3-rc.1-0-gabc1234", "1.2.3-rc.1"),
        ("v0.1.0-0-g19a5373-dirty", "0.1.0.dev0+g19a5373.dirty"),
        ("v0.1.0-3-g19a5373-dirty\n", "0.1.0.dev3+g19a5373.dirty"),
        ("19a5373", None),
        ("", None),
        ("release-1-g123", None),
    ],
)
def test_format_describe(described: str, expected: str | None) -> None:
    assert version.format_describe(described) == expected


def test_stamped_release_version_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version, "STAMPED", "2.0.0")
    monkeypatch.setattr(version, "_git_version", lambda root: pytest.fail("git must not run"))
    assert version.get_version() == "2.0.0"


def test_frozen_never_uses_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version.sys, "frozen", True, raising=False)
    monkeypatch.setattr(version, "_git_version", lambda root: pytest.fail("git must not run"))
    assert version.get_version() == version.STAMPED


def test_source_checkout_uses_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version, "STAMPED", version.PLACEHOLDER)
    monkeypatch.setattr(version, "_git_version", lambda root: "0.1.0.dev3+gabc")
    assert version.get_version() == "0.1.0.dev3+gabc"


def test_falls_back_to_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version, "STAMPED", version.PLACEHOLDER)
    monkeypatch.setattr(version, "_git_version", lambda root: None)
    assert version.get_version() == version.PLACEHOLDER


def test_git_version_no_repo(tmp_path: Path) -> None:
    assert version._git_version(tmp_path) is None  # noqa: SLF001


def test_git_version_real_repo(tmp_path: Path) -> None:
    def git(*a: str) -> None:
        subprocess.run(  # noqa: S603
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],  # noqa: S607
            cwd=tmp_path, check=True, capture_output=True,
        )  # fmt: skip

    git("init", "-q")
    (tmp_path / "f").write_text("1")
    git("add", "f")
    git("commit", "-qm", "one")
    git("tag", "v1.0.0")
    assert version._git_version(tmp_path) == "1.0.0"  # noqa: SLF001
    (tmp_path / "f").write_text("2")
    git("commit", "-qam", "two")
    assert version._git_version(tmp_path).startswith("1.0.0.dev1+g")  # type: ignore[union-attr]  # noqa: SLF001
    (tmp_path / "f").write_text("3")
    assert version._git_version(tmp_path).endswith(".dirty")  # type: ignore[union-attr]  # noqa: SLF001


def test_git_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(version.shutil, "which", lambda name: None)
    assert version._git_version(tmp_path) is None  # noqa: SLF001


def test_git_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    def boom(*a: object, **k: object) -> None:
        raise OSError

    monkeypatch.setattr(version.subprocess, "run", boom)
    assert version._git_version(tmp_path) is None  # noqa: SLF001
