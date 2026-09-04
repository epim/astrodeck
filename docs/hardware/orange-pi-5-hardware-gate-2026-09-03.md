# Orange Pi 5 hardware gate, 2026-09-03

The disposable-board checklist from workstream A of
`docs/superpowers/specs/2026-09-01-platform-security-hardening-execution-design.md`
(section A5), run against the two-process provisioner on a real Orange Pi 5
Pro. Every number below was read off the board. Raw logs are in
`/data/astrodeck-gate/*.log` on the board and in the evidence bundle
`evidence-2026-09-03.tgz` taken from the same directory.

## Board

Orange Pi 5 Pro, Armbian community 26.8.0-trunk.7 (Trixie), kernel
6.1.115-vendor-rk35xx, Python 3.13.5, wpa_supplicant 2.10, systemd-networkd
and netplan. Wi-Fi only: the wired port has no carrier. The AstroDeck app was
running throughout, closed (401), and its users, config, captures and the
release pointer were checksummed before and after: unchanged.

The board had no `/data`. The design keys everything on `/data` being a mount
point (`ConditionPathIsMountPoint=/data` on every unit, a hard mount check in
the broker), so `/data` was made a bind mount of `/srv/emmc/data` through
fstab. It survived every reboot below. A backup of the previous provisioner,
its units, both netplan files and fstab sits at
`/srv/emmc/astrodeck-preupgrade-20260903-225750`.

## What was run

1. `install-to-rootfs.sh /` on the live root, `systemd-sysusers`,
   `daemon-reload`, enable and start the broker socket and the recovery
   timer. The portal was not started; it opens nothing until commissioned.
2. Static gate: `systemd-analyze verify`, effective unit properties, socket
   ownership, peer refusal from three identities, portal and recovery units
   before commissioning, baseline checksums.
3. Commission, then hotspot windows driven from inside the board by a detached
   script, because the access point takes `wlan0` and drops SSH. A dead-man's
   switch restored the client Wi-Fi whenever a window ended without a join.
4. Closed-portal, rotation, sandbox-write checks; a full 15-minute expiry;
   an ordinary reboot; three short boots with a boot-time driver; cleanup.

Software reboots stood in for power pulls. The recorder keys on the kernel
boot ID, which a reboot changes exactly as a power cut does, so it is the
same code path, but it is not a power-cut test.

## Checklist results

Step 1, unit properties and process state. All six units verify clean.
During a window, `/proc/<pid>/status` showed the portal as UID 986
(`astrodeck-setup`) with `CapBnd` exactly `CAP_NET_BIND_SERVICE`, and the
broker as UID 0 with `CapBnd` exactly `CAP_NET_ADMIN CAP_NET_RAW`; both with
`NoNewPrivs 1` and `Seccomp 2`. The only listeners were the portal's
`10.42.0.1:80` TCP and `10.42.0.1:53` UDP. The socket is
`srw-rw---- root:astrodeck-setup`. Peer refusal: a user outside the group gets
`EACCES` at connect; root (wrong UID) gets a connection reset; from the right
UID, an unknown operation, an extra field, smuggled `argv`, and a 9,000-byte
frame all answer `invalid_request`; `scan` outside a window answers
`window_closed`; `open` before commissioning answers `operation_failed` and
the radio stays in managed mode. PASS.

Step 2, writes. Inside a transient unit carrying the broker's properties,
writes to `/etc`, `/usr/local/lib/astrodeck` and `/root` fail; writes to the
four `ReadWritePaths` succeed. `/data/astrodeck` is `drwx------ root`, the
identity file `-rw------- root`. The portal user cannot read the identity
file or write the directory. PASS, with one finding below about `/run`.

Step 3, distinct passwords. Two temporary commissions produced distinct,
correctly formatted passwords; a second commission on the real path is
refused; symlinked and world-readable identity files are rejected. The
public `astrodeck` string appears in neither secret. PASS. Whether a phone
is refused with the old password is not verified (see below).

Step 4, expiry and no reopen. Window 4 was left unjoined: the broker tore the
access point down 893 seconds after raising it, the portal logged
"portal authorization window expired" and exited 0. A portal start after a
consumed authorization exits 0 with "no setup authorization is armed". After
an ordinary reboot the portal exited closed, no access point was raised, the
recorder logged one short boot at 30 seconds of uptime (before network-pre
and before the portal), and the 60-second timer cleared it. PASS, with the
finding below about the board being left without Wi-Fi after expiry.

Step 5, three short boots. Three reboots at 23, 31 and 15 seconds of uptime
produced three distinct boot IDs; the recorder logged "short boot recorded"
twice and "physical WiFi recovery armed" on the third. The boot-time portal
opened the hotspot with the factory secret (`AP secret == factory_psk:
True`), the driver joined the board back in nine seconds, and the timer then
cleared the counter. Users, sessions, config, captures and the netplan file
were byte-identical before and after. PASS on the mechanism; a real power
pull is not verified.

Step 6, rotation. `rotate` produced generation 2 with `rotated_at` set, the
active secret differing from the factory one, and the portal still closed
afterwards. The expiry window that followed used the rotated secret and not
the printed one. Recovery restored the printed one: generation 3, reason
`recovery`, `active == factory`. PASS.

Step 7, route inventory. Live against the portal: unknown GET paths redirect
to `/` (302), unknown POST paths are 404, a foreign `Host` is 421, a cross-site
`Origin` is 403, `POST /connect` without the CSRF token is 403, chunked
transfer is 400. Neither the served page nor `/status` contained either
secret. The portal source imports no `subprocess` and handles exactly `/`,
`/status`, `/rescan` and `/connect`. PASS.

Step 8, in-place upgrade. The board kept its network through the install;
before commissioning the portal exited 1 with "privileged WiFi broker is
unavailable" and raised nothing; after commissioning it opened exactly one
window. PASS.

## Defects found only here, fixed the same night

All three are fixed in the repo, pinned in
`security_acceptance/test_orangepi_contract.py` and `orangepi5/tests`, and
were re-verified on the board.

1. `SocketBindAllow=ipv4:tcp:80 ipv4:udp:53` is rejected at parse time; the
   directive takes one rule per line. With `SocketBindDeny=any` in force the
   portal could bind nothing. The contract had only checked the text.
2. `systemctl mask --runtime netplan-wpa-wlan0.service` fails with "already
   exists" on every netplan host, because netplan generates that unit into
   `/run/systemd/system`, the exact path a runtime mask has to create. The
   August provisioner ran the same command unchecked; the hardening added a
   result check and turned a long-standing no-op into a fatal error. The
   mask is gone; the stop-and-verify that always did the work remains.
3. The networkd drop-in that gives the hotspot its address was written 0600
   root-only by the hardened atomic writer. networkd reads that directory as
   `systemd-network`, logged "Permission denied", and never assigned
   `10.42.0.1`: the access point beaconed with no address and the portal's
   bind failed with `EADDRNOTAVAIL`. That one file is now published 0644 (it
   holds no secret), and the portal waits up to 20 seconds for the address
   before binding.

## Findings not fixed

- After a window ends without a join, the broker's teardown does not restart
  the client supplicant; only a successful join runs `netplan apply`. On this
  Wi-Fi-only board the expiry left it with no network until the dead-man's
  switch restarted `netplan-wpa-wlan0.service` 44 seconds later. For a factory
  unit with no Wi-Fi yet this is moot; for an in-place upgrade it needs a
  decision.
- `ProtectSystem=strict` on this systemd leaves `/run` writable in the
  broker's namespace (only `/run/systemd/incoming` is read-only). `netplan
  apply` inside the broker needs `/run/netplan` and `/run/systemd/system`
  anyway, so the `/run` entries in `ReadWritePaths` are not an enforced
  boundary here. The broker's real boundary is the four-operation protocol
  and the UID check, as A4 already says.
- The 180-second recovery window is wall-clock time and the board has no
  RTC. With fake-hwclock the clock advanced only seconds across the three
  quick reboots, so the window is measured in stored clock time; an NTP step
  after the first record could expire a sequence early. Three distinct boot
  IDs are still required and the 60-second clear is uptime-based, so this
  changes how forgiving the gesture is, not whether a restart can fake it.
- At boot-time open the scan returned `EAGAIN` and the portal served an
  empty network list; a user would need `/rescan`.
- The portal user can list `/etc/netplan` (directory mode 0755) but not read
  the 0600 files in it.
- The second commission's refusal prints a Python traceback on the factory
  console rather than a one-line message.

## Client association fails on this board's radio (2026-09-04)

Follow-up on the phone-join item. A phone and, separately, this laptop's Intel
BE200 both fail to join the setup hotspot: the client reaches "associating",
waits about ten seconds, and drops with "authentication problem" on the phone
and Windows reason "the specific network is not available" on the laptop. The
scan sees the SSID at strong signal (around -30 dBm), so this is the WPA
association, not signal, not the portal, not DHCP.

It is not the provisioner, the sandbox, the cipher config, or the password:

- The commissioned label password is byte-for-byte the access point's active
  secret, checked on disk.
- The same hotspot raised as plain unsandboxed root, the way the earlier
  single-process portal did, fails a driven client exactly the same way. So
  the privilege separation and the systemd hardening are not the cause. The
  read-only `/proc/sys` warning the sandbox produces
  (`drop_unicast_in_l2_multicast`) is non-fatal and is present or absent
  without changing the outcome.
- During the ten seconds the laptop was associating, the board's own
  `wpa_supplicant` journal in AP mode records nothing: no association, no
  EAPOL, no station event. The radio never handed the client's frames up.
- Every hotspot bring-up logs `brcmfmac: brcmf_vif_set_mgmt_ie: vndr ie set
  error: -52`, the Broadcom firmware rejecting the beacon's management IE.

Conclusion: an AP-mode firmware fault on this board's BCM4345 (brcmfmac,
firmware dated 2017), below everything the provisioner controls. The whole
provisioning stack is verified up to the radio; a real client cannot associate
on this particular board. Options for the appliance, none of them provisioner
work: a newer brcmfmac firmware and nvram for this chip, a different onboard
radio on the shipping hardware, or a small external USB Wi-Fi adapter used only
for the setup access point. This does not affect the board running as a station
on the home network, which works throughout.

## Not verified

- A real power pull, three times. The mechanism was exercised with software
  reboots.
- A phone joining `AstroDeck-A08A` with the printed password, being refused
  with the previous one after rotation, and driving the portal by hand.
- Two physical units with different labels (one board was commissioned into
  temporary paths twice instead).

## Label

The board is commissioned as `AstroDeck-A08A`, generation 3 after recovery.
The printed password was emitted once to the console during commissioning
and is kept root-only in `/data/astrodeck/label.json`; it is not recorded in
this repository.
