"""Secret engines.

An engine turns the engine-specific options of a ``[[query]]`` into a flat mapping of
names to values. Filtering (``keys``), ``prefix`` and ``rename`` are applied centrally in
:mod:`envbao.resolve`, so adding a new credential engine only requires a new module that
implements :class:`Engine` and an entry in :data:`ENGINES`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from envbao.client import BaoClient
from envbao.engines import kv
from envbao.errors import ConfigError


class Engine(Protocol):
    """Interface every secret engine implements."""

    def validate(self, options: Mapping[str, Any]) -> None:
        """Raise :class:`~envbao.errors.ConfigError` if ``options`` are invalid."""

    def fetch(self, client: BaoClient, options: Mapping[str, Any]) -> dict[str, Any]:
        """Return the raw name -> value mapping.

        Raises:
            NotFoundError: if the secret does not exist.
        """


ENGINES: dict[str, Engine] = {"kv": kv.KvEngine()}


def get_engine(name: str) -> Engine:
    """Look up an engine by name, raising :class:`ConfigError` if unknown."""
    try:
        return ENGINES[name]
    except KeyError:
        known = ", ".join(sorted(ENGINES))
        raise ConfigError(f"unknown engine {name!r} (available: {known})") from None
