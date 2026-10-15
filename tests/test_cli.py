import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from envbao import __version__
from envbao.cli import init_config, main
from envbao.errors import EXIT_AUTH, EXIT_CONFIG, EXIT_FAILURE, EXIT_OK, EXIT_UNAVAILABLE

from .conftest import TOKEN, FakeBao, write_config


def conf(fake: FakeBao, token: str = TOKEN, extra: str = "") -> str:
    return f"""
[server]
address = "{fake.url}"
token = "{token}"

[[query]]
mount = "secret"
path = "app"
{extra}
"""


def test_exports_variables(
    fake_bao: FakeBao, config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_bao.kv2("secret", "app", {"A": "1", "B": "it's"})
    path = write_config(config_dir, conf(fake_bao))
    assert main(["--config", str(path), "--shell", "bash"]) == EXIT_OK
    out = capsys.readouterr()
    assert out.out == "export A=1\nexport B='it'\"'\"'s'\n"
    assert TOKEN not in out.err


def test_fish_and_auto_shell(
    fake_bao: FakeBao,
    config_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bao.kv2("secret", "app", {"A": "1"})
    path = write_config(config_dir, conf(fake_bao))
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    assert main(["--config", str(path)]) == EXIT_OK
    assert capsys.readouterr().out == "set -gx A '1'\n"


def test_insecure_permissions_exit_with_empty_stdout(
    fake_bao: FakeBao, config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_bao.kv2("secret", "app", {"A": "1"})
    path = write_config(config_dir, conf(fake_bao), mode=0o644)
    assert main(["--config", str(path)]) == EXIT_CONFIG
    out = capsys.readouterr()
    assert out.out == ""
    assert "chmod 0600" in out.err
    assert fake_bao.requests == []  # server never contacted


def test_insecure_directory(
    fake_bao: FakeBao, config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_config(config_dir, conf(fake_bao))
    config_dir.chmod(0o755)
    assert main(["--config", str(path)]) == EXIT_CONFIG
    assert capsys.readouterr().out == ""


def test_bad_token_exit_code(
    fake_bao: FakeBao, config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_config(config_dir, conf(fake_bao, token="s.nope", extra="kv_version = 2"))
    assert main(["--config", str(path)]) == EXIT_AUTH
    out = capsys.readouterr()
    assert out.out == ""
    assert "s.nope" not in out.err


def test_unreachable_server(config_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_config(
        config_dir,
        '[server]\naddress = "http://127.0.0.1:1"\ntoken = "t"\ntimeout = 1\n'
        '[[query]]\nmount = "s"\npath = "p"\n',
    )
    assert main(["--config", str(path)]) == EXIT_UNAVAILABLE
    assert capsys.readouterr().out == ""


def test_missing_config(config_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(config_dir / "config.toml")]) == EXIT_CONFIG
    assert capsys.readouterr().out == ""


def test_invalid_query_options(
    fake_bao: FakeBao, config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_config(config_dir, conf(fake_bao, extra="kv_version = 9"))
    assert main(["--config", str(path)]) == EXIT_CONFIG
    assert "query #1" in capsys.readouterr().err


def test_fatal_query_error_prints_nothing(
    fake_bao: FakeBao, config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_bao.kv2("secret", "app", {"A": "1"})
    path = write_config(
        config_dir,
        conf(fake_bao)
        + '[[query]]\nmount = "secret"\npath = "gone"\non_missing = "error"\nkv_version = 2\n',
    )
    assert main(["--config", str(path)]) == EXIT_UNAVAILABLE
    assert capsys.readouterr().out == ""


def test_check_mode(
    fake_bao: FakeBao, config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_config(config_dir, conf(fake_bao))
    assert main(["--config", str(path), "--check"]) == EXIT_OK
    out = capsys.readouterr()
    assert out.out == ""
    assert "OK" in out.err and "ttl 3600" in out.err


def test_log_file_created_private(
    fake_bao: FakeBao, config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_bao.kv2("secret", "app", {"A": "1"})
    path = write_config(config_dir, conf(fake_bao))
    assert main(["--config", str(path)]) == EXIT_OK
    log = config_dir / "envbao.log"
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    text = log.read_text()
    assert "exported 1 variable" in text and TOKEN not in text
    # The log file itself must not trip the permission policy on the next run.
    assert main(["--config", str(path)]) == EXIT_OK


def test_unexpected_exception_hides_details(
    fake_bao: FakeBao,
    config_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = write_config(config_dir, conf(fake_bao))

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError(f"leaks {TOKEN}")

    monkeypatch.setattr("envbao.cli.resolve", boom)
    assert main(["--config", str(path)]) == EXIT_FAILURE
    out = capsys.readouterr()
    assert out.out == "" and TOKEN not in out.err and "RuntimeError" in out.err


def test_verbose_flag(
    fake_bao: FakeBao, config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_bao.kv2("secret", "app", {"A": "1"})
    path = write_config(config_dir, conf(fake_bao))
    assert main(["--config", str(path), "-vv"]) == EXIT_OK
    assert "KV v2" in capsys.readouterr().err


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


# ---- init -----------------------------------------------------------------------------


def test_init_creates_private_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / ".envbao" / "config.toml"
    assert main(["--config", str(path), "--init"]) == EXIT_OK
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "[server]" in path.read_text()


def test_init_refuses_to_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / ".envbao" / "config.toml"
    init_config(path)
    path.write_text("mine")
    assert main(["--config", str(path), "--init"]) == EXIT_FAILURE
    assert path.read_text() == "mine"


def test_init_tightens_existing_directory(tmp_path: Path) -> None:
    d = tmp_path / ".envbao"
    d.mkdir(mode=0o755)
    init_config(d / "config.toml")
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_template_passes_permission_policy_but_needs_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / ".envbao" / "config.toml"
    main(["--config", str(path), "--init"])
    capsys.readouterr()
    assert main(["--config", str(path)]) == EXIT_CONFIG
    assert "token is required" in capsys.readouterr().err


# ---- module entry point ---------------------------------------------------------------


def test_python_dash_m(tmp_path: Path) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "envbao", "--version"],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ},
    )
    assert __version__ in result.stdout
