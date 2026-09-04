#!/bin/bash
# Install the AstroDeck provisioning service into a mounted appliance rootfs.
# Usage: install-to-rootfs.sh /path/to/mounted/rootfs
# Idempotent. Works from WSL against a mounted SD card today, and from image
# bake automation later.
set -euo pipefail

ROOT="${1:?usage: install-to-rootfs.sh /path/to/rootfs}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ -d "$ROOT/etc/systemd/system" ] || { echo "not a systemd rootfs: $ROOT"; exit 1; }

install -d "$ROOT/usr/local/lib/astrodeck" \
           "$ROOT/etc/sysusers.d" \
           "$ROOT/etc/systemd/system/multi-user.target.wants" \
           "$ROOT/etc/systemd/system/network-pre.target.wants" \
           "$ROOT/etc/systemd/system/timers.target.wants"
install -d -m 0700 "$ROOT/data/astrodeck"
for script in astrodeck-provision.py astrodeck-provision-broker.py
do
    sed 's/\r$//' "$HERE/$script" > "$ROOT/usr/local/lib/astrodeck/$script"
    chmod 755 "$ROOT/usr/local/lib/astrodeck/$script"
done
sed 's/\r$//' "$HERE/astrodeck-provision.sysusers.conf" \
    > "$ROOT/etc/sysusers.d/astrodeck-provision.conf"
chmod 644 "$ROOT/etc/sysusers.d/astrodeck-provision.conf"
for unit in \
    astrodeck-provision.service \
    astrodeck-provision-broker.service \
    astrodeck-provision-broker.socket \
    astrodeck-recovery-record.service \
    astrodeck-recovery-clear.service \
    astrodeck-recovery-clear.timer
do
    sed 's/\r$//' "$HERE/$unit" > "$ROOT/etc/systemd/system/$unit"
    chmod 644 "$ROOT/etc/systemd/system/$unit"
done
ln -sf ../astrodeck-provision.service \
    "$ROOT/etc/systemd/system/multi-user.target.wants/astrodeck-provision.service"
ln -sf ../astrodeck-recovery-record.service \
    "$ROOT/etc/systemd/system/network-pre.target.wants/astrodeck-recovery-record.service"
ln -sf ../astrodeck-recovery-clear.timer \
    "$ROOT/etc/systemd/system/timers.target.wants/astrodeck-recovery-clear.timer"

echo "installed AstroDeck provisioning and physical WiFi recovery into $ROOT"
