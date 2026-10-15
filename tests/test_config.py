from pathlib import Path

import pytest

from envbao.config import load_config, parse_config, resolve_config_path
from envbao.errors import ConfigError, PermissionPolicyError

BASE = {"server": {"address": "https://bao:8200", "token": "t"}}


def test_minimal_config() -> None:
    cfg = parse_config(BASE)
    assert cfg.server.address == "https://bao:8200"
    assert cfg.server.timeout == 5.0
    assert cfg.queries == ()


def test_token_not_in_repr() -> None:
    assert "t'" not in repr(parse_config(BASE).server).replace("token", "")
    assert "token='t'" not in repr(parse_config(BASE).server)


def test_full_query() -> None:
    cfg = parse_config(
        {
            **BASE,
            "security": {"allow_protected": ["PATH"]},
            "query": [
                {
                    "mount": "secret",
                    "path": "a",
                    "keys": ["X"],
                    "prefix": "P_",
                    "rename": {"X": "Y"},
                    "on_missing": "error",
                }
            ],
        }
    )
    q = cfg.queries[0]
    assert q.engine == "kv"
    assert q.options == {"mount": "secret", "path": "a"}
    assert q.keys == ("X",)
    assert q.prefix == "P_"
    assert q.rename == {"X": "Y"}
    assert q.on_missing == "error"
    assert cfg.allow_protected == {"PATH"}
    assert "mount=secret" in q.describe()


@pytest.mark.parametrize(
    "data, message",
    [
        ({}, "missing \\[server\\]"),
        ({"bogus": 1, **BASE}, "unknown key"),
        ({"server": {"token": "t"}}, "address is required"),
        ({"server": {"address": "https://x"}}, "token is required"),
        ({"server": {"address": "x", "token": "t", "extra": 1}}, "unknown key"),
        ({"server": {"address": "x", "token": "t", "token_file": "f"}}, "only one"),
        ({"server": {"address": 5, "token": "t"}}, "must be a string"),
        ({"server": {"address": "x", "token": "t", "timeout": -1}}, "timeout"),
        ({"server": {"address": "x", "token": "t", "timeout": True}}, "timeout"),
        ({"server": {"address": "x", "token": "t", "allow_insecure_http": "yes"}}, "boolean"),
        ({"server": "nope"}, "must be a table"),
        ({**BASE, "query": {"a": 1}}, "\\[\\[query\\]\\]"),
        ({**BASE, "query": ["x"]}, "must be a table"),
        ({**BASE, "query": [{"keys": "A"}]}, "list of strings"),
        ({**BASE, "query": [{"rename": {"a": 1}}]}, "rename"),
        ({**BASE, "query": [{"on_missing": "ignore"}]}, "on_missing"),
        ({**BASE, "security": {"x": 1}}, "unknown key"),
    ],
)
def test_invalid_configs(data: dict, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_token_file(tmp_path: Path) -> None:
    tf = tmp_path / "tok"
    tf.write_text("  s.abc\n")
    tf.chmod(0o600)
    cfg = parse_config({"server": {"address": "https://b", "token_file": str(tf)}})
    assert cfg.server.token == "s.abc"


def test_token_file_permissions_enforced(tmp_path: Path) -> None:
    tf = tmp_path / "tok"
    tf.write_text("s.abc")
    tf.chmod(0o644)
    with pytest.raises(PermissionPolicyError):
        parse_config({"server": {"address": "https://b", "token_file": str(tf)}})


def test_token_file_missing(tmp_path: Path) -> None:
    with pytest.raises(PermissionPolicyError):
        parse_config({"server": {"address": "https://b", "token_file": str(tmp_path / "x")}})


def test_load_config_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text('[server]\naddress = "https://b"\ntoken = "t"\nca_cert = "~/ca.pem"\n')
    cfg = load_config(p)
    assert cfg.server.ca_cert == Path("~/ca.pem").expanduser()


def test_load_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_load_bad_toml(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text("[server\n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(p)


def test_load_unreadable(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path)  # a directory


def test_resolve_config_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_config_path(None) == tmp_path / ".envbao" / "config.toml"
    assert resolve_config_path("~/x.toml") == tmp_path / "x.toml"
