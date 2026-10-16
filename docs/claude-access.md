# Using EnvBao credentials from Claude Code

Goal: let Claude Code *use* your credentials (call an API, push to a repo, query a service)
without the secret values ever entering the conversation transcript, and without handing it
your admin token.

## How it works

```
Claude ──▶ envbao exec -q gitea -- curl -H "Authorization: token $GITEA_TOKEN" …
              │  1. reads ~/.envbao/claude.toml  ($ENVBAO_CONFIG)
              │  2. fetches ONLY group "gitea" with Claude's own narrow token
              │  3. runs the command with GITEA_TOKEN in its environment
              └─ 4. replaces secret values in stdout/stderr with ***
```

* **Narrow token.** `scripts/setup_claude_access.py` creates the policy `envbao-claude` (read on
  `env/shell/<group>` only) and an orphan, periodic token named `envbao-claude`. Revoke it at any
  time without touching your own token.
* **`exec_only`.** The generated `claude.toml` sets `[security] exec_only = true`: plain
  `envbao` (which prints `export` lines for everything) and `--no-redact` are refused.
* **Redaction.** Secret values (6+ characters; raw, JSON-escaped and URL-encoded forms) are masked
  in the command's output, including across read boundaries.
* **Permissions.** Optional Claude Code rules deny `bao`, and reading `~/.envbao/**` and
  `~/.zshrc`, so the only route to a secret is `envbao exec`.
* **Audit.** Every `exec` is logged (groups and the program name, never arguments or values) in
  `~/.envbao/envbao.log`, and OpenBao's own audit device attributes reads to the
  `envbao-claude` token.

## Setup (once)

With an admin `BAO_ADDR`/`BAO_TOKEN` in your shell:

```sh
uv run python scripts/setup_claude_access.py                 # policy, token, ~/.envbao/claude.toml
uv run python scripts/setup_claude_access.py --settings      # also update ~/.claude/settings.json
```

Options: `--groups gitea,youtrack` (default: every group under `shell/`), `--mount`, `--force`.
`--settings` sets `ENVBAO_CONFIG` for Claude's sessions and merges these permission rules (a
`.bak` copy of the file is kept):

```json
{
  "permissions": {
    "allow": ["Bash(envbao exec:*)"],
    "deny":  ["Bash(bao:*)", "Read(~/.envbao/**)", "Read(~/.zshrc)"]
  }
}
```

Then **remove the root `BAO_TOKEN`/`BAO_ADDR` exports from `~/.zshrc`**: every process started from
your shell, including Claude's tools, inherits them.

## Using it

```sh
envbao exec -q youtrack -- curl -s -H "Authorization: Bearer $YOUTRACK_TOKEN" "https://$YOUTRACK_URL/api/users/me"
envbao exec -q gitea -q github -- ./deploy.sh
```

Note that `$VAR` inside the command line is expanded by the *calling* shell, before envbao runs. To
have the child expand it, quote it: `envbao exec -q gitea -- sh -c 'curl -H "Authorization: token $GITEA_TOKEN" …'`.
Group names are the last path segment (`shell/gitea` → `gitea`) unless a query sets `name`.

## Limits

Redaction is defence in depth, not a sandbox. A command that deliberately transforms a secret
(base64, hashing, splitting it across lines) or sends it to a remote service defeats the filter.
Only grant Claude groups you are comfortable with it using; to narrow later, edit the
`envbao-claude` policy and `claude.toml` (re-run the setup script with `--groups … --force`).
`aws` and `proton` (cloud keys and your mail bridge password) are the most sensitive groups.
