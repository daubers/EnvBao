"""Exception hierarchy and process exit codes.

Every error EnvBao raises deliberately derives from :class:`EnvBaoError` and carries the
exit code the CLI should terminate with. Messages must never contain secrets (tokens or
secret values).
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2  # argparse default
EXIT_UNAVAILABLE = 69  # server unreachable / unexpected server response (EX_UNAVAILABLE)
EXIT_AUTH = 77  # token rejected (EX_NOPERM)
EXIT_CONFIG = 78  # bad config or unsafe file permissions (EX_CONFIG)


class EnvBaoError(Exception):
    """Base class for all expected EnvBao failures."""

    exit_code = EXIT_FAILURE


class ConfigError(EnvBaoError):
    """The configuration file is missing, malformed or invalid."""

    exit_code = EXIT_CONFIG


class PermissionPolicyError(EnvBaoError):
    """A file or directory under the EnvBao config directory has unsafe permissions."""

    exit_code = EXIT_CONFIG


class ServerError(EnvBaoError):
    """The OpenBao server could not be reached or returned an unexpected response."""

    exit_code = EXIT_UNAVAILABLE


class AuthError(ServerError):
    """The OpenBao server rejected the token (HTTP 401/403)."""

    exit_code = EXIT_AUTH


class NotFoundError(ServerError):
    """The requested path does not exist (HTTP 404)."""
