import os
import sys
from pathlib import Path

import pytest

from envbao.cli import main
from envbao.config import Config, Query, ServerConfig, parse_config, resolve_config_path
from envbao.errors import EXIT_CONFIG, EXIT_UNAVAILABLE, ConfigError
from envbao.runner import MASK, Redactor, run_command, select_queries

from .conftest import TOKEN, FakeBao, write_config

SECRET = "sup3r-secret-value"


def drain(r: Redactor, chunks: list[bytes]) -> bytes:
    return b"".join(r.feed(c) for c in chunks) + r.flush()


# ---- Redactor -------------------------------------------------------------------------


def test_redacts_simple() -> None:
    assert drain(Redactor([SECRET]), [f"a {SECRET} b".encode()]) == b"a *** b"


def test_redacts_across_chunk_boundary_every_split() -> None:
    data = f"x{SECRET}y{SECRET}z".encode()
    for i in range(1, len(data)):
        out = drain(Redactor([SECRET]), [data[:i], data[i:]])
        assert out == b"x***y***z", i


def test_redacts_byte_at_a_time() -> None:
    data = f"pre {SECRET} post".encode()
    assert drain(Redactor([SECRET]), [bytes([b]) for b in data]) == b"pre *** post"


def test_never_emits_partial_secret_early() -> None:
    r = Redactor([SECRET])
    emitted = r.feed(SECRET[:-3].encode())
    assert emitted == b""  # held back until we know


def test_json_and_url_forms() -> None:
    secret = 'pa"ss/word+1'
    out = drain(Redactor([secret]), [b'{"k":"pa\\"ss/word+1"} pa%22ss%2Fword%2B1 pa"ss/word+1'])
    assert b"word" not in out and out.count(MASK) == 3


def test_longest_match_wins() -> None:
    out = drain(Redactor(["abcdef", "abcdefghij"]), [b"abcdefghij"])
    assert out == b"***"


def test_short_values_not_redacted() -> None:
    assert drain(Redactor(["abc", "1143"]), [b"port 1143 abc"]) == b"port 1143 abc"


def test_no_secrets_passthrough() -> None:
    r = Redactor([])
    assert r.feed(b"hello") == b"hello" and r.flush() == b""


def test_binary_safe() -> None:
    data = b"\xff\x00" + SECRET.encode() + b"\x80"
    assert drain(Redactor([SECRET]), [data]) == b"\xff\x00***\x80"


# ---- run_command ----------------------------------------------------------------------


def py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_child_sees_secret_but_output_is_masked(capfdbinary: pytest.CaptureFixture[bytes]) -> None:
    code = "import os,sys; print(os.environ['TOK']); print(os.environ['TOK'], file=sys.stderr)"
    assert run_command(py(code), {"TOK": SECRET}) == 0
    out, err = capfdbinary.readouterr()
    assert out == b"***\n" and err == b"***\n"


def test_child_can_use_secret(capfdbinary: pytest.CaptureFixture[bytes]) -> None:
    code = "import os,sys; sys.exit(0 if os.environ['TOK']=='" + SECRET + "' else 3)"
    assert run_command(py(code), {"TOK": SECRET}) == 0


def test_exit_code_propagates() -> None:
    assert run_command(py("raise SystemExit(7)"), {}) == 7


def test_signal_exit_code() -> None:
    assert run_command(py("import os,signal; os.kill(os.getpid(), signal.SIGTERM)"), {}) == 143


def test_environment_is_inherited_and_overridden() -> None:
    code = "import os,sys; sys.exit(int(os.environ['A'])+int(os.environ['B']))"
    assert run_command(py(code), {"B": "2"}, environ={"A": "1", "B": "9"}) == 3


def test_command_not_found(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_command(["/nonexistent/cmd"], {}) == 127
    assert "not found" in capsys.readouterr().err


def test_not_executable(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_text("x")
    assert run_command([str(f)], {}) == 126


def test_empty_command() -> None:
    with pytest.raises(ConfigError, match="no command"):
        run_command([], {})


def test_no_redact_uses_execvpe(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake(file: str, args: list[str], env: dict[str, str]) -> None:
        seen.update(file=file, args=args, tok=env["TOK"])
        raise FileNotFoundError

    monkeypatch.setattr(os, "execvpe", fake)
    assert run_command(["echo", "hi"], {"TOK": SECRET}, redact=False) == 127
    assert seen == {"file": "echo", "args": ["echo", "hi"], "tok": SECRET}


# ---- select_queries / config ----------------------------------------------------------


def cfg(*queries: Query) -> Config:
    return Config(ServerConfig("https://b", "t"), queries)


def q(name: str, path: str = "p") -> Query:
    return Query("kv", {"mount": "env", "path": path}, name=name)


def test_select_in_order_and_dedup() -> None:
    c = cfg(q("a"), q("b"), q("c"))
    assert [x.name for x in select_queries(c, ["c", "a", "c"])] == ["c", "a"]


def test_select_unknown_lists_available() -> None:
    with pytest.raises(ConfigError, match=r"unknown group 'zzz' \(available: a, b\)"):
        select_queries(cfg(q("a"), q("b")), ["zzz"])


def test_select_ambiguous() -> None:
    with pytest.raises(ConfigError, match="ambiguous"):
        select_queries(cfg(q("a"), q("a", "other")), ["a"])


def test_select_requires_names() -> None:
    with pytest.raises(ConfigError, match="at least one"):
        select_queries(cfg(q("a")), [])


def test_default_and_explicit_query_names() -> None:
    c = parse_config(
        {
            "server": {"address": "https://b", "token": "t"},
            "query": [
                {"mount": "env", "path": "shell/gitea"},
                {"mount": "env", "path": "x/y", "name": "custom"},
                {"engine": "other"},
            ],
        }
    )
    assert [x.name for x in c.queries] == ["gitea", "custom", "other"]


def test_bad_query_name() -> None:
    with pytest.raises(ConfigError, match="name"):
        parse_config(
            {
                "server": {"address": "https://b", "token": "t"},
                "query": [{"mount": "m", "path": "p", "name": "bad name!"}],
            }
        )


def test_envbao_config_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ENVBAO_CONFIG", str(tmp_path / "claude.toml"))
    assert resolve_config_path(None) == tmp_path / "claude.toml"
    assert resolve_config_path(str(tmp_path / "x.toml")) == tmp_path / "x.toml"


# ---- CLI exec -------------------------------------------------------------------------


def exec_conf(fake: FakeBao) -> str:
    return f"""
[server]
address = "{fake.url}"
token = "{TOKEN}"

[[query]]
mount = "secret"
path = "shell/gitea"
kv_version = 2

[[query]]
mount = "secret"
path = "shell/wiki"
kv_version = 2

[[query]]
mount = "secret"
path = "shell/empty"
kv_version = 2
"""


@pytest.fixture
def exec_cfg(fake_bao: FakeBao, config_dir: Path) -> Path:
    fake_bao.routes["/v1/secret/data/shell/gitea"] = (
        200,
        {"data": {"data": {"GITEA_TOKEN": SECRET}}},
    )
    fake_bao.routes["/v1/secret/data/shell/wiki"] = (
        200,
        {"data": {"data": {"WIKI_PASS": "another-secret-1"}}},
    )
    return write_config(config_dir, exec_conf(fake_bao))


def test_exec_masks_and_exits(exec_cfg: Path, capfdbinary: pytest.CaptureFixture[bytes]) -> None:
    code = "import os; print('t=' + os.environ['GITEA_TOKEN']); raise SystemExit(4)"
    rc = main(["--config", str(exec_cfg), "exec", "-q", "gitea", "--", sys.executable, "-c", code])
    assert rc == 4
    assert capfdbinary.readouterr().out == b"t=***\n"


def test_exec_multiple_groups(exec_cfg: Path) -> None:
    code = (
        "import os,sys; "
        "sys.exit(0 if 'GITEA_TOKEN' in os.environ and 'WIKI_PASS' in os.environ else 9)"
    )
    argv = [
        "--config",
        str(exec_cfg),
        "exec",
        "-q",
        "gitea",
        "-q",
        "wiki",
        "--",
        sys.executable,
        "-c",
        code,
    ]
    assert main(argv) == 0


def test_exec_only_selected_group_is_exposed(
    exec_cfg: Path, fake_bao: FakeBao, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WIKI_PASS", raising=False)  # may be set in the developer's shell
    code = "import os,sys; sys.exit(0 if 'WIKI_PASS' not in os.environ else 9)"
    assert (
        main(["--config", str(exec_cfg), "exec", "-q", "gitea", "--", sys.executable, "-c", code])
        == 0
    )
    assert all("wiki" not in p for p, _ in fake_bao.requests)


def test_exec_unknown_group_runs_nothing(
    exec_cfg: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = tmp_path / "ran"
    code = f"open({str(marker)!r}, 'w')"
    rc = main(["--config", str(exec_cfg), "exec", "-q", "nope", "--", sys.executable, "-c", code])
    assert rc == EXIT_CONFIG and not marker.exists()
    assert "available: empty, gitea, wiki" in capsys.readouterr().err


def test_exec_failed_fetch_runs_nothing(exec_cfg: Path, tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    code = f"open({str(marker)!r}, 'w')"
    rc = main(["--config", str(exec_cfg), "exec", "-q", "empty", "--", sys.executable, "-c", code])
    assert rc == 0 or rc == EXIT_UNAVAILABLE
    assert not marker.exists()


def test_exec_requires_group_and_command(exec_cfg: Path) -> None:
    assert main(["--config", str(exec_cfg), "exec", "--", "true"]) == EXIT_CONFIG
    assert main(["--config", str(exec_cfg), "exec", "-q", "gitea"]) == EXIT_CONFIG


def test_exec_insecure_permissions(exec_cfg: Path, fake_bao: FakeBao) -> None:
    exec_cfg.chmod(0o644)
    assert main(["--config", str(exec_cfg), "exec", "-q", "gitea", "--", "true"]) == EXIT_CONFIG
    assert fake_bao.requests == []


def test_exec_rejects_check(exec_cfg: Path) -> None:
    assert (
        main(["--config", str(exec_cfg), "--check", "exec", "-q", "gitea", "--", "true"])
        == EXIT_CONFIG
    )


def test_audit_log_has_no_values_or_args(exec_cfg: Path) -> None:
    main(
        [
            "--config",
            str(exec_cfg),
            "exec",
            "-q",
            "gitea",
            "--",
            sys.executable,
            "-c",
            "pass",
            "--flag-arg",
        ]
    )
    text = (exec_cfg.parent / "envbao.log").read_text()
    assert "groups gitea" in text and "python" in text.lower()
    assert SECRET not in text and "--flag-arg" not in text
