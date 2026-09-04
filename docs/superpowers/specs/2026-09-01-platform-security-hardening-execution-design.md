# Platform security hardening: implementation handoff and acceptance design

**Date:** 2026-09-01. **Status:** implementation-ready design; no production
fixes are made by this document. **Source findings:** `OPEN-003`, `OPEN-005`,
`OPEN-008`, and `OPEN-010` in `SECURITY_REVIEW_FINDINGS.md`.

**Audience:** the lower-reasoning implementation session and its reviewer.
This is intentionally more explicit than a normal design note. Where this
document names a path, function, setting, order, error policy, or acceptance
command, treat it as the selected design rather than reopening the decision.

**Implementation note (2026-09-03):** Workstream A shipped as two processes,
an unprivileged portal and a root broker, rather than the single root unit
A4 first described. A4 and A5 below describe what was built, and the
acceptance contract was extended to pin both units. The Orange Pi hardware
checklist was run against it on 2026-09-03 with software reboots standing in
for power pulls; it found three defects no host test could see, all fixed
the same night. See `docs/hardware/orange-pi-5-hardware-gate-2026-09-03.md`.
The current-state bullets further down describe the tree as it was on
2026-09-01.

## Outcome

The implementation is complete only when all of these are true:

1. No shared Orange Pi setup password exists. A commissioned device has a
   unique printed setup credential; normal network loss/reboot cannot reopen its
   AP; physical recovery re-enables the printed credential once for at most 15
   minutes.
2. On Windows, every config/identity secret is under an exact protected DACL.
   A DACL, owner, reparse-point, or filesystem failure aborts before any socket
   listens and is never converted into a default/open configuration.
3. The supported server and relay entrypoints refuse to listen as root,
   Administrator, LocalSystem, or another elevated token. Their supported
   containers have read-only roots, zero capabilities, no-new-privileges,
   bounded process counts, and only explicitly writable data/tmp mounts.
4. The production deployment exposes nginx only. nginx terminates TLS/WSS,
   rejects unknown hosts and spoofed forwarding metadata, applies body/header/
   time/connection/rate limits, never retries commands, and logs no query,
   cookie, authorization, or token value.
5. `security_acceptance/` is green in every applicable lane without weakening
   or skipping an assertion, and the Orange Pi hardware checklist is witnessed
   on a disposable lab board.

Tests are evidence that the selected controls behave as designed; they are not
a proof that no exploit exists. Independent penetration testing is still a
release gate for the public relay, updater, onboarding flow, and ownership
transfer.

## How the implementation session must use this handoff

- Preserve the dirty working tree. Existing modifications and untracked review
  files belong to the owner. Do not reset, clean, rewrite, or bundle unrelated
  changes.
- Do not edit `security_acceptance/` merely to make it green. If a platform API
  forces a different seam, change this design and the tests together in a
  separately reviewed commit while preserving the same observable property.
- Implement one workstream at a time in the order below. Run its focused tests
  before moving on. Do not combine security behavior and unrelated refactors.
- Never treat an unavailable platform as a pass. A Windows skip requires a
  Windows run; a Docker skip requires a Linux Docker run; static systemd checks
  require an Orange Pi run.
- Keep secrets out of command lines, environment dumps, process listings,
  exceptions, access logs, screenshots, CI artifacts, and test failure output.
- Stop and report instead of adding a permissive fallback. In these four areas,
  inability to establish the control is a startup/commissioning failure.

Recommended implementation slices:

1. Orange Pi identity/recovery and host tests.
2. Windows private-path substrate, persistence integration, and Windows tests.
3. Elevated-runtime interlock, image/Compose hardening, and bootstrap docs.
4. Explicit proxy trust, nginx templates/stack, and HTTPS/WSS integration tests.
5. CI lanes, documentation reconciliation, target evidence, and final review.

## Current-state facts the executor must not rediscover

- The Orange Pi portal already has CSRF, a 4 KiB advertised-body cap, input
  validation/escaping, a join lock, bounded handler slots, private file modes,
  and a partial systemd sandbox. It still has `AP_PSK = "astrodeck"`, logs that
  value, automatically opens after ordinary route loss, and has no per-unit
  commissioning/recovery state.
- The 2026-08-08 Wi-Fi design explicitly accepted the fixed password. That
  paragraph is superseded by this document.
- The owner-approved 2026-08-12 recovery design says both “generated at first
  boot” and “printed on the enclosure.” A sealed headless device cannot reveal a
  post-shipment random value. This document resolves it: factory commissioning
  is the device's first boot and happens before label printing and shipment.
- The appliance bake design selects the SoC serial for stable identity and says
  per-unit state belongs on durable `/data`, not the SD-card root filesystem.
  Do not derive a secret from the serial or MAC.
- `server/astrodeck/persist.py::harden_private_file()` is a Windows no-op and
  there is no strict server private-directory helper. The findings document's
  claim that server private directories are already forced to `0700` is
  inaccurate and must be corrected when the fix lands.
- Sensitive Windows data includes `astrodeck.json` and `.bak`, `users.json` and
  `.bak`, `session_secret`, auth/relay/update/alert credentials, precise site
  location, and credential-bearing driver/profile extras.
- Both Dockerfiles already declare named non-root users, but their primary GIDs
  are not pinned. Compose publishes 8800 and lacks read-only, capability,
  no-new-privileges, tmpfs, and PID controls. The Docker guide recommends
  `privileged: true` for USB.
- The Compose-required `ASTRODECK_TOKEN` is not a working SPA bootstrap: the raw
  transport gate blocks `/api/auth/methods` and `/auth/token`, the SPA does not
  attach the raw value, and token exchange reads persisted `auth.admin_token`,
  not the environment variable. Do not preserve that path in production.
- Both auth cookie helpers parse raw `X-Forwarded-Proto`, while Origin validation
  uses the ASGI scope scheme. Uvicorn proxy trust is implicit. Relay rate limits
  key `scope.client`; without exact proxy trust all clients collapse onto nginx,
  while wildcard trust lets a client forge its address.
- Existing focused security tests were green before this design (`198 passed,
  2 skipped` in the recorded run). The new acceptance suite is intentionally
  red until these architectural items are implemented.

## Threat model and trust boundaries

The attacker may be within Wi-Fi range; another non-admin Windows user; an
unauthenticated network peer sending slow, oversized, cross-origin, brute-force,
WebSocket, Host, or forwarding-header traffic; code executing after an app or
driver vulnerability inside a container; or an operator who accidentally runs
the service elevated.

Trusted here are physical access to the enclosure/power cable; the dedicated
runtime OS identity; on Windows, that identity plus LocalSystem and
BUILTIN\\Administrators; the exact nginx peer configured for Uvicorn; and the
host/container administrator. These controls do not protect against an already
compromised administrator/root/kernel/container daemon, offline disk access, or
the trusted relay operator.

---

## Workstream A: Orange Pi commissioning and AP authorization

### A1. Identity and credential lifecycle

The shared image contains no usable AP credential. Per-unit commissioning runs
on the device before its label is printed. Persist one identity at:

```text
/data/astrodeck/setup-identity.json
```

The directory is root-owned `0700`; the file is root-owned `0600`. Every unit
and the broker socket carry `RequiresMountsFor=/data` and
`ConditionPathIsMountPoint=/data`, and the broker refuses to start if the
durable mount is absent.
Do not create an interim second source of truth under `/var/lib/astrodeck`.

Schema version 1 contains at least:

```json
{
  "schema": 1,
  "setup_ssid": "AstroDeck-BEEF",
  "factory_psk": "<three groups of four>",
  "active_psk": "<three groups of four>",
  "generation": 1,
  "commissioned_at": 1788288000.0,
  "rotated_at": null,
  "ap_authorization": {
    "armed": true,
    "reason": "factory",
    "armed_at": 1788288000.0
  },
  "short_boots": {"boot_ids": [], "first_at": null}
}
```

The password format is three four-character lowercase-alphanumeric groups, for
example `rrxk-sl3a-qatn`. Generate every character with `secrets.choice`; never
derive it from serial/MAC or accept it through argv/environment. The local
commissioning station reads the returned value, prints password plus Wi-Fi QR,
and rejects duplicates. It must not retain a central plaintext inventory unless
the owner separately approves that secret-management system.

Expose these testable seams in `astrodeck-provision-broker.py` (the root
broker; the unprivileged portal never reads identity state):

```python
generate_setup_password() -> str
commission_setup_identity(path, *, ssid: str, now: float | None = None)
load_setup_identity(path)
rotate_setup_password(path, *, now: float | None = None) -> str
record_short_boot(path, *, boot_id: str, now: float | None = None) -> bool
clear_short_boot_counter(path) -> None
consume_ap_authorization(path, *, now: float | None = None) -> bool
```

Commissioning is exclusive-create and returns identity for the label printer;
a second call raises `FileExistsError`. Loading rejects missing, malformed,
wrong-version, weak/default, insecure-mode, wrong-owner, symlink, or non-regular
state. There is no fallback credential. Rotation changes only `active_psk`,
increments generation, and is allowed only where the replacement is displayed
to an authenticated local owner or commissioning operator. It does not silently
arm the AP. `factory_psk` remains the printed physical-recovery credential.

### A2. One-shot AP and physical recovery

Factory finalization arms one AP authorization. The broker's `open` operation
(`BrokerState.open_window`) atomically consumes it before any AP/network
mutation; the portal's `main_run()` only asks, and is told closed or open.
Consumption survives crash/reboot, so an interrupted setup does not
automatically reopen.

Once consumed, the portal is available for at most
`PORTAL_LIFETIME_S = 900`, measured with `time.monotonic()` inside the broker,
which tears the AP down at the deadline whether or not the portal is still
alive. Every portal exit path (success, timeout, signal, handler/DNS/network
failure) shuts HTTP and DNS in `finally` and sends `close`; the broker then
removes wpa_supplicant and transient networkd state. Ordinary route loss on a
commissioned device never authorizes an AP.

Observed 2026-09-03: that teardown does not restart the client supplicant;
only a successful join runs `netplan apply`. A window that expires or closes
without a join leaves an already-joined, Wi-Fi-only board without a network
until reboot. Moot for a factory unit; an in-place upgrade needs a decision,
either restart `netplan-wpa-<iface>.service` on close or document the reboot.

Physical recovery uses the approved three short power cycles:

- an early oneshot records `/proc/sys/kernel/random/boot_id` on `/data`;
- one boot ID cannot increment twice, so restarting a service is not physical;
- `SHORT_BOOT_MAX_UPTIME_S = 60` and `RECOVERY_SEQUENCE_WINDOW_S = 180` are
  constants, not configuration knobs exposed over the network;
- the boot recorder appends a new boot ID only while the persisted sequence is
  no older than 180 seconds; a stale/malformed sequence is cleared and starts
  again with the current ID;
- `astrodeck-recovery-clear.timer` starts on every boot and, after 60 seconds of
  continuous uptime, calls `clear_short_boot_counter()`; therefore an ordinary
  boot that remains healthy cannot accumulate toward recovery;
- three distinct recorded boots within the 180-second sequence window restore
  `active_psk = factory_psk`, increment generation, and arm one recovery AP
  window;
- Wi-Fi, plans, captures, and ordinary configuration remain intact.

Observed 2026-09-03: `first_at` and `now` are wall-clock times and the
appliance has no RTC. With fake-hwclock the clock advanced only seconds
across three quick reboots, so the 180-second window is measured in stored
clock time, and an NTP step after the first record could expire a sequence
early. Three distinct boot IDs are still required and the 60-second clear is
uptime-based, so this changes how forgiving the gesture is, not whether a
service restart can fake it.

This slice is deliberately **Wi-Fi onboarding recovery**, which is what
OPEN-003 requires. It does not reset AstroDeck users, change the application
claim state, or invalidate application sessions. The 2026-08-12 product design
describes a broader application-credential reset through an unimplemented dirty
bit and also says recovery creates a new password that a sealed headless device
cannot reveal. Do not invent an untested cross-service hook here. That broader
ownership/account recovery needs its own reconciled design and acceptance suite;
this document supersedes its recovery row only for the OPEN-003 implementation.

The MaskROM button is not reused. No HTTP/API/WebSocket route triggers recovery
or returns the setup credential.

In the broker, change `ap_up` to `ap_up(ssid, psk)` and pass the loaded active
secret. Delete
`AP_PSK`, every fixed fallback, and every log interpolation of a password/PSK.
Diagnostics may name a missing/invalid credential but never its value or state.

### A3. Private writes and parser regression requirements

Use one strict atomic writer for identity, authorization, provision state,
netplan, and wpa material:

1. verify/repair a root-owned `0700` parent without following a link;
2. create an unpredictable same-directory temp file;
3. `fchmod(0600)`, write, flush, and `fsync`;
4. `os.replace` over a verified non-symlink target;
5. `fsync` the parent; and
6. propagate chmod/fsync/replace errors for credentials/authorization.

A failed netplan write keeps the AP and reports a secret-free error; never apply
a partial file. A failed identity/authorization write means no AP.

Retain the already-fixed portal controls and add real loopback/raw-socket tests:

- missing/wrong CSRF on `/connect` and `/rescan` is 403 with zero scan/join/
  thread/state effects; GET `/rescan` stays inert;
- require one canonical unsigned decimal `Content-Length`; missing is 411;
  malformed, signed, negative, comma-joined, or duplicate is 400;
- reject every `Transfer-Encoding`, including chunked and TE+CL, with 400/501;
- reject more than 4096 bytes before reading; short/timeout/invalid UTF-8/
  too-many-fields closes the connection so unread bytes cannot be smuggled;
- serve a token-bearing page only for the portal Host; reject foreign Host and
  cross-site Origin/Fetch Metadata; and
- parameterize `BoundedHTTPServer(max_workers=N)`, set `request_queue_size`
  before its superclass constructor, acquire before spawning/reading, and prove
  every error path releases exactly once.

### A4. Privilege separation: unprivileged portal, root broker

As implemented (2026-09-02..03) this workstream went one step past the design
as first written. The original A4 kept a single root unit and admitted that its
sandbox "is not an authorization boundary against root's systemd D-Bus
access". The shipped design takes root away from the process that parses
hostile traffic instead of sandboxing it harder:

- `astrodeck-provision.service` runs `astrodeck-provision.py run` as the
  `astrodeck-setup` system user (declared in
  `astrodeck-provision.sysusers.conf`). It serves the portal's HTTP and DNS on
  `10.42.0.1`. It has `CapabilityBoundingSet=` and `AmbientCapabilities=` of
  exactly `CAP_NET_BIND_SERVICE`, `PrivateDevices=yes`, `ProcSubset=pid`, no
  `ReadWritePaths`, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`,
  `SocketBindDeny=any` with `SocketBindAllow=ipv4:tcp:80 ipv4:udp:53`,
  `IPAddressDeny=any` with `IPAddressAllow=10.42.0.0/24`, `@privileged` added
  to the syscall deny list, and `InaccessiblePaths` covering
  `/run/systemd/private`, `/run/dbus/system_bus_socket`, `/run/systemd/system`
  and `/etc/systemd/system`. The script imports no `subprocess` and contains
  none of the strings `systemctl`, `networkctl`, `netplan`, `wpa_supplicant`,
  `/etc/netplan`, `/run/systemd/network` or `setup-identity.json`. The
  acceptance suite pins all of this.
- `astrodeck-provision-broker.socket` listens on
  `/run/astrodeck-provision-broker.sock` as `root:astrodeck-setup` mode `0660`
  (`Accept=no`, `Backlog=8`, `RemoveOnStop=yes`) and activates
  `astrodeck-provision-broker.service`, which runs
  `astrodeck-provision-broker.py broker` as root under the sandbox below. The
  portal unit `Requires=` the socket, so the portal cannot start without it.

Root broker unit baseline (the former single-unit baseline, with binding moved
to the portal and the writable set measured):

```ini
[Unit]
RequiresMountsFor=/data
ConditionPathIsMountPoint=/data
Requires=astrodeck-provision-broker.socket

[Service]
User=root
Group=root
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ProtectProc=invisible
RuntimeDirectory=astrodeck
RuntimeDirectoryMode=0700
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
RestrictNamespaces=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
SystemCallArchitectures=native
SystemCallFilter=~@clock @cpu-emulation @debug @module @mount @obsolete @raw-io @reboot @swap
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK AF_PACKET
ReadWritePaths=/run/astrodeck /run/systemd/network /etc/netplan /data/astrodeck
KillMode=control-group
LimitCORE=0
TasksMax=64
LimitNOFILE=128
```

Do not set `PrivateDevices` on the broker; rfkill may need device access.
Include `AF_PACKET`, which wpa_supplicant may need. `ReadWritePaths` is the
exact measured set, not a prefix, and `/run` as a whole is never granted. Trace
`netplan apply` and its children on target before widening it.

Broker protocol. One connection carries one request: a single JSON object,
newline terminated, at most 8 KiB; the response is one JSON object, at most
64 KiB. The broker reads the peer's UID with `SO_PEERCRED` and serves only the
`astrodeck-setup` UID. `version` must be `1`; `operation` must be one of
`open`, `scan`, `join`, `close`; the field set must be exactly the one that
operation expects (`join` adds `ssid`, `psk`, `sae`), validated with the same
SSID/PSK rules the portal applies. Responses are `{"ok": true, ...}` or
`{"ok": false, "error": "invalid_request" | "window_closed" |
"operation_failed"}`; no other detail crosses the socket. The broker also
verifies the listener systemd handed it: exactly one descriptor, an `AF_UNIX`
stream bound at the expected path, named `provision-broker`.

Authorization ownership. `open` calls `consume_ap_authorization()` under the
identity lock, raises the AP, returns the setup SSID, a bounded scan and the
remaining seconds, and starts a `PORTAL_LIFETIME_S = 900` monotonic deadline
inside the broker. A watcher thread tears the AP down at the deadline whether
or not the portal is alive. `open` during an active window returns the
remaining time and consumes nothing. `scan`, `join` and `close` outside a
window answer `window_closed`. On the portal side `main_run()` asks `open`;
when told `authorized: false` it logs that provisioning remains closed and
exits 0; otherwise it serves HTTP and DNS for `remaining_s`, and every exit
path shuts both servers in `finally` and sends `close`. The broker's own
deadline is the fail-safe if that message never arrives.

Remaining risk. The broker still runs as root, and its sandbox is still not an
authorization boundary against root's own systemd and D-Bus authority. What
changed is the surface in front of it: hostile HTTP and DNS is parsed by a
process that cannot reach those sockets, and the root process accepts four
fixed operations from one UID. A defect in the broker's own parsing of `iw`,
`wpa_supplicant`, `networkctl` or `netplan` output would still be a root
defect. Observed 2026-09-03: on Armbian Trixie's systemd `ProtectSystem=strict`
leaves `/run` writable in the broker's namespace, and `netplan apply` inside
the broker needs `/run/netplan` and `/run/systemd/system` anyway, so the
`/run` entries in `ReadWritePaths` are not an enforced boundary on this
platform.

### A5. Files and acceptance

Modify the portal script, its service, the rootfs installer, the Orange Pi
README, and the conflicting appliance specs. Add
`astrodeck-provision-broker.py`, `astrodeck-provision-broker.service`,
`astrodeck-provision-broker.socket`, `astrodeck-provision.sysusers.conf`,
`astrodeck-recovery-record.service`, `astrodeck-recovery-clear.service`, and
`astrodeck-recovery-clear.timer`. The installer copies both scripts and the
sysusers file, installs all six units, and enables the portal in
`multi-user.target.wants`, the record service in `network-pre.target.wants`
(it must run before the portal and before any network setup decision), and
the timer in `timers.target.wants`. Identity, authorization, recovery and the
`commission`, `rotate`, `record-boot` and `clear-boots` commands all live in
the broker script; the portal script has only `run` and `self-test`. Add
`orangepi5/tests/conftest.py`, `test_provision_credentials.py`,
`test_provision_http.py`, `test_provision_files.py`, and
`test_provision_unit.py`.

Portable gate:

```bash
python -m pytest -q -p no:xdist orangepi5/tests
python -m pytest -q -p no:xdist security_acceptance/test_orangepi_contract.py \
  --run-security-acceptance -m 'not security_live'
```

Disposable Orange Pi 5 hardware gate:

1. `systemd-analyze verify` and effective properties pass for all three units;
   `/proc/$PID/status` shows the portal running as `astrodeck-setup` with
   no-new-privileges, seccomp and a capability bound of exactly
   `CAP_NET_BIND_SERVICE`, and the broker as root with exactly
   `CAP_NET_ADMIN CAP_NET_RAW`, while real AP/scan/join/retry works. The
   socket is `root:astrodeck-setup` `0660`; a connection from any other UID
   and a request outside the four operations are refused.
2. Outside writes fail; required writes work; files are root:root `0600` and
   directories `0700`.
3. Two units have different printed passwords; `astrodeck` opens neither.
4. AP expires; reboot or LAN loss does not reopen it.
5. Three real short cycles reopen once using the printed credential and preserve
   Wi-Fi/data/config and application accounts/sessions unchanged.
6. Rotation invalidates old active password; recovery restores printed value.
7. Route inventory/network probing finds no recovery operation.
8. A board already joined to WiFi and upgraded in place keeps its network,
   shows the portal exiting closed before commissioning, and opens exactly
   one window after `commission` plus a label.

Record commands, properties, timestamps, and observed results in the release
artifact. A desktop test cannot substitute for this gate.

Run 2026-09-03 on a disposable Orange Pi 5 Pro: steps 1 to 8 pass on the
mechanism, with software reboots in place of power pulls and no phone join;
those two remain unverified. Artifact:
`docs/hardware/orange-pi-5-hardware-gate-2026-09-03.md`.

---

## Workstream B: Windows private DACL enforcement

### B1. Exact DACL and owner policy

Add `server/astrodeck/windows_acl.py`, using only stdlib/ctypes. It must be
safely importable off Windows without touching `ctypes.WinDLL`.

Use a protected DACL with three allow ACEs and no deny ACEs:

```text
file:      D:P(A;;FA;;;<runtime-user-sid>)(A;;FA;;;SY)(A;;FA;;;BA)
directory: D:P(A;OICI;FA;;;<runtime-user-sid>)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)
```

ACE order may be canonicalized, but trustees/rights are exact. `P` and
`PROTECTED_DACL_SECURITY_INFORMATION` disable inherited broad ACEs. Directory
`OI|CI` causes new temp, backup, and final children to inherit the narrow ACL.
Do not grant Everyone, Authenticated Users, BUILTIN\\Users, Guests, or CREATOR
OWNER.

The existing owner must be the process token user, SYSTEM, or
BUILTIN\\Administrators. Reject any other owner because it can rewrite the
DACL. Preserve a trusted owner, group, SACL/integrity label, and descriptor
parts outside the DACL. Newly created private directories are owned by the
process user.

This isolates other standard accounts. It does not protect secrets from
Administrators, SYSTEM, same-account malware, or offline disk access.

### B2. Win32 implementation contract

Expose:

```python
class PrivateAclError(RuntimeError): ...
build_private_sddl(user_sid: str, *, directory: bool) -> str
current_user_sid() -> str
validate_acl_filesystem_name(name: str) -> None
require_acl_capable_filesystem(path: Path) -> None
harden_private_path(path: Path, *, directory: bool | None = None) -> None
```

Use the process token, not a possibly impersonated thread token:

- `OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY)`
- two-call `GetTokenInformation(TokenUser)`
- `ConvertSidToStringSidW`
- `ConvertStringSecurityDescriptorToSecurityDescriptorW`
- `GetSecurityDescriptorDacl`
- `CreateDirectoryW` with `SECURITY_ATTRIBUTES` for secure-at-creation dirs
- `CreateFileW` for existing file/directory handles
- `GetFileInformationByHandleEx(FileAttributeTagInfo)`
- `GetVolumePathNameW` plus `GetVolumeInformationW` before using a config root
- `GetSecurityInfo` for owner validation
- `SetSecurityInfo`
- `LocalFree` and `CloseHandle` on every path

Open existing paths with `READ_CONTROL | WRITE_DAC`, share read/write/delete,
`OPEN_EXISTING`, `FILE_FLAG_OPEN_REPARSE_POINT`, and
`FILE_FLAG_BACKUP_SEMANTICS`. Inspect the opened handle and reject every
reparse point before changing security or reading bytes. `Path.is_symlink()`
does not cover junctions/mount-point reparse objects. Optionally reject secret
files with multiple hard links.

Pass `DACL_SECURITY_INFORMATION |
PROTECTED_DACL_SECURITY_INFORMATION` to `SetSecurityInfo`. Never pass a null
DACL; Windows interprets it as full access. `SetSecurityInfo` returns an error
code directly—do not use `GetLastError` for that call.

Do not use `icacls`, PowerShell, pywin32, `SetNamedSecurityInfoW`, or a
best-effort subprocess fallback. They add localization/dependency problems or
reintroduce path-based reparse races.

The supported local filesystems are exactly NTFS and ReFS (case-insensitive).
`validate_acl_filesystem_name()` rejects empty, FAT, FAT32, exFAT, and every
unknown name; expanding that allow-list requires a live ACL-persistence test on
the new filesystem. `require_acl_capable_filesystem()` resolves the containing
volume even when the final file does not exist. A security descriptor API call
merely succeeding on a filesystem that discards or synthesizes ACLs is not
enough.

### B3. Persistence ordering and failure semantics

Add `ensure_private_dir(path)` to `server/astrodeck/persist.py`. On POSIX it
creates/repairs `0700`; private files remain `0600`. On Windows it invokes the
handle-based DACL layer. Also expose
`secure_private_tree(root: Path) -> None`, which validates the filesystem,
secures the root before traversal, rejects every reparse point, and repairs the
known config descendants without following links. Keep generic `ensure_dir`
for non-secret paths.

For every private write:

1. secure the parent before creating anything;
2. validate/harden existing primary and backup;
3. create unpredictable `mkstemp` inside the narrow directory;
4. write/fsync, close, then harden the staging file;
5. atomically replace;
6. harden/verify the final file; and
7. harden every backup. If backup hardening fails, remove the new backup and
   propagate the security error.

`PrivateAclError` (or a cross-platform `PrivatePermissionsError`) inherits
`RuntimeError`, not `OSError`, `PermissionError`, or `ValueError`. Existing
`read_json_or`, ConfigStore, and session-secret code catches ordinary I/O/parse
failures. An ACL failure must escape instead of becoming a missing config and
open default.

Secure the known config tree before its first read, without following links:

- before `_security_banner()` in `_cmd_run`;
- before user-store access in `create_admin`;
- first in FastAPI lifespan, outside broad auth/provider exception handlers, so
  direct ASGI startup fails closed;
- after packaged environment paths are established; and
- defensively in private stores/writers.

The packaged entrypoint calls `secure_private_tree(config_dir)` immediately
after establishing `ASTRODECK_CONFIG_DIR` and before its generic directory loop
or any status/path output. `_cmd_run` and `_cmd_create_admin` call it before
their first config/user read. `_lifespan` calls it as its first executable
statement, before the first broad `try`. These repeated calls are intentional
idempotent defense in depth, not work to deduplicate.

On error, print one actionable path/filesystem message and exit nonzero before
listening. Require NTFS/ReFS or another persistent-ACL filesystem; reject
FAT/exFAT config storage.

The default binary path is `%LOCALAPPDATA%\\AstroDeck`; source runs default to
`server/config`; the supervisor uses its configured install-root config. Apply
the same contract to all. A Windows service explicitly selects a config path
owned by its dedicated standard service account. Never run it as Administrator
or LocalSystem, and never pre-create service config as a different elevated
identity.

Also protect the Windows supervisor/install/current-release root from broad
write access; otherwise another account can replace executable code even with a
perfect config DACL. A restricted service SID is a separate future design; do
not enable one until its SID is included in the ACL.

### B4. Files and acceptance

Modify `persist.py`, config/user/session initialization, the CLI, FastAPI
lifespan, `packaging/entry.py`, Windows deployment docs, and the selected
Windows CI command. Merely adding a test will not run in that selected CI lane.
Add `windows_acl.py`, `server/tests/test_windows_private_acl.py`, and mocked
cross-platform orchestration tests.

Portable and live gates:

```powershell
server\.venv\Scripts\python.exe -m pytest -q -p no:xdist `
  security_acceptance\test_windows_acl_contract.py `
  --run-security-acceptance -m "not security_live"

server\.venv\Scripts\python.exe -m pytest -q -p no:xdist `
  security_acceptance\test_windows_acl_contract.py `
  --run-security-acceptance --run-security-live
```

The live acceptance file has an independent ctypes reader. Do not replace it
with a production inspection helper: a broken setter and verifier could agree.
Add normal tests for inherited Everyone-full-control legacy files, unexpected
owner, injected API errors, Unicode paths, unchanged reparse targets, session
secret, primary/backup, and failure before lifespan yield.

Run once as an interactive standard user and once as the exact service identity
on NTFS. Confirm a second standard account cannot read. Only the symlink test
may skip for missing symlink privilege; all other DACL checks are mandatory.

---

## Workstream C: elevated-runtime refusal and container isolation

### C1. Defense in depth at process entrypoints

Add `server/astrodeck/runtime_security.py` and
`relay/relay/runtime_security.py` with the same small testable seams. The relay
image is standalone and does not contain the `astrodeck` package, so it must not
import the server copy:

```python
class PrivilegeDetectionError(RuntimeError): ...
is_elevated_runtime() -> bool
require_unprivileged_runtime(component: str) -> None
```

On POSIX, `is_elevated_runtime()` checks effective UID 0. On Windows, it queries
`TokenElevation` on the process token and treats an elevated token or
LocalSystem as elevated. Unsupported platforms and API/query failures raise
`PrivilegeDetectionError`; `require_unprivileged_runtime()` propagates that
failure instead of guessing. Server and relay startup therefore fail closed if
privilege cannot be determined.

Call the guard before importing/building ASGI, reading secrets, or constructing
Uvicorn in both `server/astrodeck/__main__.py::_cmd_run` and
`relay/relay/server.py::main`. Do not add a production escape hatch.
`create-admin` is non-listening and may remain separately callable; supported
container bootstrap runs it as UID/GID 10001 so it cannot create root-owned
state.

This does not sandbox native drivers. It prevents an installation mistake or
`docker run --user 0` from turning a future app exploit directly into root.

### C2. Bare-metal Linux and Pi service identities

Add complete reference units at `deploy/systemd/astrodeck.service` and
`deploy/systemd/astrodeck-relay.service`. Do not use `DynamicUser`: AstroDeck
needs stable ownership of captures/config and optional membership in selected
device groups. Installation creates locked system accounts with no login shell:

```bash
useradd --system --user-group --home-dir /var/lib/astrodeck \
  --create-home --shell /usr/sbin/nologin astrodeck
useradd --system --user-group --home-dir /var/lib/astrodeck-relay \
  --create-home --shell /usr/sbin/nologin astrodeck-relay
```

The installer creates config/capture directories as the corresponding identity
with `0700` directories and `0600` secrets. The home service binds
`127.0.0.1:8800` behind host nginx and the relay binds `127.0.0.1:8080`; no
bare-metal reference publishes Uvicorn directly.

Both units set at least `UMask=0077`, `NoNewPrivileges=yes`,
`ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, empty
`CapabilityBoundingSet=`/`AmbientCapabilities=`, kernel/control-group/clock/
hostname protections, `RestrictSUIDSGID=yes`, `LockPersonality=yes`,
`MemoryDenyWriteExecute=yes`, `RestrictNamespaces=yes`,
`SystemCallArchitectures=native`, bounded tasks/files, and exact writable state
paths. The relay also uses `PrivateDevices=yes`. The base home unit uses
`PrivateDevices=yes` for simulation/network devices.

Hardware access is an explicit reviewed drop-in at
`deploy/systemd/astrodeck-hardware.conf.example`: set `PrivateDevices=no`,
`DevicePolicy=closed`, and enumerate only needed stable serial device paths or
measured character-device classes with `DeviceAllow=... rw`; add the service
account only to the udev group owning known vendor nodes. Never grant sudo,
root, `CAP_SYS_ADMIN`, a broad writable `/dev`, or the `docker` group. Verify
hotplug on the intended kernel/systemd because device-cgroup behavior varies.

The target release gate runs `systemd-analyze verify`, inspects effective
properties with `systemctl show`, confirms `/proc/$PID/status` has UID nonzero,
zero effective capabilities and `NoNewPrivs: 1`, proves out-of-scope writes
fail, and exercises actual serial/libusb hardware. Windows uses the dedicated
standard-account/DACL contract in Workstream B rather than these units.

### C3. Images

Both runtime Dockerfiles create an explicit group then user and end in a numeric
identity:

```dockerfile
RUN groupadd --gid 10001 astrodeck \
 && useradd --no-log-init --uid 10001 --gid 10001 \
      --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin astrodeck
USER 10001:10001
```

Use the equivalent `relay` name. Installed code/venv stays root-owned and
read-only. Use `/app` as working directory, set `PYTHONDONTWRITEBYTECODE=1`,
point `HOME`, `TMPDIR`, `XDG_CACHE_HOME`, and `XDG_CONFIG_HOME` under `/tmp`,
and declare `STOPSIGNAL SIGTERM`.

Base-image digest pinning, SBOM, and signing belong to `OPEN-006`; they are
recommended in the deployment commit but do not replace this runtime gate.

### C4. Compose profiles

Use two explicit profiles:

1. Root `docker-compose.yml` is hardened local development/maintenance. If it
   publishes 8800, host IP is explicitly `127.0.0.1` (and optionally `::1`),
   never all interfaces. It is not the LAN/internet deployment.
2. `deploy/reverse-proxy/docker-compose.yml` is the supported network profile.
   Only nginx publishes. The app has `expose: 8800` on an internal bridge and no
   `ports` entry. The app also attaches alone to one project-private outbound
   bridge because remote WSS, weather/maps, update checks, alerts, and OAuth
   require egress; nginx stays off that bridge. No Uvicorn port is published on
   either network.

App policy:

```yaml
user: "10001:10001"
read_only: true
init: true
cap_drop: [ALL]
security_opt:
  - no-new-privileges:true
tmpfs:
  - /tmp:rw,noexec,nosuid,nodev,size=128m,mode=1777
pids_limit: 256
stop_signal: SIGTERM
stop_grace_period: 90s
restart: unless-stopped
```

Use PID 128/grace 30 seconds for relay and PID 64/grace 30 seconds for nginx.
All three services use the shown 128 MiB `/tmp`, `SIGTERM`, and
`restart: unless-stopped`; AstroDeck keeps its 90-second grace. Set measured
`nofile` limits. Do not disable the OOM killer or impose an
unmeasured memory ceiling on imaging. Health checks remain liveness-only; do not
add autoheal that may kill an active exposure.

nginx uses the official unprivileged image as UID/GID 101, container ports
8080/8443, read-only root, all capabilities dropped, bounded `/tmp`, and
read-only config/certificate mounts. Pin a reviewed non-vulnerable image by
digest in the implementation commit.

Use named volumes for `/data/config` and `/data/captures` in the supported
rootless profile. Rootless UID mapping makes arbitrary host bind ownership
unreliable for UID 10001. Put “ordinary SSD files” in a labelled rootful-only
bind override with `create_host_path: false` and explicit ownership setup.

Never mount the Docker socket, host root, all `/dev`, `/dev/mem`,
`/dev/gpiomem`, or host PID/network namespaces. No service has
`privileged: true` or `cap_add`. Do not disable Docker's default seccomp,
AppArmor/SELinux label, user namespace, IPC, UTS, or cgroup isolation. The
production Compose files contain no device mappings; hardware access lives only
in the explicit override.

### C5. Supported browser bootstrap

Remove required `ASTRODECK_TOKEN` from Compose. On a fresh named config volume:

```bash
docker compose -f deploy/reverse-proxy/docker-compose.yml run --rm astrodeck \
  python -m astrodeck create-admin <username>
docker compose -f deploy/reverse-proxy/docker-compose.yml up -d
```

Prompt the password; never pass it in argv/environment. `create-admin` already
enables local auth, so guarded non-loopback startup succeeds. The browser uses
an HttpOnly/Secure/SameSite session cookie and no raw WebSocket query secret.

Document `ASTRODECK_TOKEN` only as legacy direct-API transport until separately
redesigned; do not claim it bootstraps Compose UI. Keep
`ASTRODECK_INSTALL_ROOT` unset in containers; updates replace images and must
not invoke the bare-metal self-updater against a read-only root.

### C6. Container device access

Delete every `privileged: true` recommendation. Network devices need no host
device access. A rootful hardware override may use:

```yaml
devices:
  - /dev/serial/by-id/<stable-id>:/dev/ttyUSB0:rwm
group_add:
  - "${ASTRODECK_DEVICE_GID:?set the host device group GID}"
```

For hot-plugged libusb cameras, if exact nodes cannot be stable:

```yaml
volumes:
  - type: bind
    source: /dev/bus/usb
    target: /dev/bus/usb
device_cgroup_rules:
  - "c 189:* rmw"
group_add:
  - "${ASTRODECK_USB_GID:?set the udev-assigned USB group GID}"
```

Host udev rules grant only known vendor nodes to that group. Keep
`cap_drop: ALL`; DAC/group/device-cgroup permissions, not capabilities, grant
access. USB is not promised rootless until verified on hardware.

Rootless per-IP rate limiting is not accepted until two physical clients retain
distinct source addresses at nginx. Rootless forwarding can collapse source
IPs; use current source-IP-preserving RootlessKit networking (or documented
slirp4netns/pasta alternative) and prove it rather than trusting a version.

### C7. Files and acceptance

Modify both Dockerfiles, root Compose, both entrypoints, and Docker/relay/
security deployment guides. Add both component-local `runtime_security.py`
modules, the bare-metal units/drop-in, both production proxy Compose files,
`docker-compose.usb.yml`, elevation/unit-policy tests, and a Linux
container-security CI lane.

Gates:

```bash
python -m pytest -q -p no:xdist security_acceptance/test_container_contract.py \
  --run-security-acceptance -m 'not security_live'

python -m pytest -q -p no:xdist security_acceptance/test_container_contract.py \
  --run-security-acceptance --run-security-live
```

The checked-in live test inspects `/proc/self/status` (`CapEff` zero,
`NoNewPrivs: 1`), proves installed paths are read-only while data/tmp work, and
verifies named-volume persistence across replacement. The target CI lane also
sends SIGTERM during a simulated active operation. Run it on amd64 and arm64.
On hardware, prove serial/libusb discovery and hotplug work without
privilege/capabilities.

---

## Workstream D: explicit proxy trust and nginx TLS/WSS edge

### D1. Topology

Commit a complete reference deployment:

```text
deploy/reverse-proxy/
  docker-compose.yml
  docker-compose.relay.yml
  README.md
  nginx/
    nginx.conf
    astrodeck.conf.template
    relay.conf.template
```

`docker-compose.yml` is the complete home-controller stack;
`docker-compose.relay.yml` is the complete VPS relay stack. Do not merge them or
depend on a surprising profile/override combination. Their nginx templates
remain separate because routes, connection shapes, and timeouts differ.
Set top-level Compose project names `astrodeck-edge` and
`astrodeck-relay-edge`, respectively, so both disposable acceptance stacks can
run without service/network/volume name collisions.
Each Compose file assigns fixed internal bridge addresses—for example nginx
`172.30.0.2`, app `172.30.0.3`—and its Python service trusts only nginx's exact
address. Select non-conflicting subnets and keep each in one documented
constant. The home controller additionally attaches alone to a dedicated
non-internal egress bridge for required outbound integrations. This is not an
inbound exposure: the app has no `ports` mapping, while the host firewall still
denies direct access to container port 8800. The public relay has no equivalent
outbound requirement and remains only on its internal backend.

Each proxy mounts the common `nginx.conf` and exactly one relevant site
template read-only. Override the image entrypoint with a fail-fast `/bin/sh`
wrapper so the stock entrypoint does not try to mutate `/etc/nginx` on a
read-only root. The wrapper runs `envsubst` with an explicit public variable
allow-list—only `${ASTRODECK_PUBLIC_HOST}` for home or `${RELAY_PUBLIC_HOST}`
for relay—from `/etc/nginx/site.conf.template` into `/tmp/site.conf`, validates
with `nginx -t`, then `exec`s nginx; fixed read-only `/certs/fullchain.pem` and
`/certs/privkey.pem` paths need no substitution. Never use bare `envsubst`,
which would erase nginx variables such as `$host`. Never pass secrets through
that template. The home stack must not load the relay template and the relay
stack must not load the home template;
mounting the whole source directory would make their listeners/default routes
collide.

The relay Compose mounts `device_tokens.json` through a read-only Compose secret
at `/run/secrets/device_tokens` and sets only
`RELAY_DEVICE_TOKENS_FILE=/run/secrets/device_tokens`. It never places
`RELAY_DEVICE_TOKENS`, signing seeds, or token JSON inline in Compose
environment/container metadata. Because Docker Compose silently ignores
service-level `uid`, `gid`, and `mode` for file-sourced secrets, the reference
loads the protected host file into a short-lived host environment variable and
uses an environment-sourced Compose secret with UID/GID 10001 and mode 0400;
the operator unsets that host variable immediately after Compose materializes
the secret. Do not switch this back to `file:` without first proving the
numeric non-root process can read it without making the host file broadly
readable.

nginx is the only published listener; firewall rules also deny upstream 8800.
A host-installed nginx profile may proxy to loopback and trusts only
`127.0.0.1`/`::1`, with the app loopback-bound.

### D2. Uvicorn trusted-proxy contract

Add:

- `ASTRODECK_FORWARDED_ALLOW_IPS`
- `RELAY_FORWARDED_ALLOW_IPS`
- `ASTRODECK_UVICORN_ACCESS_LOG`
- `RELAY_UVICORN_ACCESS_LOG`

Trust defaults empty/disabled. Parse comma-separated IP literals/CIDRs; reject
malformed entries, `*`, `0.0.0.0/0`, and `::/0`. Pass explicitly:

```python
proxy_headers=bool(trusted_proxy_ips)
forwarded_allow_ips=trusted_proxy_ips or ""
access_log=access_log_enabled
```

Expose `parse_forwarded_allow_ips(raw: str | None) -> str` in both
`astrodeck.runtime_security` and `relay.config`. It trims/canonicalizes with
`ipaddress`, rejects the values above and duplicates, and returns a canonical
comma-separated string (empty input returns `""`). Keeping this small seam makes
wildcard rejection directly testable without starting a socket.

Disable Uvicorn access logs in the nginx profile so it cannot re-log query
secrets. Keep them for loopback development if desired.

Change both auth `_is_secure` helpers to use only
`request.url.scheme == "https"`. Application code never reads raw
`X-Forwarded-Proto`; Uvicorn normalizes scope only for the trusted immediate
peer. Origin validation continues using normalized scope scheme and Host.

nginx overwrites, never appends, forwarding metadata:

```nginx
proxy_set_header Host              $http_host;
proxy_set_header X-Forwarded-Proto https;
proxy_set_header X-Forwarded-For   $remote_addr;
proxy_set_header Forwarded         "";
proxy_set_header X-Real-IP         "";
proxy_set_header X-Forwarded-Host  "";
proxy_set_header X-Request-ID      $request_id;
```

Preserve browser `Origin`. Reject missing/unknown Host before proxying. Never
use `$proxy_add_x_forwarded_for` or wildcard proxy trust. Relay's native limiter
then receives normalized clients instead of nginx or attacker input.

### D3. TLS, limits, timeouts, and non-replay

Common nginx requirements:

- unprivileged container ports 8080/8443, published as selected host ports;
- TLS 1.2/1.3 only; session tickets off;
- HSTS on a permanent HTTPS hostname, without `preload` or
  `includeSubDomains` unless separately approved;
- default HTTP/HTTPS vhosts reject with 444/`ssl_reject_handshake`;
- known HTTP host redirects to `https://$host$uri`, dropping query strings;
- reject every query string on home `/ws`, relay `/h/<home-id>/ws`, and relay
  `/scope` with HTTP 400 (production WSS authenticates by session cookie or
  device header and needs no query), and reject a plain `token` argument on
  other routes as defense in depth;
- `client_max_body_size 8m`;
- header timeout 10 s, body idle 30 s, keepalive 15 s, downstream send 30 s;
- upstream connect 5 s, ordinary read/send 60 s, WebSocket read timeout 75 s,
  `/api/connect/*` read timeout 140 s;
- request buffering on so slow/incomplete bodies do not occupy Python, with
  bounded tmpfs; response temp-file buffering off for streaming;
- `proxy_next_upstream off` so a failed POST is never replayed;
- limit rejection status 429; and
- WebSocket Upgrade/Connection mapping and HTTP/1.1 upstream behavior.

Selected home limits (change only with measured legitimate traffic):

| Zone/location | Sustained | Burst | Connection ceiling |
|---|---:|---:|---:|
| static/general | 30 requests/s/IP | 120 | 32/IP, 128/vhost |
| `/api/` | 20 requests/s/IP | 60 | inherited |
| `/auth/local`, `/auth/token`, `/auth/setup/local` | 5 requests/min/IP | 5, no delay | inherited |
| update/config/factory-reset mutations | 2 requests/min/IP | 2, no delay | inherited |
| `/ws` opens | 10 requests/min/IP | 5, no delay | 8/IP, 128/vhost |

Use exact home zone names/rates `static=30r/s`, `general=20r/s`,
`auth=5r/m`, `critical=2r/m`, and `ws_open=10r/m`, all keyed by
`$binary_remote_addr`. Connection zones are `per_ip` keyed by
`$binary_remote_addr` and `per_host` keyed by `$server_name`. The `/ws`
location repeats both `limit_conn per_ip 8` and `limit_conn per_host 128`;
declaring one location-level connection limit would otherwise discard both
server-level inherited limits.

Each exact auth location also overrides `client_max_body_size 4k`; there is no
reason to admit an 8 MiB password/token document to an expensive verifier.

Stricter auth/critical locations repeat/include the full proxy parameters;
nginx does not inherit a sibling location's `proxy_set_header` directives.

Relay edge limits mirror native defaults: browser HTTP 20/s burst 40; `/scope`
handshakes 2/s burst 10; browser WS opens 1/s burst 5; 32 connections/IP and
256 total. Native limits remain defense in depth. Keep one home security origin
per relay hostname/process.

Use relay zone names/rates `relay_http=20r/s`, `relay_scope=2r/s`, and
`relay_ws=1r/s`; connection zones are `relay_per_ip` and `relay_total`.

The edge supplies the IP half of OPEN-008. The application supplies the account
half because nginx must not parse JSON credentials. Add a bounded in-memory
local-login limiter in `astrodeck/auth/login_rate_limit.py`, keyed by a keyed
digest of normalized username (never store or log the submitted username as the
limiter key). Its pure seam is:

```python
LoginAttemptLimiter(
    key_secret: bytes,
    *,
    max_failures: int = 10,
    window_s: float = 600,
    max_keys: int = 10_000,
    idle_ttl_s: float = 3600,
)
begin_attempt(normalized_username: str, *, now: float) -> int | None
record_success(normalized_username: str) -> None
__len__() -> int
```

`begin_attempt()` atomically checks and reserves an attempt under a lock: it
returns `None` when the verifier may run, or a positive `Retry-After` when
blocked. This closes the parallel check-then-record race; a success removes its
reserved history, while an unsuccessful reservation remains a failure. The
window is sliding and `Retry-After` is capped at 60 seconds. Unknown,
disabled, and real accounts traverse the same password-verification and limiter
path and get indistinguishable generic responses. Track all submitted names so
the limiter is not an account-existence oracle. Never evict a non-idle account
bucket merely to admit a new attacker-chosen name: after `max_keys` active keyed
digests, route new names through one fail-closed overflow bucket until idle
pruning frees capacity. Otherwise username churn could evict the exact account
being brute-forced. A successful login clears that account key. Derive
`key_secret` from the real session secret with a domain-separated HMAC; never
persist raw usernames or a new plaintext key. Apply the same edge IP limit to
`/auth/token` and `/auth/setup/local`; the account limiter is specific to
password login. Add route tests proving parallel attempts, distributed source
IPs, and key churn cannot bypass the account bucket, memory is bounded, success
resets it, and denial never reveals account existence.

The reference runs one AstroDeck application process. Do not add Uvicorn
workers or horizontally scale this in-memory account limiter; a multi-process
deployment needs a shared atomic limiter before it can claim equivalent
per-account protection.

Certificate issuance is environment-specific. The reference uses read-only
cert/key mounts, documents ACME or a locally trusted CA, tests renewal plus
graceful reload, and never puts a private key in source/image.

### D4. Token-safe logs

Do not use combined/default access format. Emit only timestamp, generated
`$request_id`, client IP, method, normalized `$uri`, status, bytes, duration,
and upstream status. nginx overwrites client `X-Request-ID` with that generated
value before proxying so audit correlation cannot be forged. Never include:

```text
$request $request_uri $args $query_string
$http_authorization $http_cookie $http_x_auth_token
referer or user-agent
```

Use `$uri`, not `$request_uri`, and send safe access logs to stdout. nginx error
messages have no URI-redaction hook, so keep request-serving vhost errors at
critical-only (or disabled while startup/config errors remain visible). The
safe access log still records status and upstream status.

Use JSON logs with `escape=json` so a hostile path cannot inject a second log
record. The home format is named `astrodeck_audit`. The relay template defines
an HTTP-context `map` that replaces the `/h/<home-id>/` segment with
`/h/:home/` and logs that value through `relay_audit`; do not reintroduce home
identifiers that were removed from relay health output.

Set `access_log off` at the common HTTP level so nginx's implicit combined log
cannot capture rejected/default-host requests; the known site vhost explicitly
enables its named safe format. Global/request error logging remains critical-
only and contains no debug request dump.

Every service in both reference Compose stacks uses Docker's rotating `local`
log driver with `max-size: 10m` and `max-file: 5`; operators may replace it only
with an equivalently access-controlled, bounded collector. Audit availability
must not become a host-disk exhaustion path.

Acceptance sends unique `/ws?token=<canary>` and
`/ws?%74oken=<canary>` probes and fails unless both are rejected before upstream
or if the canary appears in nginx/app/relay logs. Do not print full request
objects in error paths.

### D5. Files and acceptance

Modify Uvicorn entrypoints/config, both auth `_is_secure` helpers,
proxy/security/relay docs, and production Compose. Add the reverse-proxy tree,
server/relay proxy-trust unit tests, and a Linux HTTPS/WSS integration lane.

Portable gate:

```bash
python -m pytest -q -p no:xdist security_acceptance/test_reverse_proxy_contract.py \
  --run-security-acceptance -m 'not security_live'
```

For a live lab stack, create a disposable local admin and trusted test
certificate, then set secret CI inputs without echoing them:

```bash
export ASTRODECK_SECURITY_BASE_URL=https://astrodeck-test.example
export ASTRODECK_SECURITY_USERNAME=security-test-admin
export ASTRODECK_SECURITY_PASSWORD='<secret CI input>'
export ASTRODECK_SECURITY_COMPOSE_FILE=deploy/reverse-proxy/docker-compose.yml
export RELAY_SECURITY_BASE_URL=https://relay-test.example
export RELAY_SECURITY_DEVICE_TOKEN='<secret from disposable token file>'
export RELAY_SECURITY_HOME_ID=home-security-test
export RELAY_SECURITY_COMPOSE_FILE=deploy/reverse-proxy/docker-compose.relay.yml
python -m pytest -q -p no:xdist security_acceptance/test_reverse_proxy_contract.py \
  --run-security-acceptance --run-security-live
```

Run both named Compose projects for this gate. The checked-in live suite checks
the home stack's trusted HTTPS, unknown Host, cross-origin
denial, 4 KiB auth-body rejection, Secure/HttpOnly/SameSite session login, WSS,
auth throttling even while spoofed XFF changes, TLS-version policy,
oversized/slow headers, hidden upstream, effective configuration syntax, and
redaction across all Compose service logs. It also opens a real relay WSS
`/scope`, registers with a disposable device token, round-trips an HTTPS browser
request through the wire protocol, and checks relay query/home/token log
redaction. The target CI lane must additionally
run a purpose-built upstream-drop fixture proving a POST is observed exactly
once, two real source IPs, certificate renewal/reload, and nginx surviving an
app restart with 502 then recovery. Those topology/fault-injection gates cannot
be truthfully simulated by a single-process unit test.

---

## Acceptance suite operation

`security_acceptance/` stays outside normal project `testpaths` and is skipped
unless explicitly enabled. This prevents target-state tests from breaking
today's CI while giving the executor a fixed oracle.

Portable all-workstream gate:

```powershell
server\.venv\Scripts\python.exe -m pytest -q -p no:xdist security_acceptance `
  --run-security-acceptance -m "not security_live"
```

```bash
server/.venv/bin/python -m pytest -q -p no:xdist security_acceptance \
  --run-security-acceptance -m 'not security_live'
```

Do not run one aggregate live command and accept platform skips. Run each
workstream's live command above on its named target: Windows DACL on Windows
NTFS/ReFS; containers on native Linux amd64 and arm64; proxy tests against the
disposable HTTPS stack; Orange Pi through its hardware checklist. The live
tests fail on a wrong host rather than treating that mismatch as evidence.

If the checked-in Windows venv points to a removed interpreter, recreate it
instead of editing expectations. Run Orange Pi tests serially
(`-p no:xdist`) because globals, sockets, and provisioning state are shared.

Expected initial failures include the fixed AP password, missing commissioned
identity APIs, absent Windows ACL/private-dir seam, missing elevation guard,
unhardened Compose, absent proxy tree, raw XFP parsing, and implicit Uvicorn
trust. The recorded 2026-09-02 Windows baseline is 53 collected, 53 skipped by
default, and—when explicitly enabled without live tests—39 failed, 2 passed,
and 12 deselected. Record the baseline; do not turn failures into skips.

| Lane | Mandatory target | What it proves |
|---|---|---|
| portable acceptance | Windows + Linux PR CI | APIs/config invariants and failure propagation |
| existing suites | Windows + Linux | no application regression |
| Windows live | NTFS, standard + service identities | actual DACL/ACE/owner behavior |
| container live | Linux amd64 + arm64 | UID, capabilities, NNP, RO/writable paths |
| proxy live | deployed HTTPS/WSS lab | edge behavior, not config text |
| appliance manual | Orange Pi 5 disposable image | Wi-Fi/systemd/power-cycle/persistence |
| container hardware | intended Pi/host + devices | serial/libusb/hotplug with narrow access |

Every guard gets a sabotage test that deliberately observes red: inject
Everyone into a DACL, replace `$uri` with `$request_uri`, publish 8800, trust
`*`, set USER 0, reuse identity, restart a service three times under one boot
ID, or remove the auth limit.

## Documentation reconciliation

Update all related text atomically with behavior:

- mark the fixed-password allowance superseded;
- clarify factory commissioning before label printing and recovery restoring
  that printed value;
- remove automatic-AP-on-network-loss and `astrodeck` password claims;
- remove every `privileged: true` example;
- distinguish loopback Compose from production proxy Compose;
- replace broken environment-token quick start with `create-admin`;
- document Windows NTFS/dedicated-standard-account requirements;
- describe the unprivileged portal / root broker split, the `astrodeck-setup`
  account, and the broker-script commissioning commands in the Orange Pi
  README;
- correct the inaccurate private-directory finding; and
- change OPEN-003/005/008/010 status only after all target evidence exists.
  A design or static test is not “fixed.”

## Rollback and release safety

- Orange Pi migration is one-way only after a valid label exists. Do not delete
  old state until new identity, printed label, and reload verification succeed;
  never fall back to the public password.
- Windows DACL repair is recoverable by trusted account/Administrator, but a
  failure stops service. Test a copied config tree first; never recurse over an
  arbitrary parent.
- A failed hardened deployment rolls back only behind loopback/firewall. Never
  roll back by publishing unproxied 8800 or adding privilege.
- Run `nginx -t` before graceful reload and observe a live WebSocket plus an
  in-progress simulated operation.

## Explicit non-goals / remaining risks

- Relay end-to-end encryption (`OPEN-001`) and asymmetric device identity
  (`OPEN-002`).
- Ownership-transfer/factory-reset and broader application-account/claim
  recovery semantics (`OPEN-004` and the unresolved portion of the 2026-08-12
  product design).
- Fully locked/signed/SBOM images (`OPEN-006`) and out-of-process SDK isolation
  (`OPEN-007`).
- Third-party direct `uvicorn astrodeck.api.app:...` hosting (`OPEN-009`). The
  supported units/images call the guarded module entrypoint; do not close that
  separate finding on the strength of this work.
- Replacing legacy browser query-token mode (`OPEN-011`); production rejects it
  and uses session cookies.
- CSP, Rust maintenance, or relay multi-tenancy work.
- Making the root broker's sandbox an authorization boundary against root's
  own systemd and D-Bus authority. What this work adds is that no
  network-facing code runs as root and the broker accepts four fixed
  operations from one UID (A4); the broker process itself is still root.

## Primary references

- Microsoft: [SetSecurityInfo](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setsecurityinfo),
  [security-information flags](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-information),
  [SDDL](https://learn.microsoft.com/en-gb/windows/win32/secauthz/security-descriptor-string-format),
  [CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew),
  and [CreateDirectoryW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createdirectoryw).
- Docker: [Compose services](https://docs.docker.com/reference/compose-file/services/),
  [capabilities/devices](https://docs.docker.com/engine/containers/run/),
  [no-new-privileges/read-only](https://docs.docker.com/reference/cli/docker/container/run),
  and [rootless mode](https://docs.docker.com/engine/security/rootless/).
- Uvicorn: [proxy settings](https://www.uvicorn.org/settings/).
- nginx: [WebSockets](https://nginx.org/en/docs/http/websocket.html),
  [request limits](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html),
  [connection limits](https://nginx.org/en/docs/http/ngx_http_limit_conn_module.html),
  [proxy behavior](https://nginx.org/en/docs/http/ngx_http_proxy_module.html), and
  [unknown-host routing](https://nginx.org/en/docs/http/request_processing.html).
- systemd upstream: [`systemd.exec`](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml).
