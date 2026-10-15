# EnvBao – implementation plan

## Context
Build `envbao`, a single-binary CLI invoked from `.zshrc`/`.bashrc` (`eval "$(envbao)"`) that pulls secrets from a running OpenBao server and emits shell `export` statements. Config (server, token, queries) lives in `~/.envbao/config.toml`. Security first: strict permission checks, no secret leakage, auditable logging. Repo is currently empty apart from a stub `pyproject.toml` (Python >=3.13, uv, no deps). Remote: `ssh://git@git.daubney.dev:222/matt/EnvBao.git`. Licence BSD-3-Clause.

## Stack
- Python 3.13, `uv`, src layout (`src/envbao/`), stdlib only at runtime where possible: `tomllib`, `urllib.request`/`ssl`, `logging`, `argparse`, `shlex`. (No `hvac`/`httpx` → smaller binary, smaller attack surface.)
- Dev: `pytest`, `pytest-cov`, `ruff`, `mypy`, `pyinstaller`, `responses`-free: tests use a local `http.server` fake OpenBao.
- Build: PyInstaller `--onefile` (via `envbao.spec`), version injected from git tag.

## Layout
```
src/envbao/
  __main__.py / cli.py     argparse: default=emit exports; --shell {bash,zsh,fish,auto}; --config PATH; --check; --version; -v
  config.py                load + validate config.toml (dataclasses), token/token_file/ENVBAO_TOKEN-free by design
  security.py              permission checks (dir 0700, files 0600, owned by euid, no symlink escape, not group/world accessible); exit code 78 on failure
  client.py                minimal OpenBao HTTP client (X-Vault-Token, TLS verify on by default, timeout, CA bundle option, refuse http:// to non-loopback unless allow_insecure_http)
  engines/__init__.py      Engine protocol + registry  (extension point for mid-term: approle, database, aws, ssh etc.)
  engines/kv.py            KV v1/v2 with auto-detect via sys/internal/ui/mounts/<path>; list/read, key filter, prefix, rename
  render.py                env-var name validation ([A-Za-z_][A-Za-z0-9_]*), shlex-quoted export/set -gx output, reject control chars/newlines
  logging_setup.py         stderr + rotating file log ~/.envbao/envbao.log (0600); NEVER logs token/values, only names/paths/status
tests/                     unit + integration (fake OpenBao server) + permission tests (tmp_path, chmod)
docs/                      usage.md, configuration.md, security.md, development.md, releasing.md
.gitea/workflows/test.yml  on push/PR: ruff, mypy, pytest --cov (fail-under 90), py3.13
.gitea/workflows/release.yml on tag v*.*.*: validate semver, build linux-amd64/linux-arm64/macos-arm64, sha256sums, publish via Gitea release API
envbao.spec, LICENSE (BSD-3-Clause, © Matt Daubney), README.md, CHANGELOG.md, .gitignore, pyproject.toml (deps/groups/scripts)
```

## Config (`~/.envbao/config.toml`)
```toml
[server]
address = "https://bao.example.com:8200"
token = "s.xxxx"            # or token_file = "~/.envbao/token" (also perm-checked)
namespace = ""              # optional X-Vault-Namespace
ca_cert = ""                # optional
timeout = 5

[[query]]                   # one per secret source; "engine" defaults to "kv"
engine = "kv"
mount = "secret"
path = "myapp/prod"
keys = ["DB_URL", "API_KEY"]   # optional filter
prefix = "MYAPP_"              # optional
rename = { DB_URL = "DATABASE_URL" }   # optional
on_missing = "warn"            # warn | error
```
Unknown `engine` → clear error; engine registry means new credential engines are added as modules without touching CLI/render.

## Security behaviours
1. Before reading anything: `~/.envbao` must be a real directory owned by current user with mode `0700`; every file inside `0600` and owned by user; otherwise log CRITICAL, print nothing to stdout, exit non-zero (78). Symlinks rejected.
2. Config parse errors, auth failures (403), connection/TLS errors → logged, **stdout stays empty** (so `eval` is a no-op) and a short message goes to stderr; exit code non-zero. Partial-failure policy governed by `on_missing`.
3. Output quoting via `shlex.quote`; names validated; values containing NUL rejected.
4. Token never logged or placed in argv/env; HTTP errors redacted. Optional `--check` validates config/perms/token (`auth/token/lookup-self`) without emitting values.
5. Fail-closed on TLS; plain HTTP only with explicit `allow_insecure_http=true` and loopback otherwise warned.
6. Logs: file at `0600`, events: perm violation, auth fail, each query success/fail (path only), count of vars exported.

## CI/CD (Gitea Actions)
- `test.yml`: `astral-sh/setup-uv`, `uv sync --all-groups`, ruff, mypy, pytest with coverage.
- `release.yml`: trigger `push: tags: ['v*']`; regex-check `^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$`; matrix of runners by label (`linux-amd64`, `linux-arm64`, and **`macos-arm64`** for the macOS build — labels documented in `docs/releasing.md`, as PyInstaller can't cross-compile); version written into `src/envbao/_version.py` at build from tag; artifacts `envbao-<ver>-<os>-<arch>` + `SHA256SUMS` uploaded to the Gitea release (token secret `RELEASE_TOKEN`).

## Execution steps
0. First action once approved: copy this plan verbatim into the repo as `docs/plans/001-initial-implementation.md` (plan mode currently blocks writing outside the plan file), and commit it with the scaffold.
1. `git init`, add remote, scaffold pyproject/LICENSE/.gitignore and a full **README.md** (what/why, install from release binary, quick start with `eval "$(envbao)"` for zsh/bash, example config, permission requirements, security notes, exit codes, CI/release summary, links to `docs/`, licence badge). README is kept in sync with features at the end of step 4.
2. Implement security → config → render → client → kv engine → cli (TDD, tests alongside each module).
3. Fake-OpenBao integration tests (KV v1, v2, auto-detect, 403, TLS/HTTP policy, missing keys).
4. Docs, CI workflows, PyInstaller spec; local build smoke test.
5. Initial commit (no push unless asked).

## Verification
- `uv run pytest --cov` (≥90%), `ruff check`, `mypy --strict`.
- `uv run pyinstaller envbao.spec` → run `dist/envbao --version`; `eval "$(dist/envbao --config test.toml)"` against a local `bao server -dev` (if installed) or the fake server; confirm `chmod 644 config.toml` makes it exit non-zero with empty stdout.
- Lint the workflow YAML; tag-regex unit test with valid/invalid semver strings.
