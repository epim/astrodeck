# Orange Pi 5 appliance tooling

Tooling specific to the Orange Pi 5 Pro AstroDeck appliance (Armbian Trixie
minimal, vendor kernel, boots from microSD; eMMC reserved for write-heavy
data). Hardware/platform background:
`docs/hardware/orange-pi-5-appliance-phase0.md`.

## provision/ — WiFi onboarding hotspot

First-run flow (spec:
`docs/superpowers/specs/2026-08-08-wifi-provisioning-design.md`): if the board
boots and cannot reach a network within 90 s, it raises a WPA2 hotspot
**AstroDeck-XXXX** (password **astrodeck**; XXXX = last 4 of the WiFi MAC)
with a captive setup page at `http://10.42.0.1/`. Connect from a phone, pick
your WiFi, enter the password; the board writes a netplan config, joins your
network as **astropi**, and the hotspot disappears. If the join fails or the
network later vanishes, the hotspot returns on the next boot cycle.

Zero packages: wpa_supplicant AP mode + systemd-networkd DHCP + Python
stdlib. Runs on the bare Armbian minimal image.

Install into a mounted rootfs (SD card or image):

    ./provision/install-to-rootfs.sh /mnt/card-rootfs

Tests (host-side, no hardware): `python -m pytest orangepi5/tests -q`
Self-check of the deployed file: `astrodeck-provision.py --self-test`
