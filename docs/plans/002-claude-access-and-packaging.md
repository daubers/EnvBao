# Let Claude use OpenBao credentials via `envbao exec`, plus faster startup

## Context
Credentials now live in OpenBao (`env/shell/<group>`). The user wants Claude Code to be able to use them. Findings:
- Going through the `envbao` binary is fine functionally, but the PyInstaller **onefile** binary re-extracts itself on every call (~1.4 s measured, also paid on every shell start via `.zshrc`).
- Letting Claude run `bao kv get` or inherit the shell env puts every secret value into the transcript. This session already shows the risk: `BAO_TOKEN` in the shell env is a **root** token, visible to any Claude command.
- Chosen model: a run-with-secrets wrapper. Claude names the groups it needs; `envbao exec` fetches only those and runs the command with them in its environment; output is redacted so values never reach the transcript. Claude gets its **own narrow token**, not the root/shell one.

User choices: access via `envbao exec` wrapper; all nine groups allowed (gitea, github, youtrack, wiki, pkgs, elastic, photos, aws, proton — aws/proton flagged as highest-sensitivity, can be narrowed later by one policy edit); switch release to onedir for startup time.

## Step 0 (first action once approved)
Copy this plan verbatim to the repo as `docs/plans/002-claude-access-and-packaging.md` (plan mode only allows writing the plan file; the user wants all plans kept in `docs/plans/`) and include it in the first commit.

## 1. `envbao exec` (new subcommand)
- `envbao [--config PATH] exec -q GROUP [-q GROUP …] [--no-redact] -- CMD [ARGS…]` (also `--all` is deliberately **not** provided).
- Config: add optional `name` to `[[query]]` (default = last path segment, e.g. `shell/gitea` → `gitea`); names must be unique (validated in `config.py`). `-q` selects queries by name; unknown name → exit 78 with list of valid names.
- Reuses `resolve.run_query` / `BaoClient` / protected-variable filtering (`src/envbao/resolve.py`), same permission check (`security.check_config_dir`), fail-closed (no command run if any selected query fails).
- Execution: build `env = os.environ | secrets`; run the child with `subprocess.Popen`, inherit stdin, pipe stdout/stderr through a **redactor** that replaces every secret value (len ≥ 6) with `***`, handling matches split across chunk boundaries via a rolling buffer; forward SIGINT/SIGTERM; propagate the child's exit code (127 if not found). `--no-redact` uses `os.execvpe` (exact exec, TTY-friendly) for interactive humans.
- Audit log (`envbao.log`/syslog): groups requested, `argv[0]` only (never arguments), exit status. Never values.
- Files: `src/envbao/cli.py` (subparser), new `src/envbao/runner.py` (env build, redactor, process handling), `src/envbao/config.py` (`name`), `tests/test_runner.py`, `tests/test_cli.py`.

## 2. Claude-specific credentials (provisioning script)
New `scripts/setup_claude_access.py` (uses the admin `BAO_TOKEN` once, like `migrate_zshrc.py`; reuse its `Bao` wrapper, `write_private`, `install_config`, `policy_text` patterns):
- Policy `envbao-claude`: read on `env/data/shell/<g>` for each allowed group + `auth/token/lookup-self` (+ `sys/internal/ui/mounts/env`).
- Token: orphan, periodic 720h, `display-name=envbao-claude`, written to `~/.envbao/claude-token` (0600); never printed.
- `~/.envbao/claude.toml` (0600) with the nine named queries pointing at that token file; existing files backed up, never overwritten without `--force`.
- Reports whether an OpenBao audit device is enabled (`bao audit list`) and says how to enable one if not (does **not** enable it unasked).
- Optionally merges into `~/.claude/settings.json` (backup first, JSON merge, shown as a diff before writing): `env.ENVBAO_CONFIG=~/.envbao/claude.toml` (new env var honoured by `resolve_config_path`), `permissions.allow: ["Bash(envbao exec:*)"]`, `permissions.deny: ["Bash(bao:*)", "Read(~/.envbao/**)", "Read(~/.zshrc)"]`.
- Tests with the stub-`bao` approach from `tests/test_migrate_zshrc.py`.

## 3. Startup time: PyInstaller onedir
- `envbao.spec`: switch to `COLLECT` (onedir; `exclude_binaries=True`), output `dist/envbao/`.
- `release.yml` / `test.yml`: smoke-test `dist/envbao/envbao --version`; package `envbao-<ver>-<target>.tar.gz` (directory `envbao/`), checksum those; release assets become tarballs.
- `scripts/install.sh` (new, POSIX sh): verify checksum if `SHA256SUMS` present, extract to `~/.local/lib/envbao/`, symlink `~/.local/bin/envbao` → `~/.local/lib/envbao/envbao`, clear macOS quarantine flag, print `envbao --version`. (This is where the symlink the user asked about earlier actually belongs.)
- Verify startup drops well below the current 1.4 s (target ≲0.3 s).

## 3b. Native packages (so no folders need moving by hand)
The onedir build is a folder, so ship installable packages that place it and create the `envbao` symlink themselves:
- **macOS `.pkg`** (built on the `macos-arm64` runner with `pkgbuild --root … --identifier dev.daubney.envbao --version <ver> --install-location /usr/local/lib/envbao`, plus a `postinstall` script that symlinks `/usr/local/bin/envbao` → `/usr/local/lib/envbao/envbao` and clears the quarantine flag). Unsigned/not notarised; docs note `sudo installer -pkg … -target /` (avoids the Gatekeeper prompt) or right-click → Open. Uninstall script `scripts/packaging/uninstall-macos.sh`.
- **Debian/Ubuntu/Raspberry Pi OS `.deb`** (built with `dpkg-deb --build` from a staged tree, no extra tooling): files in `/opt/envbao/`, symlink `/usr/bin/envbao`, `DEBIAN/control` (`Package: envbao`, `Version` from the tag with `-` prerelease mapped to `~`, `Architecture: amd64|arm64`, `Depends: libc6`, maintainer, description), `postrm` removes nothing outside the package. Built on `linux-amd64` (Debian/Ubuntu x86-64) and `linux-arm64` (Ubuntu arm64 and **64-bit** Raspberry Pi OS). 32-bit Raspberry Pi OS (`armhf`) is **not** covered — would need an armv7 runner; documented as unsupported.
- glibc compatibility: Linux builds link to the build host's glibc; document the minimum (build on the oldest Debian/Ubuntu runner, ideally bookworm/22.04) in `docs/releasing.md`.
- New `scripts/packaging/` (`build_deb.sh`, `build_pkg.sh`, `postinstall`, `control.in`) with tests that stage the tree and inspect it (`dpkg-deb -c`/`--info` where available; `pkgutil --payload-files` on macOS; otherwise unit-test the generated control/postinstall text and the version mapping function).
- `release.yml` matrix produces per target: `envbao_<ver>_amd64.deb`, `envbao_<ver>_arm64.deb`, `envbao-<ver>-macos-arm64.pkg`, plus the generic `.tar.gz` + `scripts/install.sh` for other systems; one `SHA256SUMS` covers all.
- README Install section leads with: `sudo dpkg -i envbao_<ver>_<arch>.deb` / `sudo installer -pkg envbao-<ver>-macos-arm64.pkg -target /`.

## 4. Docs & hygiene
- New `docs/claude.md` (model, setup, how Claude should call it, redaction limits: a command can still exfiltrate a secret by transforming it, e.g. base64 — treat as defence in depth, not a sandbox); update `docs/security.md`, `docs/configuration.md` (`name`, `ENVBAO_CONFIG`), `docs/usage.md` (`exec`), README install section (tarball + `install.sh`), `docs/releasing.md`, CHANGELOG.
- Recommend (and print in the setup script output) removing the root `BAO_TOKEN`/`BAO_ADDR` exports from `~/.zshrc` (lines ≈2930–2931) so Claude and every child process stop inheriting a root token. Not edited automatically.
- Next release: tag `v0.2.0` after merging (tagging/pushing only when the user says so).

## Verification
1. `uv run pytest --cov` (≥90%), ruff, mypy. Redactor tests: value split across chunks, multiple secrets, stderr, binary output, short values not redacted.
2. `envbao exec -q gitea -- sh -c 'echo $GITEA_TOKEN'` prints `***`; `-- sh -c 'test -n "$GITEA_TOKEN"'` exits 0; unknown group exits 78 with no command run; exit codes propagate.
3. Run setup script; confirm `bao token lookup` on the Claude token shows only `envbao-claude` policy and that it is denied `env/data/shell/<unlisted>` and `sys/policies`.
4. Build onedir locally; `time dist/envbao/envbao --version`; build the `.pkg` locally and inspect the payload (`pkgutil --payload-files`); build a `.deb` locally via `dpkg-deb` if available (else in a Debian container) and check `--info`/`-c`; install via `install.sh` into a temp prefix. Pipeline run on tag `v0.2.0` produces all assets (checked after the user approves tagging/pushing).
5. From a Claude session with the new settings, `envbao exec -q youtrack -- curl …` works and the transcript contains no secret value; `bao kv get …` is denied by permissions.
