# Orange Pi 5 appliance tooling

Tooling specific to the Orange Pi 5 Pro AstroDeck appliance (Armbian Trixie
minimal, vendor kernel, boots from microSD; eMMC reserved for write-heavy
data). Hardware/platform background:
`docs/hardware/orange-pi-5-appliance-phase0.md`.

## provision/ — WiFi onboarding hotspot

The shared-image password and automatic hotspot-on-route-loss behavior in the
older WiFi design are superseded by
`docs/superpowers/specs/2026-09-01-platform-security-hardening-execution-design.md`.
Every board must be commissioned before shipment. Commissioning creates
`/data/astrodeck/setup-identity.json` as root-owned `0600` state under a
root-owned `0700` directory and prints a unique 12-character setup password in
three groups. The password must be placed on that device's enclosure (and may
also be encoded in a WiFi QR label). The image contains no fallback password.

The hotspot **AstroDeck-XXXX** and its captive portal at `http://10.42.0.1/`
open only when a persisted one-shot authorization is armed, and stay open for
at most 15 minutes. A reboot, failed setup attempt, or later network loss does
not authorize another hotspot. Three distinct power cycles shorter than 60
seconds within a 180-second window restore the printed factory password and
authorize one new 15-minute WiFi onboarding window. This physical recovery
does not reset application accounts, sessions, captures, plans, or other
configuration. There is no network recovery endpoint.

Zero packages: wpa_supplicant AP mode + systemd-networkd DHCP + Python
stdlib. Runs on the bare Armbian minimal image.

### Two processes, one privilege boundary

Nothing that parses traffic from the hotspot runs as root.

- `astrodeck-provision.service` runs `astrodeck-provision.py run` as the
  `astrodeck-setup` system user (created by `astrodeck-provision.sysusers.conf`).
  It serves the captive portal's HTTP and DNS on `10.42.0.1`, which is the
  only thing it needs the `CAP_NET_BIND_SERVICE` capability for. It cannot
  spawn processes, cannot see `/dev`, cannot reach the systemd or D-Bus system
  sockets, may bind only `80/tcp` and `53/udp`, may talk only to
  `10.42.0.0/24`, and never reads the identity file.
- `astrodeck-provision-broker.socket` owns `/run/astrodeck-provision-broker.sock`
  (`root:astrodeck-setup`, mode `0660`) and activates
  `astrodeck-provision-broker.service`, which runs
  `astrodeck-provision-broker.py broker` as root inside the measured sandbox
  from the design (`CAP_NET_ADMIN CAP_NET_RAW` only, writable paths limited
  to `/run/astrodeck`, `/run/systemd/network`, `/etc/netplan` and
  `/data/astrodeck`). The broker checks the connecting peer's UID against
  `astrodeck-setup` and accepts exactly four operations: `open`, `scan`,
  `join` and `close`. Any other request, any extra field, and any request
  outside an authorized window is refused.

The broker, not the portal, consumes the one-shot authorization, raises the
hotspot, and holds the 15-minute deadline. If the portal dies, hangs or is
killed, the broker tears the hotspot down on its own when the deadline
passes. On a board with no armed authorization the portal asks the broker,
is told no, logs that provisioning remains closed, and exits 0. On a board
that was never commissioned there is no identity for the broker to consult,
the request fails closed, and the portal exits 1 logging that the broker is
unavailable. Neither case opens a radio.

### Install, commission, label

Install into a mounted rootfs (SD card or image). The installer copies both
scripts, the sysusers file and all six units, and enables the portal, the
early boot recorder (in `network-pre.target.wants`, so it runs before any
network decision) and the 60-second healthy-boot timer:

    ./provision/install-to-rootfs.sh /mnt/card-rootfs

After the durable `/data` volume is mounted, commission a unit from the local
factory console. The generated JSON on stdout is the label-printer handoff and
must not be copied to logs or retained as a plaintext fleet inventory. Without
`--ssid` the setup SSID is derived from the WiFi MAC:

    sudo /usr/bin/python3 /usr/local/lib/astrodeck/astrodeck-provision-broker.py \
      commission --ssid AstroDeck-BEEF

Commissioning is exclusive: running it again against the same state fails.
If a local authenticated owner deliberately rotates the active setup password,
the replacement is printed once and the hotspot stays closed:

    sudo /usr/bin/python3 /usr/local/lib/astrodeck/astrodeck-provision-broker.py rotate

The `record-boot` and `clear-boots` modes of the same script are used only by
the recovery units. Do not replace their boot-ID mechanism with a service
restart hook.

### Upgrading a board that is already on WiFi

A board provisioned by the earlier single-process portal keeps its WiFi
configuration; the installer does not touch netplan. What changes is that
nothing can open a hotspot on it again until it has been commissioned: the
new portal finds no armed authorization and exits closed, and the three
short-boots gesture restores a factory credential that only exists after
commissioning. So commission the board as part of the upgrade, over SSH or the
console, and write the printed password on the enclosure before you need it.

### Checks

Host-side tests, no hardware. Run them serially, because provisioning
state and sockets are module globals:

    python -m pytest -p no:xdist orangepi5/tests -q
    server/.venv/bin/python -m pytest -q -p no:xdist \
      security_acceptance/test_orangepi_contract.py \
      --run-security-acceptance -m 'not security_live'

Self-checks of the deployed files:

    /usr/local/lib/astrodeck/astrodeck-provision.py --self-test
    /usr/local/lib/astrodeck/astrodeck-provision-broker.py --self-test

On the board:

    systemctl status astrodeck-provision-broker.socket astrodeck-provision.service
    journalctl -u astrodeck-provision.service -u astrodeck-provision-broker.service

Release still requires the disposable Orange Pi 5 hardware checklist in the
security execution design. Host tests cannot prove actual systemd confinement,
socket ownership, power-cycle timing, AP expiry, or WiFi driver behavior. The
checklist was run on 2026-09-03 with software reboots standing in for power
pulls, and found three defects no host test could see; results and the open
items are in `docs/hardware/orange-pi-5-hardware-gate-2026-09-03.md`.
