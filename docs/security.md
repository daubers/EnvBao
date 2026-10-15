# Security model

## Threat model

`envbao` protects a long-lived OpenBao token on a single-user workstation and makes sure secrets
reach the shell without being exposed or interpreted as code. It does **not** defend against an
attacker who already runs as you or as root, nor does it protect the environment of processes once
variables are exported (any child process inherits them).

## Controls

1. **Permission policy** - before anything else, `~/.envbao` (or the `--config` parent) and every
   entry beneath it must be: not a symlink, a regular file/directory, owned by the current user,
   and without any group/other permission bits (effectively `0700` dirs / `0600` files). Otherwise
   `envbao` logs CRITICAL, prints nothing to stdout, never contacts the server, and exits 78.
   A `token_file` outside the directory is checked identically.
2. **Fail closed** - output is produced only after every query succeeded. Errors yield empty stdout.
3. **Transport** - TLS verification is always on (optional `ca_cert`). `http://` is only allowed to
   loopback unless `allow_insecure_http = true`. Redirects are never followed, so the token cannot be
   forwarded elsewhere. Responses are size-limited.
4. **Injection safety** - names must match `[A-Za-z_][A-Za-z0-9_]*`; values are quoted
   (`shlex.quote`, or fish single-quote escaping) and NUL bytes are rejected. Tests round-trip
   hostile values (`$(...)`, backticks, quotes, newlines) through a real shell.
5. **Protected variables** - secrets may not set shell/loader control variables such as `PATH`,
   `IFS`, `ENV`, `BASH_ENV`, `PROMPT_COMMAND`, `LD_*`, `DYLD_*`, `PYTHONPATH`, `NODE_OPTIONS`, etc.,
   unless explicitly listed in `[security] allow_protected`. Refusals are logged at ERROR.
6. **No secret leakage** - the token is excluded from `repr()`, never put on a command line or in
   the environment, and never logged. Secret values are never logged. HTTP error messages carry only
   status and path. Unexpected exceptions log only their type, not a traceback, unless `-vv`.
7. **Audit trail** - log file (`0600`, created atomically with that mode, rotated) plus best-effort
   syslog for WARNING and above. Events: permission violations, auth failures, per-query outcome,
   overrides, refused variables, count of exported variables.

## Recommendations

- Use a token with a narrow read-only policy and a TTL; renew it periodically.
- Prefer `token_file` if you want to rotate the token without editing the config.
- Keep `~/.envbao` out of backups/sync tools that change permissions.
- Verify release binaries against `SHA256SUMS`.

## Reporting a vulnerability

Please report privately to the maintainer rather than opening a public issue.
