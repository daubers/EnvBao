#!/bin/sh
# Remove EnvBao installed from the macOS .pkg. Leaves ~/.envbao (your config) untouched.
set -eu
sudo rm -f /usr/local/bin/envbao
sudo rm -rf /usr/local/lib/envbao
sudo pkgutil --forget dev.daubney.envbao >/dev/null 2>&1 || true
echo "EnvBao removed. Your configuration in ~/.envbao was left in place."
