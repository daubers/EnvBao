import shlex
import subprocess

import pytest

from envbao.errors import ConfigError
from envbao.render import detect_shell, is_protected, is_valid_name, render


@pytest.mark.parametrize("name", ["A", "_a", "FOO_BAR1", "a"])
def test_valid_names(name: str) -> None:
    assert is_valid_name(name)


@pytest.mark.parametrize("name", ["", "1A", "A-B", "A B", "A=B", "A\n", "A;rm", "é"])
def test_invalid_names(name: str) -> None:
    assert not is_valid_name(name)


@pytest.mark.parametrize("name", ["PATH", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "BASH_FUNC_x%%"])
def test_protected(name: str) -> None:
    assert is_protected(name)


def test_not_protected() -> None:
    assert not is_protected("MY_API_KEY")


def test_posix_render() -> None:
    assert render({"A": "b c", "D": "it's"}) == "export A='b c'\nexport D='it'\"'\"'s'\n"


def test_empty_render() -> None:
    assert render({}) == ""


def test_fish_render() -> None:
    assert render({"A": "it's \\ ok"}, "fish") == "set -gx A 'it\\'s \\\\ ok'\n"


@pytest.mark.parametrize(
    "value",
    [
        "$(touch /tmp/pwned)",
        "`id`",
        "a; b",
        "a\nb",
        "'\"'",
        "$HOME",
        "\\",
        "*",
        "!!",
        "a b\tc",
        "é",
    ],
)
def test_roundtrip_through_real_shell(value: str) -> None:
    script = render({"V": value}) + 'printf %s "$V"'
    out = subprocess.run(  # noqa: S603
        ["/bin/sh", "-c", script],
        capture_output=True,
        check=True,
        text=True,  # noqa: S607
    )
    assert out.stdout == value


def test_values_are_single_argument() -> None:
    assert shlex.split(render({"V": "a; b"}).split("=", 1)[1]) == ["a; b"]


def test_invalid_name_raises() -> None:
    with pytest.raises(ConfigError, match="invalid environment"):
        render({"BAD NAME": "x"})


def test_nul_raises() -> None:
    with pytest.raises(ConfigError, match="NUL"):
        render({"A": "x\x00y"})


def test_unknown_shell() -> None:
    with pytest.raises(ConfigError, match="unsupported"):
        render({"A": "x"}, "powershell")


@pytest.mark.parametrize(
    "shell, expected",
    [
        ("/bin/zsh", "zsh"),
        ("/usr/bin/fish", "fish"),
        ("/bin/bash", "bash"),
        ("/bin/tcsh", "sh"),
        ("", "sh"),
    ],
)
def test_detect_shell(shell: str, expected: str) -> None:
    assert detect_shell({"SHELL": shell}) == expected


def test_detect_shell_from_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/zsh")
    assert detect_shell() == "zsh"
