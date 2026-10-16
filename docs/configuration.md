# Configuration reference

File: `~/.envbao/config.toml`, or the path in `--config` / the `ENVBAO_CONFIG` environment variable (the flag wins). Unknown keys and wrong types are rejected, so typos fail loudly.

## `[server]`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `address` | string | *required* | Base URL, e.g. `https://bao.example.com:8200`. No credentials in the URL. |
| `token` | string | | OpenBao token. Exactly one of `token` / `token_file` is required. |
| `token_file` | string | | Path (`~` expanded) to a file containing the token. Must pass the same permission policy (owner only, not a symlink). |
| `namespace` | string | | Sent as `X-Vault-Namespace`. |
| `ca_cert` | string | system store | PEM CA bundle used to verify the server. |
| `timeout` | number | `5` | Per-request timeout in seconds. |
| `allow_insecure_http` | bool | `false` | Permit `http://` to a non-loopback host. Loopback (`127.0.0.1`, `::1`, `localhost`) is always allowed over HTTP. Not recommended: the token travels in clear. |

## `[security]`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `allow_protected` | list of strings | `[]` | Protected variable names (see [security.md](security.md)) that secrets may set, e.g. `["PATH"]`. |
| `exec_only` | bool | `false` | Refuse plain `envbao` (which prints every value) and `exec --no-redact`; only redacted `envbao exec` and `--check` work. Used by the Claude profile. |

## `[[query]]`

One table per secret source. Queries run in order; later ones override earlier ones.

Common keys:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `engine` | string | `"kv"` | Secret engine. Currently only `kv`. |
| `name` | string | last `path` segment | Group name for `envbao exec -q NAME` (letters, digits, `.` `_` `-`). |
| `keys` | list of strings | all | Only export these keys from the secret. |
| `rename` | table | `{}` | Map secret key → variable name. Applied before `prefix`. |
| `prefix` | string | `""` | Prepended to every variable name. |
| `on_missing` | `"warn"` \| `"error"` | `"warn"` | What to do if the secret or a listed key does not exist. `error` aborts the whole run. |

### `engine = "kv"`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mount` | string | *required* | KV mount path, e.g. `secret`. |
| `path` | string | *required* | Secret path within the mount. |
| `kv_version` | `1` \| `2` \| `"auto"` | `"auto"` | `auto` queries `sys/internal/ui/mounts/<mount>`; set explicitly if the token cannot read that endpoint. |

Names that are not valid variable names (`[A-Za-z_][A-Za-z0-9_]*`) are skipped with a warning.

## Adding an engine

Implement `envbao.engines.Engine` (`validate` and `fetch`) in a new module under
`src/envbao/engines/`, register it in `ENGINES`, and document its options here. Filtering,
renaming, prefixing, name validation and rendering are shared.
