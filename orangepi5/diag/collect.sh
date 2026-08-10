#!/bin/bash
# AstroDeck appliance telemetry collector -- TEMPORARY debug scaffolding.
#
# Why this exists: the appliance is headless, has no console, and /var/log lives
# on the eMMC. When the board is unreachable there is no way to ask it anything.
# This writes to the SD ROOTFS (/var/lib is on /), so pulling the card and
# reading it on another machine is a working debug channel until the A-to-A
# cable arrives and MaskROM/serial becomes available.
#
# It also silences the blinking LEDs on every boot (owner request: quiet means
# healthy). That logic is the seed of the real LED daemon in sub-project 8.
#
# Ordering note: the wall clock may be wrong before NTP (this board has no RTC),
# so every sample records BOTH the clock and the monotonic uptime. Order by
# uptime, not by timestamp.

DIAG=/var/lib/astrodeck/diag
LOG="$DIAG/telemetry.log"
MAX_BYTES=$((20 * 1024 * 1024))
INTERVAL=60

mkdir -p "$DIAG" 2>/dev/null

log() { printf '%s\n' "$*" >>"$LOG"; }

boot_id=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)
iface=$(ls /sys/class/net 2>/dev/null | grep -E '^wl' | head -1)
[ -n "$iface" ] || iface=wlan0

# ------------------------------------------------------------------ one-shot
log ""
log "===== BOOT $boot_id ====="
log "collected_at_clock=$(date -Is 2>/dev/null)  uptime=$(cut -d' ' -f1 /proc/uptime)"
log "kernel=$(uname -r)  host=$(hostname)  wifi_iface=$iface"
log "fake_hwclock=$(cat /etc/fake-hwclock.data 2>/dev/null)"

log "--- leds ---"
for d in /sys/class/leds/*; do
    [ -e "$d/trigger" ] || continue
    name=$(basename "$d")
    cur=$(sed -n 's/.*\[\([^]]*\)\].*/\1/p' "$d/trigger" 2>/dev/null)
    maxb=$(cat "$d/max_brightness" 2>/dev/null)
    curb=$(cat "$d/brightness" 2>/dev/null)
    log "led name=$name trigger=$cur max_brightness=$maxb brightness=$curb"
    log "led name=$name available_triggers=$(cat "$d/trigger" 2>/dev/null | tr '\n' ' ')"
    # Silence anything that blinks. Leave default-on (the power indicator) and
    # anything already quiet exactly as it is.
    case "$cur" in
        heartbeat|timer|activity|disk-activity|mmc*|cpu*|panic|backlight)
            echo none >"$d/trigger" 2>/dev/null
            echo 0 >"$d/brightness" 2>/dev/null
            log "led name=$name ACTION=silenced was=$cur" ;;
        *)
            log "led name=$name ACTION=left_alone trigger=$cur" ;;
    esac
done

log "--- mounts ---"
findmnt -n -o TARGET,SOURCE,FSTYPE /var/log 2>/dev/null | while read -r l; do log "mount $l"; done
findmnt -n -o TARGET,SOURCE,FSTYPE /srv/emmc 2>/dev/null | while read -r l; do log "mount $l"; done
log "--- failed units ---"
systemctl --failed --no-legend --plain 2>/dev/null | while read -r l; do log "failed $l"; done
log "--- usb ---"
lsusb 2>/dev/null | while read -r l; do log "usb $l"; done
log "--- kernel messages (wifi/mmc/errors) ---"
dmesg 2>/dev/null | grep -iE 'aic8800|wlan|mmc[0-9]|i/o error|ext4-fs error' \
    | tail -40 | while read -r l; do log "dmesg $l"; done
sync

# ------------------------------------------------------------------ sampling
prev=""
while true; do
    # Rotate rather than fill the card.
    sz=$(stat -c %s "$LOG" 2>/dev/null || echo 0)
    if [ "$sz" -gt "$MAX_BYTES" ]; then
        mv -f "$LOG" "$LOG.1" 2>/dev/null
        log "===== ROTATED ====="
    fi

    up=$(cut -d' ' -f1 /proc/uptime)
    ts=$(date -Is 2>/dev/null)

    link=$(iw dev "$iface" link 2>/dev/null)
    if printf '%s' "$link" | grep -q '^Connected'; then
        bssid=$(printf '%s' "$link" | sed -n 's/^Connected to \([^ ]*\).*/\1/p')
        ssid=$(printf '%s' "$link" | sed -n 's/^[[:space:]]*SSID: \(.*\)$/\1/p')
        sig=$(printf '%s' "$link" | sed -n 's/^[[:space:]]*signal: \(.*\)$/\1/p')
        freq=$(printf '%s' "$link" | sed -n 's/^[[:space:]]*freq: \(.*\)$/\1/p')
        state="assoc"
    else
        bssid=""; ssid=""; sig=""; freq=""; state="DISCONNECTED"
    fi

    ip4=$(ip -4 -o addr show dev "$iface" 2>/dev/null | awk '{print $4}' | head -1)
    gw=$(ip route show default 2>/dev/null | awk '{print $3}' | head -1)
    oper=$(cat "/sys/class/net/$iface/operstate" 2>/dev/null)

    if [ -n "$gw" ] && ping -c1 -W2 "$gw" >/dev/null 2>&1; then pgw=ok; else pgw=FAIL; fi
    if ping -c1 -W2 1.1.1.1 >/dev/null 2>&1; then pnet=ok; else pnet=FAIL; fi

    line="ts=$ts up=$up oper=$oper state=$state ssid=\"$ssid\" bssid=$bssid sig=\"$sig\" freq=\"$freq\" ip=$ip4 gw=$gw ping_gw=$pgw ping_net=$pnet"
    log "$line"

    # When anything material changes, capture what the network stack said about
    # it. These journals live on zram and do not survive a hard pull, so copying
    # them onto the card is the whole point.
    cur="$state|$ssid|$bssid|$ip4|$gw|$pgw|$pnet"
    if [ "$cur" != "$prev" ]; then
        log "CHANGE from[$prev] to[$cur]"
        journalctl -b --since "-${INTERVAL}s" --no-pager -q -n 30 \
            -u wpa_supplicant -u systemd-networkd -u netplan-wpa-wlan0 \
            -u astrodeck-provision 2>/dev/null | while read -r l; do log "  journal $l"; done
        prev="$cur"
    fi

    sync
    sleep "$INTERVAL"
done
