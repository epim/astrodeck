# AstroDeck appliance: product program charter

**Date:** 2026-08-10. **Status:** decisions taken; sub-project specs pending.
**Owner request:** sell the Orange Pi 5 Pro appliance as a standalone product —
stable, secure, licence-clean, with the features a buyer expects. Nine numbered
requirements (below).

This document is a **charter, not an implementation spec**. It records the
decisions, the findings that changed the request, and the decomposition. Each
sub-project gets its own spec → plan → implementation cycle.

Evidence convention: claims marked **[v]** were verified by reading the cited
file during this survey. Claims marked **[r]** were reported by a survey agent
and are *not yet* independently verified — verify before acting on them.

## The nine requirements

1. All open-source licences included and visible from a Credits screen.
2. Reachable at `astrodeck.lan`; drop all `astropi` references.
3. A default password for first-time web access after WiFi onboarding.
4. Stable, reliable in-app firmware upgrade management, including rollback.
5. Background download, flip between versions, auto-resume an imaging plan.
6. Prevent escalation from `viewer` → admin → the wider system.
7. Meaningful use of the board LEDs; stop the perpetual blue flashing.
8. A design brief for a DC controller HAT (switched 5521 jacks + USB hub).
9. The driver breadth expected of a product supporting many peripherals.

## Owner decisions

| # | Decision |
|---|---|
| D1 | Ship a **flashed OS image**. Accepts the GPL source-offer obligation for the Debian userland. |
| D2 | Appliance ships **closed**: `auth.methods == ['local']` baked into the image. Source and Docker installs keep today's open default. |
| D3 | Driver breadth: keep everything current **and** add INDI and Alpaca. |
| D4 | Update model: **unattended-and-reliable**, serialised quiesce → flip → resume with a stated gap. Not zero-downtime. No containers. |
| D5 | DSS2: **swap for an openly-licensed survey**; pursue STScI permission in parallel, not on the launch path. |
| D6 | Hostname: advertise **both** — mDNS `astrodeck.local` plus DHCP hostname so `.lan` works where the router cooperates. Presented to the customer as `.local`. |
| D7 | HAT downstream USB: **4× USB-A + 2× USB-C**. |
| D8 | Storage: **OS stays on SD**; eMMC carries `/var/log` and `/data`. Captures default to eMMC; NVMe and external USB are optional. Whole-OS-on-eMMC is a later move. |
| D9 | Bootloader stays **unlocked**; the owner keeps root and SSH. Harden the software boundary only. |
| D10 | **Non-redistributable assets are fetched, not bundled.** Anything we may not lawfully convey stays out of the image; the software detects the user's hardware, discloses what it found and why it cannot drive it, and offers to fetch the vendor's asset on the user's behalf with consent. One mechanism for all of them — Player One SDK, QHY, the DSS2 replacement pack, INDI 3rdparty blobs — expressed as data, not per-vendor code. Assets with a real distribution grant (ZWO's verbatim MIT) stay bundled. |

D10 rationale and consequences: this converts the Player One launch blocker from
a legal dead end into a product flow, and it generalises the decision already
taken for DSS2 (D5). It also fixes a silent-failure path — today a missing
vendor library means no backend, no device offered and **no log line**, so a
camera the customer owns simply does not appear. Detection must therefore work
*without* the vendor library (raw USB identity), so the box can name hardware it
cannot yet drive. Two hard constraints on the mechanism: the web application must
never write an executable it then loads (that is the primitive the containment
work exists to remove, so fetch/verify/install goes through the privileged
broker), and fetched assets must survive both an application update and an OS
re-flash — which means they live on `/data`, not in the release directory.

D9 rationale: shipping GPL-3 userland to a consumer makes the appliance a User
Product under GPL-3 §6, so Installation Information is owed the moment anyone
ships secure boot or a locked bootloader. Requirement 6 does not need it — the
threat model is a remote viewer escalating, not the owner holding their own box.
The obligation attaches per unit conveyed, so locking *future* units stays open;
unlocking units already sold does not.

## Findings that changed the request

**Requirement 5 is physically impossible as stated, and its premise is
inverted.** Two instances cannot hold the rig: cameras are exclusive USB
handles, the AM5 is one pyserial handle held for the session, the EAF and CAA
are hidraw handles. Containers change none of it. **[r]** Auto-resume already
exists, is armed by default and was demonstrated on the real rig; what is
missing is the opposite half — `restart_blocker` refuses to apply an update
while a plan runs. Containers would also *remove* self-update (the Dockerfile
never sets `ASTRODECK_INSTALL_ROOT`), native guiding and autofocus (no Rust
stage) and ZWO support, at ~350 MB per retained version against ~9 MB. **[r]**
Realistic flip: 60-90 s floor, 3-6 min typical. State it; never imply seconds.

**`astrodeck.lan` cannot be delivered by the product.** `.lan` has no resolver
and is fulfilled — or not — by the customer's router. `.local` is resolved by
the client OS and ships with the box. No mDNS responder exists in the repo.

**Requirement 3 has nowhere to land today.** Default bind is `0.0.0.0`
(`server/astrodeck/__main__.py:163`) **[v]** and `_active_provider` defaults to
`NoneAuthProvider` (`server/astrodeck/auth/deps.py:41`), whose own docstring
reads *"The open default. Resolves EVERY caller to admin/ALL_CAPS"*
(`auth/providers.py:40-49`) **[v]**. With `methods == []` the UI renders no
password field, so there is no login screen to type a default into. D2 is the
prerequisite for both requirement 3 and requirement 6.

**A universal default password is not shippable.** UK PSTI Act 2022 Schedule 1
§1 prohibits universal default passwords; the EU CRA treats them as a defect.
This also condemns the current hotspot PSK — `AP_PSK = "astrodeck"`,
`orangepi5/provision/astrodeck-provision.py:30` **[v]** — which guards the
moment the customer types their home WiFi password into the portal. Both become
per-device values.

**The appliance does not exist as a buildable artifact.** The repo contains one
`.service` file (the WiFi hotspot). No unit for the server or supervisor, no
udev rules, no usbfs bump, no image bake, no hostname mechanism, no time sync.
**[r]** Eight of nine requirements land on a substrate that must be built first.

**On arm64, ASCOM and NINA both vanish** — ASCOM COM is win32-only by
construction and NINA is a Windows application. That removes the two paths that
made "vendor-neutral" true on Windows. See the driver decision below.

## Defects confirmed during the survey

These are product-blocking and become issues in their own right.

| Defect | Evidence | Effect |
|---|---|---|
| `POST /api/auth/config` never calls `_preserve_auth_secrets` — defined at `:5711`, called only by the sibling remote route at `:5774` | `server/astrodeck/api/app.py:5711,5736,5774` **[v]** | Its docstring promises blank-means-unchanged. The UI echoes the redacted block, so one Save in a token-only deployment sets `admin_token` to `""` with `methods` still `[]` — the box silently flips to open admin. |
| Checksum sidecar matched by suffix alone; `release.yml` publishes five `.sha256` assets from parallel jobs | `server/astrodeck/update/github.py:57-61,94`; `.github/workflows/release.yml:121,176-182` **[v]** | Upload order is a race. A binary's checksum sorting first makes every appliance in the field fail apply. Fails closed; no test covers it. |
| Update commit is not crash-atomic; `probing` is a loop-local in RAM and `set_current` precedes the health verdict | `supervisor/supervisor.py:125-200` **[v]** | A power cut mid-probe — the normal dark-site failure — permanently commits an unproven version while `last_good` still names the old one. One successful `/healthz` commits forever; no stabilization window. |
| Factory reset touches only `CONFIG_DIR` and `CAPTURE_DIR` | `server/astrodeck/factory_reset.py` **[v]** | A customer who sells or returns the box hands over their home SSID and WPA2 passphrase. `reset_auth` also restores `methods` to `[]` — "factory reset" currently means "unauthenticate the box". |
| `write_json_atomic` sets no file mode | `server/astrodeck/persist.py:158` **[v]** | Session secret, relay device token and OIDC secret land at umask default (0644 on stock Armbian) on removable storage. |
| Coordinate frame gated on a backend string: `if getattr(tel, "backend", "") != "alpaca": return False` | `server/astrodeck/hub.py:2291`; `devices/backends/zwo_am5.py:103`; no epoch handling in `devices/lx200.py` **[v]** | Every non-Alpaca mount silently receives J2000 where it may expect JNOW. **Suspected live on the AM5** — ~0.36° in 2026, absorbed by the plate-solve centring loop so nothing looks wrong. Confirm the AM5's epoch convention on the rig. |
| No pinned time source and no clock-confidence gate | `systemd-timesyncd` enabled and `fake-hwclock` running on the reference card **[v]**; nothing in the repo configures or checks either **[v]** | Armbian supplies time sync, so an earlier claim that "no time synchronisation exists" was **wrong** — see the correction below. What is missing is ours: no NTP source is pinned (it falls back to Debian's pool, which must be reachable through the customer's network), and no code gates on clock confidence. Dawn-park, sun watchdog, solar exclusion, resume windows and the polar fit all read one clock with no cross-check, so a wrong clock stays silently self-consistent. |
| The onboarding portal runs **once at boot** and never again | `orangepi5/provision/astrodeck-provision.service` is `Restart=no`; `provision.log` carries one block per boot and none since 2026-08-09 11:48 **[v]** | Once online at boot the provisioner exits. A later WiFi drop, channel change, password change or failed lease renewal leaves the box with no route in and no way to ask for help. |
| The appliance has no stable name, so a DHCP change makes it unfindable | no mDNS responder anywhere in the repo **[v]** | The customer's only handle on the box is an address their router chose and can change at any renewal. This is requirement 2's real justification. |

### The 2026-08-10 incident — and what it did not show

The reference board was unreachable at its last known address. I swept the
`/24`, found five hosts and none of them the Pi, saw no hotspot broadcasting,
and concluded the WiFi had dropped with no recovery. **That conclusion was
wrong, and the instrument was bad.** 254 concurrent pings fired from WSL under
mirrored networking is precisely the probe that yields false negatives under
rate limiting, and I read its silence as evidence.

The SD card settled it. The board had **not** rebooted since 2026-08-09 11:48
and was still writing to the rootfs at 2026-08-10 00:21, a minute or two
before the card was pulled. It was alive and working throughout. What is
actually established is narrower: it was not at its previous address. The
likely explanation is a DHCP change — the appliance did not fail, it became
**unfindable**, which is a stronger argument for the mDNS responder than the
one it replaced.

Recorded because the failure mode generalises: a probe that cannot fail
loudly reads as evidence. Re-run any such sweep sequentially or with
`arp-scan` before drawing a conclusion from a negative result.

### The recurring defect class

The boot-only provisioner is structurally the **third** appearance of one shape
in this project, after the AM5 serial link (5.5 h dead through sunrise) and the
Wanderer filterwheel: *a cached belief standing in for a measurement, so the
flag that should drive recovery is the flag suppressing it.* The 2026-08-10
incident did not demonstrate it — but the gap is real and verified in the unit
file. Treat it as a class-level requirement rather than three bugs: **every
link the appliance depends on needs a liveness measurement and an
unconditional recovery path that does not consult a cached belief.** That
covers WiFi, serial, USB and the relay.

It also settles the shape of requirement 7. A box that cannot reach the network
is by definition the box that cannot report that it cannot reach the network,
so "offline, needs attention" is the **canonical** case for the physical
indicator rather than a nice-to-have.

## Cross-cutting architecture

**The manufactured unit.** A repo-defined image bake, built by CI, producing a
flashable `.img.xz` plus a manifest. Every later requirement installs into it.

**Storage (D8).** Read-mostly on removable, write-heavy on soldered:

- **SD:** boot + rootfs. Written only during a system update.
- **eMMC:** `/var/log`, and `/data` — config, users, session secret, appliance
  identity, network config, plan and session state, frame counters,
  calibration masters. Captures default here.

This buys a property worth designing around: **customer data survives an OS
re-flash.** A dead card is recovered by writing a new one; WiFi, credentials,
profiles and plan state come back. It also fixes factory reset — network config
moves to `/data/network.json` with netplan regenerated from it at boot, so a
card swap preserves WiFi and a factory reset genuinely clears it.

Two bake requirements: mount the eMMC log partition at **`/var/log`**, never
`/var/log.hdd` (`armbian-ramlog` binds `/var/log` over `/var/log.hdd`
unconditionally, silently burying anything mounted there while `ls` looks
right), with a boot-time assertion that the log filesystem is the eMMC; and
address every filesystem by label or PARTUUID, never `mmcblk` number, so the
later whole-OS-on-eMMC move is a build flag rather than a redesign.

**Capture target.** Explicit selection (never "whichever is biggest"),
identified by filesystem label. Absent at run start → refuse with a specific
reason. Lost mid-run → fall back to eMMC, warn on the bus and in the night log,
record per-frame which target received it. A USB drive on a slewing mount will
disconnect; frames must never go missing quietly. The free-space guard is
safety-critical under D8, not a backstop: retention protects calibration
masters and unexported frames, deletes oldest-first, and warns before it acts.

**Two update tiers, one customer-visible version.**

- **Tier 1 — app release.** Existing signed tarball + supervisor, with the
  three confirmed defects fixed and a per-release venv replacing the shared
  one. Small, frequent, no reboot, quiesce-and-resume. This is what flips
  mid-plan.
- **Tier 2 — system image.** A/B rootfs slots with bootloader-managed
  automatic rollback. Delivers kernel, udev, vendor `.so`, INDI packages and
  the onboarding portal itself. Reboot required; daylight only.

Tier 2 is **designed now, sequenced with the eMMC move** (D8) — building slot
machinery against the SD and redoing it for eMMC is waste. The interim system
update is "write a new card", which is acceptable only because of the
data-survival property above. Say so plainly; a one-tier updater is not a
firmware system.

**One privilege boundary.** Every privileged verb — WiFi reconfigure, update
apply, LED, power off, hostname — goes through a single typed broker on a unix
socket with a fixed verb set, no free-form paths and no shell. The server runs
as a non-root `astrodeck` user under a systemd sandbox. **Do not set
`PrivateDevices=yes`** — it hides `/dev/ttyUSB*` and `/dev/bus/usb` and every
camera, mount and focuser dies. Use a `DeviceAllow` allowlist.

Per-unit secrets are generated at first boot and never baked into a shared
image: session secret, hotspot PSK, admin password. A constant in the image
means one forged cookie opens every box ever sold. All `0600`, fail closed if
unwritable.

**Time is a subsystem.** NTP plus persisted `fake-hwclock`, and a
**clock-confidence gate**: until wall-clock time is positively established, the
operations that derive from it refuse rather than compute.

**Compliance is a build step.** The credits generator gains an OS-package
input; CI produces the Debian source mirror from the same bake that produces
the image; `LICENSE` and `THIRD-PARTY-NOTICES` land in all four packaging forms
(they are in none today — a breach of the project's own Apache-2.0 §4(a)
**[r]**); notices become readable without an account. Nothing hand-maintained.

**LED principle (owner).** Quiet means healthy. Solid red for power. The second
indicator is silent unless the customer must act: onboarding needed, update in
progress (do not remove power), failed to start, locate-me. Exception-based,
never status-based. The kernel's default trigger must be set to `none` in a way
that survives reboot, and the writer must live **outside** the server process —
the states worth signalling are exactly the ones where that process is absent.

## Driver strategy (D3)

Adopt INDI as the driver plane, run `indiserver` as a supervised separate
process, speak its protocol over TCP 7624 with an **MIT pure-Python client
(`indipyclient`)**. Keep first-party native drivers for the reference rig and
close that programme to new hardware. Keep the existing Alpaca client. Build the
Alpaca **server** direction later and phased.

The licence folklore is backwards: INDI core and `indi-3rdparty` are
**LGPL-2.1** at repo level, not GPL; only `drivers/auxiliary/*` and
`libs/stream/*` are GPL-2+, in separate executables. Over a socket nothing
links. **The one thing that would relicense AstroDeck is `pyindi-client`** — a
GPL-3-or-later SWIG binding linked into the interpreter — which is also
independently unusable (aarch64 wheels stop at cp312; trixie ships 3.13.5).

Rejected: INDIGO (strict subset organised by protocol dialect; its Alpaca
bridge forces 1×1 binning and omits `MoveAxis`; its BSD-2-Clause grant only
dates from 3.0 in Sept 2024). `open-astro/AlpacaBridge` (near-exact overlap with
drivers we already own; AGPL-3 in a flashed image; bus factor 1; engine under a
competing commercial product). `ceterumnet/AlpacaHub` (dormant; **no ZWO camera
driver**). `AlpacaPi` (commercial use requires written agreement). All **[r]**.

Packaging is the real cost: Debian trixie ships INDI 1.9.9 (≈3 y 8 mo behind),
its `+dfsg` repack strips the 3rdparty tree, and the official PPA publishes for
no Debian suite. AstroDeck becomes INDI's arm64 packager. Budget ~32 MiB for
all core drivers; do **not** bundle `indi-3rdparty-libs` (~195 MiB of vendor
SDKs). **Do not put QHY on a flashed card** — `libqhy` ships prebuilt armv8
binaries with no licence file of any kind. All **[r]**.

Alpaca has **no authentication concept** (its OpenAPI declares `security: []`)
**[r]**. So the server direction ships default-off, on a separate listener never
mounted on the FastAPI app, loopback-bound, with a source-CIDR allowlist,
per-device-type opt-in and a persistent UI indicator. Adding auth would break
every stock client and forfeit the ASCOM name.

Two corrections to earlier survey claims, verified here: a `SafetyMonitor`
device class **does** exist (`devices/base.py:576`) with a fail-closed
`SafetyReading` — what is missing is a *measurement* role (typed temperature,
humidity, dewpoint, wind, sky brightness) alongside it, not a safety verdict.
`CoverCalibrator` also exists (`base.py`); what is missing is a backend. **[v]**

## Decomposition

| # | Sub-project | Covers | Depends on | Size |
|---|---|---|---|---|
| 1 | Appliance image foundation — bake, systemd units, non-root user, udev + usbfs, storage layout, **time subsystem**, free-space/retention guard | prereq for 2,3,4,6,7,9 | — | XL |
| 2 | Naming, discovery, first-boot handoff, **network watchdog** | 2 | 1 | M |
| 3 | Secure-by-default first run + per-device credential | 3 | 1 | M |
| 4 | Privilege containment — app fixes + OS confinement + broker | 6 | 3 | L |
| 5 | Licence compliance for a sold box | 1 | — | L |
| 6 | Update pipeline correctness + rollback that rolls back | 4 | 1 | L |
| 7 | Update during a plan — quiesce, flip, resume | 5 | 6 | L |
| 8 | Board status indicators | 7 | 1 | M |
| 9 | Driver coverage — INDI plane, Alpaca both directions, arm64 vendor binaries | 9 | 1 | XL |
| 10 | DC power + USB hub HAT design brief | 8 | — | S |

5 and 10 have no dependencies and start immediately. Within 9, the
coordinate-frame capability seam lands **before** any INDI mount connects.

## Pending measurements

- **LED inventory on the real board**: node names, `max_brightness`, default
  triggers, which are software-controllable, visibility in an enclosure. The
  vocabulary cannot be designed against a guess.
- **AM5 epoch convention**: slew, read back what the mount reports, compare.
  Settles whether the coordinate-frame defect is live today.
- **arm64 vendor blobs on RK3588**: nothing substitutes for plugging a camera
  into the real board.
- **The previous boot's network events**: `/var/log` lives on the eMMC, so it
  was not on the card. After the next boot, read `/var/log.hdd/` on the board
  for wpa_supplicant and networkd records — that is what shows whether the
  address changed, the link dropped, or neither. Note journald is volatile
  under `armbian-ramlog` (zram), so only what the periodic rsync flushed
  survived the card being pulled from a running system.
