# PRO-2 — Complete FITS headers for stacker interop (F-B) — Design Spec

**Status:** Design spec (for supervisor review, then `writing-plans`)
**Created:** 2026-07-22
**Author:** Opus designer
**Feature:** PRO-2 = Foundation **F-B** in `docs/superpowers/specs/2026-07-22-pro-novice-feature-program.md` (§5 PRO-2, §3 F-B)
**Implementation tier (floor):** Sonnet (per program brief). WCS-parse task may warrant Opus review.
**Consumers this unblocks:** PRO-1 (calibration matching keys off temp/gain/exp/filter), PRO-10 (export bundles), any re-solve-free downstream tool.

---

## 1. Goal

Make every FITS AstroDeck writes carry the full keyword set that the common stackers
(PixInsight/WBPP, Siril, AstroPixelProcessor, DeepSkyStacker) key on for calibration
matching, image-scale, and re-solve-free astrometry — **and** fix two live correctness
bugs in `save_fits` along the way. Scope is the acquisition→file data contract; no UI.

Concretely:

1. Add the missing header cards: `FOCALLEN`, `XPIXSZ`/`YPIXSZ`, `AIRMASS`, `SITELAT`/
   `SITELONG`, `OBJCTALT`, `SET-TEMP`, `FOCPOS`/`FOCTEMP`, `ROTATANG`, `EGAIN`, `EQUINOX`
   (+ a small set of cheap high-interop extras), while **retaining every card written today**.
2. Add **WCS write-back** so a plate-solved frame carries a valid celestial WCS
   (`CTYPE`/`CRVAL`/`CRPIX`/`CD`) a stacker can read without re-solving.
3. Fix bug 1: `SWCREATE` is hardcoded to `"AstroDeck 0.1.0"` instead of the real package
   version (`imaging/fitsio.py:45`).
4. Fix bug 2: `TELESCOP` is never written on the capture path (root cause below).

## 2. Architecture

The single header writer is `server/astrodeck/imaging/fitsio.py::save_fits(frame, path, ...)`
(read in full; `imaging/fitsio.py:12-49`). It is a **pure writer**: it receives explicit
values and turns them into header cards. It has exactly four callers, all via
`asyncio.to_thread`:

| Caller | file:line | What it passes today |
|--------|-----------|----------------------|
| `Hub.capture` (manual **and** sequence — the sequence engine calls `hub.capture`, `sequence/engine.py:1009`) | `hub.py:1422-1425` | `target, filter_name, frame_type, ra_hours, dec_deg, instrument=cam.name` |
| `Hub.solve_and_sync` (throwaway solve frame) | `hub.py:1771-1773` | `ra_hours, dec_deg, instrument` |
| `Hub.rotate_to_pa` (throwaway solve frame) | `hub.py:1844-1846` | `ra_hours, dec_deg, instrument` |
| `polar/native.py` (throwaway solve frame) | `polar/native.py:198` | `ra_hours, dec_deg, instrument` |

**Design principle — keep `save_fits` pure and device-free.** All the new values are read
from devices/config/coords by the *caller* (`Hub.capture`, which already reads RA/Dec and
the filter this way — `hub.py:1404-1418`) and handed to `save_fits`. `save_fits` never
touches a device or the config store, so it stays trivially unit-testable (its current
tests build a bare `CameraFrame` and call it directly — `server/tests/test_fitsio.py`).

To avoid a 20-argument signature, add **one** new optional parameter carrying the new
values as a dataclass:

```python
# imaging/fitsio.py
@dataclass
class FrameMeta:
    # optics
    focal_length_mm: float | None = None
    pixel_size_um: float | None = None      # UNBINNED physical µm; save_fits multiplies by frame.binning
    # site + pointing (all None on a default/unknown site)
    site_lat_deg: float | None = None       # +N
    site_lon_deg: float | None = None       # +E
    site_elev_m: float | None = None
    obj_alt_deg: float | None = None
    obj_az_deg: float | None = None
    airmass: float | None = None
    equinox: float | None = 2000.0
    radesys: str = "ICRS"
    # per-frame device telemetry (None when the role is absent / not reporting)
    set_temp_c: float | None = None
    focuser_pos: int | None = None
    focuser_temp_c: float | None = None
    rotator_angle_deg: float | None = None
    egain_e_per_adu: float | None = None
    # optional quality (already on the frame)
    hfr: float | None = None
    star_count: int | None = None
    # astrometry
    wcs: "WcsSolution | None" = None

def save_fits(frame, path, *, target="", filter_name="", frame_type="Light",
              ra_hours=None, dec_deg=None, telescope="", instrument="",
              meta: FrameMeta | None = None) -> Path: ...
```

`meta=None` (the default) reproduces today's behavior exactly (plus the SWCREATE fix), so
the three throwaway-solve callers need **zero** changes. Only `Hub.capture` builds a
`FrameMeta`. This is the whole backward-compat story for the callers.

WCS write-back is a separate, post-hoc path (a solve happens *after* the frame is on disk):
a standalone `fitsio.write_wcs(path, wcs)` reopens the file and merges WCS cards. `save_fits`
also writes `meta.wcs` when present (for a solve-then-save flow), but `write_wcs` is the
primary hook (§6).

## 3. Tech stack

- Python 3, `astropy.io.fits` (already the FITS dep) and `astropy.wcs.WCS` (already used to
  synthesize a WCS in `catalog/hips_local.py:87-93` — reuse that idiom for the sim solver).
- `numpy` (frame data).
- No new third-party dependency.

---

## 4. Global Constraints (verbatim from program brief §2 — binding on every task)

- **Privacy:** real observing-site coordinates **<REDACTED-LAT> N / <REDACTED-LON> W** and the label
  **"<REDACTED-SITE-LABEL>"** must NEVER appear in code, tests, docs, or fixtures. Site default stays
  **"My Observatory"**, coords `0.0`. (Both prior review privacy scans were clean — keep it
  that way.) — *Directly relevant here:* SITELAT/SITELONG tests MUST use invented coords
  (e.g. `40.0 / -105.0`), never the developer's real site.
- **Secrets:** admin tokens and the Ed25519 signing seed are never committed literally.
- **Git:** never `git add -A` — stage explicit paths only. Push to origin only when the user asks.
- **Backend tests:** `server/.venv/Scripts/pytest.exe`. Do **not** run `vite build`
  concurrently with pytest.
- **UI gate:** `cd ui && npx tsc -b` is the CI gate; the repo has **no jsdom/DOM harness** —
  component logic is tested via `npx tsx` inline-assert files. *(No UI in this feature.)*
- **astrotown:** do not disrupt the deployed box; this program is dev-branch work until a
  release is cut.
- **Honest-disabled UI (§11.8 idiom):** dim token + lock glyph + `aria-disabled` + `title`
  reason — never native `disabled`. *(No UI in this feature.)*

**Definition of done:** tsc-b clean (n/a — no UI) · backend suite green · new logic covered
by a test in the repo's idiom · spec-compliance + code-quality task reviews passed · privacy
scan clean · tracker updated.

---

## 5. The keyword table (the load-bearing artifact)

Legend for **When present**: **always** = every save · **has-site** = a non-default site is
configured (`hub.site["is_default"] is False`) · **has-radec** = mount RA/Dec available ·
**has-device** = that device role is connected & reporting · **has-solve** = a successful
plate-solve produced a WCS and write-back was requested.

`file:symbol` is the exact value source I read. Format = the FITS value + comment.

### 5a. Cards retained exactly as today (do not touch except where noted)

| Keyword | FITS type | Value source (file:symbol) | Units/format | When present | Notes |
|---|---|---|---|---|---|
| `EXPTIME` | float | `CameraFrame.exposure_s` (`devices/base.py:46`) | seconds | always | unchanged |
| `GAIN` | int | `CameraFrame.gain` (`devices/base.py:47`) | driver units | always | unchanged |
| `OFFSET` | int | `CameraFrame.offset` (`devices/base.py:48`) | driver units | always | unchanged |
| `XBINNING` / `YBINNING` | int | `CameraFrame.binning` (`devices/base.py:49`) | factor | always | symmetric bin (both = binning) |
| `IMAGETYP` | str | `frame_type` arg → `Hub.capture` (`hub.py:1382`) | `Light`/`Dark`/`Flat`/`Bias` | always | verbatim; Siril/WBPP accept these tokens |
| `DATE-OBS` | str | `CameraFrame.timestamp` → UTC (`fitsio.py:28`) | `YYYY-MM-DDThh:mm:ss[.s]`, no TZ | always | **do not alter** — guarded by 2 existing tests (`test_fitsio.py:28-45`) |
| `CCD-TEMP` | float | `CameraFrame.temperature_c` (`fitsio.py:29-30`) | °C | has-device (sensor temp not None) | unchanged |
| `BAYERPAT` | str | `CameraFrame.bayer_pattern` (`fitsio.py:31-32`) | e.g. `RGGB` | mono→absent | unchanged |
| `OBJECT` | str | `target` arg | free text | target set | unchanged |
| `FILTER` | str | `filter_name` arg (resolved `hub.py:1404-1410`) | filter name | filterwheel active | unchanged |
| `RA` | float | `ra_hours*15` (`fitsio.py:37-38`) | **deg** | has-radec | value now J2000 (see §5c EQUINOX + open decision 3) |
| `DEC` | float | `dec_deg` (`fitsio.py:39-40`) | deg | has-radec | value now J2000 |
| `INSTRUME` | str | `cam.name` (`hub.py:1425`) | camera name | always | unchanged |
| `TELESCOP` | str | see **bug 2** (§7.2) | OTA name | telescope_name set | **fixed** — currently never written |
| `SWCREATE` | str | see **bug 1** (§7.1) | `AstroDeck <ver>` | always | **fixed** — currently hardcoded `0.1.0` |

### 5b. New cards — optics (from config, always available)

| Keyword | FITS type | Value source (file:symbol) | Units/format | When present | Notes |
|---|---|---|---|---|---|
| `FOCALLEN` | float | `Hub.effective_optics()["focal_length_mm"]` (`hub.py:933`; backed by `config.Optics.focal_length_mm`, `config.py:68`, default 530, always > 0) | mm | always (>0) | the same FL used everywhere (pixel scale/FOV); trusting it here is consistent |
| `XPIXSZ` / `YPIXSZ` | float | `effective_optics()["pixel_size_um"]` (`hub.py:934`) **× `frame.binning`** | µm | pixel size known (`have_optics`) | binned effective pixel — matches NINA/PixInsight image-scale expectation (open decision 2) |

### 5c. New cards — site + pointing geometry (need a real site and/or RA/Dec)

| Keyword | FITS type | Value source (file:symbol) | Units/format | When present | Notes |
|---|---|---|---|---|---|
| `SITELAT` | float | `Hub.site["latitude"]` (`hub.py:873`; `config.Site.latitude` `config.py:58`) | signed deg, +N | has-site | **omit when `is_default`** — never write 0/0 (misleads matching) |
| `SITELONG` | float | `Hub.site["longitude"]` (`hub.py:873`; `config.py:59`) | signed deg, **+E** | has-site | AstroDeck stores East-positive (`config.py:59`), matching NINA/FITS |
| `SITEELEV` | float | `Hub.site["elevation_m"]` (`hub.py:874`) | metres | has-site | cheap extra; optional (open decision 6) |
| `OBJCTALT` | float | `coords.altaz(ra_hours, dec_deg, lat, lon, frame.timestamp)[0]` (`catalog/coords.py:63`) | deg | has-site + has-radec | altitude of pointing at exposure time |
| `OBJCTAZ` | float | `coords.altaz(...)[1]` (`catalog/coords.py:63`) | deg | has-site + has-radec | optional extra |
| `AIRMASS` | float | `coords.airmass(OBJCTALT)` — **new** helper in `catalog/coords.py` (Kasten-Young 1989) | dimensionless | has-site + has-radec + alt>0 | no airmass fn exists today → add one; omit below horizon |
| `OBJCTRA` | str | sexagesimal of J2000 `ra_hours` — new space-sep formatter (near `coords.format_ra`, `coords.py:37`) | `HH MM SS.s` | has-radec | high-interop extra (DSS/Siril read it); recommend include |
| `OBJCTDEC` | str | sexagesimal of J2000 `dec_deg` (near `coords.format_dec`, `coords.py:45`) | `+DD MM SS` | has-radec | pairs with OBJCTRA |
| `EQUINOX` | float | constant `2000.0` (RA/Dec brought to J2000, see §7.2 note + open decision 3) | year | has-radec | honest only after JNow→J2000 conversion |
| `RADESYS` | str | constant `"ICRS"` | — | has-radec | recommend include; pairs with EQUINOX |

`coords.format_ra`/`format_dec` currently emit `05h 35m 17.0s` / `-05° 23' 28"` (with letters/
symbols); the FITS `OBJCTRA`/`OBJCTDEC` convention is **space-separated** `05 35 17.0` /
`-05 23 28`. Add a thin space-sep formatter rather than reusing the display formatters verbatim.

### 5d. New cards — per-frame device telemetry (read from live device roles)

The caller reads each from `self.devices.get(<role>)` in `Hub.capture`; each read is
independently guarded (best-effort — see §8).

| Keyword | FITS type | Value source (file:symbol) | Units/format | When present | Notes |
|---|---|---|---|---|---|
| `SET-TEMP` | float | `cam.get_cooler()["target_c"]` (sim: `devices/sim.py:357`; read idiom `hub.py:2358-2362`) | °C | camera has cooler + `get_cooler` present + on | the cooler *setpoint* (vs `CCD-TEMP` actual); WBPP matches darks on setpoint |
| `FOCPOS` | int | `focuser.get_position()` (`devices/base.py:273`) | steps | has-device (focuser) | NINA writes `FOCPOS`; add `FOCUSPOS` alias only if a reviewer wants belt-and-suspenders |
| `FOCTEMP` | float | `focuser.get_temperature()` (`devices/base.py:282`) | °C | has-device + temp not None | omit when focuser has no thermometer |
| `ROTATANG` | float | `rotator.get_position()` (**sky PA**, `devices/base.py:322`) | deg [0,360) | has-device (rotator) | sky position angle through the sync offset; unsynced ⇒ offset 0 ⇒ == mechanical, still valid |
| `EGAIN` | float | `CameraFrame.egain_e_per_adu` — **new** frame field, populated by the backend at the capture gain (Player One `player_one_sdk.get_egain`, `player_one_sdk.py:326`; ZWO `ElecPerADU`, `zwo_asi_sdk.py:228`; sim constant) | e⁻/ADU | backend reports it | see open decision 5; enables photometric weighting (F-A/PRO-1) |

### 5e. New cards — WCS block (from a plate-solve; §6)

| Keyword | FITS type | Value source | Units/format | When present |
|---|---|---|---|---|
| `CTYPE1` / `CTYPE2` | str | `SolveResult.wcs` | `RA---TAN` / `DEC--TAN` | has-solve |
| `CUNIT1` / `CUNIT2` | str | `SolveResult.wcs` | `deg` | has-solve |
| `CRVAL1` / `CRVAL2` | float | `SolveResult.wcs` (ASTAP `.wcs`/`.ini`) | deg | has-solve |
| `CRPIX1` / `CRPIX2` | float | `SolveResult.wcs` | pixel | has-solve |
| `CD1_1`/`CD1_2`/`CD2_1`/`CD2_2` | float | `SolveResult.wcs` (CD matrix preferred) | deg/pixel | has-solve |
| `EQUINOX` / `RADESYS` | float/str | `2000.0` / `ICRS` | — | has-solve (consistent with §5c) |

CD matrix is preferred over `CDELT*`+`CROTA*` because it encodes scale, rotation, and parity
(flip) in one block, and ASTAP emits it directly. If only `CDELT`/`CROTA` are available, fall
back to those (both are valid FITS WCS; astropy reads either).

### 5f. Deliberately omitted (and why)

- **`MOONANGL` / `MOONPHASE`** — moon math is advisory-only today and stackers don't match on
  it; belongs to PRO-14, not the data contract. *Omit.*
- **`PIERSIDE`** — `tel.pier_side()` exists (`devices/base.py:239`) and NINA writes it, but it
  is not a stacker calibration key and adds a device round-trip per frame. *Omit from F-B;*
  trivially addable later if PRO-1 wants flip-aware grouping.
- **`OBSERVER` / site name string** — not a calibration/interop key; `INSTRUME`+`TELESCOP`
  already identify the rig. *Omit.*
- **`HFR` / `STARCNT`** — `CameraFrame.hfr`/`stars` exist (`devices/base.py:54-55`) and are
  cheap; **recommend include** as `HFR`/`STARCNT` for weighted stacking, but they are
  *nice-to-have*, not required. Carried in `FrameMeta` so a reviewer can drop them without
  reshaping anything. (PRO-10's per-sub manifest is the authoritative quality record.)

---

## 6. WCS write-back

### 6.1 Where a WCS is produced

`solve/astap.py::AstapSolver.solve()` runs ASTAP, which writes **two** result files next to
the input FITS: a `.ini` (key=value) and a `.wcs` **FITS-format headerlet** (a text file of
80-char WCS cards). Today `solve()` parses only `CRVAL1`, `CRVAL2`, `CROTA2`, `CDELT2` from
the `.ini` (`astap.py:136-139`) and then **deletes both** (`astap.py:129-130`) — the full WCS
is computed and thrown away. `SolveResult` (`solve/base.py:8-15`) carries only
`ra_hours/dec_deg/rotation_deg/pixel_scale_arcsec` — not enough to reconstruct a WCS.

### 6.2 Approach — capture the WCS into the result, write it back post-hoc

1. **Extend `SolveResult`** with `wcs: WcsSolution | None = None`. Define `WcsSolution`
   (in `solve/base.py`) as a small dataclass of the WCS numerics: `ctype1/ctype2` (default
   `RA---TAN`/`DEC--TAN`), `cunit` (`deg`), `crval1/crval2`, `crpix1/crpix2`,
   `cd11/cd12/cd21/cd22` (with optional `cdelt1/cdelt2/crota2` fallback), `equinox=2000.0`,
   `radesys="ICRS"`. Keeping it plain-number (not an `astropy` object) makes it serializable
   and unit-testable.

2. **Populate it robustly in `AstapSolver.solve`** by reading ASTAP's `.wcs` **headerlet**
   with astropy (`fits.Header.fromtextfile(wcs_path)`) *before* the unlink, and copying the
   standard WCS cards into `WcsSolution`. This is more robust than hand-parsing the `.ini` and
   reuses astropy's card parser. **Extract a pure `_wcs_from_astap(ini_path, wcs_path) ->
   WcsSolution | None` helper** so it is unit-testable without launching ASTAP — exactly the
   pattern already used for `_solve_args` (`astap.py:72`, extracted "so the flag wiring … is
   unit-testable without launching a subprocess"). If the `.wcs` is missing, fall back to the
   `.ini` numerics already being read. Never let a WCS-parse failure fail the solve — on any
   exception, return the `SolveResult` with `wcs=None`.

3. **`SimSolver.solve`** builds a synthetic `WcsSolution` from its known pointing + scale +
   rotation using `astropy.wcs.WCS` (the `catalog/hips_local.py:87-93` idiom: set `ctype`,
   `crval`, `crpix`, `cdelt`) and `.to_header()` → fill `WcsSolution`. This keeps the sim rig
   and the tests exercising the write path end-to-end.

4. **`fitsio.write_wcs(path, wcs)`** — the writer: open the existing FITS
   (`fits.open(path, mode="update")`), set the WCS cards from `WcsSolution`, `flush()`. Also:
   `save_fits` writes `meta.wcs` when present (solve-then-save case). Both funnel through one
   internal `_apply_wcs(header, wcs)` so the card set is identical.

### 6.3 The hook point + the honest scoping note

The **mechanism** (SolveResult.wcs + `_wcs_from_astap` + `fitsio.write_wcs`) is the F-B
deliverable and the concrete hook is inside `AstapSolver.solve` (parse) and `fitsio.write_wcs`
(write). **But** note the architecture: `Hub.solve_and_sync` (and `rotate_to_pa`, `polar`)
solve a **throwaway** binned-2 frame (`hub.py:1770` `CAPTURE_DIR/_solve/solve.fits`), not a
saved science light — so today there is no science sub for a solved WCS to attach to. Two
honest options, to be ruled on (open decision 4):

- **(Recommended) Ship the mechanism now; wire the science-sub write as a bounded opt-in.**
  Add a config flag `solve_saved_lights` (default **off**). When on, after `Hub.capture`
  saves a light, plate-solve *that saved file in place* (reuse `providers.pick_solver`, no new
  throwaway) and call `fitsio.write_wcs(saved_path, result.wcs)`. Off by default keeps the
  per-frame solve cost opt-in and F-B at M-effort. This is the "re-solve-free downstream" win
  the program brief names, delivered safely.
- **(Minimal) Ship only the mechanism**; PRO-1/PRO-10 or a later feature decides when to
  solve subs. F-B then adds no per-frame solve at all.

Either way the parse/write code is identical; only the *caller policy* differs. Recommend the
opt-in.

---

## 7. The two bug fixes

### 7.1 SWCREATE hardcoded version

**Root cause:** `imaging/fitsio.py:45` writes a string literal:
`hdr["SWCREATE"] = "AstroDeck 0.1.0"`. The real version is
`astrodeck.__version__` (`server/astrodeck/__init__.py:3`, currently `0.2.16`) — the
canonical source already imported this way across the tree (`api/app.py:59`,
`update/state.py:11`, `devices/backends/*`).

**Fix:** at the top of `fitsio.py`, `from .. import __version__` and write
`hdr["SWCREATE"] = (f"AstroDeck {__version__}", "Creating software")`. No import cycle:
`astrodeck/__init__.py` only assigns `__version__` (no submodule imports), and `fitsio`
already imports `..devices.base`. This is an unconditional card (always written).

### 7.2 TELESCOP missing on the capture path

**Root cause (two-fold):**
1. `Hub.capture` calls `save_fits(...)` passing `instrument=cam.name` but **never**
   `telescope=` (`hub.py:1422-1425`); `save_fits` writes `TELESCOP` only `if telescope:`
   (`fitsio.py:41-42`), so it is always omitted on the capture path (and on the three
   throwaway-solve callers too — none passes `telescope=`).
2. There is **no source** for an OTA/telescope name today: a repo-wide search for
   `telescope_name`/`scope_name` returns nothing, and `config.Optics` (`config.py:67-78`) has
   focal length + pixels but no name. So even threading `telescope=` has nothing to thread.

**Fix:** add `telescope_name: str = ""` to `config.Optics` (surfaced through
`Hub.effective_optics()` alongside `focal_length_mm`), and in `Hub.capture` pass
`telescope=<effective optics telescope_name>` to `save_fits`. `TELESCOP` is written when the
name is set, omitted when blank (honest — no misleading placeholder). Do **not** fall back to
the mount device name: `TELESCOP` is the optical tube, not the mount, and a wrong string
pollutes stacker grouping (open decision 1).

---

## 8. Missing-value policy (decisive, per value)

**Default rule: omit the card when the source is unavailable.** A wrong or placeholder value
is worse than an absent card — calibration matchers (PRO-1, WBPP) group on these, and a bogus
`SITELAT=0.0` or `FOCALLEN=0` silently corrupts a match. Concretely:

| Value | Unavailable when | Action |
|---|---|---|
| `SITELAT`/`SITELONG`/`SITEELEV` | `hub.site["is_default"]` is True | **omit** (never write 0/0) |
| `OBJCTALT`/`OBJCTAZ`/`AIRMASS` | no RA/Dec, or default site, or alt ≤ 0 | **omit** |
| `AIRMASS` | alt ≤ 0 (below horizon) | **omit** (formula diverges) |
| `SET-TEMP` | no cooler / `get_cooler` absent / cooler off | **omit** |
| `FOCPOS`/`FOCTEMP` | focuser not connected / temp None | **omit** the specific card |
| `ROTATANG` | rotator not connected | **omit** |
| `EGAIN` | backend does not report it | **omit** |
| `XPIXSZ`/`YPIXSZ` | pixel size unknown (`have_optics` False) | **omit** |
| `TELESCOP` | `telescope_name` blank | **omit** |

**Values that are always written (a default is correct):**

- `SWCREATE` — always (real version).
- `EQUINOX=2000.0` + `RADESYS=ICRS` — always **when RA/Dec are written** (they describe the
  frame of the written RA/Dec; see the J2000 conversion note in §7.2/open decision 3).
- `FOCALLEN` — always (>0): `effective_optics` always yields a focal length (config default
  530); this is the same value the app already trusts for pixel scale and FOV, so writing it
  is consistent, not a fabrication.

---

## 9. Error handling & backward compatibility

- **A header write must never fail a capture.** All new device/coords/config reads happen in
  `Hub.capture` *before* the `asyncio.to_thread(save_fits, ...)` call and are each wrapped
  best-effort, exactly like the existing RA/Dec and filter reads (`hub.py:1406-1418`): any
  exception ⇒ that value stays `None` ⇒ its card is omitted; the frame still saves. One hung
  focuser/rotator/cooler can never crash or block the save beyond its own guarded read.
- **`save_fits` stays pure and total:** given a `FrameMeta`, it only emits cards for non-None
  values; a `None`/NaN value is skipped, not written. It performs no I/O other than the file
  write it already does.
- **Never break existing readers:** all changes are **additive** cards; unknown cards are
  ignored by every FITS reader. No existing card is renamed or removed. `astropy` continues to
  handle `BITPIX=16`/`BZERO=32768`/`BSCALE` for uint16 automatically. The `DATE-OBS` format
  (no timezone) is untouched and remains covered by its two tests.
- **WCS write-back is non-fatal:** `write_wcs` on a missing/locked/corrupt file logs and
  returns; a WCS-parse failure in `AstapSolver` yields `wcs=None` and the solve/sync path is
  otherwise unchanged (centering never depends on the headerlet).
- **Caller compat:** `meta` defaults to `None`, so `solve_and_sync`, `rotate_to_pa`, and
  `polar/native.py` need no edits and behave as before (plus the SWCREATE fix). The
  `build/lib/astrodeck/**` tree is a stale build copy — **do not edit it**; ship from
  `server/astrodeck/**` only.

---

## 10. Testing

Run with `server/.venv/Scripts/pytest.exe`. Extend `server/tests/test_fitsio.py` (pure,
device-free — it builds a bare `CameraFrame`) and add solver/WCS tests. **Use invented site
coords (e.g. `40.0 / -105.0`), never the developer's real site.**

**`test_fitsio.py` additions (pure `save_fits`):**

1. `test_all_new_cards_present_and_correct` — build a `CameraFrame` (binning=2) + a fully
   populated `FrameMeta`; assert each card + value/units: `FOCALLEN==530.0`,
   `XPIXSZ == pixel_size_um*2` and `YPIXSZ==XPIXSZ`, `SITELAT==40.0`, `SITELONG==-105.0`,
   `OBJCTALT`/`AIRMASS` present and finite, `SET-TEMP==-10.0`, `FOCPOS==12345`,
   `FOCTEMP==5.5`, `ROTATANG==123.4`, `EGAIN==0.8`, `EQUINOX==2000.0`, `RADESYS=='ICRS'`.
2. `test_swcreate_is_real_version` — assert `header["SWCREATE"] == f"AstroDeck {__version__}"`
   **and** `"0.1.0" not in header["SWCREATE"]` (pins the bug 1 fix).
3. `test_optional_cards_omitted_when_absent` — `save_fits(frame, path, meta=None)` (or empty
   `FrameMeta`): assert `"FOCPOS" not in header`, `"SITELAT" not in header`,
   `"SET-TEMP" not in header`, `"AIRMASS" not in header`, while core cards
   (`EXPTIME`/`GAIN`/`DATE-OBS`) are still present — proves omit-not-placeholder.
4. `test_telescop_written_only_when_named` — `telescope="Askar 71F"` ⇒
   `header["TELESCOP"]=="Askar 71F"`; `telescope=""` ⇒ `"TELESCOP" not in header` (pins bug 2).
5. `test_airmass_omitted_below_horizon` — meta with `obj_alt_deg <= 0` (or airmass None) ⇒
   `"AIRMASS" not in header`.

**WCS tests:**

6. `test_wcs_writeback_roundtrips` — `save_fits` a frame, build a `WcsSolution` (TAN centered
   on a known RA/Dec + scale), `fitsio.write_wcs(path, wcs)`, reopen with
   `astropy.wcs.WCS(header)`; assert `wcs.has_celestial`, `CTYPE1=='RA---TAN'`,
   `EQUINOX==2000.0`, and that `wcs.pixel_to_world(CRPIX1-1, CRPIX2-1)` ≈ `(CRVAL1, CRVAL2)`
   within a small tolerance (round-trip proof).
7. `test_wcs_from_astap_parse` — feed `_wcs_from_astap` a fixture ASTAP `.wcs` headerlet
   (a text FITS header) + `.ini`; assert the `WcsSolution` fields (CRVAL/CRPIX/CD) match.
   No subprocess (mirrors the `_solve_args` unit-test pattern).
8. `test_simsolver_returns_wcs` — `SimSolver` with a sim rig returns a `SolveResult.wcs`
   whose center ≈ the sim pointing.

**Hub integration (follow existing `test_hub_capture_precession.py` sim-rig idiom):**

9. `test_capture_writes_full_headers` — with the sim rig (camera+focuser+rotator where
   available) run `hub.capture(save=True)`, open the saved FITS, assert `SET-TEMP`,
   `FOCALLEN`, `INSTRUME`, and (when a rotator/focuser is present) `ROTATANG`/`FOCPOS` are
   present — proving the caller→`save_fits` wiring, not just the pure writer.
10. (If open decision 3 = convert) `test_capture_radec_is_j2000` — a JNow-reporting mock mount
    ⇒ written `RA`/`DEC` equal the `from_mount_frame` (J2000) values, with `EQUINOX==2000.0`.

**Privacy scan:** grep the diff for `<REDACTED-LAT>` / `<REDACTED-LON>` / `<REDACTED-SITE-LABEL>` → must be clean.

---

## 11. Open decisions (supervisor to rule; each has a recommendation)

1. **`TELESCOP` source.** Add `Optics.telescope_name` config field (write when set, omit when
   blank) **vs.** reuse the mount device name. **Recommend:** add the config field; no
   mount-name fallback (`TELESCOP` = OTA, not mount; a wrong string corrupts stacker grouping).
2. **`XPIXSZ` binning convention.** Write `pixel_size_um × binning` (binned effective pixel,
   what NINA/PixInsight expect so `206.265·XPIXSZ/FOCALLEN` gives the true binned scale)
   **vs.** the unbinned physical size (relying on `XBINNING`). **Recommend:** `× binning` —
   it matches the dominant stacker expectation and avoids a double-count ambiguity downstream.
3. **RA/DEC epoch + `EQUINOX`.** Convert the mount's reported position JNow→J2000 via the
   existing `Hub.from_mount_frame` (`hub.py:1367`, a no-op for sim/NINA, precession for an
   Alpaca JNow mount) and write `EQUINOX=2000.0`/`RADESYS=ICRS` **vs.** write the raw mount
   RA/Dec and omit EQUINOX. **Recommend:** convert — it makes RA/DEC honest, matches the J2000
   frame ASTAP and the catalog already use, and is nearly free (helper exists, already used by
   the solve path). Small behavior change on the written RA/DEC value for Alpaca JNow mounts.
4. **WCS write-back scope (§6.3).** Ship the mechanism only **vs.** also add an opt-in
   `solve_saved_lights` flag that solves each saved light in place and stamps its WCS.
   **Recommend:** ship the mechanism now + the opt-in flag (default off) so the
   "re-solve-free downstream" benefit is reachable without forcing a per-frame solve cost.
5. **`EGAIN` threading.** Add `egain_e_per_adu` to `CameraFrame`, populated by backends at the
   capture gain (Player One `get_egain`, ZWO `ElecPerADU`, sim constant), write `EGAIN` when
   present **vs.** omit `EGAIN` from F-B and defer to F-A (photometry core). **Recommend:**
   thread it now — PRO-1 (matching) and F-A/PRO-6 (SNR) both want e⁻/ADU, and it is a single
   optional frame field + a per-backend read; backends that can't report leave it None.
6. **Cheap interop extras.** Include `OBJCTRA`/`OBJCTDEC` (sexagesimal), `RADESYS`, `SITEELEV`,
   and quality `HFR`/`STARCNT`? **Recommend:** include `OBJCTRA`/`OBJCTDEC` + `RADESYS`
   (high interop value, near-zero cost) and `HFR`/`STARCNT` (already on the frame; useful for
   weighted stacking); treat `SITEELEV`, `OBJCTAZ`, `PIERSIDE` as optional and droppable.

---

## 12. Supervisor rulings (2026-07-22)

Reviewed against the code (SWCREATE literal at `fitsio.py:45`, TELESCOP-omission root cause,
raw-mount RA/DEC written today at `hub.py:1416→1424` with no epoch, `from_mount_frame` present
at `hub.py:1367`). Spec **approved**; the open decisions are ruled as follows. These are
binding on the plan.

1. **TELESCOP source** → add `Optics.telescope_name` config field; write when set, omit when
   blank; **no** mount-name fallback.
2. **XPIXSZ/YPIXSZ** → write `pixel_size_um × binning` (binned effective pixel).
3. **RA/DEC epoch** → **convert** the mount-reported position JNow→J2000 via
   `Hub.from_mount_frame` (best-effort guarded, like the existing reads), and write
   `EQUINOX=2000.0` + `RADESYS=ICRS` whenever RA/DEC are written. Note in the plan: this shifts
   the written RA/DEC pointing-hint for Alpaca JNow mounts (no-op for sim/NINA); the WCS remains
   the authoritative astrometry.
4. **WCS write-back** → ship the full mechanism (`SolveResult.wcs` + extractable
   `_wcs_from_astap` + `fitsio.write_wcs` + `SimSolver` WCS) **and** add the opt-in
   `solve_saved_lights` config flag (**default OFF**) that, when on, solves each saved light in
   place and stamps its WCS. Keep the parse/write code identical across both callers.
5. **EGAIN** → thread now: add optional `egain_e_per_adu` to `CameraFrame`, populate per-backend
   (Player One `get_egain`, ZWO `ElecPerADU`, sim constant), write `EGAIN` when present, omit
   when a backend can't report. Accepted that this touches the camera SDK backends, not just
   `fitsio.py`/`hub.py` — it is the natural home and PRO-1/F-A both consume it.
6. **Cheap extras** → include `OBJCTRA`/`OBJCTDEC`, `RADESYS`, `HFR`, `STARCNT`, and `SITEELEV`;
   **drop** `OBJCTAZ` and `PIERSIDE` from F-B (trivially addable later if PRO-1 wants them).
