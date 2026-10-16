#!/bin/sh
# Install EnvBao from a release tarball (for systems without a .deb/.pkg).
#
# Usage: scripts/install.sh envbao-<version>-<target>.tar.gz [PREFIX]
#
# Extracts to PREFIX/lib/envbao and links PREFIX/bin/envbao to it (default PREFIX: ~/.local).
# If a SHA256SUMS file sits next to the tarball, the checksum is verified first.
set -eu

[ $# -ge 1 ] && [ $# -le 2 ] || { sed -n '2,7p' "$0" >&2; exit 2; }
tarball=$1
prefix=${2:-$HOME/.local}
[ -f "$tarball" ] || { echo "error: $tarball not found" >&2; exit 1; }

sums="$(dirname "$tarball")/SHA256SUMS"
if [ -f "$sums" ]; then
    name=$(basename "$tarball")
    expected=$(awk -v n="$name" '$2 == n || $2 == "*" n { print $1 }' "$sums")
    [ -n "$expected" ] || { echo "error: $name is not listed in $sums" >&2; exit 1; }
    if command -v sha256sum >/dev/null 2>&1; then
        actual=$(sha256sum "$tarball" | awk '{ print $1 }')
    else
        actual=$(shasum -a 256 "$tarball" | awk '{ print $1 }')
    fi
    [ "$expected" = "$actual" ] || { echo "error: checksum mismatch for $name" >&2; exit 1; }
    echo "checksum OK"
else
    echo "warning: no SHA256SUMS next to the tarball; skipping verification" >&2
fi

lib="$prefix/lib"
mkdir -p "$lib" "$prefix/bin"
rm -rf "$lib/envbao"
tar -xzf "$tarball" -C "$lib"
[ -x "$lib/envbao/envbao" ] || { echo "error: tarball did not contain envbao/envbao" >&2; exit 1; }
ln -sf "$lib/envbao/envbao" "$prefix/bin/envbao"
# Downloaded via a browser on macOS? Clear the quarantine flag (harmless elsewhere).
xattr -dr com.apple.quarantine "$lib/envbao" 2>/dev/null || true

echo "installed: $prefix/bin/envbao -> $lib/envbao/envbao"
"$prefix/bin/envbao" --version
case ":$PATH:" in
    *":$prefix/bin:"*) ;;
    *) echo "note: $prefix/bin is not on your PATH" >&2 ;;
esac
