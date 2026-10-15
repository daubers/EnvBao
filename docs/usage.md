# Usage

```
envbao [--config PATH] [--shell {auto,bash,zsh,sh,fish}] [-v|-vv] [--check | --init]
envbao --version
```

| Option | Description |
|--------|-------------|
| *(none)* | Fetch all queries and print export statements to stdout. |
| `--config PATH` | Use another config file. Its **parent directory** is the one permission-checked. |
| `--shell` | Output dialect. `auto` (default) picks from `$SHELL`; unknown shells get POSIX syntax. |
| `--check` | Validate permissions, config and token (via `auth/token/lookup-self`). Prints nothing to stdout. |
| `--init` | Create `~/.envbao` (0700) and a template `config.toml` (0600). Never overwrites. |
| `-v` / `-vv` | Show INFO / DEBUG messages on stderr. |

## Shell integration

bash / zsh:

```sh
command -v envbao >/dev/null && eval "$(envbao)"
```

fish (`~/.config/fish/config.fish`):

```fish
type -q envbao; and envbao | source
```

**Behaviour on failure:** stdout is empty and the exit code is non-zero, so `eval` does nothing and
your shell starts normally; the reason is printed to stderr and logged. If you prefer silence at
shell start-up, append `2>/dev/null` (details remain in `~/.envbao/envbao.log`).

## Output

Values are always single-quoted, so they can never be interpreted as shell syntax:

```sh
export DATABASE_URL='postgres://u:p@h/db'
export MYAPP_GREETING='it'"'"'s fine'
```

Non-string secret values (numbers, booleans, nested objects) are converted to JSON text.
Later `[[query]]` tables override earlier ones; overrides are logged.

## Logs

Records go to stderr (WARNING+ by default), the system log (WARNING+, best effort), and
`~/.envbao/envbao.log` (INFO+, mode 0600, rotated at 256 KiB, 3 backups). The log contains paths,
variable *names*, counts and outcomes only.
