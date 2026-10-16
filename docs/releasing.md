# Releasing and CI

## Pipelines (`.gitea/workflows/`)

- **`test.yml`** - on every push and pull request: ruff, ruff format check, strict mypy, pytest with a
  90 % coverage gate, and a PyInstaller build smoke test.
- **`release.yml`** - on a pushed tag `vMAJOR.MINOR.PATCH[-prerelease][+build]` ([SemVer 2.0.0](https://semver.org)):
  1. `validate` rejects tags that are not valid SemVer (`scripts/version_from_tag.py`).
  2. `build` runs tests then builds the one-directory `envbao` app with PyInstaller on each runner,
     stamping the version from the tag and checking `envbao --version` matches. It then packages it:
     `envbao-<ver>-<target>.tar.gz` everywhere, plus `envbao_<ver>_<arch>.deb` (linux, built in pure
     Python by `scripts/packaging/build_deb.py`) or `envbao-<ver>-macos-arm64.pkg` (macOS, via
     `pkgbuild` in `scripts/packaging/build_pkg.sh`).
  3. `release` writes one `SHA256SUMS` and creates a Gitea release (marked *prerelease* if the tag
     has a `-suffix`) with all packages attached.

## Cutting a release

```sh
git tag v1.0.0 && git push origin v1.0.0
```

Update `CHANGELOG.md` first.

## Runner requirements

PyInstaller cannot cross-compile, so the pipeline needs one Gitea `act_runner` per target, registered
with these labels:

| Label | Host |
|-------|------|
| `linux-amd64` | Linux x86-64 (also runs tests, validation and publishing; needs `curl`, `python3`) |
| `linux-arm64` | Linux aarch64 |
| `macos-arm64` | macOS on Apple Silicon (host-mode runner, e.g. `macos-arm64:host`) |

Runners need network access to fetch `astral-sh/setup-uv` and Python packages.

## Secrets

| Secret | Purpose |
|--------|---------|
| `RELEASE_TOKEN` | Gitea access token with `write:repository` scope, used to create the release and upload assets. |

## Notes

- `actions/upload-artifact@v3` / `download-artifact@v3` are used because Gitea does not yet support v4.
- Linux builds link against the glibc of the build host, and the `.deb` declares `libc6 (>= 2.31)`:
  keep the Linux runners on Debian 11 / Ubuntu 20.04 or newer, ideally the oldest you want to
  support. Pre-release tags map to Debian `~` versions (`1.0.0-rc.1` → `1.0.0~rc.1`).
- Raspberry Pi OS 64-bit is covered by the arm64 `.deb`; 32-bit (armhf) would need an armv7 runner.
- The macOS `.pkg` is unsigned and not notarised; install with `sudo installer -pkg … -target /`.
