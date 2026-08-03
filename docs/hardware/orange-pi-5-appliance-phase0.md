# Orange Pi 5 Pro as the AstroDeck appliance — Phase 0 verdict

**Date:** 2026-08-03. Board ordered, arriving in a few days. This answers the
one question worth answering before it does: *is anything actually blocking?*

## Verdict: NOT blocked, and the title of issue #118 was wrong

#118 was filed as "blocker is ZWO Linux/arm64 SDKs". Both halves of that are
wrong. arm64 builds exist, and the licensing — the only thing that could
genuinely have killed the project, because no amount of work routes around a
refusal to redistribute — is clean.

**Licensing, the part that mattered.** ZWO's `LICENSE.txt` is verbatim MIT.
Player One's grants development "without any restrictions". Critically, neither
permission is **platform-scoped**: both files are byte-identical to the copies
in `indilib/indi-3rdparty`, where a single `license.txt` governs the
`armv6/armv7/armv8/x64/x86/mac` subdirectories together. So the Linux arm64
libraries are redistributable on exactly the terms the Windows DLLs already
vendored here are.

**How much already exists.** More than expected:

| already done | where |
|---|---|
| multi-arch by construction | `Dockerfile` |
| native `linux-arm64` PyInstaller build on an `ubuntu-24.04-arm` runner | `.github/workflows/release.yml` |
| `linux/arm64` image push | `.github/workflows/release.yml` |
| per-platform vendored-library resolution incl. `linux-arm64` | `devices/sdk_paths.py` |
| **Player One arm64 `.so` already vendored** | `vendor/playerone/linux-arm64/` |
| correct `linux-aarch64` ASTAP URL | `scripts/fetch_astap.py` |
| supervisor is stdlib-only; systemd needs a unit file, not code | `supervisor/supervisor.py` |

## Fixed today, before the board arrives

**The EAF focuser would have vanished silently.** Every vendor-library stem in
this codebase was taken from its Windows DLL basename. ZWO's focuser is
`EAF_focuser.dll` on Windows and `libEAFFocuser.so` on Linux — no
transformation of one yields the other. Resolution was checked against the real
upstream filenames: `ASICamera2`, `PlayerOneCamera` and `CAARotator` all match;
`EAF_focuser` matches nothing, and the fallback `libEAF_focuser.so` does not
exist either. The failure mode is the exact one `sdk_paths` was written to
prevent — no library, no backend, no device offered, **no log line**. On the
appliance the mount and filter wheel would work and the focuser would simply be
absent. Fixed with a POSIX stem alias, Windows resolution untouched.

**`fetch_astap.py` downloaded the x86_64 solver on arm64.** The default-platform
line keyed off `sys.platform` and never consulted `platform.machine()`, so
running the fetcher *on the board* would download the one binary that board
cannot execute — and print that it was doing so. The `linux-aarch64` entry had
been correct in `BINARIES` all along; nothing ever selected it.

**The fetcher's layout was one the solver could not read.** It writes the binary
to `out/<platform>/astap_cli` but extracts the star database flat into `out`,
and discovery only ever looked at the flat path. A correctly-fetched bundle
therefore yielded a database the solver could see and a binary it could not, and
it fell back to a system install — which on an appliance image is no install at
all. Discovery now checks the platform subdirectory first, flat second.

## Remaining work, ranked

**1. Vendor the ZWO Linux arm64 libraries.** *(the real gating item)*
`vendor/zwo/` has only `ASICamera2.dll`, `CAARotator.dll`, `EAF_focuser.dll` and
`macos/`. There is no `linux-arm64/`. Upstream `indi-3rdparty/libasi/armv8/`
carries `libASICamera2`, `libEAFFocuser`, `libCAARotator`, `libEFWFilter` and
`libUSB2ST4Conv` — shared objects renamed `.bin` so distro packaging tools do
not strip them. Note `vendor/zwo/README.md` currently claims upstream ships
"only `libASICamera2.a`, a static archive"; that is out of date and should be
corrected when the files land.

**2. udev rules and the usbfs bump.** Neither exists anywhere in the repo
(`git ls-files | grep rules$` returns nothing). Without them, USB access fails
without root, and large USB3 frames fail to allocate. Upstream values:

```
ACTION=="add", ATTR{idVendor}=="03c3", \
  RUN+="/bin/sh -c '/bin/echo 1024 >/sys/module/usbcore/parameters/usbfs_memory_mb'"
SUBSYSTEMS=="usb", ATTR{idVendor}=="03c3", MODE="0666"
KERNEL=="hidraw*", ATTRS{idVendor}=="03c3", MODE="0666", TAG+="uaccess"
```

That `hidraw` line is load-bearing here specifically: the EAF (`03C3:1F10`) and
the CAA (`03C3:1F20`) are HID devices, not USB-bulk ones.

**3. Ship the `astrodeck_native` wheel.** Not an arm64 regression — it is
missing on *every* platform. The Rust engine is built exactly once, in
`ci.yml`, purely so the native tests run; the wheel is never uploaded, attached,
or installed. The PyInstaller spec has no entry for it and the Dockerfile has no
Rust stage. Consequence: the container and every single-file binary today have
no native engine, so native autofocus and native guiding are not offered and
polar align degrades.

**4. A systemd unit.** The supervisor itself is portable stdlib; only the
Windows Scheduled Task assumption needs replacing.

**5. Housekeeping.** `brltty` will claim the CH340 Wanderer filter wheel on
Debian/Ubuntu arm64 and must be masked. Existing rig profiles carry `COM*` port
strings that will not resolve on the board.

## What Phase 0 did NOT verify

The ZWO and Player One arm64 libraries have not been downloaded, loaded, or
tested on real hardware. Their existence and licence terms are established; that
they load on RK3588S and drive these specific devices is a Phase 1 question that
needs the board. Nothing here has been run on arm64 at all.

USB3 bandwidth and power delivery on the RK3588S under a full imaging train are
also unmeasured. A powered hub is likely, and sustained livestacking will want a
real heatsink.
