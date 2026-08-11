# Fetching what we may not convey: the vendor asset mechanism

**Date:** 2026-08-10. **Status:** design. Implements **D10** of the product
charter (`2026-08-10-appliance-product-program.md`).
**Owner decision:** non-redistributable assets are not bundled. The software
detects the user's hardware, says what it found and why it cannot drive it, and
offers to fetch the vendor's asset on their behalf, with consent.

Evidence convention: **[v]** verified by reading the cited file during this
survey; **[r]** reported, not independently checked.

## Shape

One catalogue file, one broker verb set, one storage root, one status endpoint.
Every non-redistributable asset — Player One SDK, the DSS2-replacement survey
pack, QHY, INDI 3rdparty blobs — is a **row in
`server/astrodeck/assets/catalogue.json`**, not a code path. Adding a vendor is
adding a row.

Two sharpenings against the original brief, both of which change the size of the
work:

**The broker is required for what the server would later load or execute — not
for everything fetched.** A HiPS tile is inert data; a `.so` is code the server
`dlopen`s. The catalogue carries `privileged: true|false`, and only privileged
assets take the broker path. The survey pack keeps its existing in-app fetcher
and joins the mechanism only for consent, credits, detection and status. One
policy, two transports — rather than rebuilding a working 4,000-file resumable
sync as a privileged verb for no security gain.

**Detection is worth more than fetching, and it is cheap.** Ship it first.

## Detection, without any vendor library

`server/astrodeck/devices/usb_identity.py::scan()` reads
`/sys/bus/usb/devices/*/{idVendor,idProduct,manufacturer,product,serial}`.
Stdlib only — no `pyusb`, no `libusb`, **no device open**, which matters because
opening a camera takes an exclusive handle and a probe that opened devices would
fight the driver.

**Nothing in the repo enumerates USB today.** A grep for `pyusb`, `usb.core`,
`/sys/bus/usb`, `idVendor`, `hidapi` and `pyudev` across `server/astrodeck`
returns zero hits; the only sight of hardware without a vendor SDK is
`pyserial`'s `comports()`, which sees CDC-ACM devices only — the AM5 and the
CH340 — and never a camera or a HID accessory. **[r]**

Also worth recording so nobody wastes an afternoon: `devices/fingerprint.py` is
**not** a hardware-identity table. It records last-known device *state* (focuser
position, filter slot, RA/Dec, parked, tracking) to detect a power cut. No VID,
PID, serial or model anywhere. **[r]**

IDs to seed the catalogue's `usb` table, currently living only as inline hex
literals and prose: `03c3` ZWO (`1f10` EAF, `1f20` CAA, `4001` AM5), `a0a0`
Player One, `1618` QHY. **[r]**

## The silent-failure seam — the real prerequisite

Today "the vendor SDK is missing" is byte-identical to "no such hardware is
attached." Four `except Exception: pass` sites swallow both, and `drivers.py`
renders the empty result as `"no {label} detected"` — **so a customer with a
Poseidon-M Pro plugged in is told their camera is not plugged in.** **[r]**

Minimal fix preserving the existing invariant (an absent library must not break
discovery): each `discover()` catches its own SDK's load failure distinctly —
all three constructors raise with `code == -1` for not-found — records a
structured marker, and still returns `[]`. Enumeration failures keep the bare
swallow.

This marker, not the USB scan, is the **portable** half: it works on Windows and
macOS where `/sys` does not exist. Land it first.

## The security primitive

The constraint is not "the app must never `dlopen`" — a ctypes driver cannot
avoid that. It is that **the web application must never write the file it later
loads**, because that makes the app its own code-delivery channel. The split
runs along *who writes*, not *who loads*:

- **Server (unprivileged `astrodeck`)** — detects, discloses, takes consent,
  names an `asset_id`. Reads `/data/assets`; cannot write it. Loads from it.
- **Broker (privileged)** — owns the catalogue signature check, the network
  dial, the digest, the extraction and the install. Writes `/data/assets`.
  Never loads anything it installed.

**No broker exists today.** A grep for `AF_UNIX` across `server/astrodeck`
returns nothing. **[r]** The fetcher and the broker are **one work item**, not
two — which is also the cheapest moment to get the verb set right.

### Verbs

Fixed, typed, JSON over `AF_UNIX` at `/run/astrodeck/broker.sock`,
`srw-rw---- root:astrodeck`.

```
list_assets       {}                                   -> [{asset_id, version, sha256, installed_at, source}]
media_candidates  {asset_id}                           -> [{token, filename, bytes, sha256, matches_pin}]
install_asset     {asset_id, version, source}          -> {job_id}
                    source = {"kind":"network"} | {"kind":"media","token":"<opaque>"}
asset_job         {job_id}                             -> {state, phase, bytes, total, error_code, error_detail}
remove_asset      {asset_id}                           -> {removed}
verify_assets     {}                                   -> [{asset_id, ok, measured_sha256}]
record_consent    {asset_id, version, by, quote_sha256, catalogue_sha256} -> {at}
withdraw_consent  {asset_id}                           -> {removed}
```

Note what is **absent**: no `url`, no `path`, no caller-supplied `sha256`, no
`mode`, no `dest`. `media_candidates` returns opaque tokens the broker minted
from its own enumeration, so even the sideload path never lets the server name a
filesystem path. **The catalogue lookup is the security property; a URL
parameter would delete it.**

Consent is bound to the **bytes and the words that were shown** — a row must
carry both the catalogue digest and the hash of the licence text disclosed.
Either differing means `consent_lapsed` and the UI must re-disclose.

## Storage

`/data/assets` — on the eMMC, so fetched assets survive both an application
update (which replaces the release directory) and an OS re-flash.

This needs a new concept: **there is no `/data` and no `ASTRODECK_DATA_DIR`
today**, and `VENDOR_ROOT` is computed from `__file__` (`sdk_paths.py:30`), so
it always resolves inside `releases/<version>/` — anything written there is
destroyed by the next update. **[r]**

## Offline, and the sideload path

The honest framing first: the appliance is *used* at dark sites and
*provisioned* at home. So the primary answer is **UX** — surface the fetch during
onboarding while the customer is online, so a dark site is never the first time
the box mentions it. No sideload cleverness compensates for discovering this at
11 p.m. under a clear sky.

For the genuinely offline customer: **`/data/assets/incoming/`**, a drop
directory (`root:astrodeck 0775`) reachable by scp, by mounting the eMMC, or by
USB stick. The broker enumerates and hashes it; `install_asset` takes the
*identical* validation path with only the network dial skipped.

A real benefit falls out of pinning: because provenance comes from the digest
and not the transport, the file can come from any mirror, a friend's laptop or a
vendor CD, and the box accepts it **iff** it is the bytes we pinned. Pinning is
what makes an untrusted transport safe.

**No HTTP upload.** The repo has no upload surface today, and adding one costs a
dependency, a hidden-import, a streaming path, a size ceiling and a new
authenticated write surface — to move bytes that must cross into the privileged
side anyway. **No USB automount** in v1 either: `mount -t auto` on
attacker-supplied media is a worse primitive than anything else in this design.

## Three live defects this must fix on the way past

| Defect | Evidence | Effect |
|---|---|---|
| **The PyInstaller binary ships the Player One libraries the tarball strips.** `build_release.py:66` has `_UNSHIPPABLE_VENDOR_DIRS = frozenset({"playerone"})` and removes every library suffix; `packaging/astrodeck.spec` copies `server/astrodeck/vendor` **wholesale**, its own comment saying "Copied wholesale… (and harmlessly, the others)." | `scripts/build_release.py:66,70-85`; `packaging/astrodeck.spec:43-46` **[v]** | Two packagers, one policy, one of them not applying it. Whether anything was ever *conveyed* depends on whether a binary was published — the release workflow has one run in history and it had no binaries job, so probably not. Fix before that changes. |
| **The Credits screen asserts we redistribute six Player One binaries.** `gen_credits.py` walks the **dev tree** and appends `"Binaries shipped: …"` with `tier="redistributed-binary"`. | `ui/src/credits.generated.json` entry "Player One Camera SDK", group *"Needs an owner decision"* **[v]** | The compliance page states as fact what the compliance fix undid. `collect_vendor` must take `delivery` from the catalogue, not infer it from the build machine's disk. |
| **The satisfied-probe and the loader read different environment variables.** `licensing.py:93` probes `PLAYERONE_SDK_DIR`; the loader uses `ASTRODECK_PLAYERONE_SDK_DIR`. The unprefixed name appears nowhere else in the repo. | `server/astrodeck/licensing.py:93` vs `devices/cameras/player_one_sdk.py:207,219`, `tests/test_sdk_paths.py:138-140` **[v]** | They can disagree in both directions. Since release builds strip the binaries, the env var is the *only* resolution route — so an operator who does it right gets a working camera and a Credits screen saying it is unavailable. |

## Compliance representation

`licence_policy.py` today reasons only about obligations incurred by *shipping*;
there is no "we do not distribute this" concept. The precedent exists in prose —
`LicenseRef-Terms-Of-Service` already carries a fetched-never-bundled asset
(2MASS) — so add it to the type system:

- `_p("LicenseRef-Fetched-Not-Distributed", (), "Not shipped by AstroDeck.
  Fetched on this machine under the licensor's own terms.")` — **`requires=()`**
  is load-bearing: declaring a fetched SDK `proprietary` trips `_FLAGGED` and
  hoists it into *"Needs an owner decision"*, which is exactly where the Player
  One entry sits now. **A resolved decision is not an open question**, and
  leaving it there trains the owner to ignore that group.
- New credits group: *"Fetched on your machine, never shipped."*

Per asset the screen shows the licensor's verbatim quote **separated from our
reading**; why it is fetched (*not permitted to redistribute* and *too large to
bundle* are different facts and must not look alike); the pinned version, digest
and source URL; whether it is installed here and when; and `without` — what does
not work until it is. "Unavailable" with no consequence attached reads as a bug.

## What we will not build

1. **No "install anyway" / "accept this digest" override, anywhere.** It is the
   control that gets clicked every time, after which the pin protects nothing.
   A mismatch is a dead stop with two honest routes, both re-entering the same
   validation.
2. **No auto-fetch at boot and no silent background fetch.** An appliance that
   phones a vendor unprompted is the thing D10 exists to avoid — and it weakens
   the only argument that makes fetching materially different from redistributing.
3. **No vendor index scraping.** A theme change yields zero candidates silently;
   a compromised page yields a URL. Pins travel in releases.
4. **No execution of anything from an archive, ever.** QHY's supported install
   path is a root shell script that `cp -a`s into eight system directories with
   no `DESTDIR`. If QHY ever happens it is re-expressed as catalogue rows.
5. **No QHY and no ToupTek in v1.** QHY has no licence file anywhere, ~106
   firmware images and a load-at-plug pipeline; ToupTek has no licence text and a
   versionless moving endpoint that would trip the pin on every silent bump. Both
   are product decisions with a bad effort-to-exposure ratio. **[r]**
6. **No second signing key.** Reuse the release Ed25519 key.
7. **No generic "download this URL" verb**, not even admin-only.
8. **No apt/non-free route in v1** — though Debian carries
   `libplayeronecamera2t64` for arm64 in non-free with the licence transcribed in
   full, which is signed, mirrored and someone else's rot problem. Trixie ships
   upstream 3.1.0 (Dec 2022) against bindings verified at 3.10.1, so it needs a
   header diff first. The catalogue's ordered `channels` list is where it lands
   later without a redesign. **[r]**

## Corrections to carry forward

- **ASTAP is not a fetch case** — MPL-2.0 grants redistribution and
  `build_release.py` already ships the notice. But its SourceForge artifact is
  versionless and rebuilt continuously, so `fetch_astap.py`'s recorded sha256 is
  a *record of what was fetched*, not a pin. **[r]**
- **`stage.py`'s `safe_extract` must not be reused.** Its blanket refusal of all
  link members is stricter than tarfile's `data` filter and would reject exactly
  the vendor layout `sdk_paths.py` documents as normal (`libFoo.so` → 
  `libFoo.so.3.10.1`). It also has no cumulative-size or member-count ceiling,
  so no decompression-bomb guard. **[r]**
- **`signing.verify_artifact` does `artifact.read_bytes()`** — a 512 MiB
  allocation at the download ceiling, on an Orange Pi. A chunked streaming hasher
  already exists at `sync/manifest.py:210-217`. **[r]**
- **`download.py`'s `timeout=120.0` does not bound wall-clock duration** —
  httpx timeouts are per-phase and the read timeout is per-chunk, so a slow-drip
  server holds the fetch open indefinitely. Needs an outer `asyncio.timeout`. **[r]**
- **`validate_scan_host` is the right mechanic with inverted policy** — it blocks
  private addresses to protect a LAN scan; a vendor fetch must reach the public
  internet and refuse private space. Reuse the shape, not the predicate. **[r]**
- **Platform tags disagree**: `fetch_astap.py` uses `linux-aarch64` /
  `windows-x86_64` while `sdk_paths.platform_tag()` returns `linux-arm64` /
  `win-x64` — with a comment asserting they match. Standardise on
  `platform_tag()`. **[r]**
- **ZWO's missing arm64 libraries are a BUNDLING bug, not a fetch case.** ZWO's
  grant is verbatim MIT with an explicit distribution verb. Putting a
  redistributable library behind the consent flow would be a self-inflicted wound. **[r]**

## Legal questions, stated rather than resolved

Whether an automated in-product fetch of a licence-silent binary makes the
vendor the distributor or us; whether Player One's use-only grant carries an
implied licence to the copy their own server serves, and whether that survives
commercialisation; whether arranging for unlicensed binaries to land on a *sold*
appliance creates exposure regardless of who ran the download.

**Fetching does not manufacture a grant where none exists.** For Player One the
distinction between "we convey a copy" and "the vendor's server conveys a copy
to a customer using a Player One camera for the purpose the grant contemplates"
is real and is the strongest of these. For QHY and ToupTek the problem is
*absence*, not restriction — the fetch removes our most direct exposure and does
not make the end state clean, and it must never be described to a customer as
though it does.

Engineering can shrink all of it by asking. The written request to Player One
recorded at `vendor/playerone/README.md:39` is unanswered; no equivalent has
gone to QHY or ToupTek, where the ask is *easier* because there is no licence
text to contradict. **A one-line yes collapses the entire fetch apparatus for
that vendor into a bundled file.** Record any answer *beside* the verbatim
quote, never instead of it — that discipline is what `licensing.py` was written
to enforce after "permits redistribution" was read as fact for weeks and six
binaries shipped on it.
