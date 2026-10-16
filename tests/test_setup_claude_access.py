import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from envbao.config import load_config

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))  # the script imports its sibling migrate_zshrc
_spec = importlib.util.spec_from_file_location(
    "setup_claude_access", SCRIPTS / "setup_claude_access.py"
)
assert _spec and _spec.loader
sca = importlib.util.module_from_spec(_spec)
sys.modules["setup_claude_access"] = sca
_spec.loader.exec_module(sca)

STUB = """#!{python}
import json, os, sys
state = os.environ["STUB_DIR"]
args = sys.argv[1:]
stdin = "" if sys.stdin.isatty() else sys.stdin.read()
open(os.path.join(state, "calls.log"), "a").write(json.dumps({{"args": args}}) + "\\n")
if args[0] == "read":
    print(json.dumps({{"data": {{"options": {{"version": "2"}}}}}}))
elif args[:2] == ["kv", "list"]:
    print(json.dumps(["aws", "gitea", "wiki/"]))
elif args[:2] == ["policy", "write"]:
    open(os.path.join(state, "policy.hcl"), "w").write(stdin)
elif args[:2] == ["token", "create"]:
    print("s.claude-token-123")
elif args[:2] == ["audit", "list"]:
    if os.environ.get("STUB_AUDIT"):
        print("file/  file  file_path=/x")
    else:
        sys.stderr.write("No audit devices are enabled."); sys.exit(2)
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
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_DIR", str(state))
    monkeypatch.setenv("BAO_ADDR", "https://vault.example/")
    monkeypatch.setenv("BAO_TOKEN", "s.admin-root")
    monkeypatch.setenv("HOME", str(tmp_path))
    return {"state": state, "cfg": tmp_path / ".envbao", "home": tmp_path}


def run(env: dict[str, Path], *extra: str) -> int:
    return sca.main(["--config-dir", str(env["cfg"]), *extra])


def test_policy_text() -> None:
    hcl = sca.policy_text("env", 2, ["aws", "gitea"])
    assert 'path "env/data/shell/aws"' in hcl and 'path "env/data/shell/gitea"' in hcl
    assert "shell/*" not in hcl and "lookup-self" in hcl
    assert 'path "env/shell/aws"' in sca.policy_text("env", 1, ["aws"])


def test_merge_settings_idempotent_and_preserving() -> None:
    existing = {"model": "x", "permissions": {"allow": ["Bash(ls:*)"], "deny": ["Bash(bao:*)"]}}
    once = sca.merge_settings(existing, "/c/claude.toml")
    twice = sca.merge_settings(once, "/c/claude.toml")
    assert once == twice
    assert once["model"] == "x" and "Bash(ls:*)" in once["permissions"]["allow"]
    assert once["permissions"]["deny"].count("Bash(bao:*)") == 1
    assert once["env"]["ENVBAO_CONFIG"] == "/c/claude.toml"
    assert "Bash(envbao exec:*)" in once["permissions"]["allow"]
    assert existing["permissions"]["allow"] == ["Bash(ls:*)"]  # input not mutated


def test_end_to_end(env: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    assert run(env) == 0
    out = capsys.readouterr()
    text = out.out + out.err
    assert "s.claude-token-123" not in text and "s.admin-root" not in text
    assert "no OpenBao audit device" in text and "Recommended" in text
    assert "wiki/" not in (env["state"] / "policy.hcl").read_text()  # sub-folders skipped

    cfg_dir = env["cfg"]
    assert stat.S_IMODE(cfg_dir.stat().st_mode) == 0o700
    for name in ("claude.toml", "claude-token"):
        assert stat.S_IMODE((cfg_dir / name).stat().st_mode) == 0o600
    assert (cfg_dir / "claude-token").read_text() == "s.claude-token-123"

    cfg = load_config(cfg_dir / "claude.toml")
    assert cfg.exec_only and cfg.server.token == "s.claude-token-123"
    assert [q.name for q in cfg.queries] == ["aws", "gitea"]
    assert all(q.on_missing == "error" for q in cfg.queries)

    argv = " ".join(a for line in (env["state"] / "calls.log").read_text().splitlines()
                    for a in json.loads(line)["args"])  # fmt: skip
    assert "s.admin-root" not in argv and "s.claude-token" not in argv
    assert "-orphan" in argv and "-display-name=envbao-claude" in argv


def test_audit_device_present_no_warning(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("STUB_AUDIT", "1")
    assert run(env) == 0
    assert "no OpenBao audit device" not in capsys.readouterr().out


def test_explicit_groups(env: dict[str, Path]) -> None:
    assert run(env, "--groups", "gitea") == 0
    assert [q.name for q in load_config(env["cfg"] / "claude.toml").queries] == ["gitea"]


def test_refuses_to_replace_without_force(env: dict[str, Path], capsys) -> None:  # type: ignore[no-untyped-def]
    assert run(env) == 0
    assert run(env) == 1
    assert "--force" in capsys.readouterr().err
    assert run(env, "--force") == 0
    assert (env["cfg"] / "claude.toml.bak").exists()


def test_settings_merge(env: dict[str, Path]) -> None:
    settings = env["home"] / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"model": "m"}))
    assert run(env, "--settings") == 0
    data = json.loads(settings.read_text())
    assert data["model"] == "m" and data["env"]["ENVBAO_CONFIG"].endswith("claude.toml")
    assert "Read(~/.envbao/**)" in data["permissions"]["deny"]
    assert json.loads((settings.parent / "settings.json.bak").read_text()) == {"model": "m"}


def test_settings_created_when_missing(env: dict[str, Path]) -> None:
    settings = env["home"] / "s" / "settings.json"
    assert run(env, "--settings", str(settings)) == 0
    assert "permissions" in json.loads(settings.read_text())


def test_requires_bao_env(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAO_TOKEN")
    assert run(env) == 1


def test_no_groups(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(sca, "list_groups", lambda bao, mount: [])
    (env["cfg"] / "claude.toml").unlink(missing_ok=True)
    (env["cfg"] / "claude-token").unlink(missing_ok=True)
    assert run(env) == 1
    assert "no groups found" in capsys.readouterr().err
