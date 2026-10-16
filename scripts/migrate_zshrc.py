"""One-off migration of ``export NAME=value`` secrets from ~/.zshrc into OpenBao.

Run without arguments for a dry run that lists variable *names* and line numbers only.
With ``--apply`` it will, per service group:

1. write the variables to ``<mount>/shell/<group>`` via the ``bao`` CLI (JSON on stdin),
2. read them back and compare SHA-256 digests,

and, once everything verified, create a read-only policy and token for EnvBao and write
``~/.envbao/config.toml`` plus ``~/.envbao/token`` (modes 0600; directory 0700).

``~/.zshrc`` is parsed as text and never sourced. Secret values are never printed,
logged or passed on a command line; they only travel over stdin/stdout pipes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess  # noqa: S404
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MOUNT = "env"
SECRET_PREFIX = "shell"  # noqa: S105 - a path, not a credential
POLICY_NAME = "envbao-shell"
TOKEN_PERIOD = "720h"  # noqa: S105 - a duration

#: Shell/toolchain configuration that belongs in the shell startup file, not in OpenBao.
EXCLUDED = frozenset(
    {
        "PATH", "ZSH", "ZSH_THEME", "LDFLAGS", "CPPFLAGS", "NVM_DIR", "BAO_TOKEN", "BAO_ADDR",
    }
)  # fmt: skip

#: Longest prefixes first: ``PROTON_BRIDGE_`` must win over any shorter match.
GROUP_PREFIXES: tuple[tuple[str, str], ...] = (
    ("PROTON_BRIDGE_", "proton"),
    ("YOUTRACK_", "youtrack"),
    ("ELASTIC_", "elastic"),
    ("PHOTOS_", "photos"),
    ("GITHUB_", "github"),
    ("GITEA_", "gitea"),
    ("PKGS_", "pkgs"),
    ("WIKI_", "wiki"),
    ("AWS_", "aws"),
)

_EXPORT_RE = re.compile(r"^export[ \t]+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_GROUP_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class MigrationError(Exception):
    """A fatal, user-presentable problem. Messages never contain secret values."""


@dataclass(frozen=True)
class Entry:
    """A literal assignment found in the shell file."""

    name: str
    value: str = ""
    lines: tuple[int, ...] = ()

    def __repr__(self) -> str:  # keep values out of tracebacks and logs
        return f"Entry(name={self.name!r}, lines={self.lines})"


@dataclass(frozen=True)
class ParseResult:
    """Outcome of scanning a shell file."""

    entries: dict[str, Entry]
    skipped_dynamic: dict[str, int]  # name -> line (value needs shell expansion)


# ---------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------


def _parse_value(raw: str) -> str | None:
    """Return the literal value of the right-hand side, or None if it is not a plain literal."""
    raw = raw.strip()
    if raw[:1] == "'":
        end = raw.find("'", 1)
        if end == -1:
            return None
        rest = raw[end + 1 :].strip()
        return raw[1:end] if not rest or rest.startswith("#") else None
    if raw[:1] == '"':
        out: list[str] = []
        i = 1
        while i < len(raw):
            ch = raw[i]
            if ch == "\\" and i + 1 < len(raw) and raw[i + 1] in '"\\$`':
                out.append(raw[i + 1])
                i += 2
                continue
            if ch in "$`":
                return None
            if ch == '"':
                rest = raw[i + 1 :].strip()
                return "".join(out) if not rest or rest.startswith("#") else None
            out.append(ch)
            i += 1
        return None
    # bare word: ends at whitespace; anything needing expansion/quoting is not a literal
    match = re.match(r"^(\S*)(?:\s+#.*|\s*)$", raw)
    if not match:
        return None
    word = match.group(1)
    if re.search(r"[$`\\'\"(){}*?;&|<>~]", word):
        return None
    return word


def parse_zshrc(text: str) -> ParseResult:
    """Extract top-level literal ``export NAME=value`` assignments from ``text``.

    Later assignments override earlier ones; all line numbers are retained so the
    caller can report every line that may be deleted.
    """
    entries: dict[str, Entry] = {}
    dynamic: dict[str, int] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        match = _EXPORT_RE.match(line)
        if not match:
            continue
        name, raw = match.groups()
        value = _parse_value(raw)
        if value is None:
            dynamic[name] = lineno
            continue
        previous = entries.get(name)
        lines = (*previous.lines, lineno) if previous else (lineno,)
        entries[name] = Entry(name, value, lines)
        dynamic.pop(name, None)
    return ParseResult(entries, dynamic)


def classify(
    entries: Mapping[str, Entry], extra: Mapping[str, str] | None = None
) -> tuple[dict[str, dict[str, Entry]], list[str]]:
    """Group entries by service. Returns ``(groups, unmapped_names)``."""
    extra = extra or {}
    groups: dict[str, dict[str, Entry]] = {}
    unmapped: list[str] = []
    for name, entry in entries.items():
        if name in EXCLUDED:
            continue
        group = extra.get(name) or next((g for p, g in GROUP_PREFIXES if name.startswith(p)), None)
        if group is None:
            unmapped.append(name)
            continue
        groups.setdefault(group, {})[name] = entry
    return dict(sorted(groups.items())), sorted(unmapped)


# ---------------------------------------------------------------------------------------
# bao CLI wrapper
# ---------------------------------------------------------------------------------------


def scrub(text: str, secrets: Sequence[str]) -> str:
    """Remove every known secret string from ``text`` before it is shown."""
    for secret in sorted((s for s in secrets if len(s) >= 4), key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    return text


class Bao:
    """Thin wrapper over the ``bao`` CLI that keeps secrets out of argv and output."""

    def __init__(self, secrets: Sequence[str], executable: str = "bao") -> None:
        self._secrets = list(secrets)
        self._exe = executable

    def add_secret(self, value: str) -> None:
        self._secrets.append(value)

    def run(
        self, *args: str, stdin: str | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(  # noqa: S603
            [self._exe, *args],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and proc.returncode != 0:
            detail = scrub(proc.stderr.strip(), self._secrets)
            raise MigrationError(f"`bao {' '.join(args[:2])}` failed: {detail or proc.returncode}")
        return proc


def detect_kv_version(bao: Bao, mount: str) -> int:
    """Return the KV engine version (1 or 2) of ``mount``."""
    out = bao.run("read", "-format=json", f"sys/internal/ui/mounts/{mount}").stdout
    try:
        options = json.loads(out).get("data", {}).get("options") or {}
    except ValueError:
        raise MigrationError(f"could not parse mount info for '{mount}'") from None
    return 2 if str(options.get("version", "1")) == "2" else 1


def read_secret(bao: Bao, mount: str, path: str, version: int) -> dict[str, str] | None:
    """Return the stored key/value map, or None if the secret does not exist."""
    proc = bao.run("kv", "get", f"-mount={mount}", "-format=json", path, check=False)
    if proc.returncode != 0:
        if "No value found" in proc.stderr:
            return None
        raise MigrationError(f"`bao kv get` failed: {scrub(proc.stderr.strip(), [])}")
    data = json.loads(proc.stdout).get("data", {})
    inner = data.get("data") if version == 2 else data
    return {k: str(v) for k, v in (inner or {}).items()}


def digest(value: str) -> str:
    """SHA-256 hex digest of ``value`` (used to compare without showing values)."""
    return hashlib.sha256(value.encode()).hexdigest()


# ---------------------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------------------


def render_config(address: str, mount: str, groups: Sequence[str], kv_version: int) -> str:
    """Return the EnvBao ``config.toml`` text for the migrated groups."""
    lines = [
        "# Generated by scripts/migrate_zshrc.py. Keep this directory owner-only (0700).",
        "[server]",
        f"address = {json.dumps(address)}",
        'token_file = "~/.envbao/token"',
    ]
    for group in groups:
        lines += [
            "",
            "[[query]]",
            'engine = "kv"',
            f"mount = {json.dumps(mount)}",
            f"path = {json.dumps(f'{SECRET_PREFIX}/{group}')}",
            f"kv_version = {kv_version}",
            'on_missing = "warn"',
        ]
    return "\n".join(lines) + "\n"


def policy_text(mount: str, kv_version: int) -> str:
    """HCL for the read-only EnvBao policy."""
    data_path = (
        f"{mount}/data/{SECRET_PREFIX}/*" if kv_version == 2 else f"{mount}/{SECRET_PREFIX}/*"
    )
    return (
        f'path "{data_path}" {{\n  capabilities = ["read"]\n}}\n'
        f'path "sys/internal/ui/mounts/{mount}" {{\n  capabilities = ["read"]\n}}\n'
        'path "auth/token/lookup-self" {\n  capabilities = ["read"]\n}\n'
    )


def write_private(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` with mode 0600, replacing any existing file."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.chmod(path, 0o600)


def install_config(config_dir: Path, config_text: str, token: str | None) -> None:
    """Create ``config_dir`` (0700), back up any existing config, write config and token."""
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(config_dir, 0o700)
    config_path = config_dir / "config.toml"
    if config_path.exists():
        backup = config_dir / "config.toml.bak"
        shutil.copyfile(config_path, backup)
        os.chmod(backup, 0o600)
    write_private(config_path, config_text)
    if token is not None:
        write_private(config_dir / "token", token)


# ---------------------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------------------


def report(
    groups: Mapping[str, Mapping[str, Entry]],
    unmapped: Sequence[str],
    parsed: ParseResult,
    out: object,
) -> None:
    """Print group -> names/lines (never values)."""
    emit = out.write  # type: ignore[attr-defined]
    for group, members in groups.items():
        emit(f"{SECRET_PREFIX}/{group}\n")
        for name, entry in members.items():
            emit(f"    {name}  (line {', '.join(map(str, entry.lines))})\n")
    if unmapped:
        emit(f"unmapped (not migrated; use --group NAME=group): {', '.join(unmapped)}\n")
    if parsed.skipped_dynamic:
        names = ", ".join(f"{n} (line {ln})" for n, ln in sorted(parsed.skipped_dynamic.items()))
        emit(f"skipped, value needs shell expansion: {names}\n")


def migrate_group(
    bao: Bao, mount: str, version: int, group: str, members: Mapping[str, Entry], *, force: bool
) -> None:
    """Write one group and verify it by digest. Raises MigrationError on any problem."""
    path = f"{SECRET_PREFIX}/{group}"
    if read_secret(bao, mount, path, version) is not None and not force:
        raise MigrationError(f"{mount}/{path} already exists; use --force to overwrite")
    payload = json.dumps({name: e.value for name, e in members.items()})
    bao.run("kv", "put", f"-mount={mount}", path, "-", stdin=payload)

    stored = read_secret(bao, mount, path, version) or {}
    bad = [n for n, e in members.items() if digest(stored.get(n, "")) != digest(e.value)]
    if bad:
        raise MigrationError(f"verification FAILED for {mount}/{path}: {', '.join(bad)}")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--zshrc", type=Path, default=Path("~/.zshrc"))
    p.add_argument("--mount", default=DEFAULT_MOUNT, help="KV mount name (default: env)")
    p.add_argument("--apply", action="store_true", help="write to OpenBao and create EnvBao config")
    p.add_argument("--force", action="store_true", help="overwrite existing shell/<group> secrets")
    p.add_argument("--group", action="append", default=[], metavar="NAME=group")
    p.add_argument("--config-dir", type=Path, default=Path("~/.envbao"))
    p.add_argument("--no-check", action="store_true", help="skip running `envbao --check`")
    return p.parse_args(argv)


def _run_check(config_dir: Path) -> bool:
    exe = shutil.which("envbao")
    cmd = [exe] if exe else [sys.executable, "-m", "envbao"]
    env = dict(os.environ)
    if not exe:
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")
    proc = subprocess.run(  # noqa: S603
        [*cmd, "--config", str(config_dir / "config.toml"), "--check"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    sys.stderr.write(proc.stderr)
    return proc.returncode == 0


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    args = parse_args(argv)
    environ = os.environ if environ is None else environ
    extra: dict[str, str] = {}
    for item in args.group:
        name, sep, group = item.partition("=")
        if not sep or not _GROUP_RE.match(group):
            sys.stderr.write(f"error: bad --group {name!r}; expected NAME=group\n")
            return 2
        extra[name] = group

    try:
        text = args.zshrc.expanduser().read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        sys.stderr.write(f"error: cannot read {args.zshrc}: {exc.strerror}\n")
        return 1
    parsed = parse_zshrc(text)
    groups, unmapped = classify(parsed.entries, extra)
    report(groups, unmapped, parsed, sys.stdout)

    if not args.apply:
        sys.stdout.write("\nDry run only. Re-run with --apply to write to OpenBao.\n")
        return 0
    if not groups:
        sys.stderr.write("error: nothing to migrate\n")
        return 1

    address = environ.get("BAO_ADDR", "").rstrip("/")
    if not address or not environ.get("BAO_TOKEN"):
        sys.stderr.write("error: BAO_ADDR and BAO_TOKEN must be set in the environment\n")
        return 1
    secrets = [e.value for g in groups.values() for e in g.values()] + [environ["BAO_TOKEN"]]
    bao = Bao(secrets)

    try:
        version = detect_kv_version(bao, args.mount)
        sys.stdout.write(f"\nmount '{args.mount}' is KV v{version}\n")
        for group, members in groups.items():
            migrate_group(bao, args.mount, version, group, members, force=args.force)
            sys.stdout.write(
                f"  OK  {args.mount}/{SECRET_PREFIX}/{group} ({len(members)} keys verified)\n"
            )
    except MigrationError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1

    config_dir = args.config_dir.expanduser()
    config_text = render_config(address, args.mount, list(groups), version)
    token: str | None = None
    problem: str | None = None
    try:
        bao.run("policy", "write", POLICY_NAME, "-", stdin=policy_text(args.mount, version))
        token = bao.run(
            "token", "create", f"-policy={POLICY_NAME}", "-orphan", f"-period={TOKEN_PERIOD}",
            "-display-name=envbao", "-field=token",
        ).stdout.strip()  # fmt: skip
        bao.add_secret(token)
    except MigrationError as exc:
        problem = str(exc)

    install_config(config_dir, config_text, token)
    sys.stdout.write(
        f"wrote {config_dir / 'config.toml'}"
        + (f" and {config_dir / 'token'}\n" if token else "\n")
    )

    if problem or not token:
        sys.stderr.write(
            f"error: secrets were migrated, but the EnvBao token could not be created: {problem}\n"
            f"       Create a token with policy '{POLICY_NAME}' and save it (mode 0600) to "
            f"{config_dir / 'token'}.\n"
        )
        return 1
    if not args.no_check and not _run_check(config_dir):
        sys.stderr.write("error: `envbao --check` failed\n")
        return 1

    lines = sorted({ln for g in groups.values() for e in g.values() for ln in e.lines})
    sys.stdout.write(
        "\nDone. After verifying, remove these lines from your zshrc: "
        + ", ".join(map(str, lines))
        + '\nand add:  command -v envbao >/dev/null && eval "$(envbao)"\n'
        "The old values were exposed in plaintext: rotate them in their services.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
