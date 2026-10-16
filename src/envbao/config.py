"""Loading and validation of ``config.toml``.

The parser is deliberately strict: unknown keys and wrongly typed values are rejected so
that typos (for example in a security-relevant option) never silently fall back to a
default.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from envbao.errors import ConfigError
from envbao.security import check_path

log = logging.getLogger(__name__)

DEFAULT_DIR = Path("~/.envbao")
CONFIG_NAME = "config.toml"
DEFAULT_TIMEOUT = 5.0
ON_MISSING_CHOICES = ("warn", "error")

_SERVER_KEYS = {
    "address",
    "token",
    "token_file",
    "namespace",
    "ca_cert",
    "timeout",
    "allow_insecure_http",
}
_SECURITY_KEYS = {"allow_protected"}
_TOP_KEYS = {"server", "security", "query"}
_QUERY_COMMON_KEYS = {"engine", "name", "keys", "prefix", "rename", "on_missing"}
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
CONFIG_ENV_VAR = "ENVBAO_CONFIG"


@dataclass(frozen=True)
class ServerConfig:
    """Connection settings for the OpenBao server."""

    address: str
    token: str = field(repr=False)  # never show the token in reprs/tracebacks
    namespace: str | None = None
    ca_cert: Path | None = None
    timeout: float = DEFAULT_TIMEOUT
    allow_insecure_http: bool = False


@dataclass(frozen=True)
class Query:
    """One secret source and how its keys map to environment variables.

    ``options`` holds the engine-specific settings (e.g. ``mount``/``path`` for ``kv``);
    they are validated by the engine itself.
    """

    engine: str
    options: Mapping[str, Any]
    keys: tuple[str, ...] | None = None
    prefix: str = ""
    rename: Mapping[str, str] = field(default_factory=dict)
    on_missing: str = "warn"
    name: str = ""

    def describe(self) -> str:
        """Short, secret-free description of this query for logs."""
        opts = " ".join(f"{k}={v}" for k, v in self.options.items())
        return f"{self.engine}({opts})"


@dataclass(frozen=True)
class Config:
    """Fully validated configuration."""

    server: ServerConfig
    queries: tuple[Query, ...]
    allow_protected: frozenset[str] = frozenset()


def resolve_config_path(explicit: str | None) -> Path:
    """Return the config path: ``explicit``, ``$ENVBAO_CONFIG`` or ``~/.envbao/config.toml``."""
    chosen = explicit or os.environ.get(CONFIG_ENV_VAR)
    if chosen:
        return Path(chosen).expanduser()
    return DEFAULT_DIR.expanduser() / CONFIG_NAME


def _require_table(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def _reject_unknown(table: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(f"unknown key(s) in {name}: {', '.join(unknown)}")


def _get_str(table: Mapping[str, Any], key: str, name: str) -> str | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{name}.{key} must be a string")
    return value


def _get_str_list(table: Mapping[str, Any], key: str, name: str) -> list[str] | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{name}.{key} must be a list of strings")
    return list(value)


def _read_token_file(path_str: str) -> str:
    path = Path(path_str).expanduser()
    # The token file may live outside ~/.envbao, so it is checked on its own as well.
    check_path(path, expect_dir=False)
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read token_file {path}: {exc}") from None


def _parse_server(raw: Mapping[str, Any]) -> ServerConfig:
    table = _require_table(raw, "server")
    _reject_unknown(table, _SERVER_KEYS, "[server]")

    address = _get_str(table, "address", "server")
    if not address:
        raise ConfigError("server.address is required")

    token = _get_str(table, "token", "server")
    token_file = _get_str(table, "token_file", "server")
    if token and token_file:
        raise ConfigError("set only one of server.token and server.token_file")
    if token_file:
        token = _read_token_file(token_file)
    if not token:
        raise ConfigError("a token is required: set server.token or server.token_file")

    timeout = table.get("timeout", DEFAULT_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, int | float) or timeout <= 0:
        raise ConfigError("server.timeout must be a positive number")

    allow_http = table.get("allow_insecure_http", False)
    if not isinstance(allow_http, bool):
        raise ConfigError("server.allow_insecure_http must be a boolean")

    ca_cert = _get_str(table, "ca_cert", "server")
    return ServerConfig(
        address=address,
        token=token,
        namespace=_get_str(table, "namespace", "server") or None,
        ca_cert=Path(ca_cert).expanduser() if ca_cert else None,
        timeout=float(timeout),
        allow_insecure_http=allow_http,
    )


def _default_name(engine: str, options: Mapping[str, Any]) -> str:
    """Name used by ``envbao exec -q``: last path segment (e.g. ``shell/gitea`` -> ``gitea``)."""
    path = options.get("path")
    if isinstance(path, str) and path.strip("/"):
        return path.strip("/").rsplit("/", 1)[-1]
    return engine


def _parse_query(raw: Any, index: int) -> Query:
    name_label = f"query #{index + 1}"
    if not isinstance(raw, dict):
        raise ConfigError(f"{name_label} must be a table")

    engine = _get_str(raw, "engine", name_label) or "kv"
    keys = _get_str_list(raw, "keys", name_label)
    prefix = _get_str(raw, "prefix", name_label) or ""

    rename = raw.get("rename", {})
    if not isinstance(rename, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in rename.items()
    ):
        raise ConfigError(f"{name_label}.rename must be a table of string to string")

    on_missing = _get_str(raw, "on_missing", name_label) or "warn"
    if on_missing not in ON_MISSING_CHOICES:
        raise ConfigError(f"{name_label}.on_missing must be one of {', '.join(ON_MISSING_CHOICES)}")

    options = {k: v for k, v in raw.items() if k not in _QUERY_COMMON_KEYS}
    name = _get_str(raw, "name", name_label) or _default_name(engine, options)
    if not _NAME_RE.match(name):
        raise ConfigError(f"{name_label}.name {name!r} may only contain letters, digits, . _ -")
    return Query(
        engine=engine,
        options=options,
        keys=tuple(keys) if keys is not None else None,
        prefix=prefix,
        rename=dict(rename),
        on_missing=on_missing,
        name=name,
    )


def parse_config(data: Mapping[str, Any]) -> Config:
    """Validate parsed TOML ``data`` and build a :class:`Config`."""
    _reject_unknown(data, _TOP_KEYS, "config file")
    if "server" not in data:
        raise ConfigError("missing [server] table")
    server = _parse_server(data["server"])

    queries_raw = data.get("query", [])
    if not isinstance(queries_raw, list):
        raise ConfigError("queries must be declared with [[query]] tables")
    queries = tuple(_parse_query(q, i) for i, q in enumerate(queries_raw))

    security = _require_table(data.get("security", {}), "security")
    _reject_unknown(security, _SECURITY_KEYS, "[security]")
    allow_protected = _get_str_list(security, "allow_protected", "security") or []

    return Config(server=server, queries=queries, allow_protected=frozenset(allow_protected))


def load_config(path: Path) -> Config:
    """Read and validate the config file at ``path``.

    The caller is responsible for having checked file permissions first.

    Raises:
        ConfigError: if the file is missing, unparsable or invalid.
    """
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from None
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror}") from None
    return parse_config(data)
