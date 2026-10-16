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

## Install

Download the binary for your platform from the project's releases page
(`envbao-<version>-linux-amd64`, `-linux-arm64`, `-macos-arm64`), verify it against `SHA256SUMS`,
then:

```sh
chmod +x envbao-*; mv envbao-* ~/.local/bin/envbao
```

Or run from source: `uv sync && uv run envbao --help`.

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
