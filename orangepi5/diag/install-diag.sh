#!/bin/bash
# Install the temporary telemetry collector into a MOUNTED appliance rootfs.
#
#   sudo ./install-diag.sh /mnt/sd
#
# Idempotent. Strips CRLF on the way in (these files are edited on Windows).
# Removal: ./install-diag.sh /mnt/sd --uninstall
set -euo pipefail

ROOT="${1:?usage: install-diag.sh <mounted-rootfs> [--uninstall]}"
MODE="${2:-install}"
SRC="$(cd "$(dirname "$0")" && pwd)"

LIBDIR="$ROOT/usr/local/lib/astrodeck/diag"
UNIT="$ROOT/etc/systemd/system/astrodeck-diag.service"
WANTS="$ROOT/etc/systemd/system/multi-user.target.wants/astrodeck-diag.service"

[ -d "$ROOT/etc/systemd/system" ] || { echo "not a rootfs: $ROOT" >&2; exit 1; }

if [ "$MODE" = "--uninstall" ]; then
    rm -f "$WANTS" "$UNIT"
    rm -rf "$LIBDIR"
    echo "uninstalled (collected data under /var/lib/astrodeck/diag left in place)"
    exit 0
fi

mkdir -p "$LIBDIR" "$ROOT/etc/systemd/system/multi-user.target.wants" \
         "$ROOT/var/lib/astrodeck/diag"

sed 's/\r$//' "$SRC/collect.sh" > "$LIBDIR/collect.sh"
chmod 0755 "$LIBDIR/collect.sh"
sed 's/\r$//' "$SRC/astrodeck-diag.service" > "$UNIT"
chmod 0644 "$UNIT"

# Enable by symlink -- systemctl is not available against an offline rootfs.
ln -sf /etc/systemd/system/astrodeck-diag.service "$WANTS"

echo "installed:"
echo "  $LIBDIR/collect.sh"
echo "  $UNIT"
echo "  $WANTS -> /etc/systemd/system/astrodeck-diag.service"
echo "output will land in <rootfs>/var/lib/astrodeck/diag/telemetry.log"
