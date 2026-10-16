#!/bin/sh
# Build a macOS installer package from the PyInstaller one-dir output.
#
# Usage: scripts/packaging/build_pkg.sh VERSION DIST_DIR OUT_DIR
#   e.g. scripts/packaging/build_pkg.sh 1.2.3 dist out
#
# Installs the application to /usr/local/lib/envbao and a symlink /usr/local/bin/envbao.
# The package is unsigned; install it with:  sudo installer -pkg FILE -target /
set -eu

[ $# -eq 3 ] || { sed -n '2,8p' "$0" >&2; exit 2; }
version=$1 dist=$2 out=$3
[ -x "$dist/envbao/envbao" ] || { echo "error: $dist/envbao/envbao not found" >&2; exit 1; }
command -v pkgbuild >/dev/null || { echo "error: pkgbuild (macOS) is required" >&2; exit 1; }

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/usr/local/lib" "$stage/usr/local/bin" "$out"
cp -R "$dist/envbao" "$stage/usr/local/lib/envbao"
ln -s ../lib/envbao/envbao "$stage/usr/local/bin/envbao"

pkg="$out/envbao-$version-macos-arm64.pkg"
pkgbuild --root "$stage" \
    --identifier dev.daubney.envbao \
    --version "$version" \
    --install-location / \
    "$pkg" >/dev/null
echo "$pkg"
