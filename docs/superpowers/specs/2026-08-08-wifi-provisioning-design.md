# WiFi provisioning hotspot for the Orange Pi appliance

**Date:** 2026-08-08. **Status:** approved (approach A).
**Owner request:** boot with no network → board raises a WiFi hotspot with a
captive setup page; user connects from a phone, enters home WiFi credentials;
board joins the network. First piece of the appliance onboarding path.

> **Security supersession (2026-09-01):** The fixed `astrodeck` password and
> automatic hotspot-on-route-loss lifecycle below are historical and must not
> ship. The credential, one-shot authorization, 15-minute window, and physical
> recovery rules in
> `2026-09-01-platform-security-hardening-execution-design.md` replace them.

## Context and constraints

- Target: Orange Pi 5 Pro running Armbian community 26.8 Trixie **minimal**
  (vendor 6.1 kernel), booted from microSD. eMMC is reserved for write-heavy
  data (separate work).
- The image has **no NetworkManager, no hostapd, no dnsmasq** — and the board
  has no network, so nothing can be apt-installed at provisioning time.
- The image does have: **wpa_supplicant** (supports AP mode), **systemd-networkd**
  (built-in DHCP server), **netplan** (renderer networkd, currently matching
  only `e*`/`lan*`/`wan*` — wlan0 unmanaged), **python3.13**.
- WiFi chip is an **AIC8800 (SDIO)**; vendor kernel ships `aic8800_sdio`
  modules and `/usr/lib/firmware/aic8800/`, dtb `rk3588s-orangepi-5-pro.dtb`.
- Code lives in the repo under a new top-level **`orangepi5/`** directory
  (owner decision). Another agent works in this repo concurrently: commits use
  explicit paths only.

## Decision: zero-package stdlib stack (approach A)

AP mode via wpa_supplicant, client DHCP + AP-side DHCP server via
systemd-networkd, portal (HTTP + captive DNS) via Python stdlib. No packages
are installed on the board. Rejected: offline-staged hostapd/dnsmasq debs
(fragile offline dependency closure; fallback if the AIC8800 misbehaves in
wpa_supplicant AP mode) and offline NetworkManager (largest closure, replaces
the image's networking model).

## Superseded product decisions (historical)

- Hotspot security: **WPA2-PSK, fixed password `astrodeck`**.
- SSID: **`AstroDeck-XXXX`**, XXXX = last four hex digits of the wlan0 MAC,
  uppercase.
- AP on 2.4 GHz (channel 6) for universal phone compatibility.

## Components (`orangepi5/provision/`)

| file | role |
|---|---|
| `astrodeck-provision.py` | single-file provisioner, Python stdlib only |
| `astrodeck-provision.service` | systemd unit, enabled at install |
| `install-to-rootfs.sh` | idempotent installer against any mounted rootfs (laptop-side card prep today, image bake later) |
| `../tests/test_provision.py` | pure-function tests (SSID derivation, config emission, YAML escaping) — no hardware |

## Runtime flow (every boot — permanent behavior, not a one-shot)

1. Service starts after `network.target`; waits up to **90 s** for a default
   route (poll `ip route show default` every 3 s). Online → exit 0.
2. Offline → bring wlan0 up, `iw dev wlan0 scan`, cache SSID+signal list.
3. Raise AP: write wpa_supplicant AP config (`mode=2`, RSN/CCMP, frequency
   2437) to `/run/astrodeck/`; start a dedicated wpa_supplicant instance on
   wlan0; write a networkd unit to `/run/systemd/network/` giving wlan0
   `10.42.0.1/24`, `DHCPServer=yes`, DNS emitted as 10.42.0.1;
   `networkctl reload`.
4. Portal: HTTP on :80 (scan picker + manual SSID + password form) and a UDP
   DNS catch-all on :53 answering every A query with 10.42.0.1. OS captive
   probes (`/generate_204`, `/hotspot-detect.html`, …) are redirected to the
   portal so phones auto-open the sheet.
5. On submit: write `/etc/netplan/30-astrodeck-wifi.yaml` (mode 600) with the
   chosen SSID/PSK; reply with a "joining — find me at `http://astropi.local`
   or your router's device list" page; stop the AP supplicant; remove the
   /run networkd unit; `networkctl reload`; `netplan apply`; wait **45 s**
   for a default route.
6. Route appears → write `/var/lib/astrodeck/provision-state.json`
   (ssid, timestamp, result) and exit 0. No route → delete the failed netplan
   file, re-raise the AP, show the failure on the portal ("check password"),
   await another attempt.
7. Any future boot where the configured network is unreachable for 90 s →
   hotspot returns automatically. That is the recovery story (moved house,
   new router).

## Error handling

- `wlan0` absent (driver failure): log loudly, exit 0 — the service must
  never block boot or crashloop. `Restart=no`.
- All transient state in `/run`; only the netplan yaml and the state dir
  persist. Logs to journald via stdout.
- Portal input: SSID/PSK are escaped into YAML double-quoted scalars
  (backslash and quote escaping); PSK length validated (8–63 chars for WPA2,
  or empty for open networks which emit no `password:` key).

## Security notes (v1 scope)

- The fixed WPA2 PSK is a product-onboarding convenience, not a security
  boundary; the home PSK crosses the hotspot link WPA2-encrypted with a
  publicly known key. Acceptable for v1 (backyard, minutes-long exposure);
  a per-unit password or WPA3-SAE is a v2 product decision.
- The portal runs as root (needs :80/:53 and network control). It accepts
  two form fields and writes one file; surface kept minimal. No auth on the
  portal — physical proximity during setup is the trust model (industry
  norm: Chromecast, ASIAIR).

## Testing

- `pytest orangepi5/tests` covers the pure functions in CI (no hardware).
- `astrodeck-provision.py --self-test` compiles configs and renders pages
  without touching the network (usable in CI and on-box).
- Hardware validation (this session): install to card via
  `install-to-rootfs.sh` from WSL → boot board → `AstroDeck-XXXX` appears →
  phone flow joins the LAN → SSH reachable as `astropi`.

## Out of scope (deliberately)

Ethernet provisioning UI, mDNS advertisement, per-unit passwords, TLS on the
portal, integration with the AstroDeck server UI (the service is intentionally
standalone so it can ship on a bare image before AstroDeck itself is
installed), eMMC log layout (separate task).
