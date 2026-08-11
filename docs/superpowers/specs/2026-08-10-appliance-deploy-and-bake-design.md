# Deploying AstroDeck to the appliance, and the manufacturing bake

**Date:** 2026-08-10. **Status:** design; sub-project 1 of the product charter.
**Owner request:** "figure out how to deploy astrodeck to the board. The
manufacturing process should have astrodeck preinstalled."
**Charter:** `2026-08-10-appliance-product-program.md` (decisions D1-D9).

Evidence convention as in the charter: **[v]** verified by reading the cited
file or running the cited command; **[r]** reported, not independently checked.

## The core decision

**Ship the artifact we already have, and make the bake do exactly what a
successful update does.** A factory unit is then byte-for-byte in the state a
just-updated unit is in.

The bake lays down `/opt/astrodeck/{current, releases/<v>/, state/}` in the
supervisor's own layout, builds that release's venv, and writes `current` and
`state/last-good`. Day-one self-update is therefore real rather than
aspirational — which is precisely what the Dockerfile fails at, by never setting
`ASTRODECK_INSTALL_ROOT` so `supervised_install_root()` returns `None` and apply
is refused outright. **[r]**

Rejected, with reasons:

- **PyInstaller onefile** — the supervisor launches `python -m astrodeck`, and a
  frozen binary is not that. `sys.executable` becomes the binary, so dependency
  reconcile and venv restore are meaningless, and it embeds CPython 3.12 beside
  the board's 3.13.5. That job has also never executed. **[r]**
- **Container** — forbidden by D4; ~350 MB per retained version against ~9 MB;
  no Rust stage; no ZWO; self-update inert by construction.
- **`.deb` + apt repo** — a second signing, suite and hosting system running in
  parallel with the one we already test, and apt would contend with the
  supervisor over `/opt/astrodeck`. Two updaters is the thing one person cannot
  maintain.
- **A prebuilt venv inside the tarball** — tempting (no work at apply time) but
  shebangs and `pyvenv.cfg` hard-code absolute paths and bind the release to the
  OS's Python minor version, so a tier-2 Python bump would brick every staged
  tier-1 release.

## Removing the network and the compiler from the install path

A dark-site box must need neither. Three changes:

1. **The tarball gains `wheelhouse/`** — every dependency resolved once for
   cp313/aarch64/glibc 2.41, plus `astrodeck_native`, plus `setuptools` and
   `wheel`. The last two because `server/pyproject.toml` has **no
   `[build-system]` table**, so PEP 517 build isolation reaches for PyPI even
   when nothing has changed. **[r]**
2. **`reconcile_deps` becomes offline** — `pip install --no-index --find-links
   <release>/wheelhouse --no-build-isolation <release>/server`, falling back to
   today's behaviour when no wheelhouse is present so source and Docker installs
   are untouched. Today `stage.py` runs a bare `pip install <server_dir>` with
   no `--no-index`, `-c`, or `--only-binary`. **[r]**
3. **Per-release venv** at `releases/<v>/venv`, with the supervisor deriving
   `--python` per version. This deletes `snapshot_env`, the rollback freeze file
   and `_real_restore`, and makes **rollback a network-free pointer flip**.

### Wheel availability: settled

Resolved on 2026-08-10 with `pip download --only-binary=:all: --python-version
3.13 --abi cp313 --abi abi3 --abi none --platform manylinux*_aarch64` against
the eleven declared runtime dependencies. **All 34 distributions in the closure
resolved to wheels. Zero sdists. Exit 0.** **[v]**

Notably present, all previously assumed rather than checked: `pillow` 12.3.0
cp313, `cryptography` 50.0.0 cp311-abi3, `bcrypt` 5.0.0 cp39-abi3,
`pydantic_core` 2.46.4 cp313, `cffi`, `httptools`, `watchfiles`, `websockets`.

**Two caveats, both load-bearing:**

- **A cross-platform resolution is not the shippable one.** pip evaluates
  environment markers against the *host*, not `--platform`. Run from Windows,
  the closure omitted `uvloop` (linux-only) and included `colorama`
  (win32-only). The wheelhouse must be produced on arm64 Linux in a
  `debian:trixie` container. This check proves the wheels exist; it does not
  produce the artifact. **[v]**
- **The unpinned floors drift.** Eleven `>=` constraints resolved to
  fastapi **0.141.1**, numpy 2.5.2, astropy 8.0.1, packaging 26.3. FastAPI 0.141
  is the version family that made `include_router` lazy and silently stopped the
  RBAC boot assertion grading five routers with CI still green. A committed
  `constraints-linux-aarch64-cp313.txt` is the control, not hygiene. **[v]**

## The bake

Built by customising a **self-mirrored, SHA256-pinned stock Armbian community
image** on a **native `ubuntu-24.04-arm` runner** — native chroot, no qemu, no
binfmt. Not the Armbian build framework, which pins git branches rather than
SHAs and which Armbian itself does not run on GitHub-hosted runners. **[r]**

| Stage | Runs where | Does |
|---|---|---|
| `base-pin` | x86, manual | Mirror the chosen Armbian asset to our own release and record its SHA256. **Required**: armbian/community keeps 3 releases + 3 pre-releases and deletes the rest daily at 03:00 UTC, and publishes no `.sha`/`.asc` — today's build input is unfetchable within days. **[r]** |
| `ui` | x86 | `npm ci && npm run build`. Must **not** be copied into `server/astrodeck/webui`, or a stale copy shadows the fresh SPA under the mtime tiebreak. |
| `native-wheel` | arm64 runner | `maturin build --release`, abi3-py311 so one wheel covers 3.11-3.13, **default baseline target, never `-C target-cpu=native`**. |
| `wheelhouse` | arm64, `debian:trixie` container | `pip download --only-binary=:all:` against the committed constraints. Fails loudly on any missing wheel — a red CI job instead of a 3 a.m. compile on a customer's box. |
| `app-tarball` | x86 | `build_release.py --strict`, Ed25519 sign, sidecars named **exactly** after the artifact. |
| `os-packages` | arm64 container | `apt-get --download-only` the pinned set into a local `file://` repo. Armbian's repo has no snapshot service, so vendoring the `.deb`s is the only reproducible option. **[r]** |
| `image-customise` | arm64, native chroot | Expand the image, relocate the GPT backup header, resize; `policy-rc.d` returning 101; install packages, purge `brltty`, create the `astrodeck` user, install units/udev/tmpfiles/LABEL-only fstab, lay down `/opt/astrodeck`. |
| `lint-gate` | x86 | **Fail the build on any `mmcblk` string** anywhere in packaging, units, fstab or first-boot code. Assert every mount is `LABEL=`/`PARTUUID=`. D8 makes whole-OS-on-eMMC a later move; one hardcoded `mmcblk0p2` turns a build flag into a redesign. |
| `finalise` | arm64 | fstrim, unmount, compress, emit `.img.xz` + `.sha256` + a manifest recording base image SHA, every `.deb` version, every wheel hash, app version and signature, plus the Debian source-offer bundle. Functional reproducibility, not bit-identical. |

## Bake time versus first boot

Everything identical across units happens at bake. Everything per-unit happens
in **one first-boot oneshot**, ordered before `systemd-networkd` and before
`astrodeck.service`, gated on a marker at **`/data/.astrodeck-provisioned`**.

The marker lives on `/data` — on the eMMC, never the SD — so **an OS re-flash
restores a customer's box rather than re-manufacturing it.** That falls straight
out of D8 and is the property that makes "write a new card" a survivable
support instruction.

First boot: assert the rootfs grew (Armbian's own resize does it — assert, never
reimplement); find the eMMC **by label** and, if blank, partition, format and
label `ASTROLOG`/`ASTRODATA`, refusing rather than falling back to the SD when
ambiguous; mount and assert `/var/log`'s source is the eMMC; derive the unit ID
from the SoC serial and persist it; generate the per-unit session secret,
hotspot PSK and admin credential; seed `auth.methods == ['local']`.

**Identity comes from `/proc/device-tree/serial-number`, not the wlan0 MAC** —
a randomised SDIO MAC would rename the unit under the customer. Every consumer
reads the persisted file rather than re-deriving. **[r]**

## Traps that would each have cost a day

- **`/etc/modprobe.d` is a no-op for the usbfs bump** when `usbcore` is built
  in, and it fails silently. Use `extraargs=` in `armbianEnv.txt`:
  `usbcore.usbfs_memory_mb=1024 usbcore.autosuspend=-1`. **[r]**
- **Do not carry `TAG+="uaccess"`** from upstream udev snippets — it grants an
  ACL to the active logind seat user, and a seatless system service never gets
  one. Use `GROUP=astrodeck MODE=0660`. **[r]**
- **`supervisor.py:249-250` assigns `ASTRODECK_CONFIG_DIR` and
  `ASTRODECK_CAPTURE_DIR` unconditionally** from `--root`, so a systemd
  `Environment=` line is silently discarded. `packaging/entry.py` already uses
  `setdefault` for the same two variables, so the intended precedence is settled
  elsewhere in the codebase. Fix both to `setdefault` + a parity test. **[r]**
- **The supervisor calls `_resolve_target()` once before its loop**, so
  restarting the child re-launches the same version and never re-reads
  `current`. Do not test a version flip by restarting the child. **[r]**
- **GitHub's arm64 runners are Neoverse N2 (Armv9/SVE2); the RK3588S is
  Cortex-A76 (Armv8.2).** Anything built native-tuned will `SIGILL` on the
  appliance. **[r]**
- **Without the native wheel, polar alignment silently resolves to the
  SIMULATOR and reports a fabricated error figure** — `_resolve_polar` lacks the
  guard `_resolve_solve` has. Same shape as the profile-provider override that
  ran a simulated polar aligner for weeks. Autofocus degrades to a `DeviceError`
  a running sequence downgrades to a warning and keeps shooting. **[r]**

## Blockers, hardest first

| Size | Blocker | Fix |
|---|---|---|
| ~~L~~ → M | **Player One's SDK licence has no distribution verb**, and the owner's imaging camera is a POA Poseidon-M Pro. A flashed image conveyed to a customer is redistribution. `build_release.py` already strips these via `_UNSHIPPABLE_VENDOR_DIRS`. **[r]** | **Resolved in principle by D10** (2026-08-10): not bundled, fetched on demand with consent. No longer a launch blocker — it becomes a feature to build. Still apply the same filter in `astrodeck.spec`, which does not strip them today. |
| L | **No image bake and no appliance packaging tree exist.** Two systemd units in the whole repo, both ours, neither the server. **[v]** | Create `packaging/appliance/{systemd,udev,tmpfiles,bake}` + two workflows. |
| L | **No first-boot mechanism exists.** Every unit is unconditional-every-boot. Nowhere for per-unit identity, PSK, credential or eMMC provisioning to live. **[r]** | `astrodeck-firstboot.service`, marker on `/data`. |
| M | **Nothing in CI produces an aarch64 wheelhouse or native wheel**; no shipped artifact has ever carried the Rust engine on any platform. **[r]** | maturin job on `ubuntu-24.04-arm`; wheelhouse job in `debian:trixie`; add `native` to `build_release.py`'s `_ASSETS` so `--strict` fails on absence; give `_resolve_polar` the guard `_resolve_solve` has. |
| M | **ZWO Linux arm64 libraries are not vendored** — `vendor/zwo/` holds three Windows DLLs and `macos/`. Guide camera, EAF, EFW and CAA are absent regardless of udev. **[r]** | Fetch from `indi-3rdparty/libasi/armv8` into `vendor/zwo/linux-arm64/`; correct `vendor/zwo/README.md:42-46`, which still claims upstream ships only a static `.a`. |
| M | **The updater cannot reliably pick its own checksum** — suffix-only asset matching against five `.sha256` assets from parallel jobs. **[v]** | Exact-name matching; test with a realistic five-asset payload. |
| M | **Update commit is not crash-atomic** and one `/healthz` commits forever. **[v]** | Persist probe state before launching; treat an unresolved marker at start as rollback; add a stabilization window. |
| S | **No udev rules, no usbfs bump, no `libusb-1.0-0`; `brltty` will claim the CH340 filter wheel.** Secrets land at umask default. **[v]** | `99-astrodeck.rules` (GROUP=astrodeck MODE=0660 on `03c3`/`a0a0` usb_device and hidraw, plus the AM5 and CH340 ttys, `power/control=on`); boot args; `chmod 0600` on secret writes. |
| S | **The release pipeline is unproven at HEAD** — one release run ever (v0.2.16, 2026-07-22), version is now 0.2.71. **[r]** | Cut a throwaway tag and exercise release end to end before the bake depends on it. |

## What remains unproven until hardware

No vendor camera blob has ever been `dlopen`'d on an RK3588S. The dependency
closure has never been *installed* on the board (only resolved). The Rust
workspace has never been compiled for aarch64, and float ordering and FMA differ
by target, so the PPEC convergence numbers CI gates on have never been checked
there — **a clean import is not a passing gate.** None of this is a reason to
delay the bake; all of it is a reason the smoke stage is a human with a camera
in their hand, not a CI job.
