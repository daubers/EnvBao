# Development

## Setup

```sh
uv sync --all-groups
```

## Checks (also run by CI)

```sh
uv run ruff check . && uv run ruff format --check .
uv run mypy                       # strict
uv run pytest --cov               # fails below 90 % coverage
```

## Layout

| Module | Responsibility |
|--------|----------------|
| `cli.py` | Argument parsing, orchestration, exit codes |
| `security.py` | Filesystem permission policy |
| `config.py` | Strict TOML parsing into dataclasses |
| `client.py` | stdlib HTTP client for the OpenBao API (TLS, no redirects) |
| `engines/` | Secret engines (`kv`); extension point for more |
| `resolve.py` | Runs queries, applies keys/rename/prefix, filters unsafe names |
| `render.py` | Name validation, quoting, shell dialects |
| `logging_setup.py` | stderr, private rotating file, syslog |
| `runner.py` | `exec`: secret selection, output redaction, process handling |
| `version.py` | Release/stamped version, git-derived version for checkouts |
| `errors.py` | Exceptions and exit codes |

## Testing approach

- `tests/conftest.py` provides `fake_bao`, an in-process HTTP server emulating the OpenBao endpoints
  used (KV v1/v2, mount detection, `lookup-self`, redirects), so tests need no real server.
- Permission tests build real directories under `tmp_path` and `chmod` them.
- Rendering tests execute the output in `/bin/sh` to prove values round-trip unchanged.

## Building locally

```sh
uv run pyinstaller envbao.spec --clean --noconfirm
./dist/envbao/envbao --version
```

PyInstaller cannot cross-compile; build on the platform you target.

## Testing against a real OpenBao

```sh
bao server -dev -dev-root-token-id=dev &
export BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=dev
bao kv put -mount=secret myapp/prod DB_URL=postgres://x API_KEY=abc
envbao --init   # then set address/token, add a [[query]] for secret/myapp/prod
envbao --check && envbao
```

## Packaging locally

```sh
python3 scripts/packaging/build_deb.py 0.2.0 arm64 dist out   # .deb (pure Python, any OS)
sh scripts/packaging/build_pkg.sh 0.2.0 dist out              # macOS .pkg (needs pkgbuild)
```

The binary inside is for the OS you built on, so only ship packages built on the matching runner.
