# FITS Header Completeness (PRO-2 / F-B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every FITS AstroDeck writes carry the full keyword set that common stackers (PixInsight/WBPP, Siril, APP, DSS) key on — image scale, calibration matching, site/pointing geometry, per-frame telemetry, and a re-solve-free WCS — while fixing two live `save_fits` bugs and retaining every card written today.

**Architecture:** `imaging/fitsio.py::save_fits` stays a **pure, device-free writer**: it gains one optional `FrameMeta` dataclass argument carrying all new values plus the existing scalar args. Only `Hub.capture` builds a `FrameMeta` (best-effort guarded reads of config/site/coords/devices); the three throwaway-solve callers pass `meta=None` and are unchanged. WCS write-back is a separate post-hoc path: `AstapSolver`/`SimSolver` capture a plain-number `WcsSolution` into `SolveResult.wcs`, and `fitsio.write_wcs(path, wcs)` merges the WCS cards into an existing file. An opt-in `solve_saved_lights` config flag (default OFF) solves each saved light in place and stamps it.

**Tech Stack:** Python 3, `astropy.io.fits` + `astropy.wcs.WCS` (already deps), `numpy`. No new third-party dependency.

## Global Constraints

_(Verbatim from spec §4 — binding on every task. Each task's requirements implicitly include this section.)_

- **Privacy:** real observing-site coordinates **<REDACTED-LAT> N / <REDACTED-LON> W** and the label **"<REDACTED-SITE-LABEL>"** must NEVER appear in code, tests, docs, or fixtures. Site default stays **"My Observatory"**, coords `0.0`. SITELAT/SITELONG tests MUST use invented coords (e.g. `40.0 / -105.0`), never the developer's real site.
- **Secrets:** admin tokens and the Ed25519 signing seed are never committed literally.
- **Git:** never `git add -A` — stage explicit paths only. Push to origin only when the user asks.
- **Backend tests:** `server/.venv/Scripts/pytest.exe`. Do **not** run `vite build` concurrently with pytest.
- **UI gate:** `cd ui && npx tsc -b` is the CI gate; the repo has **no jsdom/DOM harness**. *(No UI in this feature.)*
- **astrotown:** do not disrupt the deployed box; this program is dev-branch work until a release is cut.
- **Honest-disabled UI (§11.8 idiom):** dim token + lock glyph + `aria-disabled` + `title` reason — never native `disabled`. *(No UI in this feature.)*
- **Additive / back-compat:** all new cards are additive; no existing card renamed/removed. `meta=None` reproduces today's behavior. Do **NOT** edit `build/lib/astrodeck/**` (stale build copy); ship from `server/astrodeck/**` only.

**Definition of done:** backend suite green · new logic covered by a test in the repo's idiom · spec-compliance + code-quality reviews passed · privacy scan clean (`<REDACTED-LAT>` / `<REDACTED-LON>` / `<REDACTED-SITE-LABEL>` absent from the diff) · tracker updated.

**Test command convention:** all pytest commands below run **from the repo root** `C:\Users\bear\astro` and use the required interpreter `server/.venv/Scripts/pytest.exe`. pytest auto-discovers `server/pyproject.toml` (rootdir=`server`, `testpaths=["tests"]`, `asyncio_mode="auto"`). `-n0` disables xdist for a fast single-test run.

---

## Task Boundary Notes (deviations from the suggested seams)

The suggested boundary "(T1) two bug fixes + `Optics.telescope_name`" is split by the code's file seams to avoid double-editing:

- **Bug 1 (SWCREATE)** is folded into **T2**, because T2 already rewrites `save_fits` — fixing the version string in the same rewrite avoids editing that function twice.
- **Bug 2 (TELESCOP)** is split: its *config prerequisite* (`Optics.telescope_name` + `effective_optics` surfacing) is **T1**; its *hub wiring* (`telescope=` on the `save_fits` call) lands in **T4**, because T4 rewrites that exact `Hub.capture` call to also pass `meta=`. Editing that one call once, in T4, is cleaner than touching it in both T1 and T4.
- **OBJCTRA/OBJCTDEC** are formatted by the **caller** (T4) and passed via two added `FrameMeta` fields (`objctra`/`objctdec`), rather than importing `coords` into the pure writer. This keeps `save_fits` config/coords-free (spec §2 principle) and keeps T2 independent of T3.
- **`FrameMeta.obj_az_deg`** from the spec's dataclass is dropped (YAGNI): supervisor ruling 6 drops the `OBJCTAZ` card, so the field would be dead. Trivially re-addable.

---

### Task 1: `Optics.telescope_name` config field + `effective_optics` surfacing

**Files:**
- Modify: `server/astrodeck/config.py:67-78` (add field to `Optics`)
- Modify: `server/astrodeck/hub.py:932-944` (surface `telescope_name` in the `effective_optics()` return dict)
- Test: `server/tests/test_config.py` (new test)

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `config.Optics.telescope_name: str = ""` (new pydantic field; default empty).
  - `Hub.effective_optics()` return dict gains key `"telescope_name": str` (empty when unset). Consumed by T4.

- [ ] **Step 1: Write the failing test**

Add to `server/tests/test_config.py`:

```python
def test_optics_telescope_name_defaults_empty_and_surfaces():
    from astrodeck.config import Optics
    from astrodeck.hub import Hub
    # default is empty (honest: TELESCOP omitted when blank)
    assert Optics().telescope_name == ""
    # a set name round-trips through the model
    assert Optics(telescope_name="Askar 71F").telescope_name == "Askar 71F"
    # effective_optics() surfaces the key so Hub.capture can thread it
    h = Hub()
    assert "telescope_name" in h.effective_optics()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `server/.venv/Scripts/pytest.exe server/tests/test_config.py::test_optics_telescope_name_defaults_empty_and_surfaces -v -n0`
Expected: FAIL — `AttributeError`/`ValidationError` on `telescope_name` (field does not exist) or `KeyError` on the missing dict key.

- [ ] **Step 3: Add the config field**

In `server/astrodeck/config.py`, inside `class Optics(BaseModel)` (after `guide_focal_length_mm`, around line 78):

```python
    # TELESCOP source (PRO-2 F-B, supervisor ruling 1): the optical tube's name.
    # Written to the FITS TELESCOP card when set, omitted when blank. NOT the
    # mount device name (a wrong string pollutes stacker grouping).
    telescope_name: str = ""
```

- [ ] **Step 4: Surface it in `effective_optics`**

In `server/astrodeck/hub.py`, in the `effective_optics()` return dict (around line 932-944), add the key alongside `focal_length_mm`:

```python
        return {
            "focal_length_mm": o.focal_length_mm,
            "telescope_name": o.telescope_name,
            "pixel_size_um": px,
```

- [ ] **Step 5: Run test to verify it passes**

Run: `server/.venv/Scripts/pytest.exe server/tests/test_config.py::test_optics_telescope_name_defaults_empty_and_surfaces -v -n0`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add server/astrodeck/config.py server/astrodeck/hub.py server/tests/test_config.py
git commit -m "feat(config): Optics.telescope_name + surface in effective_optics (PRO-2 F-B T1)"
```

**Implementation tier:** Sonnet (mechanical config field + one dict key). Review floor: Sonnet.

---

### Task 2: `FrameMeta` + pure `save_fits` new-card writing + SWCREATE fix + omit policy

**Files:**
- Modify: `server/astrodeck/imaging/fitsio.py` (whole module body through `save_fits`)
- Modify: `server/astrodeck/imaging/__init__.py` (export `FrameMeta`)
- Test: `server/tests/test_fitsio.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure writer).
- Produces:
  - `fitsio.FrameMeta` dataclass with fields (all `None`-default except noted):
    `focal_length_mm: float|None`, `pixel_size_um: float|None` (UNBINNED µm; `save_fits` multiplies by `frame.binning`), `site_lat_deg: float|None` (+N), `site_lon_deg: float|None` (+E), `site_elev_m: float|None`, `obj_alt_deg: float|None`, `airmass: float|None`, `equinox: float=2000.0`, `radesys: str="ICRS"`, `objctra: str|None`, `objctdec: str|None`, `set_temp_c: float|None`, `focuser_pos: int|None`, `focuser_temp_c: float|None`, `rotator_angle_deg: float|None`, `egain_e_per_adu: float|None`, `hfr: float|None`, `star_count: int|None`, `wcs: "WcsSolution|None"=None` (declared here, applied in T6).
  - `save_fits(frame, path, *, target="", filter_name="", frame_type="Light", ra_hours=None, dec_deg=None, telescope="", instrument="", meta: "FrameMeta|None"=None) -> Path` — writes new cards only for non-None/finite values; `meta=None` ⇒ today's behavior + the SWCREATE fix. Consumed by T4/T6/T7.

- [ ] **Step 1: Write the failing tests**

Add to the top of `server/tests/test_fitsio.py` (add `import pytest` and the `__version__` import):

```python
import pytest
from astrodeck import __version__
from astrodeck.imaging.fitsio import FrameMeta


def _frame_binned(binning: int = 2):
    return CameraFrame(
        data=np.zeros((16, 16), dtype=np.uint16),
        exposure_s=30.0, gain=100, offset=10, binning=binning,
        bayer_pattern=None, temperature_c=-9.8, timestamp=1_772_775_791.0)


def test_all_new_cards_present_and_correct(tmp_path):
    meta = FrameMeta(
        focal_length_mm=530.0, pixel_size_um=3.76,
        site_lat_deg=40.0, site_lon_deg=-105.0, site_elev_m=1600.0,
        obj_alt_deg=55.0, airmass=1.221,
        objctra="05 35 17.9", objctdec="-05 23 28",
        set_temp_c=-10.0, focuser_pos=12345, focuser_temp_c=5.5,
        rotator_angle_deg=123.4, egain_e_per_adu=0.8, hfr=2.1, star_count=42)
    path = save_fits(_frame_binned(2), tmp_path / "light.fits",
                     ra_hours=5.5, dec_deg=-5.39, meta=meta)
    with fits.open(path) as hdul:
        h = hdul[0].header
    assert h["FOCALLEN"] == pytest.approx(530.0)
    assert h["XPIXSZ"] == pytest.approx(3.76 * 2)   # pixel_size_um x binning
    assert h["YPIXSZ"] == pytest.approx(h["XPIXSZ"])
    assert h["SITELAT"] == pytest.approx(40.0)
    assert h["SITELONG"] == pytest.approx(-105.0)
    assert h["SITEELEV"] == pytest.approx(1600.0)
    assert h["OBJCTALT"] == pytest.approx(55.0)
    assert h["AIRMASS"] == pytest.approx(1.221)
    assert h["OBJCTRA"] == "05 35 17.9"
    assert h["OBJCTDEC"] == "-05 23 28"
    assert h["SET-TEMP"] == pytest.approx(-10.0)
    assert h["FOCPOS"] == 12345
    assert h["FOCTEMP"] == pytest.approx(5.5)
    assert h["ROTATANG"] == pytest.approx(123.4)
    assert h["EGAIN"] == pytest.approx(0.8)
    assert h["HFR"] == pytest.approx(2.1)
    assert h["STARCNT"] == 42
    assert h["EQUINOX"] == pytest.approx(2000.0)
    assert h["RADESYS"] == "ICRS"


def test_swcreate_is_real_version(tmp_path):
    path = save_fits(_frame(), tmp_path / "light.fits")
    with fits.open(path) as hdul:
        sw = hdul[0].header["SWCREATE"]
    assert sw == f"AstroDeck {__version__}"
    assert "0.1.0" not in sw


def test_optional_cards_omitted_when_absent(tmp_path):
    # meta=None (throwaway-solve caller behavior): only core cards, no placeholders
    path = save_fits(_frame(), tmp_path / "light.fits")
    with fits.open(path) as hdul:
        h = hdul[0].header
    for absent in ("FOCPOS", "SITELAT", "SET-TEMP", "AIRMASS", "EGAIN",
                   "ROTATANG", "XPIXSZ", "OBJCTRA"):
        assert absent not in h, absent
    for present in ("EXPTIME", "GAIN", "DATE-OBS"):
        assert present in h, present


def test_telescop_written_only_when_named(tmp_path):
    p1 = save_fits(_frame(), tmp_path / "named.fits", telescope="Askar 71F")
    with fits.open(p1) as hdul:
        assert hdul[0].header["TELESCOP"] == "Askar 71F"
    p2 = save_fits(_frame(), tmp_path / "blank.fits", telescope="")
    with fits.open(p2) as hdul:
        assert "TELESCOP" not in hdul[0].header


def test_airmass_omitted_below_horizon(tmp_path):
    # caller passes airmass=None (its coords.airmass helper returns None <= horizon)
    meta = FrameMeta(obj_alt_deg=-3.0, airmass=None)
    path = save_fits(_frame(), tmp_path / "light.fits",
                     ra_hours=5.5, dec_deg=-5.39, meta=meta)
    with fits.open(path) as hdul:
        assert "AIRMASS" not in hdul[0].header
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `server/.venv/Scripts/pytest.exe server/tests/test_fitsio.py -v -n0`
Expected: the 5 new tests FAIL (`ImportError: cannot import name 'FrameMeta'`); the 2 existing DATE-OBS tests still PASS.

- [ ] **Step 3: Rewrite `fitsio.py` (imports + `FrameMeta` + `_finite` + `save_fits`)**

Replace the entire contents of `server/astrodeck/imaging/fitsio.py` above any `write_wcs` (there is none yet) with:

```python
"""FITS output with proper astro headers."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from astropy.io import fits

from .. import __version__
from ..devices.base import CameraFrame


@dataclass
class FrameMeta:
    """Everything Hub.capture reads from config/site/coords/devices and hands to
    the pure writer. Every field is optional; save_fits emits a card only for a
    non-None, finite value (omit-not-placeholder, spec §8)."""
    # optics
    focal_length_mm: float | None = None
    pixel_size_um: float | None = None      # UNBINNED µm; save_fits x frame.binning
    # site + pointing geometry
    site_lat_deg: float | None = None       # +N
    site_lon_deg: float | None = None       # +E
    site_elev_m: float | None = None
    obj_alt_deg: float | None = None
    airmass: float | None = None
    equinox: float = 2000.0
    radesys: str = "ICRS"
    objctra: str | None = None              # 'HH MM SS.s' (J2000)
    objctdec: str | None = None             # '+DD MM SS'  (J2000)
    # per-frame device telemetry
    set_temp_c: float | None = None
    focuser_pos: int | None = None
    focuser_temp_c: float | None = None
    rotator_angle_deg: float | None = None
    egain_e_per_adu: float | None = None
    # quality (already on the frame)
    hfr: float | None = None
    star_count: int | None = None
    # astrometry (applied in Task 6)
    wcs: "WcsSolution | None" = None         # noqa: F821 - str annotation, Task 6


def _finite(x) -> bool:
    """True when x is a real, writable number (not None / NaN / inf)."""
    if x is None:
        return False
    if isinstance(x, float) and not math.isfinite(x):
        return False
    return True


def save_fits(frame: CameraFrame, path: Path, *, target: str = "",
              filter_name: str = "", frame_type: str = "Light",
              ra_hours: float | None = None, dec_deg: float | None = None,
              telescope: str = "", instrument: str = "",
              meta: "FrameMeta | None" = None) -> Path:
    hdu = fits.PrimaryHDU(frame.data)
    hdr = hdu.header
    hdr["EXPTIME"] = (frame.exposure_s, "Exposure time (s)")
    hdr["GAIN"] = frame.gain
    hdr["OFFSET"] = frame.offset
    hdr["XBINNING"] = frame.binning
    hdr["YBINNING"] = frame.binning
    hdr["IMAGETYP"] = frame_type
    # FITS 4.0 sec 4.4.2 requires 'YYYY-MM-DDThh:mm:ss[.s...]' with NO timezone
    # designator (UTC implied); isoformat() on a tz-aware datetime would append
    # '+00:00', which strict FITS parsers reject.
    hdr["DATE-OBS"] = datetime.fromtimestamp(frame.timestamp, tz=timezone.utc).replace(tzinfo=None).isoformat()
    if frame.temperature_c is not None:
        hdr["CCD-TEMP"] = (frame.temperature_c, "Sensor temperature (C)")
    if frame.bayer_pattern:
        hdr["BAYERPAT"] = frame.bayer_pattern
    if target:
        hdr["OBJECT"] = target
    if filter_name:
        hdr["FILTER"] = filter_name
    if ra_hours is not None:
        hdr["RA"] = (ra_hours * 15.0, "RA of telescope (deg, J2000)")
    if dec_deg is not None:
        hdr["DEC"] = (dec_deg, "Dec of telescope (deg, J2000)")
    if telescope:
        hdr["TELESCOP"] = telescope
    if instrument:
        hdr["INSTRUME"] = instrument

    m = meta or FrameMeta()
    # --- optics ---
    if _finite(m.focal_length_mm) and m.focal_length_mm > 0:
        hdr["FOCALLEN"] = (float(m.focal_length_mm), "Focal length (mm)")
    if _finite(m.pixel_size_um) and m.pixel_size_um > 0:
        eff = float(m.pixel_size_um) * max(1, int(frame.binning or 1))
        hdr["XPIXSZ"] = (eff, "Binned pixel size (um)")
        hdr["YPIXSZ"] = (eff, "Binned pixel size (um)")
    # --- site (only when a real site is configured; caller passes None on default) ---
    if _finite(m.site_lat_deg):
        hdr["SITELAT"] = (float(m.site_lat_deg), "Observatory latitude (deg, +N)")
    if _finite(m.site_lon_deg):
        hdr["SITELONG"] = (float(m.site_lon_deg), "Observatory longitude (deg, +E)")
    if _finite(m.site_elev_m):
        hdr["SITEELEV"] = (float(m.site_elev_m), "Observatory elevation (m)")
    # --- pointing geometry ---
    if _finite(m.obj_alt_deg):
        hdr["OBJCTALT"] = (float(m.obj_alt_deg), "Altitude of target (deg)")
    if _finite(m.airmass):
        hdr["AIRMASS"] = (float(m.airmass), "Airmass (Kasten-Young)")
    if m.objctra:
        hdr["OBJCTRA"] = (m.objctra, "RA of target (J2000)")
    if m.objctdec:
        hdr["OBJCTDEC"] = (m.objctdec, "Dec of target (J2000)")
    # --- device telemetry ---
    if _finite(m.set_temp_c):
        hdr["SET-TEMP"] = (float(m.set_temp_c), "Cooler setpoint (C)")
    if m.focuser_pos is not None:
        hdr["FOCPOS"] = (int(m.focuser_pos), "Focuser position (steps)")
    if _finite(m.focuser_temp_c):
        hdr["FOCTEMP"] = (float(m.focuser_temp_c), "Focuser temperature (C)")
    if _finite(m.rotator_angle_deg):
        hdr["ROTATANG"] = (float(m.rotator_angle_deg), "Rotator sky PA (deg)")
    if _finite(m.egain_e_per_adu):
        hdr["EGAIN"] = (float(m.egain_e_per_adu), "Gain (e-/ADU)")
    # --- quality ---
    if _finite(m.hfr):
        hdr["HFR"] = (float(m.hfr), "Half-flux radius (px)")
    if m.star_count is not None:
        hdr["STARCNT"] = (int(m.star_count), "Detected star count")
    # --- frame of the written RA/Dec: always paired when RA/Dec are present ---
    if ra_hours is not None and dec_deg is not None:
        hdr["EQUINOX"] = (float(m.equinox), "Equinox of RA/Dec")
        hdr["RADESYS"] = (m.radesys, "Reference frame")

    hdr["SWCREATE"] = (f"AstroDeck {__version__}", "Creating software")

    path.parent.mkdir(parents=True, exist_ok=True)
    hdu.writeto(path, overwrite=True)
    return path
```

- [ ] **Step 4: Export `FrameMeta`**

In `server/astrodeck/imaging/__init__.py`, change the fitsio import and `__all__`:

```python
from .fitsio import FrameMeta, save_fits
```

and add `"FrameMeta"` to the `__all__` list.

- [ ] **Step 5: Run tests to verify they pass**

Run: `server/.venv/Scripts/pytest.exe server/tests/test_fitsio.py -v -n0`
Expected: PASS (7 tests: 2 existing DATE-OBS + 5 new).

- [ ] **Step 6: Commit**

```bash
git add server/astrodeck/imaging/fitsio.py server/astrodeck/imaging/__init__.py server/tests/test_fitsio.py
git commit -m "feat(fitsio): FrameMeta + new header cards + omit policy + SWCREATE version fix (PRO-2 F-B T2)"
```

**Implementation tier:** Sonnet (pattern-following pure writer; each card is a guarded assignment). Review floor: Sonnet.

---

### Task 3: `coords.airmass` (Kasten-Young) + space-separated OBJCTRA/OBJCTDEC formatters

**Files:**
- Modify: `server/astrodeck/catalog/coords.py` (add 3 pure functions near `format_ra`/`format_dec`, ~line 37-51)
- Test: `server/tests/test_coords_airmass.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces (all pure, no deps beyond `math`):
  - `coords.airmass(alt_deg: float) -> float | None` — Kasten-Young 1989; `None` when `alt_deg <= 0`.
  - `coords.format_ra_fits(hours: float) -> str` — `'HH MM SS.s'` (space-separated).
  - `coords.format_dec_fits(deg: float) -> str` — `'+DD MM SS'` (space-separated, integer seconds).
  Consumed by T4.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_coords_airmass.py`:

```python
"""Kasten-Young airmass + FITS-convention sexagesimal formatters (PRO-2 F-B)."""
import pytest

from astrodeck.catalog.coords import airmass, format_dec_fits, format_ra_fits


def test_airmass_zenith_is_one():
    assert airmass(90.0) == pytest.approx(1.0, abs=0.01)


def test_airmass_thirty_degrees_near_two():
    # sec(60 deg) = 2.0; Kasten-Young is a touch under
    assert 1.9 < airmass(30.0) < 2.05


def test_airmass_none_at_or_below_horizon():
    assert airmass(0.0) is None
    assert airmass(-5.0) is None


def test_format_ra_fits_space_separated():
    assert format_ra_fits(5.5883) == "05 35 17.9"


def test_format_dec_fits_space_separated_signed():
    assert format_dec_fits(-5.3911) == "-05 23 28"
    assert format_dec_fits(5.3911) == "+05 23 28"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `server/.venv/Scripts/pytest.exe server/tests/test_coords_airmass.py -v -n0`
Expected: FAIL — `ImportError: cannot import name 'airmass'`.

- [ ] **Step 3: Add the three functions**

In `server/astrodeck/catalog/coords.py`, after `format_dec` (line 51), insert:

```python
def format_ra_fits(hours: float) -> str:
    """FITS OBJCTRA convention: space-separated 'HH MM SS.s' (J2000)."""
    total = (hours % 24.0) * 3600.0            # seconds of time
    h = int(total // 3600)
    m = int((total % 3600) // 60)
    s = total % 60.0
    return f"{h:02d} {m:02d} {s:04.1f}"


def format_dec_fits(deg: float) -> str:
    """FITS OBJCTDEC convention: space-separated '+DD MM SS' (J2000)."""
    sign = "-" if deg < 0 else "+"
    total = int(round(abs(deg) * 3600.0))      # arcsec, rollover-safe
    d, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{sign}{d:02d} {m:02d} {s:02d}"


def airmass(alt_deg: float) -> float | None:
    """Relative optical airmass from apparent altitude, Kasten & Young (1989).
    Returns None at or below the horizon (the formula diverges) so the caller
    omits the AIRMASS card rather than write a bogus value."""
    if alt_deg is None or alt_deg <= 0.0:
        return None
    h = float(alt_deg)
    return 1.0 / (math.sin(math.radians(h)) + 0.50572 * (6.07995 + h) ** -1.6364)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `server/.venv/Scripts/pytest.exe server/tests/test_coords_airmass.py -v -n0`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/catalog/coords.py server/tests/test_coords_airmass.py
git commit -m "feat(coords): airmass (Kasten-Young) + FITS sexagesimal formatters (PRO-2 F-B T3)"
```

**Implementation tier:** Sonnet (pure math + string formatting). Review floor: Sonnet.

---

### Task 4: `Hub.capture` builds `FrameMeta` (guarded reads) + JNow→J2000 + TELESCOP wiring

**Files:**
- Modify: `server/astrodeck/hub.py:1412-1425` (the `Hub.capture` save block: add J2000 conversion + build meta + pass `telescope=`/`meta=`)
- Modify: `server/astrodeck/hub.py` (add private `Hub._frame_meta` helper method near `capture`)
- Test: `server/tests/test_hub_capture_precession.py` (2 new integration tests; reuses `_StatusAlpacaTel` already defined there)

**Interfaces:**
- Consumes:
  - `fitsio.FrameMeta` and `fitsio.save_fits(..., meta=...)` (T2).
  - `coords.altaz(ra_hours, dec_deg, lat_deg, lon_deg, unix_time) -> (alt, az)` (existing), `coords.airmass` / `coords.format_ra_fits` / `coords.format_dec_fits` (T3).
  - `Hub.effective_optics()["telescope_name" | "focal_length_mm" | "pixel_size_um" | "have_optics"]` (T1 + existing).
  - `Hub.from_mount_frame(tel, ra_hours, dec_deg)` (existing, `hub.py:1367`); `Hub.site` (existing).
  - `frame.egain_e_per_adu` read via `getattr(frame, "egain_e_per_adu", None)` so this task is decoupled from T5's field.
- Produces: `Hub._frame_meta(self, frame, ra_hours, dec_deg) -> FrameMeta` (async). Consumed by nothing downstream (internal).

- [ ] **Step 1: Write the failing tests**

Add to `server/tests/test_hub_capture_precession.py` (imports `fits` from astropy — add `from astropy.io import fits` at the top of the file; `Optics`/`Site` from `astrodeck.config`):

```python
async def test_capture_writes_full_headers(monkeypatch, tmp_path):
    """Caller -> save_fits wiring: config/site/device telemetry lands in the FITS."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    from astrodeck.config import Optics, Site
    cfg = temp_store.cfg()
    cfg.optics = Optics(focal_length_mm=530.0, pixel_size_um=3.76,
                        telescope_name="Askar 71F")
    cfg.site = Site(latitude=40.0, longitude=-105.0, elevation_m=1600.0,
                    is_default=False)               # INVENTED coords (privacy)
    temp_store.bump_and_save()

    h = Hub()
    await h.connect_sim()
    try:
        cam = h.devices["camera"]
        await cam.set_cooler(True, -10.0)           # so SET-TEMP is present
        await h.capture(1.0, 100, 30, binning=2, save=True, target="M42")
        saved = Path(h.last_frame.saved_path)
        with fits.open(saved) as hdul:
            hd = hdul[0].header
        assert hd["TELESCOP"] == "Askar 71F"
        assert hd["FOCALLEN"] == pytest.approx(530.0)
        assert hd["XPIXSZ"] == pytest.approx(3.76 * 2)
        assert hd["INSTRUME"]
        assert hd["SET-TEMP"] == pytest.approx(-10.0)
        assert hd["SITELAT"] == pytest.approx(40.0)
        assert hd["SITELONG"] == pytest.approx(-105.0)
        assert "FOCPOS" in hd                        # sim focuser present
        assert "ROTATANG" in hd                      # sim rotator present
        assert hd["EQUINOX"] == pytest.approx(2000.0)
        assert hd["RADESYS"] == "ICRS"
    finally:
        await h.disconnect_all()


async def test_capture_radec_written_as_j2000(monkeypatch, tmp_path):
    """A JNOW Alpaca mount's reported position is precessed to J2000 before it is
    written, and EQUINOX=2000.0 pairs it (supervisor ruling 3). FICTIONAL coords."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    await h.connect_sim()
    try:
        ra_jnow, dec_jnow = 7.7777, 22.2222
        h.devices["telescope"] = _StatusAlpacaTel(ra_jnow, dec_jnow, equ=1)
        await h.capture(0.5, 100, 30, 1, save=True)
        saved = Path(h.last_frame.saved_path)
        with fits.open(saved) as hdul:
            hd = hdul[0].header
        exp_ra, exp_dec = precess_jnow_to_j2000(ra_jnow, dec_jnow)
        assert hd["RA"] == pytest.approx(exp_ra * 15.0, abs=1e-2)
        assert hd["DEC"] == pytest.approx(exp_dec, abs=1e-2)
        assert hd["EQUINOX"] == pytest.approx(2000.0)
        assert abs(hd["RA"] - ra_jnow * 15.0) > 0.05   # genuinely moved off JNOW
    finally:
        await h.disconnect_all()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `server/.venv/Scripts/pytest.exe "server/tests/test_hub_capture_precession.py::test_capture_writes_full_headers" "server/tests/test_hub_capture_precession.py::test_capture_radec_written_as_j2000" -v -n0`
Expected: FAIL — `KeyError: 'TELESCOP'` / `KeyError: 'FOCALLEN'` (capture does not yet build/pass meta or telescope), and the RA is the raw JNOW value (no conversion).

- [ ] **Step 3: Add the `_frame_meta` helper**

In `server/astrodeck/hub.py`, add this method to the `Hub` class immediately before `async def capture` (around line 1380):

```python
    async def _frame_meta(self, frame, ra_hours: float | None,
                          dec_deg: float | None) -> "FrameMeta":
        """Best-effort telemetry snapshot for the FITS header (spec §8/§9). Every
        read is individually guarded: an absent/hung device or a failed read
        leaves its value None (its card omitted) and never blocks or fails the
        save. Only Hub.capture builds this; save_fits stays device-free."""
        from .catalog import coords
        meta = FrameMeta()
        # optics (config; FOCALLEN always available, pixel size only when known)
        try:
            opt = self.effective_optics()
            fl = opt.get("focal_length_mm")
            if fl:
                meta.focal_length_mm = float(fl)
            if opt.get("have_optics") and opt.get("pixel_size_um"):
                meta.pixel_size_um = float(opt["pixel_size_um"])   # UNBINNED
        except Exception:
            pass
        # site (only when a real, non-default site is configured)
        lat = lon = None
        try:
            s = self.site
            if not s.get("is_default", True):
                lat, lon = float(s["latitude"]), float(s["longitude"])
                meta.site_lat_deg, meta.site_lon_deg = lat, lon
                meta.site_elev_m = float(s.get("elevation_m", 0.0))
        except Exception:
            lat = lon = None
        # pointing geometry (needs J2000 RA/Dec; alt/airmass also need a real site)
        if ra_hours is not None and dec_deg is not None:
            try:
                meta.objctra = coords.format_ra_fits(ra_hours)
                meta.objctdec = coords.format_dec_fits(dec_deg)
            except Exception:
                pass
            if lat is not None and lon is not None:
                try:
                    alt, _az = coords.altaz(ra_hours, dec_deg, lat, lon,
                                            frame.timestamp)
                    if alt > 0:
                        meta.obj_alt_deg = alt
                        meta.airmass = coords.airmass(alt)
                except Exception:
                    pass
        # cooler setpoint (only when a cooler is present AND on)
        try:
            cam = self.devices.get("camera")
            getc = getattr(cam, "get_cooler", None)
            if callable(getc):
                cooler = await getc()
                if cooler and cooler.get("on") and cooler.get("target_c") is not None:
                    meta.set_temp_c = float(cooler["target_c"])
        except Exception:
            pass
        # focuser position + optional thermometer
        try:
            foc = self.devices.get("focuser")
            if foc and getattr(foc, "connected", False):
                meta.focuser_pos = int(await foc.get_position())
                t = await foc.get_temperature()
                if t is not None:
                    meta.focuser_temp_c = float(t)
        except Exception:
            pass
        # rotator sky position angle
        try:
            rot = self.devices.get("rotator")
            if rot and getattr(rot, "connected", False):
                meta.rotator_angle_deg = float(await rot.get_position())
        except Exception:
            pass
        # EGAIN (populated on the frame by the backend in Task 5; getattr keeps
        # this task decoupled from that field's existence)
        eg = getattr(frame, "egain_e_per_adu", None)
        if eg:
            meta.egain_e_per_adu = float(eg)
        # quality (already measured on the frame)
        if frame.hfr is not None:
            meta.hfr = float(frame.hfr)
        if frame.stars is not None:
            meta.star_count = int(frame.stars)
        return meta
```

- [ ] **Step 4: Import `FrameMeta` into hub and rewrite the capture save block**

In `server/astrodeck/hub.py`, change the imaging import (lines 37-49) to include `FrameMeta`:

```python
from .imaging import (
    FrameMeta,
    auto_levels,
    cloud_score,
    compute_histogram,
    detect_stars,
    display_histogram,
    measure_stars,
    save_fits,
    stretch_with,
    to_jpeg,
    to_png,
    to_thumb,
)
```

Then replace the capture save block (currently lines 1412-1425):

```python
            ra = dec = None
            tel = self.devices.get("telescope")
            if tel and tel.connected:
                try:
                    ra, dec = await tel.get_position()
                except Exception:
                    pass
            # Offloaded so a 25-120 MB uint16 FITS write to the Pi's SD card never
            # freezes the event loop for seconds every frame (WS/preview stall,
            # queued guide events, delayed STOP) — same as solve_and_sync's write.
            await asyncio.to_thread(
                save_fits, frame, local_save_path, target=target, filter_name=filt,
                frame_type=frame_type, ra_hours=ra, dec_deg=dec,
                instrument=cam.name)
```

with:

```python
            ra = dec = None
            tel = self.devices.get("telescope")
            if tel and tel.connected:
                try:
                    ra, dec = await tel.get_position()
                    # Bring a JNOW Alpaca mount's report to J2000 (the frame ASTAP
                    # and the catalog use); no-op for sim/NINA. Best-effort guarded
                    # (supervisor ruling 3).
                    if ra is not None:
                        ra, dec = await self.from_mount_frame(tel, ra, dec)
                except Exception:
                    pass
            # Gather header telemetry (best-effort; never fails the save) and the
            # OTA name for TELESCOP (omitted when blank).
            meta = await self._frame_meta(frame, ra, dec)
            try:
                telescope_name = (self.effective_optics().get("telescope_name")
                                  or "").strip()
            except Exception:
                telescope_name = ""
            # Offloaded so a 25-120 MB uint16 FITS write to the Pi's SD card never
            # freezes the event loop for seconds every frame (WS/preview stall,
            # queued guide events, delayed STOP) — same as solve_and_sync's write.
            await asyncio.to_thread(
                save_fits, frame, local_save_path, target=target, filter_name=filt,
                frame_type=frame_type, ra_hours=ra, dec_deg=dec,
                telescope=telescope_name, instrument=cam.name, meta=meta)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `server/.venv/Scripts/pytest.exe "server/tests/test_hub_capture_precession.py::test_capture_writes_full_headers" "server/tests/test_hub_capture_precession.py::test_capture_radec_written_as_j2000" -v -n0`
Expected: PASS. Then run the whole file to confirm no regression: `server/.venv/Scripts/pytest.exe server/tests/test_hub_capture_precession.py -v -n0` → all PASS.

- [ ] **Step 6: Commit**

```bash
git add server/astrodeck/hub.py server/tests/test_hub_capture_precession.py
git commit -m "feat(hub): capture builds FrameMeta + JNow->J2000 + TELESCOP wiring (PRO-2 F-B T4)"
```

**Implementation tier:** Opus (JNow→J2000 wiring + fully guarded multi-device reads that must never fail a capture — subtle correctness). Review: Opus.

---

### Task 5: `CameraFrame.egain_e_per_adu` + per-backend reads

**Files:**
- Modify: `server/astrodeck/devices/base.py:44-64` (add field to `CameraFrame`)
- Modify: `server/astrodeck/devices/cameras/engine.py:68-101` (thread `egain` from `caps.extra` — covers Player One + ZWO, which already populate `extra["egain"]`)
- Modify: `server/astrodeck/devices/sim.py:362-385` (sim constant)
- Test: `server/tests/test_camera_egain.py` (new)

**Interfaces:**
- Consumes: nothing structural (independent field). `Hub.capture` (T4) already reads it via `getattr`.
- Produces:
  - `CameraFrame.egain_e_per_adu: float | None = None` (new dataclass field).
  - `engine._egain_from_caps(caps) -> float | None` (pure helper).
  - `SimCamera.SIM_EGAIN: float` constant.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_camera_egain.py`:

```python
"""EGAIN threading: e-/ADU onto CameraFrame per backend (PRO-2 F-B / ruling 5)."""
import numpy as np

from astrodeck.devices.cameras.engine import _egain_from_caps


class _Caps:
    def __init__(self, extra):
        self.extra = extra


def test_egain_from_caps_reads_extra():
    assert _egain_from_caps(_Caps({"egain": 0.8})) == 0.8


def test_egain_from_caps_none_when_absent():
    assert _egain_from_caps(_Caps({})) is None
    assert _egain_from_caps(None) is None


async def test_sim_expose_sets_egain():
    from astrodeck.devices.sim import SimCamera, SimRig
    cam = SimCamera(SimRig())
    await cam.connect()
    frame = await cam.expose(0.05, 100, 30, binning=1)
    assert frame.egain_e_per_adu == SimCamera.SIM_EGAIN
    assert frame.egain_e_per_adu is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `server/.venv/Scripts/pytest.exe server/tests/test_camera_egain.py -v -n0`
Expected: FAIL — `ImportError: cannot import name '_egain_from_caps'`.

- [ ] **Step 3: Add the `CameraFrame` field**

In `server/astrodeck/devices/base.py`, inside `class CameraFrame` (after `data_is_linear: bool = True`, line 64):

```python
    #: e-/ADU at the capture gain, when the backend reports it (Player One
    #: get_egain / ZWO ElecPerADU / sim constant). None -> EGAIN card omitted.
    egain_e_per_adu: float | None = None
```

- [ ] **Step 4: Thread egain through the native engine**

In `server/astrodeck/devices/cameras/engine.py`, add the pure helper near the top of the module (after the imports / before the class), and use it in the `expose` return:

```python
def _egain_from_caps(caps) -> float | None:
    """e-/ADU from the adapter's capability extras (Player One + ZWO both set
    extra['egain']); None when unavailable."""
    eg = caps.extra.get("egain") if (caps and getattr(caps, "extra", None)) else None
    return float(eg) if eg else None
```

Then in `expose` change the returned `CameraFrame` (lines 97-101) to add the field:

```python
        return CameraFrame(
            data=data, exposure_s=seconds, gain=gain, offset=offset,
            binning=binning, bayer_pattern=caps.bayer_pattern,
            temperature_c=temp, timestamp=time.time(),
            full_well=caps.max_adu, data_is_linear=True,
            egain_e_per_adu=_egain_from_caps(caps))
```

- [ ] **Step 5: Add the sim constant + thread it**

In `server/astrodeck/devices/sim.py`, add a class constant to `SimCamera` (near `AMBIENT_C`, above `expose`):

```python
    SIM_EGAIN = 0.8      # e-/ADU: a plausible constant so EGAIN is exercised end-to-end
```

and in the `expose` return `CameraFrame` (lines 373-385) add the field:

```python
        return CameraFrame(
            data=data,
            exposure_s=seconds,
            gain=gain,
            offset=offset,
            binning=binning,
            bayer_pattern=None,
            temperature_c=await self.get_temperature(),
            timestamp=time.time(),
            full_well=self.full_well, data_is_linear=True,
            egain_e_per_adu=self.SIM_EGAIN,
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `server/.venv/Scripts/pytest.exe server/tests/test_camera_egain.py -v -n0`
Expected: PASS (3 tests). Player One / ZWO adapters already put `egain` into `caps.extra`, so their frames now carry it automatically (existing camera contract tests stay green).

- [ ] **Step 7: Commit**

```bash
git add server/astrodeck/devices/base.py server/astrodeck/devices/cameras/engine.py server/astrodeck/devices/sim.py server/tests/test_camera_egain.py
git commit -m "feat(cameras): thread egain_e_per_adu onto CameraFrame (PRO-2 F-B T5)"
```

**Implementation tier:** Sonnet (one field + one extras read + one constant). Review floor: Sonnet.

---

### Task 6: `WcsSolution` + `SolveResult.wcs` + `_wcs_from_astap` + `SimSolver` WCS + `fitsio.write_wcs`

**Files:**
- Modify: `server/astrodeck/solve/base.py:8-15` (add `WcsSolution`, extend `SolveResult`)
- Modify: `server/astrodeck/solve/astap.py` (add `from astropy.io import fits`, `_read_ini`, `_wcs_from_astap`; populate `wcs` in `solve`)
- Modify: `server/astrodeck/solve/simsolver.py` (build a synthetic `WcsSolution`)
- Modify: `server/astrodeck/imaging/fitsio.py` (add `_apply_wcs`, `write_wcs`; apply `meta.wcs` in `save_fits`)
- Modify: `server/astrodeck/imaging/__init__.py` (export `write_wcs`)
- Test: `server/tests/test_fitsio.py` (WCS write-back round-trip), `server/tests/test_solver.py` (ASTAP parse + SimSolver WCS)

**Interfaces:**
- Consumes: `fitsio.save_fits` / `FrameMeta.wcs` (T2).
- Produces:
  - `solve.base.WcsSolution` dataclass: `crval1: float`, `crval2: float`, `crpix1: float`, `crpix2: float`, `cd11/cd12/cd21/cd22: float|None = None`, `cdelt1/cdelt2/crota2: float|None = None`, `ctype1: str="RA---TAN"`, `ctype2: str="DEC--TAN"`, `cunit: str="deg"`, `equinox: float=2000.0`, `radesys: str="ICRS"`.
  - `SolveResult.wcs: "WcsSolution | None" = None`.
  - `astap._wcs_from_astap(ini_path: Path, wcs_path: Path) -> WcsSolution | None` (prefers the `.wcs` headerlet; returns None on any failure).
  - `fitsio._apply_wcs(hdr, wcs) -> None` and `fitsio.write_wcs(path: Path, wcs) -> Path` (non-fatal reopen-and-merge). Consumed by T7.

- [ ] **Step 1: Write the failing tests**

Add to `server/tests/test_fitsio.py`:

```python
def test_wcs_writeback_roundtrips(tmp_path):
    from astrodeck.imaging.fitsio import write_wcs
    from astrodeck.solve.base import WcsSolution
    from astropy.wcs import WCS
    path = save_fits(_frame(), tmp_path / "light.fits")   # 16x16 -> center 8.5
    scale = 1.5 / 3600.0
    wcs = WcsSolution(crval1=83.8221, crval2=-5.3911, crpix1=8.5, crpix2=8.5,
                      cd11=-scale, cd12=0.0, cd21=0.0, cd22=scale)
    write_wcs(path, wcs)
    with fits.open(path) as hdul:
        h = hdul[0].header
        assert h["CTYPE1"] == "RA---TAN"
        assert h["EQUINOX"] == 2000.0
        w = WCS(h)
        assert w.has_celestial
        world = w.wcs_pix2world([[wcs.crpix1 - 1, wcs.crpix2 - 1]], 0)[0]
    assert world[0] == pytest.approx(83.8221, abs=1e-6)
    assert world[1] == pytest.approx(-5.3911, abs=1e-6)
```

Add to `server/tests/test_solver.py`:

```python
def test_wcs_from_astap_parse(tmp_path):
    from astropy.io import fits as _fits
    from astrodeck.solve.astap import _wcs_from_astap
    hdr = _fits.Header()
    hdr["CTYPE1"] = "RA---TAN"
    hdr["CTYPE2"] = "DEC--TAN"
    hdr["CRVAL1"] = 83.8221
    hdr["CRVAL2"] = -5.3911
    hdr["CRPIX1"] = 512.0
    hdr["CRPIX2"] = 512.0
    hdr["CD1_1"] = -0.0004305
    hdr["CD1_2"] = 0.0
    hdr["CD2_1"] = 0.0
    hdr["CD2_2"] = 0.0004305
    wcs_path = tmp_path / "solve.wcs"
    hdr.totextfile(str(wcs_path))
    sol = _wcs_from_astap(tmp_path / "solve.ini", wcs_path)   # .ini absent -> uses .wcs
    assert sol is not None
    assert sol.crval1 == pytest.approx(83.8221)
    assert sol.crpix1 == pytest.approx(512.0)
    assert sol.cd11 == pytest.approx(-0.0004305)
    assert sol.cd22 == pytest.approx(0.0004305)


async def test_simsolver_returns_wcs(tmp_path):
    import numpy as np
    from astrodeck.devices.base import CameraFrame
    from astrodeck.imaging.fitsio import save_fits
    frame = CameraFrame(data=np.zeros((32, 32), dtype=np.uint16), exposure_s=1.0,
                        gain=100, offset=10, binning=1, bayer_pattern=None,
                        temperature_c=-10.0, timestamp=1_772_775_791.0)
    path = save_fits(frame, tmp_path / "sim.fits")
    rig = _FakeRig()                                  # ra_hours=5.5, dec_deg=41.2
    solver = SimSolver(sim_rig=rig, mode="sim")
    res = await solver.solve(path)
    assert res.success and res.wcs is not None
    assert res.wcs.crval1 == pytest.approx(rig.ra_hours * 15.0)
    assert res.wcs.crval2 == pytest.approx(rig.dec_deg)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `server/.venv/Scripts/pytest.exe "server/tests/test_fitsio.py::test_wcs_writeback_roundtrips" "server/tests/test_solver.py::test_wcs_from_astap_parse" "server/tests/test_solver.py::test_simsolver_returns_wcs" -v -n0`
Expected: FAIL — `ImportError` on `WcsSolution` / `write_wcs` / `_wcs_from_astap`.

- [ ] **Step 3: Add `WcsSolution` + extend `SolveResult`**

Replace `server/astrodeck/solve/base.py` lines 8-15 with:

```python
@dataclass
class WcsSolution:
    """Plain-number celestial WCS (serializable, unit-testable). CD matrix is
    preferred; cdelt/crota are an accepted fallback (astropy reads either)."""
    crval1: float
    crval2: float
    crpix1: float
    crpix2: float
    cd11: float | None = None
    cd12: float | None = None
    cd21: float | None = None
    cd22: float | None = None
    cdelt1: float | None = None
    cdelt2: float | None = None
    crota2: float | None = None
    ctype1: str = "RA---TAN"
    ctype2: str = "DEC--TAN"
    cunit: str = "deg"
    equinox: float = 2000.0
    radesys: str = "ICRS"


@dataclass
class SolveResult:
    success: bool
    ra_hours: float = 0.0
    dec_deg: float = 0.0
    rotation_deg: float = 0.0
    pixel_scale_arcsec: float = 0.0
    message: str = ""
    wcs: "WcsSolution | None" = None
```

- [ ] **Step 4: Populate the WCS in `AstapSolver.solve`**

In `server/astrodeck/solve/astap.py`, add the astropy import near the top (with the stdlib imports):

```python
from astropy.io import fits
```

and add these module-level helpers (after `_solve_args`, before `find_astap`):

```python
def _read_ini(ini_path: Path) -> dict[str, str]:
    kv: dict[str, str] = {}
    for line in ini_path.read_text().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            kv[k.strip()] = v.strip()
    return kv


def _wcs_from_astap(ini_path: Path, wcs_path: Path) -> "WcsSolution | None":
    """Build a WcsSolution from ASTAP's result files. Prefers the `.wcs`
    headerlet (a text FITS header) parsed with astropy; falls back to the `.ini`
    numerics. Returns None on ANY failure — a WCS-parse error must never fail the
    solve. Mirrors the `_solve_args` pattern: pure + subprocess-free, so it is
    unit-testable from fixture files."""
    try:
        if wcs_path.exists():
            h = fits.Header.fromtextfile(str(wcs_path))
            def _f(key):
                return float(h[key]) if key in h else None
            return WcsSolution(
                crval1=float(h["CRVAL1"]), crval2=float(h["CRVAL2"]),
                crpix1=float(h["CRPIX1"]), crpix2=float(h["CRPIX2"]),
                cd11=_f("CD1_1"), cd12=_f("CD1_2"),
                cd21=_f("CD2_1"), cd22=_f("CD2_2"),
                cdelt1=_f("CDELT1"), cdelt2=_f("CDELT2"), crota2=_f("CROTA2"),
                ctype1=str(h.get("CTYPE1", "RA---TAN")),
                ctype2=str(h.get("CTYPE2", "DEC--TAN")))
        kv = _read_ini(ini_path)
        if "CRVAL1" not in kv or "CRPIX1" not in kv:
            return None
        def _g(key):
            return float(kv[key]) if key in kv else None
        return WcsSolution(
            crval1=float(kv["CRVAL1"]), crval2=float(kv["CRVAL2"]),
            crpix1=float(kv["CRPIX1"]), crpix2=float(kv["CRPIX2"]),
            cd11=_g("CD1_1"), cd12=_g("CD1_2"),
            cd21=_g("CD2_1"), cd22=_g("CD2_2"),
            cdelt1=_g("CDELT1"), cdelt2=_g("CDELT2"), crota2=_g("CROTA2"))
    except Exception:
        return None
```

and add `WcsSolution` to the base import at the top of the file:

```python
from .base import PlateSolver, SolveResult, WcsSolution
```

Then replace the result-parsing tail of `solve` (current lines 120-142) with:

```python
        ini = fits_path.with_suffix(".ini")
        wcs_path = fits_path.with_suffix(".wcs")
        if not ini.exists():
            return SolveResult(False, message="ASTAP produced no result file")
        kv = _read_ini(ini)
        # Capture the full WCS from the .wcs headerlet BEFORE deleting the files.
        wcs = _wcs_from_astap(ini, wcs_path)
        try:
            ini.unlink()
            wcs_path.unlink(missing_ok=True)
        except OSError:
            pass

        if kv.get("PLTSOLVD") != "T":
            return SolveResult(False, message=kv.get("ERROR", "no solution"))
        ra_deg = float(kv["CRVAL1"])
        dec_deg = float(kv["CRVAL2"])
        rot = float(kv.get("CROTA2", 0))
        scale = abs(float(kv.get("CDELT2", 0))) * 3600
        return SolveResult(True, ra_hours=ra_deg / 15.0, dec_deg=dec_deg,
                           rotation_deg=rot, pixel_scale_arcsec=scale,
                           wcs=wcs, message="solved by ASTAP")
```

- [ ] **Step 5: Build a synthetic WCS in `SimSolver`**

In `server/astrodeck/solve/simsolver.py`, add imports (`import math`, `from astropy.io import fits`, and `WcsSolution` to the base import) and a module-level helper, then attach `wcs=` to both success returns:

```python
import math
from astropy.io import fits
from .base import PlateSolver, SolveResult, WcsSolution


def _sim_wcs(fits_path: Path, ra_hours: float, dec_deg: float,
             rot_deg: float, scale_arcsec: float) -> WcsSolution:
    """A valid TAN WcsSolution centered on the sim pointing (hips_local idiom:
    ctype RA---TAN/DEC--TAN, crval, crpix at image center, CD from scale+rot)."""
    try:
        with fits.open(fits_path) as hdul:
            ny, nx = hdul[0].data.shape[-2:]
    except Exception:
        nx = ny = 1000                       # tolerate a not-yet-written path
    scale = scale_arcsec / 3600.0
    rot = math.radians(rot_deg)
    return WcsSolution(
        crval1=ra_hours * 15.0, crval2=dec_deg,
        crpix1=(nx + 1) / 2.0, crpix2=(ny + 1) / 2.0,
        cd11=-scale * math.cos(rot), cd12=scale * math.sin(rot),
        cd21=scale * math.sin(rot), cd22=scale * math.cos(rot))
```

In the sim-rig success return (currently `return SolveResult(True, ra_hours=self.sim_rig.ra_hours, ...)`):

```python
            return SolveResult(True, ra_hours=self.sim_rig.ra_hours,
                               dec_deg=self.sim_rig.dec_deg,
                               rotation_deg=rot_pa, pixel_scale_arcsec=1.55,
                               wcs=_sim_wcs(fits_path, self.sim_rig.ra_hours,
                                            self.sim_rig.dec_deg, rot_pa, 1.55),
                               message="solved (simulator)")
```

and in the hint-only success return:

```python
            return SolveResult(True, ra_hours=ra_hint, dec_deg=dec_hint,
                               rotation_deg=0.0, pixel_scale_arcsec=1.55,
                               wcs=_sim_wcs(fits_path, ra_hint, dec_hint, 0.0, 1.55),
                               message="solved (simulator, from hint)")
```

- [ ] **Step 6: Add `_apply_wcs` + `write_wcs` to `fitsio.py` and apply `meta.wcs` in `save_fits`**

In `server/astrodeck/imaging/fitsio.py`, insert the `meta.wcs` application into `save_fits` immediately before the `SWCREATE` line:

```python
    # --- WCS (from a plate-solve; save_fits handles the solve-then-save case,
    #     write_wcs the post-hoc case; both funnel through _apply_wcs) ---
    if m.wcs is not None:
        _apply_wcs(hdr, m.wcs)

    hdr["SWCREATE"] = (f"AstroDeck {__version__}", "Creating software")
```

and append these two functions at the end of the module:

```python
def _apply_wcs(hdr, wcs) -> None:
    """Merge a WcsSolution's cards into a header — shared by save_fits and
    write_wcs so both emit an identical WCS block."""
    hdr["CTYPE1"] = (wcs.ctype1, "WCS projection")
    hdr["CTYPE2"] = (wcs.ctype2, "WCS projection")
    hdr["CUNIT1"] = wcs.cunit
    hdr["CUNIT2"] = wcs.cunit
    hdr["CRVAL1"] = (float(wcs.crval1), "RA at reference (deg)")
    hdr["CRVAL2"] = (float(wcs.crval2), "Dec at reference (deg)")
    hdr["CRPIX1"] = (float(wcs.crpix1), "Reference pixel X")
    hdr["CRPIX2"] = (float(wcs.crpix2), "Reference pixel Y")
    if wcs.cd11 is not None:
        hdr["CD1_1"] = float(wcs.cd11)
        hdr["CD1_2"] = float(wcs.cd12) if wcs.cd12 is not None else 0.0
        hdr["CD2_1"] = float(wcs.cd21) if wcs.cd21 is not None else 0.0
        hdr["CD2_2"] = float(wcs.cd22) if wcs.cd22 is not None else 0.0
    elif wcs.cdelt1 is not None:
        hdr["CDELT1"] = float(wcs.cdelt1)
        hdr["CDELT2"] = float(wcs.cdelt2) if wcs.cdelt2 is not None else float(wcs.cdelt1)
        if wcs.crota2 is not None:
            hdr["CROTA2"] = float(wcs.crota2)
    hdr["EQUINOX"] = (float(wcs.equinox), "Equinox of WCS")
    hdr["RADESYS"] = wcs.radesys


def write_wcs(path: Path, wcs) -> Path:
    """Merge a plate-solved WCS into an existing FITS in place. Non-fatal: a
    missing/locked/corrupt file (or a None wcs) is swallowed and the path is
    returned unchanged — WCS write-back must never break a save (spec §9)."""
    if wcs is None:
        return path
    try:
        with fits.open(path, mode="update") as hdul:
            _apply_wcs(hdul[0].header, wcs)
            hdul.flush()
    except Exception:
        pass
    return path
```

- [ ] **Step 7: Export `write_wcs`**

In `server/astrodeck/imaging/__init__.py`, change the fitsio import to `from .fitsio import FrameMeta, save_fits, write_wcs` and add `"write_wcs"` to `__all__`.

- [ ] **Step 8: Run tests to verify they pass**

Run: `server/.venv/Scripts/pytest.exe "server/tests/test_fitsio.py::test_wcs_writeback_roundtrips" "server/tests/test_solver.py::test_wcs_from_astap_parse" "server/tests/test_solver.py::test_simsolver_returns_wcs" -v -n0`
Expected: PASS. Then confirm no regression in the existing solver tests (they pass a non-existent `x.fits`, tolerated by `_sim_wcs`'s fallback): `server/.venv/Scripts/pytest.exe server/tests/test_solver.py server/tests/test_fitsio.py -v -n0` → all PASS.

- [ ] **Step 9: Commit**

```bash
git add server/astrodeck/solve/base.py server/astrodeck/solve/astap.py server/astrodeck/solve/simsolver.py server/astrodeck/imaging/fitsio.py server/astrodeck/imaging/__init__.py server/tests/test_fitsio.py server/tests/test_solver.py
git commit -m "feat(solve,fitsio): WcsSolution + SolveResult.wcs + _wcs_from_astap + SimSolver WCS + write_wcs (PRO-2 F-B T6)"
```

**Implementation tier:** Opus (WCS-headerlet parse + CD/CDELT card writing + round-trip correctness — the subtle task). Review: Opus.

---

### Task 7: `solve_saved_lights` opt-in flag + post-save WCS stamping

**Files:**
- Modify: `server/astrodeck/config.py:410-437` (add `solve_saved_lights: bool = False` to `AppConfig`)
- Modify: `server/astrodeck/hub.py` (after the capture save block: opt-in solve-in-place + `write_wcs`; import `write_wcs`)
- Test: `server/tests/test_hub_capture_precession.py` (new test)

**Interfaces:**
- Consumes: `providers.pick_solver(hub) -> PlateSolver` (existing), `SolveResult.wcs` (T6), `fitsio.write_wcs(path, wcs)` (T6), `Hub.effective_optics()["fov_h_deg"]` (existing).
- Produces: `config.AppConfig.solve_saved_lights: bool = False` (top-level flag, mirrors `deadman_url`).

- [ ] **Step 1: Write the failing test**

Add to `server/tests/test_hub_capture_precession.py`:

```python
async def test_solve_saved_lights_stamps_wcs(monkeypatch, tmp_path):
    """When solve_saved_lights is ON, a saved light is solved in place and gets a
    celestial WCS a stacker can read without re-solving (supervisor ruling 4)."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    temp_store.cfg().solve_saved_lights = True
    temp_store.bump_and_save()
    # Force the sim solver (deterministic; independent of whether ASTAP is on box).
    from astrodeck.solve import SimSolver
    monkeypatch.setattr("astrodeck.providers.pick_solver",
                        lambda hub: SimSolver(getattr(hub, "sim_rig", None), mode=None))

    h = Hub()
    await h.connect_sim()
    try:
        await h.capture(0.5, 100, 30, 1, save=True, target="M42")
        saved = Path(h.last_frame.saved_path)
        from astropy.wcs import WCS
        with fits.open(saved) as hdul:
            hd = hdul[0].header
            assert hd["CTYPE1"] == "RA---TAN"
            w = WCS(hd)
        assert w.has_celestial
    finally:
        await h.disconnect_all()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `server/.venv/Scripts/pytest.exe "server/tests/test_hub_capture_precession.py::test_solve_saved_lights_stamps_wcs" -v -n0`
Expected: FAIL — `AttributeError: 'AppConfig' object has no attribute 'solve_saved_lights'` (and, once that exists, `KeyError: 'CTYPE1'` until the hub wiring lands).

- [ ] **Step 3: Add the config flag**

In `server/astrodeck/config.py`, inside `class AppConfig(BaseModel)` (after `weather`, around line 437):

```python
    # --- opt-in re-solve-free astrometry (PRO-2 F-B, supervisor ruling 4;
    #     appended — old configs load fine) ---
    solve_saved_lights: bool = False   # ON => solve each saved light in place, stamp WCS
```

- [ ] **Step 4: Import `write_wcs` into hub**

In `server/astrodeck/hub.py`, extend the imaging import to include `write_wcs`:

```python
from .imaging import (
    FrameMeta,
    auto_levels,
    cloud_score,
    compute_histogram,
    detect_stars,
    display_histogram,
    measure_stars,
    save_fits,
    stretch_with,
    to_jpeg,
    to_png,
    to_thumb,
    write_wcs,
)
```

- [ ] **Step 5: Wire the post-save solve** *(supervisor correction: AFTER preview publish, not before)*

In `server/astrodeck/hub.py`, insert the block **immediately before `return info`** at the end of `capture()` — i.e. *after* `info = await self._publish_preview(frame)` and after the save-logging `if/elif` block. Placing it here (not before publish) means the solve's 1–10 s on a real ASTAP rig **never delays the preview the user sees**; the frame is already on disk, so stamping its WCS a few seconds later loses nothing for a downstream stacker. It is at method-body indentation (8 spaces), not inside the save branch, and is guarded on `local_save_path is not None` — which is only truthy when the local-save branch ran, so `ra`/`dec` (bound in that same branch) are guaranteed in scope:

```python
        # Opt-in (default OFF): AFTER the preview has published (so the solve's
        # 1-10 s never delays what the user sees), solve the saved light in place
        # and stamp its WCS so downstream stackers need no re-solve. Guarded on
        # local_save_path (a local save ran => ra/dec are bound). Best-effort — a
        # solve failure/timeout must never fail the capture (spec §6.3/§9).
        if local_save_path is not None and config_store.cfg().solve_saved_lights:
            try:
                from . import providers as _providers
                solver = _providers.pick_solver(self)
                fov_hint = self.effective_optics().get("fov_h_deg") or None
                res = await solver.solve(local_save_path, ra_hint=ra,
                                         dec_hint=dec, fov_deg_hint=fov_hint)
                if res.success and res.wcs is not None:
                    await asyncio.to_thread(write_wcs, local_save_path, res.wcs)
                    bus.log("info", f"stamped WCS on {local_save_path.name}", "solve")
            except Exception as e:  # noqa: BLE001 - never fail the capture
                bus.log("warning",
                        f"solve-saved-light failed ({e}); frame saved without WCS",
                        "solve")
        return info
```

Note the `return info` above is the existing final line — the block goes just before it (do not add a second `return`).

- [ ] **Step 6: Run test to verify it passes**

Run: `server/.venv/Scripts/pytest.exe "server/tests/test_hub_capture_precession.py::test_solve_saved_lights_stamps_wcs" -v -n0`
Expected: PASS.

- [ ] **Step 7: Full-file + focused regression sweep**

Run: `server/.venv/Scripts/pytest.exe server/tests/test_hub_capture_precession.py server/tests/test_fitsio.py server/tests/test_solver.py server/tests/test_coords_airmass.py server/tests/test_camera_egain.py server/tests/test_config.py -v`
Expected: all PASS.

- [ ] **Step 8: Privacy scan the diff**

Run: `git diff --staged --unified=0 | grep -nE "37\.348110|121\.801704|<REDACTED-SITE-LABEL>" || echo CLEAN`
Expected: `CLEAN`.

- [ ] **Step 9: Commit**

```bash
git add server/astrodeck/config.py server/astrodeck/hub.py server/tests/test_hub_capture_precession.py
git commit -m "feat(hub,config): opt-in solve_saved_lights stamps WCS on saved lights (PRO-2 F-B T7)"
```

**Implementation tier:** Sonnet (config flag + a guarded solve→write_wcs wiring that mirrors `solve_and_sync`). Review floor: Sonnet.

---

## Final verification (after Task 7)

- [ ] **Full suite green.** Run the whole backend suite once: `server/.venv/Scripts/pytest.exe` (from repo root; uses the default `-n 12`). Expected: no new failures vs. baseline.
- [ ] **Privacy scan the whole feature diff** (not just staged): `git diff main --unified=0 | grep -nE "37\.348110|121\.801704|<REDACTED-SITE-LABEL>" || echo CLEAN` → `CLEAN`.
- [ ] **No `build/lib` edits:** `git diff --name-only main | grep "build/lib" && echo "VIOLATION" || echo OK` → `OK`.
- [ ] **Tracker updated** in the program brief (`docs/superpowers/specs/2026-07-22-pro-novice-feature-program.md`): mark PRO-2 / F-B implemented.

---

## Self-Review

**1. Spec coverage** (each spec §/ruling → task):

| Spec item | Task |
|---|---|
| §1.3 / §7.1 / ruling — SWCREATE real version | T2 (`test_swcreate_is_real_version`) |
| §1.4 / §7.2 / ruling 1 — TELESCOP + `Optics.telescope_name`, no mount fallback | T1 (config) + T4 (hub wiring) |
| §5b — FOCALLEN | T2 write + T4 read |
| §5b / ruling 2 — XPIXSZ/YPIXSZ = pixel_size_um × binning | T2 (`test_all_new_cards`, XPIXSZ==3.76×2) |
| §5c — SITELAT/SITELONG/SITEELEV (omit on default site) | T2 write + T4 site guard |
| §5c — OBJCTALT + AIRMASS (omit ≤ horizon) | T3 (airmass) + T2 write + T4 alt>0 guard |
| §5c / ruling 6 — OBJCTRA/OBJCTDEC, RADESYS | T3 formatters + T2 write + T4 caller |
| §5c / ruling 3 — EQUINOX=2000.0 + JNow→J2000 conversion | T2 (EQUINOX/RADESYS when RA/Dec) + T4 (`from_mount_frame`, `test_capture_radec_written_as_j2000`) |
| §5d — SET-TEMP / FOCPOS / FOCTEMP / ROTATANG | T2 write + T4 guarded reads |
| §5d / ruling 5 — EGAIN + `CameraFrame.egain_e_per_adu` per-backend | T5 (field + engine/sim) + T2 write + T4 read |
| §5f / ruling 6 — HFR / STARCNT include; drop OBJCTAZ / PIERSIDE | T2 (HFR/STARCNT written; no OBJCTAZ/PIERSIDE card; `obj_az_deg` field dropped) |
| §6 / ruling 4 — WcsSolution + SolveResult.wcs + `_wcs_from_astap` + SimSolver WCS + write_wcs | T6 |
| §6.3 / ruling 4 — opt-in `solve_saved_lights` (default OFF) | T7 |
| §8 — omit-not-placeholder policy | T2 (`_finite`, `test_optional_cards_omitted_when_absent`, `test_airmass_omitted_below_horizon`) |
| §9 — header write never fails a capture; additive; no `build/lib` | T4 (guarded `_frame_meta`), T6 (`write_wcs` swallows), T7 (guarded solve); Global Constraints |
| §10 — test list | T2/T3/T4/T5/T6/T7 tests map to spec tests 1-10 |

All spec sections and all six §12 rulings map to a task. No gaps.

**2. Placeholder scan:** No "TBD/TODO/handle edge cases/similar to Task N". Every code step shows complete code; every test step shows real assertions; every run step shows the exact command + expected result. Clean.

**3. Type consistency across tasks:**
- `FrameMeta` fields defined in T2 are exactly the fields written by T2's `save_fits` and populated by T4's `_frame_meta` (`focal_length_mm`, `pixel_size_um`, `site_lat_deg`/`site_lon_deg`/`site_elev_m`, `obj_alt_deg`, `airmass`, `objctra`/`objctdec`, `set_temp_c`, `focuser_pos`, `focuser_temp_c`, `rotator_angle_deg`, `egain_e_per_adu`, `hfr`, `star_count`, `equinox`, `radesys`, `wcs`). Match confirmed.
- `WcsSolution` field names (`crval1/crval2/crpix1/crpix2/cd11..cd22/cdelt1/cdelt2/crota2/ctype1/ctype2/cunit/equinox/radesys`) are identical across T6's `base.py` definition, `_wcs_from_astap`, `_sim_wcs`, and `_apply_wcs`. Match confirmed.
- `save_fits(..., meta=...)` signature (T2) is called with `meta=`/`telescope=` in T4 and referenced by T6's `meta.wcs` block — consistent.
- `write_wcs(path, wcs)` (T6) is the exact call in T7. `pick_solver(hub)` / `SolveResult.wcs` (T6) consumed unchanged in T7.
- T4 reads `getattr(frame, "egain_e_per_adu", None)`; T5 adds that exact field name to `CameraFrame`. Consistent regardless of task order.

No inconsistencies found.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-22-fits-header-completeness.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task (T1-T7), review between tasks, fast iteration. Tiers: Sonnet for T1/T2/T3/T5/T7, Opus for T4 and T6.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
