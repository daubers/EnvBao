"""Run the configured queries and build the final environment mapping."""

from __future__ import annotations

import json
import logging
from typing import Any

from envbao.client import BaoClient
from envbao.config import Config, Query
from envbao.engines import get_engine
from envbao.errors import ConfigError, EnvBaoError, NotFoundError
from envbao.render import is_protected, is_valid_name

log = logging.getLogger(__name__)


def stringify(value: Any) -> str:
    """Convert a secret value to the string placed in the environment."""
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"))


def validate_queries(config: Config) -> None:
    """Validate engine-specific options of every query without contacting the server."""
    for index, query in enumerate(config.queries, start=1):
        try:
            get_engine(query.engine).validate(query.options)
        except ConfigError as exc:
            raise ConfigError(f"query #{index}: {exc}") from None


def _select(raw: dict[str, Any], query: Query) -> dict[str, Any]:
    if query.keys is None:
        return raw
    missing = [k for k in query.keys if k not in raw]
    for key in missing:
        log.warning("key %r not present in %s", key, query.describe())
    if missing and query.on_missing == "error":
        raise NotFoundError(f"required key(s) missing from {query.describe()}")
    return {k: raw[k] for k in query.keys if k in raw}


def run_query(client: BaoClient, query: Query, config: Config) -> dict[str, str]:
    """Execute one query and return ``{ENV_NAME: value}``.

    Names that are not valid variable names, or that are protected shell/loader variables
    (unless allowed in ``[security] allow_protected``), are skipped and logged.
    """
    engine = get_engine(query.engine)
    try:
        raw = engine.fetch(client, query.options)
    except NotFoundError as exc:
        if query.on_missing == "error":
            raise
        log.warning("%s: %s", query.describe(), exc)
        return {}

    result: dict[str, str] = {}
    for key, value in _select(raw, query).items():
        name = query.rename.get(key, key)
        name = f"{query.prefix}{name}"
        if not is_valid_name(name):
            log.warning("skipping %r from %s: not a valid variable name", name, query.describe())
            continue
        if is_protected(name) and name not in config.allow_protected:
            log.error(
                "refusing to set protected variable %s from %s "
                "(add it to [security] allow_protected to permit)",
                name,
                query.describe(),
            )
            continue
        result[name] = stringify(value)
    log.info("%s: %d variable(s)", query.describe(), len(result))
    return result


def resolve(client: BaoClient, config: Config) -> dict[str, str]:
    """Run all queries in order (later queries override earlier ones).

    All-or-nothing: any non-recoverable error aborts and nothing is returned.

    Raises:
        EnvBaoError: on the first fatal failure.
    """
    env: dict[str, str] = {}
    for query in config.queries:
        try:
            values = run_query(client, query, config)
        except EnvBaoError as exc:
            log.error("query %s failed: %s", query.describe(), exc)
            raise
        for name in values:
            if name in env:
                log.warning("%s overridden by %s", name, query.describe())
        env.update(values)
    return env
