"""Minimal OpenBao HTTP client built on the standard library.

Security properties:

* TLS certificate and hostname verification are always on (optionally against a custom CA).
* ``http://`` is only accepted for loopback addresses unless ``allow_insecure_http`` is set.
* Redirects are never followed, so the token cannot be forwarded to another host.
* Error messages contain the HTTP status and request path only, never the token or bodies.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import ssl
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode, urlsplit

from envbao.config import ServerConfig
from envbao.errors import AuthError, ConfigError, NotFoundError, ServerError

log = logging.getLogger(__name__)

_API_PREFIX = "/v1/"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_address(address: str, *, allow_insecure_http: bool) -> str:
    """Return the normalised base URL, enforcing the transport-security policy.

    Raises:
        ConfigError: for unsupported schemes or plain HTTP to a non-loopback host.
    """
    parts = urlsplit(address)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError("server.address must be an http(s) URL such as https://bao:8200")
    if parts.username or parts.password:
        raise ConfigError("server.address must not contain credentials")
    if parts.scheme == "http" and not _is_loopback(parts.hostname):
        if not allow_insecure_http:
            raise ConfigError(
                "refusing plain http:// to a non-loopback host; use https or set "
                "server.allow_insecure_http = true"
            )
        log.warning(
            "using insecure http:// transport to %s; the token is sent in clear", parts.netloc
        )
    return f"{parts.scheme}://{parts.netloc}"


class BaoClient:
    """Authenticated read-only access to the OpenBao HTTP API."""

    def __init__(self, server: ServerConfig) -> None:
        self._base = validate_address(
            server.address, allow_insecure_http=server.allow_insecure_http
        )
        self._token = server.token
        self._namespace = server.namespace
        self._timeout = server.timeout
        handlers: list[urllib.request.BaseHandler] = [_NoRedirect()]
        if self._base.startswith("https://"):
            try:
                ctx = ssl.create_default_context(
                    cafile=str(server.ca_cert) if server.ca_cert else None
                )
            except (OSError, ssl.SSLError) as exc:
                raise ConfigError(f"cannot load server.ca_cert: {exc}") from None
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        self._opener = urllib.request.build_opener(*handlers)

    def get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """GET ``/v1/<path>`` and return the decoded JSON object.

        Raises:
            AuthError: HTTP 401/403.
            NotFoundError: HTTP 404.
            ServerError: any other failure (network, TLS, 5xx, malformed response).
        """
        url = f"{self._base}{_API_PREFIX}{path.lstrip('/')}"
        if params:
            url += "?" + urlencode(params)
        headers = {"X-Vault-Token": self._token, "Accept": "application/json"}
        if self._namespace:
            headers["X-Vault-Namespace"] = self._namespace
        request = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310

        shown = f"/{_API_PREFIX.strip('/')}/{path.lstrip('/')}"
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                body = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code in (401, 403):
                raise AuthError(f"access denied (HTTP {exc.code}) for {shown}") from None
            if exc.code == 404:
                raise NotFoundError(f"not found: {shown}") from None
            raise ServerError(f"server returned HTTP {exc.code} for {shown}") from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ServerError(f"cannot reach {self._base}: {reason}") from None

        if len(body) > _MAX_RESPONSE_BYTES:
            raise ServerError(f"response for {shown} exceeds size limit")
        try:
            decoded = json.loads(body)
        except ValueError:
            raise ServerError(f"response for {shown} is not valid JSON") from None
        if not isinstance(decoded, dict):
            raise ServerError(f"unexpected response shape for {shown}")
        return decoded
