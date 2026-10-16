# Migrating secrets from `~/.zshrc` into OpenBao

`scripts/migrate_zshrc.py` is a one-off helper (stdlib only, not part of the binary). It needs the
`bao` CLI and `BAO_ADDR` / `BAO_TOKEN` in the environment with enough rights to write the target
KV mount and to create a policy and token (e.g. a root or admin token).

## What it does

1. **Parses** `~/.zshrc` as text (never sourced). Only column-0 `export NAME=value` lines with
   literal values are considered; values needing shell expansion (`$HOME`, `$(...)`, backticks)
   are skipped and reported by name.
2. **Groups** variables by name prefix into `<mount>/shell/<group>` (`AWS_*` → `aws`,
   `PROTON_BRIDGE_*` → `proton`, `GITHUB_*`, `GITEA_*`, `ELASTIC_*`, `PHOTOS_*`, `PKGS_*`, `WIKI_*`,
   `YOUTRACK_*`). Toolchain variables (`PATH`, `ZSH`, `LDFLAGS`, `CPPFLAGS`, `NVM_DIR`) and
   `BAO_TOKEN` / `BAO_ADDR` are never migrated. Unrecognised names are listed; map them with
   `--group NAME=group`.
3. **Writes** each group with `bao kv put` (JSON on stdin, so values never appear in `ps`),
   refusing to overwrite an existing secret unless `--force`, then **reads it back and compares
   SHA-256 digests**.
4. Creates the read-only policy `envbao-shell`, mints an orphan periodic token (720h) for it,
   and writes `~/.envbao/token` and `~/.envbao/config.toml` (0600, directory 0700; any previous
   config is saved as `config.toml.bak`), then runs `envbao --check`.

Values are never printed or logged; `bao` error output is scrubbed of known secrets.

## Usage

```sh
uv run python scripts/migrate_zshrc.py            # dry run: names and line numbers only
uv run python scripts/migrate_zshrc.py --apply    # write, verify, generate EnvBao config
```

Options: `--zshrc PATH`, `--mount NAME` (default `env`; must already exist as a KV engine),
`--force`, `--group NAME=group`, `--config-dir DIR`, `--no-check`.

## Afterwards

1. Remove the listed line numbers from `~/.zshrc` and add
   `command -v envbao >/dev/null && eval "$(envbao)"`.
2. Open a new shell and confirm the variables are present.
3. **Rotate the credentials.** They lived in plaintext, in shell history/backups, and (if you used an
   AI assistant on that file) possibly in its transcripts.
4. The generated token period is 720h: renew it (`bao token renew`) or re-run the helper with
   `--force` to issue a new one before it lapses.
