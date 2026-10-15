"""Key/Value secrets engine (versions 1 and 2) with automatic version detection."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from envbao.client import BaoClient
from envbao.errors import ConfigError, EnvBaoError, NotFoundError, ServerError

log = logging.getLogger(__name__)

_OPTIONS = {"mount", "path", "kv_version"}


class KvEngine:
    """Reads one secret from a KV v1 or v2 mount.

    Options: ``mount`` (required), ``path`` (required), ``kv_version`` (``1``, ``2`` or
    ``"auto"``, default ``"auto"``).
    """

    def validate(self, options: Mapping[str, Any]) -> None:
        unknown = sorted(set(options) - _OPTIONS)
        if unknown:
            raise ConfigError(f"unknown option(s) for kv engine: {', '.join(unknown)}")
        for key in ("mount", "path"):
            value = options.get(key)
            if not isinstance(value, str) or not value.strip("/"):
                raise ConfigError(f"kv query requires a non-empty string '{key}'")
        version = options.get("kv_version", "auto")
        if version not in (1, 2, "auto"):
            raise ConfigError('kv_version must be 1, 2 or "auto"')

    def fetch(self, client: BaoClient, options: Mapping[str, Any]) -> dict[str, Any]:
        mount = str(options["mount"]).strip("/")
        path = str(options["path"]).strip("/")
        version = options.get("kv_version", "auto")
        if version == "auto":
            version = detect_version(client, mount)

        if version == 2:
            body = client.get(f"{mount}/data/{path}")
            data = body.get("data", {}).get("data")
        else:
            body = client.get(f"{mount}/{path}")
            data = body.get("data")

        if data is None:  # e.g. a soft-deleted KV v2 version
            raise NotFoundError(f"no data at {mount}/{path}")
        if not isinstance(data, dict):
            raise ServerError(f"unexpected data shape at {mount}/{path}")
        return data


def detect_version(client: BaoClient, mount: str) -> int:
    """Return the KV version (1 or 2) of ``mount`` via ``sys/internal/ui/mounts``."""
    try:
        body = client.get(f"sys/internal/ui/mounts/{mount}")
    except EnvBaoError as exc:
        raise type(exc)(
            f"could not detect KV version of mount '{mount}' ({exc}); "
            "set kv_version = 1 or 2 on the query to skip detection"
        ) from None
    options = body.get("data", {}).get("options") or {}
    version = str(options.get("version", "1")) or "1"
    log.debug("mount %s detected as KV v%s", mount, version)
    return 2 if version == "2" else 1
