#!/bin/bash
# Install the AstroDeck provisioning service into a mounted appliance rootfs.
# Usage: install-to-rootfs.sh /path/to/mounted/rootfs
# Idempotent. Works from WSL against a mounted SD card today, and from image
# bake automation later.
set -euo pipefail

ROOT="${1:?usage: install-to-rootfs.sh /path/to/rootfs}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ -d "$ROOT/etc/systemd/system" ] || { echo "not a systemd rootfs: $ROOT"; exit 1; }

install -d "$ROOT/usr/local/lib/astrodeck" "$ROOT/var/lib/astrodeck" \
           "$ROOT/etc/systemd/system/multi-user.target.wants"
sed 's/\r$//' "$HERE/astrodeck-provision.py" \
    > "$ROOT/usr/local/lib/astrodeck/astrodeck-provision.py"
chmod 755 "$ROOT/usr/local/lib/astrodeck/astrodeck-provision.py"
sed 's/\r$//' "$HERE/astrodeck-provision.service" \
    > "$ROOT/etc/systemd/system/astrodeck-provision.service"
ln -sf ../astrodeck-provision.service \
    "$ROOT/etc/systemd/system/multi-user.target.wants/astrodeck-provision.service"

echo "installed astrodeck-provision into $ROOT"
