"""Run a command with secrets injected into its environment (``envbao exec``).

The command receives the selected secrets as environment variables. By default its
stdout/stderr are filtered so that the secret values (raw, JSON-escaped and URL-encoded
forms) are replaced with ``***`` before they reach the caller - for example an AI tool's
transcript. This is defence in depth, not a sandbox: a command can still transform a
secret (base64, hashing, splitting) into a form the filter does not recognise.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import signal
import subprocess  # noqa: S404
import sys
import threading
from collections.abc import Iterable, Mapping, Sequence
from types import FrameType
from typing import Any, BinaryIO
from urllib.parse import quote

from envbao.client import BaoClient
from envbao.config import Config, Query
from envbao.errors import ConfigError, NotFoundError
from envbao.resolve import run_query

log = logging.getLogger("envbao")

MASK = b"***"
MIN_SECRET_LEN = 6
EXIT_NOT_EXECUTABLE = 126
EXIT_NOT_FOUND = 127
_CHUNK = 65536


def select_queries(config: Config, names: Sequence[str]) -> list[Query]:
    """Return the queries called ``names`` (in the order given).

    Raises:
        ConfigError: for an unknown or ambiguous name, or an empty selection.
    """
    if not names:
        raise ConfigError("select at least one group with -q/--query")
    selected: list[Query] = []
    available = sorted({q.name for q in config.queries})
    for name in dict.fromkeys(names):
        matches = [q for q in config.queries if q.name == name]
        if not matches:
            raise ConfigError(
                f"unknown group {name!r} (available: {', '.join(available) or 'none'})"
            )
        if len(matches) > 1:
            raise ConfigError(f"group name {name!r} is ambiguous; set a unique 'name' on the query")
        selected.append(matches[0])
    return selected


def fetch_secrets(client: BaoClient, config: Config, queries: Iterable[Query]) -> dict[str, str]:
    """Fetch the given queries. Fails closed: a missing/empty group is an error."""
    env: dict[str, str] = {}
    for query in queries:
        values = run_query(client, query, config)
        if not values:
            raise NotFoundError(f"group {query.name!r} produced no variables")
        env.update(values)
    return env


def _variants(value: str) -> set[bytes]:
    forms = {value, json.dumps(value)[1:-1], quote(value, safe=""), quote(value)}
    return {f.encode() for f in forms if f}


class Redactor:
    """Streaming replacement of secret values, safe across chunk boundaries."""

    def __init__(self, secrets: Iterable[str]) -> None:
        needles: set[bytes] = set()
        for secret in secrets:
            if len(secret) >= MIN_SECRET_LEN:
                needles |= _variants(secret)
        ordered = sorted(needles, key=len, reverse=True)
        self._pattern = re.compile(b"|".join(re.escape(n) for n in ordered)) if ordered else None
        self._hold = max((len(n) for n in ordered), default=1) - 1
        self._tail = b""

    def feed(self, data: bytes) -> bytes:
        """Consume ``data``; return the bytes that are safe to emit now."""
        if self._pattern is None:
            return data
        buf = self._pattern.sub(MASK, self._tail + data)
        cut = max(len(buf) - self._hold, 0)
        self._tail = buf[cut:]
        return buf[:cut]

    def flush(self) -> bytes:
        """Return whatever is still held back (call at end of stream)."""
        if self._pattern is None:
            return b""
        out = self._pattern.sub(MASK, self._tail)
        self._tail = b""
        return out


def _pump(src: BinaryIO, dst: BinaryIO, redactor: Redactor) -> None:
    fd = src.fileno()
    while chunk := os.read(fd, _CHUNK):
        out = redactor.feed(chunk)
        if out:
            dst.write(out)
            dst.flush()
    tail = redactor.flush()
    if tail:
        dst.write(tail)
        dst.flush()


def run_command(
    argv: Sequence[str],
    secrets: Mapping[str, str],
    *,
    redact: bool = True,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Run ``argv`` with ``secrets`` added to its environment and return its exit status."""
    if not argv:
        raise ConfigError("no command given; usage: envbao exec -q GROUP -- COMMAND [ARGS...]")
    env = {**(os.environ if environ is None else environ), **secrets}
    log.info("exec: %d variable(s) for command %s", len(secrets), os.path.basename(argv[0]))

    if not redact:
        for handler in log.handlers:  # exec discards buffered output
            with contextlib.suppress(OSError, ValueError):
                handler.flush()
        try:
            os.execvpe(argv[0], list(argv), env)  # noqa: S606 - replaces this process
        except FileNotFoundError:
            return EXIT_NOT_FOUND
        except PermissionError:
            return EXIT_NOT_EXECUTABLE

    try:
        proc = subprocess.Popen(  # noqa: S603
            list(argv), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except FileNotFoundError:
        sys.stderr.write(f"envbao: command not found: {argv[0]}\n")
        return EXIT_NOT_FOUND
    except PermissionError:
        sys.stderr.write(f"envbao: permission denied: {argv[0]}\n")
        return EXIT_NOT_EXECUTABLE

    def forward(signum: int, _frame: FrameType | None) -> None:
        if proc.poll() is None:
            proc.send_signal(signum)

    old: dict[int, Any] = {s: signal.signal(s, forward) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        assert proc.stdout is not None and proc.stderr is not None  # noqa: S101
        values = list(secrets.values())
        threads = [
            threading.Thread(
                target=_pump, args=(proc.stdout, sys.stdout.buffer, Redactor(values)), daemon=True
            ),
            threading.Thread(
                target=_pump, args=(proc.stderr, sys.stderr.buffer, Redactor(values)), daemon=True
            ),
        ]
        for t in threads:
            t.start()
        code = proc.wait()
        for t in threads:
            t.join()
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
    return 128 - code if code < 0 else code
