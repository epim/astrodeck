# AstroDeck — Settings, Observing Site, Optics & Profiles — Build-Ready Spec

**Date:** 2026-06-15
**Surface owner:** Settings / Site / Optics / Profiles / Plan-library
**Status:** Final, build-ready. Supersedes the draft after three adversarial UX reviews.
**Build order ref:** Panel review item #2 ("Settings/site + plate-solve config") — a true correctness P0.

---

## 0. Overview

This surface fixes a correctness-class P0 and three persistence gaps:

1. **Hardcoded San Francisco site** (`hub.py:38` `self.site = {"latitude": 37.77, "longitude": -122.42}`) poisons `altaz()` (header ALT/AZ, catalog alt sort, transit), the polar compass, and meridian-flip LST for every user not in SF. `POST /api/site` exists (app.py:192) but has **no UI and no persistence** — a restart reverts to SF.
2. **Plate-solve FOV hint hardcoded** to `1.0` (`hub.py:277`). The camera reports `pixel_size_um`/`sensor_width`/`sensor_height`; we compute the real FOV instead.
3. **No equipment profiles** — every reconnect re-enters Alpaca/NINA hosts.
4. **No plan library** — the only persistence is `localStorage["astrodeck-plan"]` (SequenceView.tsx:35).

It adds **one Settings view**, **one backend `config` package** (atomic JSON), **server-computed site/optics readouts** (so the UI never duplicates the load-bearing astronomy math), and four REST resource groups (`config/site/optics`, `profiles`, `plans`, plus a small `geo` helper).

### What changed from the draft after review (every valid critique resolved)

| # | Critique | Resolution |
|---|---|---|
| C1-A1, C1-I33 | Signed lat/lon UI is a silent-error trap; iOS `inputMode="decimal"` omits the minus | **Killed signed entry.** Magnitude box + N/S and E/W segmented toggles. Store stays East-positive; UI never shows a sign. (§2.5 Panel A, §6) |
| C1-A2, C1-B4, C1-H26 | LST is unverifiable by a novice; "site took effect" is false with no mount | Hero readout is **server-computed Sun altitude + tonight's astronomical-dark window + reverse place hint**, not LST. Works with zero devices connected. (§1.4, §2.5 Panel A) |
| C1-A3 | Geolocation hidden on `http://` LAN (the common Pi deployment) | Detect insecure context and **explain it**; add a **"paste coordinates" parser** (accepts `37.77, -122.42`, `122.42° W`, Google-Maps strings). (§2.5 Panel A) |
| C1-B5/6, C2-6 | Timezone is a second source of truth / 400-item picker / dead for sky math | Timezone is **display-only and auto-derived** from the browser; no manual IANA picker. Labeled "for session scheduling (coming)". LST/transit never depend on it. (§2.5 Panel A) |
| C1-C7, C2-10 | Auto-from-camera + no camera → FOV 0 with no explanation | Explicit empty state; render **"—"** not `0.0`; inline "Connect a camera to auto-fill, or enter pixel size + sensor manually". (§2.5 Panel B, §3) |
| C1-C8, C2-3 | Binning math muddy; headline scale is bin-1 but users image binned | FOV is **strictly bin-1** (bin-independent — correct for ASTAP). Image-scale headline is **labeled "at bin 1"** with a live binned sub-line from the actual capture bin. (§1.3, §2.5 Panel B) |
| C1-C9/10 | Fresh install computes FOV 0; no sanity check on FL | Ship sane non-zero defaults; **plausibility nudge** ("that FOV is unusually wide/narrow — double-check focal length"). (§2.5 Panel B) |
| C1-H29, C3-8 | Per-keystroke `GET /api/optics/preview` is a pointless network round-trip; lags/flickers | **Compute scale/FOV client-side instantly** (pure arithmetic, shared formula constant). No preview endpoint. Server echo on save is the source of truth for persisted values. (§2.5 Panel B) |
| C1-D11, C3-9 | Apply toast lies ("4 devices") when devices failed; warnings buried in desktop-only drawer | Apply returns a **per-device outcome array**; UI shows **"2 of 4 connected — Focuser, Wheel failed"** as text, not color. (§1.6 apply, §2.5 Panel C, §3) |
| C1-D12, C2-1 | Capture reads host/port from `describe()` — which doesn't exist | **Backend prerequisite block (§1.1):** add `role/host/port/dev_type/dev_num` to Alpaca devices + `describe()`. Capture warns if network may have changed. |
| C1-D13, C2-9, C2-10 | Single `mode` can't represent mixed rigs; per-profile optics stomps global | Profile stores **per-device backend**; `mode` is derived/`"mixed"`. Optics resolved **per active profile at read time**, never written to global. Auto-detected camera optics **persisted back** so disconnected apply has a real FOV. (§1.5, §1.6) |
| C1-D14 | Rename orphans the slug; `active_profile` (by name) dangles | Profiles keyed by **immutable `id` (uuid)**, name is metadata. `active_profile_id`. Rename mutates name in place. (§1.5, Shared contracts) |
| C1-D15, C2-8 | Apply runs `disconnect_all()` + bypasses engine abort; guarded the wrong button | Apply endpoint **refuses 409 if a sequence/loop/polar is running** unless `force=true`; UI requires **hold-to-confirm** when a rig is connected. Engine aborted via the app-layer guard, not hub. (§1.6, §2.5 Panel C) |
| C1-E16, C2-14 | Slug upsert silently overwrites a different plan | Plans/profiles keyed by **uuid**; save detects a **same-name collision** and prompts "Overwrite 'X'?" (never silent). (§1.7, §2.5 Panel D) |
| C1-E17 | localStorage→server migration can clear the key on a failed POST / re-migrate stale | Migration is **transactional** (clear only after verified 201) and **idempotent** (a one-shot `astrodeck-plan-migrated` flag). (§4) |
| C1-E18 | "Load" clobbers unsaved editor plan | Load checks `editorDirty`; **hold-to-confirm "Discard unsaved plan?"** before replacing. (§2.5 Panel D, §4) |
| C1-E19 | Import error copy blames the user for a version mismatch | Import distinguishes **schema-version too new** ("exported by a newer AstroDeck") from genuinely-invalid. (§1.7) |
| C1-F20/21/22, C3 | Two entry points; three names (Settings/Setup/...); unicode `⚙` | **One name everywhere: "Settings".** **One entry point: header gear button** on all viewports (desktop + mobile), 44px touch target. **Real SVG icon** (inline `gear` path, single-weight), not a unicode glyph. (§2.4) |
| C1-G23, C3-1/4 | Field-error "red border" is invisible in all-red night mode | Error cue is **inline `⚠ <message>` text + dashed/doubled border**, never hue-only. (§3, §5) |
| C1-G24 | Multi-warning Apply path stomps the single toast slot | Apply outcome is rendered **in-panel** (persistent per-device list), not via toast. (§2.5 Panel C) |
| C1-G25, C3-6 | `beforeunload` doesn't fire on in-app `setView()` | In-app **route guard** via store `dirty` flags + persistent rail/header dirty dot; **auto-save site on blur** option. No `beforeunload`. (§2.3, §3) |
| C1-H27 | Re-implementing GMST/LST in JS duplicates the load-bearing formula untested | **No client-side sidereal math.** All site-derived readouts come from the server (`/api/site/sky`). Single source of truth. (§1.4) |
| C1-H28, C2-12 | `bus.publish("config", …)` partial merge leaves `active_profile`/computed stale; unverified | `config` WS event carries a **version int only**; the store **re-GETs `/api/config`** on receipt. No partial merge. `bus.publish` is schemaless (verified events.py usage in hub) — no `events.py` change. (§2.3, §1.6) |
| C2-2 | `status.optics`/`status.site` flicker to undefined (poll_status wholesale-replaces, omits them) | Add `site` + `optics` to **`poll_status()`**, not just `summary()`. Cheap (in-process). (§1.2) |
| C2-5 | Epoch (J2000/JNow) contract undocumented | **Documented contract** in §1.2: header alt/az is self-consistent (JNow mount coords vs site); FOV value is epoch-free; framing assistant (future) must convert. |
| C2-13 | Mount has its own SiteLat/Long; app-side and mount-side LST disagree | On `PUT /api/site`, **push site to a connected mount** if supported (best-effort, logged). (§1.2, §1.6) |
| C2-15, C2-16 | Concurrent multi-client write last-writer-wins; geolocation precision over-claimed | Optimistic **version check** on `PUT` (409 on stale). Geolocation labeled **"approximate"**, stored to 4dp but UI says "~". (§1.6, §2.5 Panel A) |
| C2-17 (Windows `os.replace`) | AV scanners lock new files; `os.replace` can `PermissionError` | Atomic write wrapped in a **short retry**; `.bak` via `os.replace` too. (§1.1) |
| C3-2, C3-5 | `Toggle` not accessible; sub-44px; on/off color-only | `Toggle` gains `role="switch" aria-checked aria-label`, a **44px hit wrapper on touch**, and an explicit **ON/OFF text** cue. (§2.6, §5) |
| C3-3 | No focus rings; form-heavy view is least navigable | **Global `:focus-visible` ring** (one CSS rule). (§2.7) |
| C3-4 | Corruption-preventing helper text in failing-contrast `--text-dim` | Longitude/coords helper uses **`--text` ≥12px**, not `--text-dim`. (§2.5 Panel A, §2.7) |
| C3-10 | `.label` is 10px, below the design's own ≥11px rule | **Bump `.label` to 11px** globally (low-risk, app-wide win). (§2.7) |
| C3-11 | Dirty dot next to the panel bracket reads as a glitch | Dirty indicator lives **next to the Save button**, not the panel corner. (§2.5, §3) |
| C3-12 | `Stat` truncates the composite FOV string | FOV gets a **full-width row**, not a truncating `Stat` cell. (§2.5 Panel B) |
| C3-13 | "mixed" source chip is opaque | Source chip **expands inline**: "FL: manual · pixel/sensor: camera". (§2.5 Panel B) |

**Rejected / deferred (with reasons):**

- **C1-A2 "tiny static map pin":** rejected for this surface. A map tile fetch needs an external tile provider and network on a field Pi (often offline). We give the cheaper, offline-safe equivalents that actually catch a sign error: **server-computed Sun altitude + dark window + a coarse hemisphere/place hint** derived locally from lat/lon sign (no network). A real sky-atlas/map lands with the framing-assistant surface (Tier-1 item 4), not here.
- **C2-5 epoch conversion (J2000↔JNow):** out of scope to *implement* here (no framing assistant on this surface). We **document the contract** so the future framing surface owns it. Header alt/az is already self-consistent (mount JNow vs site), verified in hub.py:427-429.
- **C1-I30 elevation ft/m converter:** keep a single **meters** field with a loud unit label; elevation is sub-arcmin for alt/az and is only forwarded to the mount. A unit converter is over-engineering for a decorative-for-sky-math field. (Field kept, not dropped, because the mount can use it.)
- **C2-13 mount push for NINA bridge:** best-effort only; if the mount is NINA-bridged and exposes no site setter, we log and move on (the NINA profile's own site governs there).

---

## 1. Backend

### 1.0 Disjoint-file ownership (so parallel implementers don't collide)

| Implementer | Owns (creates) | Owns (edits) | Must NOT touch |
|---|---|---|---|
| **BE-A (config/site/optics)** | `server/astrodeck/config.py`, `server/tests/test_config.py` | `hub.py` (site property, effective_optics, optics in poll_status+summary, FOV in solve, push-site-to-mount) | plans/profiles modules |
| **BE-B (profiles)** | `server/astrodeck/profiles.py`, `server/tests/test_profiles.py` | `hub.py` (apply_profile, capture_profile) **— see merge note** | config internals, plans |
| **BE-C (device identity)** | — | `server/astrodeck/devices/alpaca.py`, `server/astrodeck/devices/base.py` | hub logic, config |
| **BE-D (plans)** | `server/astrodeck/plans.py`, `server/tests/test_plans.py` | — | config, profiles, hub |
| **BE-E (API)** | — | `server/astrodeck/api/app.py` (new endpoints + remove old `SiteBody`) | device/hub internals |

**hub.py merge note:** BE-A and BE-B both edit `hub.py`. Split by clearly-delimited method blocks: BE-A adds the `# --- site & optics` block (property + `effective_optics` + `poll_status`/`summary`/`solve` edits); BE-B adds the `# --- profiles` block (`apply_profile` + `capture_profile`). They never edit the same lines. BE-C is a hard prerequisite for BE-B (device identity must land first).

### 1.1 New package `server/astrodeck/config.py`

Single source of truth for persisted server config. One JSON file, atomic write, loaded once, mutated through typed helpers.

```python
CONFIG_DIR  = Path(__file__).resolve().parents[2] / "config"   # server/config/
CONFIG_FILE = CONFIG_DIR / "astrodeck.json"
PLANS_DIR   = CONFIG_DIR / "plans"
PROFILES_DIR = CONFIG_DIR / "profiles"

ARCSEC_PER_RAD = 206.265            # 206265 arcsec/rad / 1000 (µm↔mm) — the one true constant
```

**Models** (canonical; REST bodies reuse them):

```python
class Site(BaseModel):
    name: str = "My Observatory"
    latitude: float  = Field(0.0, ge=-90,  le=90)     # +N (stored signed)
    longitude: float = Field(0.0, ge=-180, le=180)    # +E (East-positive; matches coords.lst_hours)
    elevation_m: float = Field(0.0, ge=-430, le=9000) # forwarded to mount; ignored by altaz
    # timezone is NOT stored: sky math is longitude-only; display tz comes from the browser.

class Optics(BaseModel):
    focal_length_mm: float = Field(530.0, gt=0, le=20000)
    pixel_size_um:   float = Field(0.0,  ge=0, le=50)   # 0 = use camera
    sensor_width_px:  int  = Field(0,    ge=0)          # 0 = use camera
    sensor_height_px: int  = Field(0,    ge=0)          # 0 = use camera
    auto_from_camera: bool = True

class AppConfig(BaseModel):
    version: int = 1                  # bumped on every save (optimistic-concurrency token)
    site: Site = Site()
    optics: Optics = Optics()
    active_profile_id: str | None = None
```

> **Longitude sign convention — load-bearing.** `coords.lst_hours(lon)` is `(gmst + lon/15) % 24` → **East-positive**. We keep it. The UI collects magnitude + E/W toggle and converts at the boundary; the model is always signed East-positive. **Do not flip** `lst_hours` (would silently break transit + polar). `navigator.geolocation` already returns signed East-positive (ISO 6709), so SF → `-122.42` round-trips correctly — assert this in `test_config.py` (C3-7).

**Pure optics math** (also exposed to the UI as a shared TS constant — never duplicated as logic):

```python
def image_scale_arcsec_px(focal_mm, pixel_um, binning=1) -> float:
    return ARCSEC_PER_RAD * (pixel_um * binning) / focal_mm if focal_mm > 0 else 0.0

def fov_deg(focal_mm, pixel_um, w_px, h_px) -> tuple[float, float, float]:
    # FOV is bin-INDEPENDENT (fewer, bigger pixels cover the same sky). Always bin-1.
    s = image_scale_arcsec_px(focal_mm, pixel_um, binning=1)
    fw, fh = s * w_px / 3600.0, s * h_px / 3600.0
    return fw, fh, (fw*fw + fh*fh) ** 0.5
```

**`ConfigStore`** (module singleton, like `hub`):

```python
class ConfigStore:
    def cfg(self) -> AppConfig
    def set_site(self, site: Site, expected_version: int | None) -> AppConfig   # 409 on stale
    def set_optics(self, optics: Optics, expected_version: int | None) -> AppConfig
    def bump_and_save(self) -> AppConfig    # version += 1; atomic write
config_store = ConfigStore()
```

- `_load`: missing file → defaults + immediate save. Corrupt/invalid → defaults + `bus.log("warning", "config reset to defaults: <err>", "config")` + back up bad file to `astrodeck.json.bak` (via `os.replace`).
- **Atomic write (Windows-safe, C2-17):** write `*.tmp`, `os.replace(tmp, target)` inside a **3-try/120ms-backoff retry** catching `PermissionError` (AV scanners lock new files on Windows). `.bak` uses the same `os.replace`.
- **Optimistic concurrency (C2-15):** every `PUT` carries the client's last-seen `version`; a mismatch returns **409 `{detail, current: AppConfig}`** so the UI can reconcile rather than silently clobber a co-user's field.

### 1.2 Wire config into `hub.py` (BE-A)

Replace the hardcoded site; bind to the store.

- **Remove** `hub.py:38`. Add a `site` **property** returning a dict (so `poll_status`/catalog/polar stay untouched — minimal blast radius):

```python
from .config import config_store, image_scale_arcsec_px, fov_deg
@property
def site(self) -> dict:
    s = config_store.cfg().site
    return {"latitude": s.latitude, "longitude": s.longitude,
            "elevation_m": s.elevation_m, "name": s.name}
```

- **`effective_optics()`** resolves config-override-or-camera, with explicit `source` and an availability flag:

```python
def effective_optics(self) -> dict:
    o = config_store.cfg().optics
    # per-active-profile override wins (resolved at READ time, never written to global) — C2-9
    prof = profiles.active(config_store.cfg().active_profile_id)   # may be None
    if prof and prof.optics: o = prof.optics
    cam = self.devices.get("camera")
    px = o.pixel_size_um   or (getattr(cam, "pixel_size_um", 0.0) if cam and cam.connected else 0.0)
    w  = o.sensor_width_px or (getattr(cam, "sensor_width", 0)    if cam and cam.connected else 0)
    h  = o.sensor_height_px or (getattr(cam, "sensor_height", 0)  if cam and cam.connected else 0)
    have = bool(px and w and h)
    fw, fh, diag = fov_deg(o.focal_length_mm, px, w, h) if have else (0.0, 0.0, 0.0)
    src = ("config" if (o.pixel_size_um and o.sensor_width_px) else
           "camera" if (not o.pixel_size_um and not o.sensor_width_px and cam) else
           "mixed"  if have else "none")
    return {"focal_length_mm": o.focal_length_mm, "pixel_size_um": px,
            "sensor_width_px": w, "sensor_height_px": h, "have_optics": have, "source": src,
            "image_scale_arcsec_px": round(image_scale_arcsec_px(o.focal_length_mm, px), 2) if have else None,
            "fov_w_deg": round(fw, 3) if have else None,
            "fov_h_deg": round(fh, 3) if have else None,
            "fov_diag_deg": round(diag, 3) if have else None}
```

- **`poll_status()` (C2-2, the flicker fix):** add `site` + `optics` to the returned dict so the 2-second wholesale `set({status})` (store.ts:61) keeps them present:

```python
out["site"]   = {"name": s["name"], "latitude": s["latitude"], "longitude": s["longitude"]}
out["optics"] = self.effective_optics()     # in-process, no device I/O — cheap
```

  Also add the same `"optics"` to `summary()` next to `"site"`.

- **Plate-solve FOV (hub.py:276-277):**

```python
opt = self.effective_optics()
fov_hint = opt["fov_diag_deg"] or None   # None → ASTAP radius search (preserves old behavior)
result = await solver.solve(tmp, ra_hint=ra_hint, dec_hint=dec_hint, fov_deg_hint=fov_hint)
bus.log("info", f"plate solving with {solver.name} (fov hint {fov_hint or 'auto'})…", "solve")
```

  FOV is bin-1 / bin-independent — correct even though the solve frame is binned 2× (alpaca.py:182).

- **Push site to mount (C2-13):** `push_site_to_mount()` — best-effort; on a connected Alpaca telescope, PUT `sitelatitude`/`sitelongitude`/`siteelevation`; wrap in try/except + `bus.log`. Called from the API after a successful `PUT /api/site`. No-op for NINA/sim that don't expose setters.

- **Epoch contract (documented, C2-5):** mount coords are JNow (base.py:119); `altaz()` computes against JNow + site → **self-consistent**, no change. `fov_diag_deg` is epoch-free (an angular extent). The future framing assistant must convert J2000 catalog ↔ JNow; this surface does not.

### 1.3 Optics binning (display only) (C1-C8, C2-3)

- **FOV stored/echoed is always bin-1.** Never send a binned scale to anything that re-derives FOV (would halve the hint).
- The UI shows the headline image scale **"at bin 1"** and computes the binned convenience line from the **camera's current/typical bin** when known, else bin 2 as a labeled example. Pure-arithmetic, client-side (§2.5 Panel B).

### 1.5 New package `server/astrodeck/profiles.py` (BE-B)

A profile is a named, replayable connection intent. We store *which backend + which device at which address*, not live handles. **Keyed by immutable `id`** so rename never orphans (C1-D14).

```python
class ProfileDevice(BaseModel):
    role: str                   # camera|telescope|focuser|filterwheel|switch
    backend: str                # "alpaca" | "nina"   (PER-DEVICE — supports mixed rigs, C1-D13)
    host: str = ""
    port: int = 0
    dev_type: str = ""
    dev_num: int = 0
    name: str = ""

class Profile(BaseModel):
    id: str                                   # uuid4, immutable identity
    name: str                                 # display only; mutable; may collide → prompt
    devices: list[ProfileDevice] = []
    nina_host: str | None = None
    nina_port: int = 1888
    phd2_host: str | None = None
    phd2_port: int = 4400
    optics: Optics | None = None              # per-rig override, resolved at read time (never stomps global)
    site_name: str | None = None
    @property
    def mode(self) -> str:                     # derived, never stored
        b = {d.backend for d in self.devices}
        return ("nina" if self.nina_host and not b else
                "alpaca" if b == {"alpaca"} else "mixed" if b else "empty")

class ProfileLibrary:
    def list(self) -> list[dict]              # rows: {id, name, mode, devices_count, site_name, active}
    def get(self, id: str) -> Profile
    def save(self, p: Profile) -> dict         # upsert by id; atomic write profiles/<id>.json
    def name_exists(self, name, exclude_id) -> bool   # for overwrite-prompt
    def rename(self, id, name) -> dict         # mutate name in place (slug never changes)
    def delete(self, id: str) -> None
    def active(self, id: str | None) -> Profile | None
profiles = ProfileLibrary()
```

### 1.6 Profile apply / capture (BE-B, lives in hub for connection sequencing)

```python
async def apply_profile(self, p: Profile) -> dict:
    await self.disconnect_all()
    results = []                                  # per-device outcome — C1-D11
    for d in p.devices:
        try:
            if d.backend == "alpaca":
                await self.connect_alpaca_device(d.role, d.host, d.port, d.dev_type, d.dev_num, d.name)
            results.append({"role": d.role, "ok": True})
        except Exception as e:
            results.append({"role": d.role, "ok": False, "error": str(e)})
            bus.log("warning", f"profile '{p.name}': {d.role} failed: {e}", "profile")
    if p.nina_host and any(d.backend == "nina" for d in p.devices):
        try: await self.connect_nina(p.nina_host, p.nina_port); results.append({"role": "nina", "ok": True})
        except Exception as e: results.append({"role": "nina", "ok": False, "error": str(e)})
    if p.phd2_host:
        try: await self.connect_phd2(p.phd2_host, p.phd2_port); results.append({"role": "phd2", "ok": True})
        except Exception as e: results.append({"role": "phd2", "ok": False, "error": str(e)})
    # auto-detected camera optics persisted back so disconnected re-apply has a real FOV — C2-10
    cam = self.devices.get("camera")
    if cam and cam.connected and config_store.cfg().optics.auto_from_camera:
        o = config_store.cfg().optics
        config_store.set_optics(o.model_copy(update={
            "pixel_size_um": o.pixel_size_um or cam.pixel_size_um,
            "sensor_width_px": o.sensor_width_px or cam.sensor_width,
            "sensor_height_px": o.sensor_height_px or cam.sensor_height}), expected_version=None)
    config_store.cfg().active_profile_id = p.id; config_store.bump_and_save()
    ok = sum(r["ok"] for r in results)
    return {"summary": self.summary(), "results": results, "connected": ok, "total": len(results)}

async def capture_profile(self, name: str) -> Profile:
    devs = []
    for role, d in self.devices.items():
        if role not in ROLES: continue
        if getattr(d, "backend", None) == "alpaca":          # device identity added by BE-C
            devs.append(ProfileDevice(role=role, backend="alpaca", host=d.host, port=d.port,
                                      dev_type=d.dev_type, dev_num=d.dev_num, name=d.name))
        elif self.mode == "nina":
            devs.append(ProfileDevice(role=role, backend="nina", name=d.name))
    p = Profile(id=str(uuid4()), name=name, devices=devs,
                nina_host=(self.nina_client.host if self.nina_client else None))
    profiles.save(p); return p
```

- **Apply is destructive (C1-D15, C2-8):** the **API endpoint** (not hub) refuses `409` if `engine.running or hub.looping or hub.polar.running` unless `force=true`; when forced it `await engine.abort()` first (hub can't — engine lives in app.py). UI gates this with hold-to-confirm.
- **`config` WS event (C1-H28, C2-12):** after any config/profile write, `bus.publish("config", version=<int>)`. The store's `case "config"` **re-GETs `/api/config`** (no partial merge). `bus.publish` is schemaless (verified by existing `bus.publish("mount", …)` usage), so **no `events.py` change**.

### 1.7 New package `server/astrodeck/plans.py` (BE-D)

One file per plan under `config/plans/<id>.json`; **keyed by uuid** (C1-E16/C2-14). A stored plan reuses `SequencePlan` verbatim (models.py) plus an `id` + `schema_version` envelope on disk.

```python
PLAN_SCHEMA = 1
class PlanLibrary:
    def list(self) -> list[dict]   # {id, name, frames, integration_min, targets, mtime}
    def get(self, id: str) -> SequencePlan
    def save(self, plan: SequencePlan, id: str | None) -> dict   # upsert by id
    def name_exists(self, name, exclude_id) -> bool
    def delete(self, id: str) -> None
    def export_bytes(self, id: str) -> bytes
    def import_plan(self, raw: dict) -> dict      # see version handling below
plan_library = PlanLibrary()
```

`list()` returns lightweight rows (no full target arrays). **Import version handling (C1-E19):** if `raw.get("schema_version", 1) > PLAN_SCHEMA` → `422 {detail: "This plan was exported by a newer AstroDeck version", code: "version_too_new"}`; if `SequencePlan(**raw["plan"])` raises → `422 {detail: <pydantic>, code: "invalid"}`. The UI maps the two codes to different copy.

### 1.x REST endpoints (BE-E, in `api/app.py`)

Remove `SiteBody` + the old `POST /api/site` body-mutation (app.py:128-131, 192-195). Import config/profile/plan singletons. All bodies reuse `config.py`/`profiles.py`/`models.py` pydantic types.

**Config / Site / Optics**

| Method | Path | Body / Query | Returns |
|---|---|---|---|
| GET | `/api/config` | — | `AppConfig` + `{optics_computed: OpticsComputed}` |
| PUT | `/api/site` | `{site: Site, version: int}` | `AppConfig` (echo) · 409 `{detail, current}` on stale · also calls `push_site_to_mount()` + `bus.publish("config", version)` |
| PUT | `/api/optics` | `{optics: Optics, version: int}` | `AppConfig` + `optics_computed` |
| GET | `/api/site/sky` | query `lat`,`lon` (optional; default saved) | `{sun_alt_deg, dark_window:{start_iso,end_iso}|null, place_hint, lst_str}` — **all server-computed** (C1-H27) |

`PUT /api/site` requires **no device connected**; it never blocks. Keep `POST /api/site` as a thin alias → `PUT` for backward compat (referenced nowhere in UI but cheap).

**`/api/site/sky` math (server-side, single source of truth):** reuse `coords.lst_hours`; add a small `sun_altaz(lat, lon, t)` (low-precision NOAA solar position, ±0.1° — plenty) + `dark_window()` (next Sun < −18° interval tonight) in `coords.py`. `place_hint` is a coarse offline label from lat/lon sign + magnitude (e.g. "N hemisphere, W longitude · ~Pacific N. America") — **no network tile fetch**. This is the novice sanity check that catches a flipped sign (C1-A2/B4).

**Profiles**

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/profiles` | — | `[ProfileRow]` |
| GET | `/api/profiles/{id}` | — | `Profile` |
| POST | `/api/profiles` | `Profile` (no id ⇒ create) | row (upsert by id) |
| POST | `/api/profiles/capture` | `{name}` | `Profile` (built from `hub.devices`, saved) · 409 if `mode=="none"` |
| PATCH | `/api/profiles/{id}` | `{name}` | row (rename in place) |
| DELETE | `/api/profiles/{id}` | — | `{deleted: id}` |
| POST | `/api/profiles/{id}/apply` | `{force?: bool}` | `_spawn("profile", …)` → streams; **409 if running and not force** |

**Plans**

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/plans` | — | `[PlanRow]` |
| GET | `/api/plans/{id}` | — | `SequencePlan` |
| POST | `/api/plans` | `{plan: SequencePlan, id?: str, overwrite?: bool}` | row · 409 `{code:"name_collision", existing_id}` if same-name + no id + `!overwrite` |
| DELETE | `/api/plans/{id}` | — | `{deleted: id}` |
| GET | `/api/plans/{id}/export` | — | `FileResponse` JSON, `Content-Disposition: attachment; filename="<name>.astroplan.json"` |
| POST | `/api/plans/import` | raw plan envelope | row · 422 with `code` (`version_too_new`/`invalid`) |

### Backend touch-list

- **create:** `config.py`, `profiles.py`, `plans.py`, `tests/test_config.py`, `tests/test_profiles.py`, `tests/test_plans.py`.
- **edit:** `hub.py` (site property, effective_optics, poll_status+summary, FOV, push_site_to_mount, apply_profile, capture_profile); `api/app.py` (remove SiteBody/old set_site; add endpoints); `devices/alpaca.py` + `devices/base.py` (device identity — BE-C); `coords.py` (sun_altaz, dark_window).
- **`.gitignore`:** N/A (env says not a git repo); still create `config/` at runtime via `mkdir(parents=True, exist_ok=True)`.

---

## 1.1 BACKEND PREREQUISITE — device identity (BE-C, blocks Profiles)

Without this, `capture_profile` has nothing to read (verified: `describe()` returns only `{name,kind,connected}` base.py:73; host/port baked into `AlpacaConnection.base` alpaca.py:88; no role on device). Required additions:

1. `AlpacaConnection.__init__` stores `self.host`, `self.port`.
2. `_AlpacaDevice.__init__` stores `self.host = conn.host`, `self.port = conn.port`, `self.backend = "alpaca"`; it already has `dev_num`; expose `self.dev_type` (class attr already set per subclass).
3. `hub.connect_alpaca_device` sets `dev.role = role` after construction (device is keyed by role but doesn't know its own role).
4. Override `describe()` on `_AlpacaDevice` to add `{host, port, dev_type, dev_num, role, backend}`.

These four additions are the gate for the entire Profiles feature and must land before BE-B.

---

## 2. Frontend

### 2.0 Disjoint-file ownership

| Implementer | Owns (creates) | Owns (edits) |
|---|---|---|
| **FE-A (shell + Settings view)** | `ui/src/views/SettingsView.tsx`, `ui/src/views/settings/SitePanel.tsx`, `OpticsPanel.tsx`, `ProfilesPanel.tsx`, `PlansPanel.tsx` | `ui/src/App.tsx` (header gear + VIEWS), `ui/src/store.ts` (config/editor slices, `settings` view) |
| **FE-B (shared bits)** | `ui/src/components/ConfirmButton.tsx`, `ui/src/components/Segmented.tsx`, `ui/src/lib/optics.ts`, `ui/src/icons.tsx` (gear + reused SVGs) | `ui/src/api.ts` (put/patch/del), `ui/src/types.ts`, `ui/src/components/ui.tsx` (Toggle a11y), `ui/src/index.css` (focus ring, `.label` 11px) |
| **FE-C (plan interop)** | — | `ui/src/views/SequenceView.tsx` (library load/save + migration; lift plan to store) |

FE-A imports the primitives FE-B creates; agree the `lib/optics.ts` and `types.ts` signatures up front (below) so the two never block.

### 2.1 `ui/src/api.ts` — add verbs (FE-B)

```ts
export const api = {
  get:  <T=unknown>(p: string) => req<T>("GET", p),
  post: <T=unknown>(p: string, b?: unknown) => req<T>("POST", p, b),
  put:  <T=unknown>(p: string, b?: unknown) => req<T>("PUT",  p, b),
  patch:<T=unknown>(p: string, b?: unknown) => req<T>("PATCH", p, b),
  del:  <T=unknown>(p: string) => req<T>("DELETE", p),
};
```
`req` already throws on non-2xx with the parsed `detail`; ensure it surfaces the JSON `code` field (for `name_collision`/`version_too_new`).

### 2.2 `ui/src/types.ts` — additions (FE-B)

```ts
export interface Site { name: string; latitude: number; longitude: number; elevation_m: number; } // lon is +E
export interface Optics { focal_length_mm: number; pixel_size_um: number; sensor_width_px: number; sensor_height_px: number; auto_from_camera: boolean; }
export interface OpticsComputed {
  have_optics: boolean; source: "config"|"camera"|"mixed"|"none";
  focal_length_mm: number; pixel_size_um: number; sensor_width_px: number; sensor_height_px: number;
  image_scale_arcsec_px: number|null; fov_w_deg: number|null; fov_h_deg: number|null; fov_diag_deg: number|null;
}
export interface AppConfig { version: number; site: Site; optics: Optics; optics_computed: OpticsComputed; active_profile_id: string|null; }
export interface SkyInfo { sun_alt_deg: number; dark_window: { start_iso: string; end_iso: string }|null; place_hint: string; lst_str: string; }
export interface ProfileDevice { role: string; backend: "alpaca"|"nina"; host: string; port: number; dev_type: string; dev_num: number; name: string; }
export interface Profile { id: string; name: string; devices: ProfileDevice[]; nina_host: string|null; nina_port: number; phd2_host: string|null; phd2_port: number; optics: Optics|null; site_name: string|null; }
export interface ProfileRow { id: string; name: string; mode: "alpaca"|"nina"|"mixed"|"empty"; devices_count: number; site_name: string|null; active: boolean; }
export interface ApplyResult { summary: RigStatus; results: { role: string; ok: boolean; error?: string }[]; connected: number; total: number; }
export interface PlanRow { id: string; name: string; frames: number; integration_min: number; targets: number; mtime: number; }
```
Extend `RigStatus` so header/Settings read **live** site/optics that now survive the 2s replace (C2-2):
```ts
// RigStatus +=
site?: { name: string; latitude: number; longitude: number };
optics?: OpticsComputed;
```

### 2.3 `ui/src/store.ts` — slices (FE-A)

```ts
// state
config: AppConfig | null;
editorPlan: SequencePlan | null;     // lifted out of SequenceView so Settings & Plan share one object
editorDirty: boolean;                // guards "Load" overwrite (C1-E18)
siteDirty: boolean; opticsDirty: boolean;   // in-app route guard, NOT beforeunload (C1-G25)

// actions
loadConfig: () => Promise<void>;            // GET /api/config → set({config})
setEditorPlan: (p, dirty?) => void;
setSiteDirty/​setOpticsDirty: (b) => void;
```
`ViewName` union gains `"settings"`. `handleEvent` adds:
```ts
case "config":
  get().loadConfig();   // re-GET, never partial-merge (C1-H28/C2-12)
  break;
```
`App` calls `loadConfig()` once in the existing `useEffect(() => { connectWs(); }, [])`.

### 2.4 Navigation entry — ONE name, ONE place, real SVG (C1-F20/21/22)

- **Name:** **"Settings"** everywhere — view title, header tooltip, all toast/help copy. No "Setup".
- **Entry point (single, all viewports):** a **header gear button** placed left of NIGHT/LOG, on desktop **and** mobile. Not a 9th rail/bottom-nav cell (keeps 8 session tabs; avoids the 41px cram). `onClick={() => setView("settings")}`.
- **Icon:** an inline single-weight **`gear` SVG** in `ui/src/icons.tsx` (Lucide `settings` path), `currentColor`, 18px — **not** a unicode glyph. The rail/bottom-nav unicode set is out of scope to replace here (the panel review owns that), but this new control ships SVG from day one.
- **Touch:** the gear button gets `min-h-11 min-w-11` (44px) on mobile (`sm:` smaller on desktop to match NIGHT/LOG). It is the only new header control; we do not add a third tiny 28px button — it is sized correctly from the start.
- **Active state:** when `view==="settings"`, the gear is `text-accent`. A **dirty dot** (filled when `siteDirty||opticsDirty`) sits on the gear so unsaved edits are visible from any view (the in-app route guard, C1-G25/C3-6).

### 2.5 `ui/src/views/SettingsView.tsx` (FE-A)

Four panels, `grid gap-4 lg:grid-cols-2` (Site+Optics top, Profiles+Plans bottom); collapses to one column ≤ `lg`. Uses existing `Panel`/`Field`/`Stat` plus the new `Segmented`/`ConfirmButton`/accessible `Toggle`.

#### Panel A — Observing Site

- **Site name** (text).
- **Latitude:** magnitude number (0–90) + **`Segmented` N/S** toggle. **No sign in the UI.** (C1-A1)
- **Longitude:** magnitude number (0–180) + **`Segmented` E/W** toggle. Helper in **`--text` ≥12px** (C3-4): *"Pick E or W — most maps and GPS show longitude as °W in the Americas."* Store converts: `lon = ew==="W" ? -mag : mag` (East-positive). (C1-A1)
- **Elevation (m)** — loud "meters" unit label; helper: *"Forwarded to the mount; does not affect altitude math."* (C1-I30)
- **Paste coordinates** field (C1-A3): accepts `37.77, -122.42`, `37.77 N, 122.42 W`, `122.42° W`, Google-Maps `@lat,lon` — a small client parser fills magnitude + toggles. The robust, deployment-independent path.
- **Use my location** button (C1-A3): `navigator.geolocation` → fills fields (4dp, labeled **"~approximate"** C2-16). If **insecure context** (`!window.isSecureContext`, the common `http://192.168.x.x` Pi case) → button is **replaced by explanatory text**, not hidden: *"Location needs HTTPS or localhost. Paste coordinates above, or open AstroDeck via https://."* Full-width below the lat/lon pair on mobile (thumb reach).
- **Hero readout (server-computed, the novice sanity check, C1-A2/B4/H26):** from `GET /api/site/sky` (debounced on edit) — **Sun altitude** ("Sun: −14° · nautical twilight"), **tonight's dark window** ("Astro-dark 22:48 → 04:12"), **place hint** ("N hemisphere · W longitude · ~Pacific N. America"). Plus `lst_str` and browser-local time as secondary mono lines. If a flipped sign sends them to the wrong continent, Sun-alt + dark-window are visibly wrong — a human *can* catch this; LST they cannot.
- **Timezone:** **not collected** (C1-B5/6/C2-6). A one-line note: *"Wall-clock uses your browser's time zone; session scheduling (coming) will use it."*
- **Actions:** **Save Site** (`PUT /api/site` with `version`). Dirty dot next to the Save button (C3-11). On 409-stale → toast "Site changed on another device — reloaded" + re-GET. Success toast: **"Site saved."** (no over-promising "coordinates updated", C1-I31). Optional **auto-save on blur** (config is server-side + cheap) controlled by a small "auto-save" checkbox; default off.

#### Panel B — Optics & Plate-Solve

- **Focal length (mm)** — `inputMode="decimal"`.
- **Auto-detect from connected camera** — accessible `Toggle` (§2.6) with explicit **ON/OFF** text.
- **Pixel size (µm)** + **Sensor W×H (px)** — when auto ON **and a camera is connected**, fields show **live camera values dimmed/disabled**. When auto ON **and no camera** (the common first-run state): fields show **"—"** placeholders and an inline line *"Connect a camera to auto-fill, or turn this off and enter values."* — never a misleading `0.0` (C1-C7).
- **Computed block — client-side instant** (C1-H29/C3-8), via `lib/optics.ts` using the shared `ARCSEC_PER_RAD = 206.265` constant (mirrors backend, no logic duplication beyond one multiply):
  - **Image scale** `1.46 "/px` — labeled **"at bin 1"**; a sub-line **"at bin N: 2.92 "/px"** using the camera's current bin when known, else bin 2 labeled as an example (C1-C8/C2-3).
  - **Field of view** — its **own full-width row** (not a truncating `Stat`, C3-12): `2.95° × 2.21°  ·  diag 3.69°`. Renders **"—"** when `!have_optics`.
  - **Source** chip **expanded inline** (C3-13): `Source: FL manual · pixel/sensor from camera`. Pairs an icon with text, never hue-only (C3-1).
  - **Plausibility nudge** (C1-C9/10): if `fov_diag < 0.2°` or `> 15°`, show *"Unusually narrow/wide FOV — check focal length (mm, not inches/aperture)."*
- Help line: *"Seeds the plate solver's FOV hint — a correct FOV makes Solve & Sync faster and more reliable."*
- **Actions:** **Save Optics** (`PUT /api/optics` with `version`). After save, `solve_and_sync` uses it immediately (no reconnect). The persisted `optics_computed` from the echo refreshes the readout (source of truth).

#### Panel C — Equipment Profiles

- List of `ProfileRow` (name, **mode chip** incl. `mixed`, device count, **`ACTIVE` text chip** not a green dot, C1-G23/night-mode). Active row highlighted by border, not hue alone.
- Row actions (≥44px, `py-2.5 px-3`): **Apply** · **⋯** → (mobile) bottom-sheet / (desktop) menu → **Rename** (`PATCH`) / **Delete** (`ConfirmButton`).
- **Apply outcome rendered IN-PANEL** (C1-D11/G24, not via the single toast): after `POST /api/profiles/{id}/apply`, the row expands to a **per-device list** — `Camera ✓ · Mount ✓ · Focuser ✗ (timeout) · Wheel ✗`. Header copy: **"Connected 2 of 4."** Never claims a number it didn't connect. Per-device state is **text** (✓/✗ glyph + label), readable in night mode.
- **Apply is hold-to-confirm when a rig is connected** (`ConfirmButton`, C1-D15/C2-8): copy *"This disconnects your current rig"* + *", and stops the running sequence"* when `seqRunning`. If the API returns **409 (running)**, the button offers a forced retry (`{force:true}`) behind the same hold.
- **Save current rig** (header-right): inline name field → `POST /api/profiles/capture {name}`. Disabled with tooltip *"Connect a rig first"* when `mode==="none"`. Note under it: *"Saved by host:port — re-scan if your network (DHCP) changed."* (C1-D12).
- Empty state (§3).

#### Panel D — Plan Library

- List of `PlanRow` (name, `N frames · 3h 20m · 2 targets`, relative mtime). Actions: **Load** · **Export** · **Delete** (`ConfirmButton`).
- **Load** → if `editorDirty`, **hold-to-confirm "Discard unsaved plan?"** (C1-E18), then `setEditorPlan(plan,false); setView("sequence")`.
- **Save current plan** (header-right): `POST /api/plans {plan: editorPlan}`. If **409 name_collision** → prompt *"A plan named 'X' exists. Overwrite?"* → retry with `{overwrite:true, id: existing_id}` (C1-E16/C2-14). Never silent.
- **Import** (`<input type="file" accept=".json">`) → parse client-side → `POST /api/plans/import`. On **422**: `code==="version_too_new"` → *"This file was exported by a newer AstroDeck."*; `code==="invalid"` → *"Not a valid AstroDeck plan file."* (C1-E19).
- **Export** via anchor `href="/api/plans/{id}/export"` (download, no fetch).

### 2.6 Accessible `Toggle` (FE-B, edits `components/ui.tsx`) — C3-2/5

```tsx
export function Toggle({ checked, onChange, disabled, label }: {...; label: string }) {
  return (
    <span className="inline-flex items-center min-h-11 sm:min-h-0">  {/* 44px hit area on touch */}
      <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
        onClick={() => onChange(!checked)} className="relative w-9 h-5 border …">
        <span className="…knob…" />
      </button>
      <span className="label ml-2" aria-hidden>{checked ? "ON" : "OFF"}</span>  {/* non-color state cue */}
    </span>
  );
}
```
Adding `label` is backward-compatible (existing callers pass it). Focus ring comes from the global rule (§2.7).

### 2.7 Shared CSS (FE-B, edits `index.css`) — C3-3/4/10

```css
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }   /* C3-3 — one rule, whole-app win */
.label { font-size: 11px; }                                                  /* C3-10 — bump 10→11px */
.help  { font-size: 12px; color: var(--text); line-height: 1.35; }           /* C3-4 — corruption-preventing helper text uses --text */
.field-touch { min-height: 44px; }                                           /* applied to Settings number inputs on mobile (C3-5) */
.err   { color: var(--bad); }                                                /* always paired with ⚠ + text, never border-only (C3-1) */
```

### 2.8 `Segmented` (FE-B) and `ConfirmButton` (FE-B)

- **`Segmented`**: `role="radiogroup"`, two/three options, `aria-checked`, 44px-tall on touch, selection shown by **filled bg + bold label** (shape/weight, night-safe). Used for N/S, E/W.
- **`ConfirmButton`** (hold-to-confirm, C1-D15/E18 + panel review single-tap-destructive): props `{label, confirmLabel, onConfirm, danger?, holdMs=800}`. Confirm state changes the **label text** ("HOLD… → RELEASE TO CONFIRM"), not just color (C3 "keep" note). Reusable later for Abort/Disconnect.

### 2.9 `lib/optics.ts` (FE-B) — the only client math (one multiply)

```ts
export const ARCSEC_PER_RAD = 206.265;
export const scale = (fl: number, px: number, bin = 1) => fl > 0 ? ARCSEC_PER_RAD * px * bin / fl : 0;
export const fov = (fl: number, px: number, w: number, h: number) => {
  const s = scale(fl, px, 1); const fw = s*w/3600, fh = s*h/3600;
  return { w: fw, h: fh, diag: Math.hypot(fw, fh) };
};
```
Mirrors the backend constant exactly; tested in a tiny unit test against a known case (530mm/3.76µm/9576×6388 → ~1.46"/px). Persisted truth still comes from the server echo.

---

## 3. UI states (every panel)

| State | Site | Optics | Profiles | Plans |
|---|---|---|---|---|
| **loading** | inputs disabled, hero `…`, while `config===null` | computed `…` | "Loading profiles…" | "Loading plans…" |
| **empty** | always has defaults | — | ghost: "No saved profiles. Connect a rig, then **Save current rig**." | ghost: "No saved plans. Build one in **Plan**, then save it here." |
| **no-camera (auto on)** | — | fields "—" + inline "Connect a camera to auto-fill, or turn this off." FOV "—" | — | — |
| **dirty** | dot **next to Save**; header-gear dot; Save enabled when valid | same | inline name field open | editorDirty → Load asks to discard |
| **saving** | Save → spinner, inputs locked | row → spinner | row → spinner; per-device list streams in | — |
| **success** | toast "Site saved."; dirty clears; hero refreshes | toast; readout from echo | in-panel "Connected N of M"; ACTIVE chip moves | toast "Plan saved/imported." |
| **partial (apply)** | — | — | **in-panel per-device ✓/✗ + error text** (never a lying toast) | — |
| **error** | inline **⚠ text** under bad field + dashed border (never hue-only); 409-stale → reload toast | inline ⚠; plausibility nudge | toast w/ detail; row reverts | name_collision → overwrite prompt; version_too_new vs invalid copy |
| **validation** | Save disabled if lat>90/lon>180/name empty; inline ⚠ | Save disabled if FL≤0; nudge if FOV wild | name required ≤60 | name required |
| **WS down** | inputs editable (REST); Save shows "reconnecting…" if `!wsConnected` | same | **Apply disabled** while `!wsConnected` (it needs the status stream) | Load/Save/Export/Import work (pure REST) |

All field errors use **icon + inline text**, not color-only borders (C3-1). The multi-warning Apply path is rendered **in-panel**, bypassing the single-slot toast entirely (C1-G24).

---

## 4. Interop

- **SequenceView (FE-C):** lift the working plan into store `editorPlan` (replaces local `useState`/localStorage, SequenceView.tsx:28/35). On mount: `GET /api/plans`. **Migration (transactional + idempotent, C1-E17):** if `localStorage["astrodeck-plan"]` exists AND no `astrodeck-plan-migrated` flag → `POST /api/plans`; **only on verified 201** set the flag and clear the key. A failed POST leaves both intact (no data loss); the flag prevents re-migrating a stale copy on a second device. Add a "Save to library" button next to the plan name (`POST /api/plans` with collision handling). Set `editorDirty` on any edit.
- **MountView / Solve & Sync:** no code change required; optionally show `status.optics.fov_diag_deg` next to the Solve button as reassurance (now survives the 2s replace, C2-2).
- **PolarView / catalog:** no change — they read `hub.site` (now config-backed) and become correct the moment a site is saved. Re-test the polar compass direction after the fix.
- **First-run nudge (one line, optional):** if `config.site.name` is still the default and a mount is connected, fire **one** info toast "Set your observing site in Settings for correct altitude & timing" → click-through to `setView("settings")`.

---

## 5. 375px phone + red night mode

**Layout:** `grid-cols-1` on phone; panels stack in the scrollable `<main>` (`pb-20` clears the bottom nav). No horizontal scroll: inputs full-width; computed block wraps to 2 columns of stats; **FOV is its own full-width row** (won't truncate, C3-12).

**Entry / touch:** the **header gear** is the single entry on mobile, 44px (`min-h-11 min-w-11`), no 9th nav cell. Number inputs get `.field-touch` (44px). Row actions `py-2.5 px-3`. `⋯` opens a **bottom sheet** (no hover on touch). `Toggle`/`Segmented` have 44px hit wrappers. `inputMode="decimal"` on FL/pixel/sensor/elevation; **lat/lon use magnitude + segmented toggles** so the missing iOS minus key is a non-issue (C1-I33). Geolocation/paste full-width below the lat/lon pair.

**Night mode (all-red palette collapses good/warn/bad, C1-G23/C3-1):**
- **No color-only meaning anywhere on this surface.** Active profile = **`ACTIVE` text chip**; dirty = **filled vs hollow dot** (shape); save-success = **check glyph + text**; apply outcome = **✓/✗ glyph + label text**; source chip = **icon + words**; field error = **⚠ + inline text + dashed border** (hue is secondary).
- Two different dot semantics are avoided: only the **dirty** indicator uses a dot (filled/hollow); active-profile uses the text chip (C1-32).
- Helper/error text ≥12px in `--text` (not `--text-dim`, which fails AA, C3-4); `.label` now 11px (C3-10). No raw images here, so `--img-filter` is irrelevant.
- `ConfirmButton` confirm state changes **label text**, not just color (night-safe).

---

## 6. On-disk format

`server/config/astrodeck.json`:
```json
{ "version": 7,
  "site":   { "name": "Backyard", "latitude": 37.77, "longitude": -122.42, "elevation_m": 28 },
  "optics": { "focal_length_mm": 530, "pixel_size_um": 0, "sensor_width_px": 0, "sensor_height_px": 0, "auto_from_camera": true },
  "active_profile_id": "8f3c…" }
```
`config/plans/<uuid>.json` → `{ "id", "schema_version": 1, "name", "plan": <SequencePlan> }`.
`config/profiles/<uuid>.json` → a `Profile` dump (id = filename stem).
Writes atomic (`*.tmp` → `os.replace`, with retry on Windows `PermissionError`).

---

## 7. Implementation checklist

**Backend — device identity (BE-C, prerequisite for profiles)**
- [ ] `AlpacaConnection` stores `host`/`port`; `_AlpacaDevice` stores `host`/`port`/`backend`/`dev_type`; `connect_alpaca_device` sets `dev.role`.
- [ ] Override `_AlpacaDevice.describe()` to add `{host, port, dev_type, dev_num, role, backend}`.

**Backend — config/site/optics (BE-A)**
- [ ] `config.py`: `Site`/`Optics`/`AppConfig`, `image_scale_arcsec_px`, `fov_deg`, `ConfigStore` (atomic write + Windows retry + `.bak` recovery + version/optimistic-concurrency).
- [ ] `coords.py`: `sun_altaz`, `dark_window` (server-computed sky readouts).
- [ ] `hub.py`: drop hardcoded site; `site` property; `effective_optics()` (per-profile read-time override, `source`, `have_optics`); add `site`+`optics` to **`poll_status()`** and `summary()`; FOV hint in `solve_and_sync`; `push_site_to_mount()`.
- [ ] `test_config.py`: scale = 206.265·px/fl; FOV bin-independence; atomic write; corrupt-file recovery; **West-hemisphere geolocation round-trips to negative stored longitude** (C3-7); version 409.

**Backend — profiles (BE-B)**
- [ ] `profiles.py`: `ProfileDevice` (per-device backend), `Profile` (uuid id, derived `mode`), `ProfileLibrary` (uuid-keyed, rename-in-place).
- [ ] `hub.py`: `apply_profile` (per-device results, persist auto camera optics) + `capture_profile`.
- [ ] `test_profiles.py`: rename keeps id; capture builds from device identity; apply returns per-device outcome.

**Backend — plans (BE-D)**
- [ ] `plans.py`: uuid-keyed `PlanLibrary`; import version handling (`version_too_new` vs `invalid`).
- [ ] `test_plans.py`: upsert by id; name-collision detection; export bytes; import 422 codes.

**Backend — API (BE-E)**
- [ ] Remove `SiteBody` + old `set_site`; add config/site/optics/sky, profiles (+ capture/apply-with-force-and-409), plans (+ export/import) endpoints; `bus.publish("config", version)` after writes; engine-abort guard in apply endpoint.

**Frontend — shared (FE-B)**
- [ ] `api.ts` put/patch/del; `types.ts` additions + `RigStatus` site/optics.
- [ ] `components/ui.tsx` `Toggle` → `role="switch"` + `aria-checked` + `aria-label` + 44px wrapper + ON/OFF text.
- [ ] `index.css` `:focus-visible` ring, `.label` 11px, `.help`/`.field-touch`/`.err`.
- [ ] `Segmented.tsx`, `ConfirmButton.tsx`, `lib/optics.ts`, `icons.tsx` (gear SVG).

**Frontend — shell + Settings (FE-A)**
- [ ] `store.ts`: `config`/`editorPlan`/`editorDirty`/`siteDirty`/`opticsDirty` slices, `loadConfig`, `"settings"` view, `config` WS case (re-GET).
- [ ] `App.tsx`: header **gear** button (single entry, 44px, SVG, dirty dot) + `VIEWS.settings`.
- [ ] `SettingsView.tsx` + `SitePanel`/`OpticsPanel`/`ProfilesPanel`/`PlansPanel` with all §3 states, §5 night/phone rules.

**Frontend — plan interop (FE-C)**
- [ ] `SequenceView.tsx`: lift plan to `editorPlan`; transactional+idempotent localStorage migration; "Save to library"; set `editorDirty`.

**Acceptance (P0 + reviews)**
- [ ] With **no devices connected**, `PUT /api/site {site:{lat:51.5,lon:-0.13,…},version:n}`, restart server, `GET /api/config` returns London; **`/api/site/sky` Sun-alt + dark window reflect London**, and the hero readout changes visibly (the no-mount case the draft got wrong). Value survives restart.
- [ ] Catalog alt sort + header ALT reflect London (not SF) once a mount connects.
- [ ] `solve_and_sync` logs a **computed `fov hint`**, not `1.0`.
- [ ] Apply a profile with one unreachable device → in-panel **"Connected N of M"** + per-device ✗; **no toast claims all connected**.
- [ ] Apply while a sequence runs → **409 unless forced**; forced apply aborts the engine first.
- [ ] Rename a profile → same `id`, no orphan file, `active_profile_id` still valid.
- [ ] Save two same-named plans → **overwrite prompt**, never silent loss.
- [ ] Night mode: every status cue legible without color (text/shape/glyph); `:focus-visible` ring visible; longitude helper readable (`--text`, 12px).
