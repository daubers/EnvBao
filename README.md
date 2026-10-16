# EnvBao

[![Licence: BSD-3-Clause](https://img.shields.io/badge/licence-BSD--3--Clause-blue.svg)](LICENSE)

Inject environment variables from a running [OpenBao](https://openbao.org) server into your shell.
`envbao` is a single self-contained binary designed to be called from `~/.zshrc` or `~/.bashrc`:

```sh
eval "$(envbao)"
```

It reads `~/.envbao/config.toml`, fetches the secrets selected by your queries, and prints
safely quoted `export` statements for your shell to evaluate.

## Features

- **Security first** - refuses to run unless `~/.envbao` and everything in it is accessible by
  you only; all failures print *nothing* to stdout, so a failed run can never half-configure
  your shell. See [docs/security.md](docs/security.md).
- **Config-driven queries** - choose exactly which secrets, keys, prefixes and renames you want.
- **KV v1 and v2** with automatic version detection. Other credential engines are planned and
  slot into an engine registry.
- **Audited** - issues are logged to stderr, `~/.envbao/envbao.log` (mode 0600) and the system
  log. Tokens and secret values are never logged.
- **Single binary**, no runtime dependencies (built with PyInstaller).
- bash, zsh, sh and fish output.
- `envbao exec`: run a command with chosen secret groups, output redacted (see [docs/claude-access.md](docs/claude-access.md)).

## Install

Download the package for your platform from the project's releases page, together with
`SHA256SUMS`, and verify it (`shasum -a 256 -c SHA256SUMS --ignore-missing`, or `sha256sum -c`
on Linux). The packages install the application and create the `envbao` command for you:

| Platform | Asset | Install |
|----------|-------|---------|
| macOS (Apple Silicon) | `envbao-<version>-macos-arm64.pkg` | `sudo installer -pkg envbao-<version>-macos-arm64.pkg -target /` |
| Debian / Ubuntu (x86-64) | `envbao_<version>_amd64.deb` | `sudo dpkg -i envbao_<version>_amd64.deb` |
| Debian / Ubuntu / Raspberry Pi OS (64-bit ARM) | `envbao_<version>_arm64.deb` | `sudo dpkg -i envbao_<version>_arm64.deb` |
| Any other / no root | `envbao-<version>-<target>.tar.gz` | `scripts/install.sh envbao-<version>-<target>.tar.gz` |

* The `.pkg` is unsigned: using `installer` on the command line avoids the Gatekeeper prompt.
  Uninstall with `scripts/packaging/uninstall-macos.sh`.
* The `.deb` installs to `/opt/envbao` with `/usr/bin/envbao`; remove with `sudo apt remove envbao`.
  32-bit Raspberry Pi OS (armhf) is not supported.
* `scripts/install.sh` verifies the checksum, unpacks to `~/.local/lib/envbao` and links
  `~/.local/bin/envbao`. Make sure `~/.local/bin` is on your `PATH`.
* Linux packages need glibc 2.31 or newer.

Check with `envbao --version`. Or run from source: `uv sync && uv run envbao --help`.

## Quick start

```sh
envbao --init                 # creates ~/.envbao (0700) and a template config.toml (0600)
$EDITOR ~/.envbao/config.toml # add server address, token and queries
envbao --check                # verify permissions, config and token (prints no secrets)
```

Add to `~/.zshrc` or `~/.bashrc`:

```sh
command -v envbao >/dev/null && eval "$(envbao)"
```

> **Caveat: `PATH` must already include `envbao` where this line runs.** The guard
> `command -v envbao` is evaluated at that point in the file. If `~/.local/bin` (or wherever you
> installed the binary) is only added to `PATH` *later* in your `.zshrc` / `.bashrc` (a common
> case with Oh My Zsh, nvm or pyenv snippets), the guard fails and the line is **silently
> skipped**: no variables are loaded, yet running `eval "$(envbao)"` by hand later works.
>
> Either put the line *after* your last `PATH` change, or avoid the dependency by using the
> full path:
>
> ```sh
> [ -x "$HOME/.local/bin/envbao" ] && eval "$("$HOME/.local/bin/envbao")"
> ```
>
> To debug, open a new shell and run `command -v envbao`; if it prints nothing, `PATH` is the
> problem. Remember that a failing `envbao` also prints nothing to stdout by design, so check
> stderr and `~/.envbao/envbao.log` for the reason.

### Example `~/.envbao/config.toml`

```toml
[server]
address = "https://bao.example.com:8200"
token   = "s.xxxxxxxx"            # or: token_file = "~/.envbao/token"

[[query]]
mount  = "secret"
path   = "myapp/prod"
keys   = ["DB_URL", "API_KEY"]            # optional filter
prefix = "MYAPP_"                         # optional
rename = { DB_URL = "DATABASE_URL" }      # optional (applied before prefix)
on_missing = "warn"                       # "warn" (default) or "error"
```

Full reference: [docs/configuration.md](docs/configuration.md).

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Other failure |
| 2 | Command-line usage error |
| 69 | Server unreachable or returned an unexpected response |
| 77 | Token rejected by the server |
| 78 | Bad configuration or unsafe file permissions |

## Documentation

- [Usage](docs/usage.md)
- [Configuration reference](docs/configuration.md)
- [Security model](docs/security.md)
- [Using EnvBao from Claude Code](docs/claude-access.md)
- [Migrating from ~/.zshrc](docs/migration.md)
- [Development](docs/development.md)
- [Releasing and CI](docs/releasing.md)
- [Plans](docs/plans/)

## Development

```sh
uv sync --all-groups
uv run pytest --cov     # tests (coverage gate: 90%)
uv run ruff check . && uv run mypy
uv run pyinstaller envbao.spec   # builds dist/envbao
```

## Licence

BSD 3-Clause. See [LICENSE](LICENSE).
