import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from envbao.config import load_config

_spec = importlib.util.spec_from_file_location(
    "migrate_zshrc", Path(__file__).parent.parent / "scripts" / "migrate_zshrc.py"
)
assert _spec and _spec.loader
mz = importlib.util.module_from_spec(_spec)
sys.modules["migrate_zshrc"] = mz
_spec.loader.exec_module(mz)

SECRET_A = "tok-AAAA-secret-value"
SECRET_B = "pa$$word-BBBB"  # literal in single quotes below

ZSHRC = f"""\
export PATH=$HOME/bin:$PATH
export ZSH="$HOME/.oh-my-zsh"
export AWS_ACCESS_KEY_ID=ffaeb8e5-aaaa
export AWS_SECRET_ACCESS_KEY={SECRET_A}
export GITEA_TOKEN='{SECRET_B}'
export WIKI_USER="old"
    export NOT_TOP_LEVEL=x
export WIKI_USER="matt"   # trailing comment
export PROTON_BRIDGE_PASS="U_3u-Q"
export PROTON_BRIDGE_HOST="127.0.0.1"
export DYNAMIC=$(cat /etc/hostname)
export BAO_TOKEN="s.admin-token"
export BAO_ADDR=https://vault.example/
export MYSTERY_VAR=abc
export QUOTED="has \\"escaped\\" quote"
export BROKEN="no end
"""


def parsed() -> "mz.ParseResult":
    return mz.parse_zshrc(ZSHRC)


# ---- parsing --------------------------------------------------------------------------


def test_parse_literals() -> None:
    e = parsed().entries
    assert e["AWS_SECRET_ACCESS_KEY"].value == SECRET_A
    assert e["GITEA_TOKEN"].value == SECRET_B
    assert e["WIKI_USER"].value == "matt"
    assert e["WIKI_USER"].lines == (6, 8)
    assert e["QUOTED"].value == 'has "escaped" quote'


def test_parse_ignores_indented_and_non_literals() -> None:
    p = parsed()
    assert "NOT_TOP_LEVEL" not in p.entries
    assert "PATH" not in p.entries and "PATH" in p.skipped_dynamic
    assert "DYNAMIC" in p.skipped_dynamic
    assert "BROKEN" in p.skipped_dynamic


def test_later_literal_clears_dynamic() -> None:
    p = mz.parse_zshrc("export A=$X\nexport A=lit\n")
    assert p.entries["A"].value == "lit" and "A" not in p.skipped_dynamic


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("abc", "abc"),
        ("abc # c", "abc"),
        ("'a b'", "a b"),
        ('"a b"', "a b"),
        ("'x' y", None),
        ("'unterminated", None),
        ("a=b==", "a=b=="),
        ("a$b", None),
        ("~/x", None),
        ('"\\$HOME"', "$HOME"),
        ("", ""),
    ],
)
def test_parse_value(raw: str, expected: str | None) -> None:
    assert mz._parse_value(raw) == expected  # noqa: SLF001


def test_entry_repr_hides_value() -> None:
    assert SECRET_A not in repr(parsed().entries["AWS_SECRET_ACCESS_KEY"])


# ---- classification -------------------------------------------------------------------


def test_classify() -> None:
    groups, unmapped = mz.classify(parsed().entries)
    assert list(groups) == ["aws", "gitea", "proton", "wiki"]
    assert set(groups["proton"]) == {"PROTON_BRIDGE_PASS", "PROTON_BRIDGE_HOST"}
    assert "BAO_TOKEN" not in str(groups) and "BAO_ADDR" not in str(groups)
    assert unmapped == ["MYSTERY_VAR", "QUOTED"]


def test_classify_extra_mapping() -> None:
    groups, unmapped = mz.classify(parsed().entries, {"MYSTERY_VAR": "misc"})
    assert "misc" in groups and "MYSTERY_VAR" not in unmapped


# ---- scrub / helpers ------------------------------------------------------------------


def test_scrub() -> None:
    assert mz.scrub("bad abcdef here", ["abcdef", "ab"]) == "bad <redacted> here"


def test_render_config_is_valid_for_envbao(tmp_path: Path) -> None:
    text = mz.render_config("https://vault.example", "env", ["aws", "wiki"], 2)
    tok = tmp_path / "token"
    tok.write_text("t")
    tok.chmod(0o600)
    cfg = load_config_from_text(tmp_path, text.replace("~/.envbao/token", str(tok)))
    assert [q.options["path"] for q in cfg.queries] == ["shell/aws", "shell/wiki"]
    assert all(q.options["mount"] == "env" and q.options["kv_version"] == 2 for q in cfg.queries)
    assert cfg.server.address == "https://vault.example"


def load_config_from_text(directory: Path, text: str):  # type: ignore[no-untyped-def]
    p = directory / "c.toml"
    p.write_text(text)
    return load_config(p)


def test_policy_text_v1_v2() -> None:
    assert 'path "env/data/shell/*"' in mz.policy_text("env", 2)
    assert 'path "env/shell/*"' in mz.policy_text("env", 1)
    assert "lookup-self" in mz.policy_text("env", 2)


def test_install_config_permissions_and_backup(tmp_path: Path) -> None:
    d = tmp_path / ".envbao"
    d.mkdir(mode=0o755)
    (d / "config.toml").write_text("old")
    mz.install_config(d, "new", "s.tok")
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    for name in ("config.toml", "config.toml.bak", "token"):
        assert stat.S_IMODE((d / name).stat().st_mode) == 0o600
    assert (d / "config.toml.bak").read_text() == "old"
    assert (d / "token").read_text() == "s.tok"


def test_install_config_without_token(tmp_path: Path) -> None:
    mz.install_config(tmp_path / "x", "cfg", None)
    assert not (tmp_path / "x" / "token").exists()


# ---- end to end with a stub `bao` -----------------------------------------------------

STUB = """#!{python}
import json, os, sys
state = os.environ["STUB_DIR"]
args = sys.argv[1:]
stdin = "" if sys.stdin.isatty() else sys.stdin.read()
with open(os.path.join(state, "calls.log"), "a") as fh:
    fh.write(json.dumps({{"args": args, "stdin": stdin}}) + "\\n")
ver = os.environ.get("STUB_KV_VERSION", "2")
if args[0] == "read":
    print(json.dumps({{"data": {{"options": {{"version": ver}}}}}}))
elif args[:2] == ["kv", "get"]:
    f = os.path.join(state, args[-1].replace("/", "_") + ".json")
    if not os.path.exists(f):
        sys.stderr.write("No value found at " + args[-1]); sys.exit(2)
    data = json.load(open(f))
    if os.environ.get("STUB_CORRUPT"):
        data[sorted(data)[0]] = "tampered"
    print(json.dumps({{"data": {{"data": data}} if ver == "2" else data}}))
elif args[:2] == ["kv", "put"]:
    with open(os.path.join(state, args[-2].replace("/", "_") + ".json"), "w") as fh:
        fh.write(stdin)
elif args[:2] == ["policy", "write"]:
    open(os.path.join(state, "policy.hcl"), "w").write(stdin)
elif args[:2] == ["token", "create"]:
    if os.environ.get("STUB_DENY_TOKEN"):
        sys.stderr.write("permission denied"); sys.exit(2)
    print("s.new-envbao-token")
else:
    sys.exit(9)
"""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "bao"
    stub.write_text(STUB.format(python=sys.executable))
    stub.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    rc = tmp_path / "zshrc"
    rc.write_text(ZSHRC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_DIR", str(state))
    monkeypatch.setenv("BAO_ADDR", "https://vault.example/")
    monkeypatch.setenv("BAO_TOKEN", "s.admin-token")
    return {"state": state, "rc": rc, "cfg": tmp_path / ".envbao"}


def run(env: dict[str, Path], *extra: str) -> int:
    return mz.main(
        ["--zshrc", str(env["rc"]), "--config-dir", str(env["cfg"]), "--no-check", *extra]
    )


def calls(env: dict[str, Path]) -> list[dict]:
    log = env["state"] / "calls.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_dry_run_prints_names_not_values(
    env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(env) == 0
    out = capsys.readouterr()
    text = out.out + out.err
    assert "AWS_SECRET_ACCESS_KEY  (line 4)" in text
    assert "Dry run" in text and "needs shell expansion" in text
    for secret in (SECRET_A, SECRET_B, "U_3u-Q", "s.admin-token"):
        assert secret not in text
    assert calls(env) == [] and not env["cfg"].exists()


def test_apply_end_to_end(env: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    assert run(env, "--apply") == 0
    out = capsys.readouterr()
    text = out.out + out.err
    for secret in (SECRET_A, SECRET_B, "U_3u-Q", "s.admin-token", "s.new-envbao-token"):
        assert secret not in text

    stored = json.loads((env["state"] / "shell_aws.json").read_text())
    assert stored == {"AWS_ACCESS_KEY_ID": "ffaeb8e5-aaaa", "AWS_SECRET_ACCESS_KEY": SECRET_A}

    # no secret value ever appears in any argv
    argv = " ".join(" ".join(c["args"]) for c in calls(env))
    assert SECRET_A not in argv and SECRET_B not in argv and "s.admin" not in argv

    cfg_dir = env["cfg"]
    assert stat.S_IMODE((cfg_dir / "token").stat().st_mode) == 0o600
    assert (cfg_dir / "token").read_text() == "s.new-envbao-token"
    assert "token = " not in (cfg_dir / "config.toml").read_text()
    assert "env/data/shell/*" in (env["state"] / "policy.hcl").read_text()
    assert "remove these lines" in text and "rotate" in text


def test_generated_config_loads(env: dict[str, Path]) -> None:
    assert run(env, "--apply") == 0
    home = env["cfg"].parent
    os.environ["HOME"] = str(home)  # token_file uses ~
    cfg = load_config(env["cfg"] / "config.toml")
    assert cfg.server.token == "s.new-envbao-token"
    assert cfg.server.address == "https://vault.example"
    assert {q.options["path"] for q in cfg.queries} == {
        "shell/aws", "shell/gitea", "shell/proton", "shell/wiki",
    }  # fmt: skip


def test_kv_v1(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUB_KV_VERSION", "1")
    assert run(env, "--apply") == 0
    assert "kv_version = 1" in (env["cfg"] / "config.toml").read_text()
    assert 'path "env/shell/*"' in (env["state"] / "policy.hcl").read_text()


def test_refuses_to_overwrite_then_force(
    env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(env, "--apply") == 0
    capsys.readouterr()
    assert run(env, "--apply") == 1
    assert "already exists" in capsys.readouterr().err
    assert run(env, "--apply", "--force") == 0


def test_verification_failure(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("STUB_CORRUPT", "1")
    assert run(env, "--apply", "--force") == 1
    err = capsys.readouterr().err
    assert "verification FAILED" in err and "tampered" not in err
    assert not env["cfg"].exists()  # no config written when verification fails


def test_token_creation_denied(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("STUB_DENY_TOKEN", "1")
    assert run(env, "--apply") == 1
    err = capsys.readouterr().err
    assert "could not be created" in err and "migrated" in err
    assert (env["cfg"] / "config.toml").exists() and not (env["cfg"] / "token").exists()


def test_requires_bao_env(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAO_TOKEN")
    assert run(env, "--apply") == 1


def test_bad_group_option(env: dict[str, Path]) -> None:
    assert run(env, "--group", "NOEQUALS") == 2
    assert run(env, "--group", "X=Bad Group") == 2


def test_group_option_migrates_unmapped(env: dict[str, Path]) -> None:
    assert run(env, "--apply", "--group", "MYSTERY_VAR=misc") == 0
    assert json.loads((env["state"] / "shell_misc.json").read_text()) == {"MYSTERY_VAR": "abc"}


def test_missing_zshrc(tmp_path: Path) -> None:
    assert mz.main(["--zshrc", str(tmp_path / "nope")]) == 1


def test_nothing_to_migrate(tmp_path: Path, env: dict[str, Path]) -> None:
    empty = tmp_path / "empty"
    empty.write_text("export PATH=/x\n")
    assert mz.main(["--zshrc", str(empty), "--apply"]) == 1


def test_bao_error_is_scrubbed() -> None:
    bao = mz.Bao(["supersecretvalue"], executable=sys.executable)
    with pytest.raises(mz.MigrationError) as exc:
        bao.run(
            "-c", "import sys; sys.stderr.write('oops ' + 'supersecret' + 'value'); sys.exit(1)"
        )
    assert "supersecretvalue" not in str(exc.value) and "redacted" in str(exc.value)
