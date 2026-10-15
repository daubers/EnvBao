"""Logging configuration.

Logs never contain tokens or secret values; only paths, variable names and outcomes.
Records go to stderr (so stdout stays clean for ``eval``), to ``<config dir>/envbao.log``
(mode 0600, rotated) once the directory has passed the permission check, and best-effort
to the system log for auditing.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

LOG_NAME = "envbao.log"
_FORMAT = "%(asctime)s %(levelname)s envbao[%(process)d]: %(message)s"
_SYSLOG_FORMAT = "envbao[%(process)d]: %(levelname)s %(message)s"
_SYSLOG_SOCKETS = ("/dev/log", "/var/run/syslog")


class _PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Rotating handler that always creates files with mode 0600."""

    def _open(self):  # type: ignore[no-untyped-def]
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        return os.fdopen(fd, self.mode, encoding=self.encoding)


def setup_stderr_logging(verbosity: int = 0) -> None:
    """Configure the root ``envbao`` logger with a stderr handler (and best-effort syslog)."""
    logger = logging.getLogger("envbao")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(
        logging.DEBUG if verbosity >= 2 else logging.INFO if verbosity else logging.WARNING
    )
    stderr.setFormatter(logging.Formatter("envbao: %(levelname)s: %(message)s"))
    logger.addHandler(stderr)

    for sock in _SYSLOG_SOCKETS:
        if os.path.exists(sock):
            try:
                syslog = logging.handlers.SysLogHandler(address=sock)
            except OSError:
                continue
            syslog.setLevel(logging.WARNING)
            syslog.setFormatter(logging.Formatter(_SYSLOG_FORMAT))
            logger.addHandler(syslog)
            break


def add_file_logging(directory: Path) -> None:
    """Also log INFO and above to ``directory/envbao.log``. Call only after the permission check."""
    try:
        handler = _PrivateRotatingFileHandler(
            directory / LOG_NAME, maxBytes=256 * 1024, backupCount=3, encoding="utf-8"
        )
    except OSError as exc:
        logging.getLogger("envbao").warning("cannot open log file: %s", exc.strerror)
        return
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(_FORMAT))
    logging.getLogger("envbao").addHandler(handler)
