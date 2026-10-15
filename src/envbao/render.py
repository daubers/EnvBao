"""Rendering of environment variables as shell statements.

Variable names are validated against a strict pattern and values are always quoted, so
that a secret value can never be interpreted as shell syntax by ``eval``.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping

from envbao.errors import ConfigError

SHELLS = ("bash", "zsh", "sh", "fish")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

#: Variables that change how the shell or dynamic loader behaves. A compromised or
#: misconfigured secret store must not be able to set these, so they are refused unless
#: listed in ``[security] allow_protected``.
PROTECTED = frozenset(
    {
        "PATH", "IFS", "SHELL", "HOME", "USER", "LOGNAME", "ENV", "BASH_ENV", "CDPATH",
        "PROMPT_COMMAND", "PS1", "PS2", "PS4", "SHELLOPTS", "BASHOPTS", "FPATH", "ZDOTDIR",
        "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "PYTHONPATH", "PYTHONSTARTUP",
        "PERL5OPT", "NODE_OPTIONS", "RUBYOPT",
    }
)  # fmt: skip
_PROTECTED_PREFIXES = ("DYLD_", "BASH_FUNC_", "LD_")


def is_valid_name(name: str) -> bool:
    """Return True if ``name`` is a legal POSIX environment variable name."""
    return bool(_NAME_RE.match(name))


def is_protected(name: str) -> bool:
    """Return True if ``name`` is a shell/loader control variable."""
    return name in PROTECTED or name.startswith(_PROTECTED_PREFIXES)


def detect_shell(environ: Mapping[str, str] | None = None) -> str:
    """Guess the output dialect from ``$SHELL``; defaults to ``sh`` syntax."""
    shell = os.path.basename((environ or os.environ).get("SHELL", ""))
    return shell if shell in SHELLS else "sh"


def _fish_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def render(env: Mapping[str, str], shell: str = "sh") -> str:
    """Return one export statement per variable, newline separated.

    Raises:
        ConfigError: for an invalid name, a value containing NUL, or an unknown shell.
    """
    if shell not in SHELLS:
        raise ConfigError(f"unsupported shell {shell!r}")
    lines: list[str] = []
    for name, value in env.items():
        if not is_valid_name(name):
            raise ConfigError(f"invalid environment variable name {name!r}")
        if "\x00" in value:
            raise ConfigError(f"value for {name} contains a NUL byte")
        if shell == "fish":
            lines.append(f"set -gx {name} {_fish_quote(value)}")
        else:
            lines.append(f"export {name}={shlex.quote(value)}")
    return "\n".join(lines) + ("\n" if lines else "")
