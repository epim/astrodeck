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
    astrodeck-recovery-clear.timer \
    astrodeck-radio-reset.service \
    astrodeck-radio-watchdog.service \
    astrodeck-radio-watchdog.timer
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
# The radio watchdog heals a crashed WiFi driver on its own so an appliance
# never needs a customer to unplug it; astrodeck-radio-reset.service is pulled
# in on demand by the watchdog and by the broker, so it is installed but not
# enabled on its own.
ln -sf ../astrodeck-radio-watchdog.timer \
    "$ROOT/etc/systemd/system/timers.target.wants/astrodeck-radio-watchdog.timer"

echo "installed AstroDeck provisioning, physical WiFi recovery, and radio self-heal into $ROOT"

# A live in-place install must reload systemd and restart the persistent broker,
# or a broker already running keeps the OLD code and a fresh socket connection
# reuses it (observed 2026-09-04: an updated ap_up did not run until the broker
# was restarted). Offline installs into a mounted rootfs skip this; the appliance
# ships the code at image-bake time and starts fresh at boot.
if [ "$(readlink -f "$ROOT")" = "/" ] && [ -d /run/systemd/system ]; then
    systemctl daemon-reload || true
    # keep the socket, drop the running broker so the next connection is fresh
    systemctl restart astrodeck-provision-broker.socket 2>/dev/null || true
    systemctl stop astrodeck-provision-broker.service 2>/dev/null || true
    systemctl enable --now astrodeck-radio-watchdog.timer 2>/dev/null || true
    echo "live install: reloaded systemd, restarted the provisioning broker, armed the radio watchdog"
fi
