# Changelog

All notable changes are documented here. This project follows [Semantic Versioning](https://semver.org).

## [Unreleased]

### Added
- `scripts/migrate_zshrc.py`: one-off migration of zshrc exports into OpenBao KV (`env/shell/<group>`),
  with digest verification and generation of the EnvBao config and a read-only token.
- Initial implementation: config loading, strict permission policy, KV v1/v2 engine with
  auto-detection, safe shell rendering (bash/zsh/sh/fish), `--check` and `--init`, audit logging.
- Gitea pipelines for tests and tag-driven releases (linux-amd64, linux-arm64, macos-arm64).
