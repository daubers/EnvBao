import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "version_from_tag", Path(__file__).parent.parent / "scripts" / "version_from_tag.py"
)
assert _spec and _spec.loader
vft = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vft)


@pytest.mark.parametrize(
    "tag, version",
    [
        ("v0.0.1", "0.0.1"),
        ("v1.2.3", "1.2.3"),
        ("v10.20.30", "10.20.30"),
        ("v1.0.0-rc.1", "1.0.0-rc.1"),
        ("v1.0.0-alpha+001", "1.0.0-alpha+001"),
        ("v1.0.0+20130313144700", "1.0.0+20130313144700"),
    ],
)
def test_valid_tags(tag: str, version: str) -> None:
    assert vft.parse_tag(tag) == version


@pytest.mark.parametrize(
    "tag",
    [
        "1.2.3",
        "v1.2",
        "v01.2.3",
        "v1.2.3.4",
        "v1.2.3-",
        "v1.2.3-01",
        "latest",
        "",
        "v1.2.3 ",
        "V1.2.3",
    ],
)
def test_invalid_tags(tag: str) -> None:
    with pytest.raises(ValueError, match="not a valid semver"):
        vft.parse_tag(tag)


def test_main_writes_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "_version.py"
    monkeypatch.setattr(vft, "VERSION_FILE", target)
    assert vft.main(["x", "v2.0.1"]) == 0
    assert '__version__ = "2.0.1"' in target.read_text()
    assert capsys.readouterr().out == "2.0.1\n"


def test_write_version_real(tmp_path: Path) -> None:
    target = tmp_path / "v.py"
    vft.write_version("3.1.4", target)
    assert "Overwritten" in target.read_text() and '"3.1.4"' in target.read_text()


def test_main_rejects_bad_tag(capsys: pytest.CaptureFixture[str]) -> None:
    assert vft.main(["x", "nope"]) == 1
    assert "not a valid semver" in capsys.readouterr().err


def test_main_usage() -> None:
    assert vft.main(["x"]) == 2
