"""Command line interface.

Normal use is ``eval "$(envbao)"`` from a shell start-up file. In that mode **stdout
carries only shell statements** and is empty on any failure; diagnostics go to stderr and
the log. The process exit status tells scripts what went wrong (see ``envbao.errors``).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from envbao.client import BaoClient
from envbao.config import Config, load_config, resolve_config_path
from envbao.errors import EXIT_FAILURE, EXIT_OK, ConfigError, EnvBaoError, PermissionPolicyError
from envbao.logging_setup import add_file_logging, setup_stderr_logging
from envbao.render import SHELLS, detect_shell, render
from envbao.resolve import resolve, validate_queries
from envbao.runner import fetch_secrets, run_command, select_queries
from envbao.security import check_config_dir
from envbao.version import get_version

log = logging.getLogger("envbao")

CONFIG_TEMPLATE = """\
# EnvBao configuration. This file and its directory must be accessible by you only.
[server]
address = "https://bao.example.com:8200"
token = ""            # or: token_file = "~/.envbao/token" (mode 0600)
# namespace = ""
# ca_cert = "/path/to/ca.pem"
# timeout = 5

# One [[query]] table per secret. Later queries override earlier ones.
[[query]]
engine = "kv"
mount = "secret"
path = "myapp/prod"
# keys = ["DB_URL"]
# prefix = "MYAPP_"
# rename = { DB_URL = "DATABASE_URL" }
# on_missing = "warn"   # or "error"
"""


class _VersionAction(argparse.Action):
    """``--version`` that resolves the version lazily (git is never run otherwise)."""

    def __init__(self, option_strings: Sequence[str], dest: str, **kwargs: object) -> None:
        super().__init__(option_strings, dest, nargs=0, help="show version and exit")

    def __call__(self, parser: argparse.ArgumentParser, *args: object, **kwargs: object) -> None:
        sys.stdout.write(f"envbao {get_version()}\n")
        parser.exit()


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="envbao",
        description="Print shell export statements for secrets stored in OpenBao. "
        'Use as: eval "$(envbao)"',
    )
    parser.add_argument(
        "--config", metavar="PATH", help="config file (default: ~/.envbao/config.toml)"
    )
    parser.add_argument(
        "--shell",
        choices=("auto", *SHELLS),
        default="auto",
        help="output syntax (default: detect from $SHELL)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="verify permissions, config and token, then exit without printing variables",
    )
    mode.add_argument(
        "--init",
        action="store_true",
        help="create ~/.envbao with safe permissions and a template config.toml",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0, help="log more to stderr")
    parser.add_argument("--version", action=_VersionAction)

    sub = parser.add_subparsers(dest="command", metavar="{exec}")
    run_p = sub.add_parser(
        "exec",
        help="run a command with selected secret groups in its environment",
        description="Fetch the named groups and run COMMAND with them as environment variables. "
        "Output is filtered so secret values are shown as ***.",
        usage="envbao [options] exec -q GROUP [-q GROUP ...] [--no-redact] -- COMMAND [ARGS ...]",
    )
    run_p.add_argument(
        "-q",
        "--query",
        action="append",
        default=[],
        metavar="GROUP",
        help="group name (repeatable)",
    )
    run_p.add_argument(
        "--no-redact",
        action="store_true",
        help="replace this process with COMMAND (no output filtering; for interactive use)",
    )
    run_p.add_argument("argv", nargs=argparse.REMAINDER, metavar="-- COMMAND [ARGS ...]")
    return parser


def init_config(path: Path) -> None:
    """Create the config directory (0700) and a template config (0600) at ``path``."""
    directory = path.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(CONFIG_TEMPLATE)


def _check(config: Config, client: BaoClient) -> None:
    info = client.get("auth/token/lookup-self").get("data", {})
    ttl = info.get("ttl")
    sys.stderr.write(
        f"envbao: OK - permissions safe, {len(config.queries)} query(ies) valid, "
        f"token accepted{f' (ttl {ttl}s)' if ttl is not None else ''}\n"
    )


def run(args: argparse.Namespace) -> int:
    """Execute the requested action and return the exit status."""
    path = resolve_config_path(args.config)

    if args.command == "exec" and (args.check or args.init):
        raise ConfigError("--check/--init cannot be combined with exec")
    if args.init:
        try:
            init_config(path)
        except FileExistsError:
            sys.stderr.write(f"envbao: {path} already exists; not overwriting\n")
            return EXIT_FAILURE
        sys.stderr.write(f"envbao: created {path}; edit it to add your server and token\n")
        return EXIT_OK

    # Permissions first: nothing in the directory is read or logged to until it is safe.
    check_config_dir(path.parent)
    add_file_logging(path.parent)

    config = load_config(path)
    validate_queries(config)
    client = BaoClient(config.server)

    if args.check:
        _check(config, client)
        return EXIT_OK

    if args.command == "exec":
        argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        queries = select_queries(config, args.query)
        secrets = fetch_secrets(client, config, queries)
        log.info("exec: groups %s", ", ".join(q.name for q in queries))
        return run_command(argv, secrets, redact=not args.no_redact)

    env = resolve(client, config)
    shell = detect_shell() if args.shell == "auto" else args.shell
    output = render(env, shell)
    log.info("exported %d variable(s) for %s", len(env), shell)
    sys.stdout.write(output)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Program entry point; returns the process exit status."""
    args = build_parser().parse_args(argv)
    setup_stderr_logging(args.verbose)
    try:
        return run(args)
    except PermissionPolicyError as exc:
        return exc.exit_code  # already logged at CRITICAL by the security check
    except EnvBaoError as exc:
        log.error("%s", exc)
        return exc.exit_code
    except Exception as exc:  # last resort: never dump a traceback that could hold secrets
        log.critical("unexpected %s", type(exc).__name__)
        log.debug("details", exc_info=True)
        return EXIT_FAILURE
