from pathlib import Path

import pytest

from envbao.client import BaoClient, validate_address
from envbao.config import Config, Query, ServerConfig
from envbao.engines import ENGINES, get_engine
from envbao.errors import AuthError, ConfigError, NotFoundError, ServerError
from envbao.resolve import resolve, run_query, stringify, validate_queries

from .conftest import TOKEN, FakeBao


def server(fake: FakeBao, token: str = TOKEN, **kw: object) -> ServerConfig:
    return ServerConfig(address=fake.url, token=token, **kw)  # type: ignore[arg-type]


def query(**kw: object) -> Query:
    opts = {"mount": "secret", "path": "app"}
    opts.update(kw.pop("options", {}))  # type: ignore[arg-type]
    return Query(engine="kv", options=opts, **kw)  # type: ignore[arg-type]


def cfg(fake: FakeBao, *queries: Query, **kw: object) -> Config:
    return Config(server=server(fake), queries=queries, **kw)  # type: ignore[arg-type]


# ---- address policy -------------------------------------------------------------------


@pytest.mark.parametrize("addr", ["http://127.0.0.1:8200", "http://localhost", "http://[::1]:8200"])
def test_http_loopback_allowed(addr: str) -> None:
    assert validate_address(addr, allow_insecure_http=False).startswith("http://")


def test_http_remote_refused() -> None:
    with pytest.raises(ConfigError, match="plain http"):
        validate_address("http://bao.example.com", allow_insecure_http=False)


def test_http_remote_allowed_with_flag_warns(caplog: pytest.LogCaptureFixture) -> None:
    validate_address("http://bao.example.com", allow_insecure_http=True)
    assert "insecure" in caplog.text


@pytest.mark.parametrize("addr", ["ftp://x", "bao", "https://", "https://u:p@bao"])
def test_bad_addresses(addr: str) -> None:
    with pytest.raises(ConfigError):
        validate_address(addr, allow_insecure_http=True)


def test_bad_ca_cert(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="ca_cert"):
        BaoClient(ServerConfig("https://bao", "t", ca_cert=tmp_path / "missing.pem"))


def test_https_client_builds() -> None:
    BaoClient(ServerConfig("https://bao.example.com", "t"))


# ---- client ---------------------------------------------------------------------------


def test_get_sends_token_and_namespace(fake_bao: FakeBao) -> None:
    client = BaoClient(server(fake_bao, namespace="team"))
    client.get("auth/token/lookup-self")
    _, headers = fake_bao.requests[-1]
    assert headers["x-vault-token"] == TOKEN
    assert headers["x-vault-namespace"] == "team"


def test_get_with_params(fake_bao: FakeBao) -> None:
    fake_bao.routes["/v1/x?a=1"] = (200, {"ok": True})
    assert BaoClient(server(fake_bao)).get("x", {"a": "1"}) == {"ok": True}


def test_bad_token_is_auth_error_without_leak(fake_bao: FakeBao) -> None:
    with pytest.raises(AuthError) as exc:
        BaoClient(server(fake_bao, token="s.wrong")).get("auth/token/lookup-self")
    assert "s.wrong" not in str(exc.value)


def test_404(fake_bao: FakeBao) -> None:
    with pytest.raises(NotFoundError):
        BaoClient(server(fake_bao)).get("nothing")


def test_500(fake_bao: FakeBao) -> None:
    fake_bao.routes["/v1/boom"] = (500, {})
    with pytest.raises(ServerError, match="HTTP 500"):
        BaoClient(server(fake_bao)).get("boom")


def test_unreachable() -> None:
    with pytest.raises(ServerError, match="cannot reach"):
        BaoClient(ServerConfig("http://127.0.0.1:1", "t", timeout=1)).get("x")


def test_invalid_json(fake_bao: FakeBao) -> None:
    fake_bao.routes["/v1/bad"] = (200, b"not json")
    with pytest.raises(ServerError, match="not valid JSON"):
        BaoClient(server(fake_bao)).get("bad")


def test_non_object_json(fake_bao: FakeBao) -> None:
    fake_bao.routes["/v1/list"] = (200, [1, 2])
    with pytest.raises(ServerError, match="shape"):
        BaoClient(server(fake_bao)).get("list")


def test_oversized_response(fake_bao: FakeBao, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("envbao.client._MAX_RESPONSE_BYTES", 5)
    fake_bao.routes["/v1/big"] = (200, {"a": "long enough"})
    with pytest.raises(ServerError, match="size limit"):
        BaoClient(server(fake_bao)).get("big")


def test_redirects_not_followed(fake_bao: FakeBao) -> None:
    with pytest.raises(ServerError, match="HTTP 302"):
        BaoClient(server(fake_bao)).get("redirect")


# ---- kv engine ------------------------------------------------------------------------


def test_kv2_autodetect(fake_bao: FakeBao) -> None:
    fake_bao.kv2("secret", "app", {"A": "1", "B": "two"})
    env = run_query(BaoClient(server(fake_bao)), query(), cfg(fake_bao))
    assert env == {"A": "1", "B": "two"}


def test_kv1_autodetect(fake_bao: FakeBao) -> None:
    fake_bao.kv1("kv", "app", {"A": "1"})
    q = query(options={"mount": "kv"})
    assert run_query(BaoClient(server(fake_bao)), q, cfg(fake_bao)) == {"A": "1"}


def test_explicit_version_skips_detection(fake_bao: FakeBao) -> None:
    fake_bao.routes["/v1/secret/data/app"] = (200, {"data": {"data": {"A": "1"}}})
    q = query(options={"kv_version": 2})
    assert run_query(BaoClient(server(fake_bao)), q, cfg(fake_bao)) == {"A": "1"}
    assert all("sys/internal" not in p for p, _ in fake_bao.requests)


def test_detection_failure_message(fake_bao: FakeBao) -> None:
    with pytest.raises(NotFoundError, match="kv_version"):
        run_query(BaoClient(server(fake_bao)), query(on_missing="error"), cfg(fake_bao))


def test_keys_prefix_rename(fake_bao: FakeBao) -> None:
    fake_bao.kv2("secret", "app", {"DB": "x", "SKIP": "y"})
    q = query(keys=("DB",), prefix="APP_", rename={"DB": "DATABASE"})
    assert run_query(BaoClient(server(fake_bao)), q, cfg(fake_bao)) == {"APP_DATABASE": "x"}


def test_missing_key_warn_and_error(fake_bao: FakeBao, caplog: pytest.LogCaptureFixture) -> None:
    fake_bao.kv2("secret", "app", {"A": "1"})
    client = BaoClient(server(fake_bao))
    assert run_query(client, query(keys=("A", "Z")), cfg(fake_bao)) == {"A": "1"}
    assert "'Z'" in caplog.text
    with pytest.raises(NotFoundError):
        run_query(client, query(keys=("Z",), on_missing="error"), cfg(fake_bao))


def test_missing_secret_warn_and_error(fake_bao: FakeBao) -> None:
    fake_bao.routes["/v1/sys/internal/ui/mounts/secret"] = (
        200,
        {"data": {"options": {"version": "2"}}},
    )
    client = BaoClient(server(fake_bao))
    assert run_query(client, query(), cfg(fake_bao)) == {}
    with pytest.raises(NotFoundError):
        run_query(client, query(on_missing="error"), cfg(fake_bao))


def test_deleted_kv2_version_is_missing(fake_bao: FakeBao) -> None:
    fake_bao.routes["/v1/sys/internal/ui/mounts/secret"] = (
        200,
        {"data": {"options": {"version": "2"}}},
    )
    fake_bao.routes["/v1/secret/data/app"] = (200, {"data": {"data": None}})
    assert run_query(BaoClient(server(fake_bao)), query(), cfg(fake_bao)) == {}


def test_bad_data_shape(fake_bao: FakeBao) -> None:
    fake_bao.routes["/v1/secret/app"] = (200, {"data": ["x"]})
    q = query(options={"kv_version": 1})
    with pytest.raises(ServerError, match="shape"):
        run_query(BaoClient(server(fake_bao)), q, cfg(fake_bao))


def test_invalid_and_protected_names_skipped(
    fake_bao: FakeBao, caplog: pytest.LogCaptureFixture
) -> None:
    fake_bao.kv2("secret", "app", {"OK": "1", "bad-name": "2", "PATH": "/evil", "LD_PRELOAD": "x"})
    env = run_query(BaoClient(server(fake_bao)), query(), cfg(fake_bao))
    assert env == {"OK": "1"}
    assert "protected variable PATH" in caplog.text
    assert "/evil" not in caplog.text


def test_protected_can_be_allowed(fake_bao: FakeBao) -> None:
    fake_bao.kv2("secret", "app", {"PATH": "/opt/bin"})
    c = cfg(fake_bao, allow_protected=frozenset({"PATH"}))
    assert run_query(BaoClient(server(fake_bao)), query(), c) == {"PATH": "/opt/bin"}


def test_secret_values_never_logged(fake_bao: FakeBao, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    fake_bao.kv2("secret", "app", {"A": "hunter2-super-secret"})
    resolve(BaoClient(server(fake_bao)), cfg(fake_bao, query()))
    assert "hunter2" not in caplog.text
    assert TOKEN not in caplog.text


@pytest.mark.parametrize(
    "value, expected",
    [("s", "s"), (5, "5"), (1.5, "1.5"), (True, "true"), (None, "null"), ({"a": [1]}, '{"a":[1]}')],
)
def test_stringify(value: object, expected: str) -> None:
    assert stringify(value) == expected


# ---- resolve --------------------------------------------------------------------------


def test_later_queries_override(fake_bao: FakeBao, caplog: pytest.LogCaptureFixture) -> None:
    fake_bao.kv2("secret", "one", {"A": "1", "B": "1"})
    fake_bao.kv2("secret", "two", {"A": "2"})
    c = cfg(fake_bao, query(options={"path": "one"}), query(options={"path": "two"}))
    assert resolve(BaoClient(c.server), c) == {"A": "2", "B": "1"}
    assert "overridden" in caplog.text


def test_resolve_aborts_on_auth_error(fake_bao: FakeBao, caplog: pytest.LogCaptureFixture) -> None:
    c = Config(server=server(fake_bao, token="bad"), queries=(query(options={"kv_version": 2}),))
    with pytest.raises(AuthError):
        resolve(BaoClient(c.server), c)
    assert "failed" in caplog.text


# ---- engine registry / validation -----------------------------------------------------


def test_unknown_engine() -> None:
    with pytest.raises(ConfigError, match="unknown engine"):
        get_engine("approle")


def test_registry_contains_kv() -> None:
    assert "kv" in ENGINES


@pytest.mark.parametrize(
    "options, message",
    [
        ({"mount": "s"}, "'path'"),
        ({"path": "p"}, "'mount'"),
        ({"mount": "/", "path": "p"}, "'mount'"),
        ({"mount": "s", "path": "p", "kv_version": 3}, "kv_version"),
        ({"mount": "s", "path": "p", "bogus": 1}, "unknown option"),
    ],
)
def test_kv_validation(options: dict, message: str) -> None:
    c = Config(ServerConfig("https://b", "t"), (Query("kv", options),))
    with pytest.raises(ConfigError, match=message):
        validate_queries(c)


def test_validate_queries_ok() -> None:
    c = Config(ServerConfig("https://b", "t"), (query(),))
    validate_queries(c)
