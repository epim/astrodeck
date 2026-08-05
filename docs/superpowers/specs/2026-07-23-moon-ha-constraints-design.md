# PRO-14 — Enforced moon-separation / illumination / hour-angle constraints

Combined design spec + TDD implementation plan. Promote moon separation, moon
illumination, and hour-angle to first-class per-target `Schedule` constraints the
scheduler ENFORCES and skips/waits on — the same enforce-and-skip model already
used for the per-target altitude gate and the solar-exclusion cone.

---

## 1. Design

### 1.1 Goal

Give each target three new optional gates, all `0 = off`, all enforced inside the
pure gating path (`schedule.gating_status`), exactly like `min_altitude_deg`:

| Field | Meaning | When it applies | Gate result |
|---|---|---|---|
| `min_moon_sep_deg` | target must stay ≥ N° from the Moon | only while the Moon is **up** (alt > 0) | too close → `waiting` |
| `max_moon_illum_pct` | skip while Moon illuminated fraction > N % | only while the Moon is **up** | too bright → `waiting` (for moonset) |
| `max_hour_angle_h` | image only within ±N h of the meridian | always | east of window → `waiting`; past west limit → `window_closed` |

Today all of this is **advisory only**: the Moon math lives in the astropy-backed
`catalog/visibility.py` and feeds the visibility panel's scoring
(`_moon_factor`), never the scheduler. There are **no** moon or hour-angle fields
on `Schedule`. Scope = add the fields + a pure `constraint_gate` in the gating
path + the thin UI to edit them.

### 1.2 Current-state seams (all read, real file:line)

**The model to extend** — `server/astrodeck/sequence/models.py:23-39`. `Schedule`
has `min_altitude_deg` (line 34, "per-target START gate (target-alt). 0 = none")
and the time-window fields; **no** moon/HA fields. Every field defaults so
"existing saved plans deserialize unchanged" (line 24-25). `Target.schedule`
defaults via `Field(default_factory=Schedule)` (line 56) — additive fields with
defaults are automatically backward-compatible.

**The enforcement template** — `server/astrodeck/sequence/schedule.py:326-389`,
`gating_status`. It returns `{state, reason, eta_s, start_ts, stop_ts}` with
`state ∈ {ready, waiting, window_closed, never_rises}`. The altitude gate is the
model to copy:
- step 1 (367-368): stop passed → `window_closed`.
- step 2 (371-376): `target_max_altitude(...) < gate` → `never_rises` (pre-flight).
- step 3 (379-380): clock not open → `waiting`.
- step 4 (383-386): open but `target_alt < gate` → `waiting` (eta from `_time_to_gate`).
- step 5 (389): `ready`.
- `out()` helper (362-364) builds the dict, closing over `start_ts`/`stop_ts`.

**The HA math already exists** — `schedule.py:272-291`,
`hours_to_meridian_flip`, computes `ha = ((lst - ra_hours + 12.0) % 24.0) - 12.0`
(line 290) and returns `-ha`. Sign convention (docstring 286-288): **positive**
while EAST of meridian, **≤ 0** once crossed West. I will factor this exact
expression into a public `hour_angle_h` and have `hours_to_meridian_flip` call it
(one source of the HA truth). `lst_hours` comes from `coords` (import at
`schedule.py:30`), `target_altitude`/`altaz` at `schedule.py:296-300` +
`coords.py:63-76`.

**The angular-separation enforcement pattern** — `server/astrodeck/hub.py:1023-1051`,
`_check_solar`: computes Sun RA/Dec from the date (`sun_radec`, coords), takes
`sep = _ang_sep_deg(ra, dec, sun_ra, sun_dec)` (line 1046), raises when
`sep < cone`. The angular-sep helper is `hub.py:2404-2410`
(`_ang_sep_deg(ra1_h, dec1, ra2_h, dec2)`, spherical law of cosines). My moon
gate reuses the *same* formula, promoted into `coords.py` as `angular_sep_deg`.

**Where the Moon is computed today (advisory, astropy, NOT in the gating path)** —
`server/astrodeck/catalog/visibility.py`: `_moon_info` (233-279) returns
illumination/phase/alt/az/separation via astropy `get_body("moon")` (243);
`_moon_target_sep_deg` (55-67); `_moon_factor` (314-333) — and note line 323
`if not moon_up: return 1.0`, i.e. **moon altitude gates whether separation
matters at all** (design spec §9 / critique C2-#5). My `constraint_gate` copies
that "moon-up gates the constraint" rule. This module is heavy (astropy) and is
*not* imported by `schedule.py`; the pure scheduler path reuses only `coords`.

**Coords has Sun but NO Moon** — `server/astrodeck/catalog/coords.py`: `sun_radec`
(81-100, low-precision NOAA), `sun_altaz` (103-112), `lst_hours` (54-60), `altaz`
(63-76). There is **no** `moon_radec`. So the core new pure primitive is a
low-precision `moon_radec` added to `coords.py`, mirroring `sun_radec`, keeping
`schedule.py`'s "one source of sky-position truth" (module docstring 22-23)
astropy-free and fully pytest-able.

**How the engine consumes the gate (already generic)** —
`server/astrodeck/sequence/engine.py:731-846`, `_run_scheduled`. It only reads
`gs["state"]` (778): `ready` runs it (779-782); `window_closed`/`never_rises` →
`continue`, skipped and eventually `mark_skipped` (783-784, 812-818); `waiting`
→ becomes the "earliest waiter" (786-789) or, with no resolvable `start_ts`, the
`else` **bounded** re-eval sleep (842-845, `SCHEDULE_WAIT_STEP_S`). **No engine
change is required** — new states/reasons flow through unchanged.

**The UI to extend** — `ui/src/components/sequence/SchedulePanel.tsx:118-130`: the
`min alt` `Stepper` + `InfoDot` block is the exact pattern to clone three times
(`Stepper` at `../ui`, imported line 15). `disabled={disabled}` is the run-lock
convention (file header 5-9); my controls mirror it verbatim. Collapsed-chip
summary is `scheduleSummary` — `ui/src/lib/schedule.ts:33-44`. TS type —
`ui/src/types.ts:744-754`. Client default — `ui/src/store.ts:195-207`
(`defaultSchedule()`).

### 1.3 Approach

**Data shapes.** Three additive `Schedule` fields, `0 = off`, matching the
existing "0 = none/off/no cap" idiom (min_altitude_deg / max_run_min):

```python
min_moon_sep_deg:   float = Field(0.0, ge=0, le=180)   # ≥ this from the Moon (0 = off)
max_moon_illum_pct: float = Field(0.0, ge=0, le=100)   # skip while Moon > this % lit (0 = off)
max_hour_angle_h:   float = Field(0.0, ge=0, le=12)    # image within ±this h of meridian (0 = off)
```

**Behavior (the enforce/skip decision — the pure tested core).** A single pure
function `constraint_gate(target, site, now) -> tuple[str,str,float] | None`:
returns `None` when all three constraints are satisfied (or off), else
`(state, reason, eta_s)`:

- **Hour angle** (evaluated first — it's altitude-independent and predictable):
  `ha = hour_angle_h(ra, lon, now)` in `(-12, 12]`. If `ha > +lim` → target is
  West of the window and HA only increases tonight → `("window_closed", "past
  hour-angle limit (+{lim}h)", 0)`. If `ha < -lim` → not yet risen into the
  window → `("waiting", "before hour-angle window (-{lim}h)", eta)` with
  `eta = (-lim - ha) * 3600` (HA advances ~1 h per solar hour; coarse eta is fine,
  matching `_time_to_gate`'s precision).
- **Moon** (only when the Moon is up — `moon_altaz(lat,lon,now)[0] > 0`, mirroring
  `_moon_factor`'s `moon_up` rule, visibility.py:323): illumination first
  (`moon_illumination(now)*100 > max_moon_illum_pct` → `("waiting", "moon too
  bright (…%)", 0)`), then separation
  (`angular_sep_deg(ra,dec, *moon_radec(now)) < min_moon_sep_deg` →
  `("waiting", "too close to the Moon (…°)", 0)`). Moon down ⇒ both satisfied.

`gating_status` calls it as **step 4b**, after the altitude gate (step 4) and
before `ready` (step 5), so the most-fundamental reason is reported first:

```python
    # 4b. pro constraints (moon sep / illumination / hour angle) — same
    #     enforce-and-skip model as the altitude gate above (PRO-14).
    cg = constraint_gate(target, site, now)
    if cg is not None:
        state, reason, eta = cg
        # a constraint-"waiting" target has an OPEN clock window (start_ts is in
        # the past); null the start anchor so the engine's earliest-waiter branch
        # (engine.py:786-789) skips it and it rides the bounded else-sleep
        # (842-845) instead of busy-spinning on a past start_ts. window_closed
        # keeps its window verbatim (it's being skipped anyway).
        start = None if state == "waiting" else start_ts
        return {"state": state, "reason": reason, "eta_s": max(0.0, eta),
                "start_ts": start, "stop_ts": stop_ts}
```

**Moon ephemeris (the one genuinely subtle numeric bit).** Add to `coords.py`, all
pure/stdlib-`math`, no astropy:
- `moon_radec(unix_time=None) -> (ra_hours, dec_deg)` — low-precision truncated
  lunar theory (Schlyter elements + the dozen main longitude/latitude
  perturbation terms), good to ≲0.5°, plenty for a degrees-wide separation gate
  (same rationale as `_check_solar`'s "parallax negligible vs a degrees-wide
  cone", hub.py:1028-1029).
- `moon_altaz(lat, lon, unix_time=None) -> (alt, az)` — `altaz(*moon_radec(t), …)`.
- `moon_illumination(unix_time=None) -> float` in `[0,1]` — from the geocentric
  Sun–Moon elongation: `k = (1 - cos(elong)) / 2` (elong via `angular_sep_deg`
  of `sun_radec` vs `moon_radec`; new=0, full=1 — the same elongation model as
  visibility.py:245-252).
- `angular_sep_deg(ra1_h, dec1, ra2_h, dec2) -> float` — promote `_ang_sep_deg`'s
  formula (hub.py:2404-2410) into coords as the shared, public separation helper.

**UI.** Three `Stepper`+`InfoDot` rows cloned from the `min alt` block
(SchedulePanel.tsx:118-130); extend `scheduleSummary`; add the three fields to the
`Schedule` TS interface + `defaultSchedule()`.

### 1.4 Placement

- Moon/HA math → `coords.py` (one sky-position source; astropy-free; pytest).
- Fields → `models.py` `Schedule`.
- Gate + HA helper → `schedule.py` (pure; pytest). `hours_to_meridian_flip`
  refactored to reuse `hour_angle_h`.
- No engine change (states/reasons already generic).
- UI → `types.ts`, `store.ts`, `schedule.ts`, `SchedulePanel.tsx`.

### 1.5 What we deliberately do NOT do (trust-the-code scoping)

- **No** `hub._check_moon` at the slew/motion boundary. The solar cone is
  defense-in-depth at motion (hub.py:1905, engine.py:905-920); the brief scopes
  PRO-14 to "the gating/scheduler path". A motion-boundary moon guard is a
  possible follow-up (Open Decision D3).
- **No** moon pre-flight "never clears" scan analogous to `never_rises` (step 2).
  Moon-sep uses point-in-time `waiting` like the below-altitude gate (step 4);
  the existing stop-boundary / dawn-cutoff machinery bounds a perpetually-blocked
  target. See Open Decision D2.

---

## 2. Global Constraints (verbatim — apply to EVERY task)

- **Privacy.** The real site coordinates `<REDACTED-LAT>` / `<REDACTED-LON>` and the label
  `"<REDACTED-SITE-LABEL>"` must **NEVER** appear in code, tests, or docs. The site default
  is `"My Observatory"` / `0.0`. Tests use deterministic non-default sites (e.g.
  `MID = {latitude: 40.0, longitude: -74.0, …}` already in
  `server/tests/test_schedule.py:19-21`).
- **Never `git add -A`.** Stage only the specific files each task touches.
- **UI gate:** `cd ui && npx tsc -b` must pass (zero errors) before a UI task is done.
- **NO jsdom.** Pure logic is tested via `npx tsx` inline-assert (the idiom in
  `ui/src/lib/__tests__/eta.test.ts` / `schedule.test.ts`). No React test runner.
- **Backend tests:** `server/.venv/Scripts/pytest.exe` run from the **repo root**,
  single-process `-n0`.
- **Client toasts** (if any surfaced): `useStore.getState().enqueueToast` — not
  needed here (no new toasts).
- **Honest-disabled (§11.8):** dim + lock + `aria-disabled` + `title`, never native
  `disabled`, for *capability-gated* controls. These schedule controls are **not**
  capability-gated — they follow the existing panel's run-lock `disabled={disabled}`
  convention verbatim (SchedulePanel.tsx header 5-9); no new honest-disabled
  surface is introduced.
- **Do not disrupt astrotown** (no deploys, no touching the live box).

---

## 3. TDD Implementation Plan

Interfaces block (exact signatures) precedes the tasks; each task is red→green
with actual code and exact commands + expected output.

### 3.0 Interfaces (exact signatures)

```python
# server/astrodeck/catalog/coords.py  (NEW)
def angular_sep_deg(ra1_h: float, dec1: float, ra2_h: float, dec2: float) -> float: ...
def moon_radec(unix_time: float | None = None) -> tuple[float, float]: ...          # (ra_hours, dec_deg)
def moon_altaz(lat_deg: float, lon_deg: float,
               unix_time: float | None = None) -> tuple[float, float]: ...           # (alt_deg, az_deg)
def moon_illumination(unix_time: float | None = None) -> float: ...                  # 0..1

# server/astrodeck/sequence/schedule.py  (NEW)
def hour_angle_h(ra_hours: float, lon_deg: float, now: float | None = None) -> float: ...  # (-12, 12]
def constraint_gate(target: "Target", site: dict[str, Any],
                    now: float) -> tuple[str, str, float] | None: ...                # (state, reason, eta_s) | None
# hours_to_meridian_flip(...)  -> refactor body to `return -hour_angle_h(ra_hours, lon_deg, now)`
# gating_status(...)           -> insert the step-4b constraint block (unchanged signature)
```

```typescript
// ui/src/types.ts  — Schedule interface gains:
min_moon_sep_deg: number;    // ≥ this from the Moon while it's up (0 = off)
max_moon_illum_pct: number;  // skip while Moon > this % illuminated (0 = off)
max_hour_angle_h: number;    // image within ±this h of the meridian (0 = off)
```

---

### Task 1 — `coords.py`: Moon ephemeris + shared angular separation
**Impl tier: Opus.** Justification: genuinely subtle numeric — a truncated lunar
theory with sign/units traps; a wrong term silently shifts the Moon by degrees
and flips separation decisions. Everything downstream is mechanical.

**Files:** `server/astrodeck/catalog/coords.py` (add funcs);
`server/tests/test_coords_moon.py` (new).

**Step 1 (red): write the cross-validation test against astropy.** astropy is
already a dep (`catalog/visibility.py` imports it; `server/tests/test_visibility.py`
runs). Use it as the oracle for the low-precision Moon.

```python
# server/tests/test_coords_moon.py
"""Low-precision Moon ephemeris (coords.py) cross-checked against astropy.

The gating path (schedule.py) is astropy-free, so coords grows a hand-rolled
low-precision Moon. These tests pin it to astropy's get_body('moon') within the
tolerance a degrees-wide separation/illumination gate needs (≲1.5°)."""
from __future__ import annotations

import time
import numpy as np
import astropy.units as u
from astropy.coordinates import EarthLocation, get_body, get_sun, SkyCoord
from astropy.time import Time

from astrodeck.catalog import coords

# Non-default deterministic site (never the real backyard).
LAT, LON = 40.0, -74.0
# A spread of instants across a lunation so we hit varied phase/position.
_TIMES = [time.mktime((2024, m, d, 3, 0, 0, 0, 0, 0)) - time.timezone
          for (m, d) in [(1, 5), (3, 21), (6, 20), (9, 10), (11, 30)]]


def _astropy_moon_radec(t: float) -> tuple[float, float]:
    body = get_body("moon", Time(t, format="unix")).transform_to("icrs")
    return body.ra.to_value(u.hourangle), body.dec.to_value(u.deg)


def test_moon_radec_matches_astropy_within_1p5deg():
    for t in _TIMES:
        mra, mdec = coords.moon_radec(t)
        ara, adec = _astropy_moon_radec(t)
        sep = coords.angular_sep_deg(mra, mdec, ara, adec)
        assert sep < 1.5, f"moon off by {sep:.2f} deg at t={t}"


def test_moon_illumination_matches_astropy_within_0p03():
    for t in _TIMES:
        tm = Time(t, format="unix")
        moon = get_body("moon", tm)
        sun = get_sun(tm)
        elong = moon.separation(sun).to_value(u.rad)
        k_astro = (1 - np.cos(elong)) / 2
        k = coords.moon_illumination(t)
        assert abs(k - k_astro) < 0.03, f"illum {k:.3f} vs {k_astro:.3f} at t={t}"


def test_angular_sep_deg_basic():
    # 90° apart on the equator (6h of RA); antipodal dec.
    assert abs(coords.angular_sep_deg(0.0, 0.0, 6.0, 0.0) - 90.0) < 1e-6
    assert abs(coords.angular_sep_deg(0.0, -30.0, 0.0, 30.0) - 60.0) < 1e-6


def test_moon_altaz_agrees_with_altaz_of_radec():
    t = _TIMES[0]
    mra, mdec = coords.moon_radec(t)
    assert coords.moon_altaz(LAT, LON, t) == coords.altaz(mra, mdec, LAT, LON, t)
```

Run (red — funcs don't exist yet):
```
server/.venv/Scripts/pytest.exe server/tests/test_coords_moon.py -n0 -q
```
Expected: `ImportError`/`AttributeError` on `coords.moon_radec`.

**Step 2 (green): implement in `coords.py`** (append after `sun_altaz`, ~line 113):

```python
def angular_sep_deg(ra1_h: float, dec1: float, ra2_h: float, dec2: float) -> float:
    """Angular separation (deg) between two (RA hours, Dec deg) points — the
    shared spherical-law-of-cosines helper (promoted from hub._ang_sep_deg)."""
    ra1, ra2 = math.radians(ra1_h * 15.0), math.radians(ra2_h * 15.0)
    d1, d2 = math.radians(dec1), math.radians(dec2)
    cos_sep = (math.sin(d1) * math.sin(d2)
               + math.cos(d1) * math.cos(d2) * math.cos(ra1 - ra2))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


def moon_radec(unix_time: float | None = None) -> tuple[float, float]:
    """Apparent (RA hours, Dec degrees) of the Moon — low precision (≲0.5°).

    Truncated lunar theory (Schlyter orbital elements + the main longitude /
    latitude perturbation terms). Sufficient for a degrees-wide moon-separation
    gate; the pure scheduler path stays astropy-free (one sky source = coords)."""
    t = unix_time if unix_time is not None else time.time()
    d = t / 86400.0 + 2440587.5 - 2451543.5      # days since 2000 Jan 0.0 (Schlyter epoch)

    def rad(x: float) -> float:
        return math.radians(x % 360.0)

    # Moon orbital elements (degrees).
    N = 125.1228 - 0.0529538083 * d              # long. ascending node
    i = 5.1454                                    # inclination
    w = 318.0634 + 0.1643573223 * d              # arg. of perigee
    a = 60.2666                                   # mean distance (Earth radii)
    e = 0.054900                                  # eccentricity
    M = 115.3654 + 13.0649929509 * d             # mean anomaly
    # Sun elements needed for perturbations.
    Ms = 356.0470 + 0.9856002585 * d
    ws = 282.9404 + 4.70935e-5 * d
    Ls = (ws + Ms) % 360.0                        # Sun mean longitude
    Lm = (N + w + M) % 360.0                       # Moon mean longitude
    D = Lm - Ls                                    # mean elongation
    F = Lm - N                                      # argument of latitude

    # Eccentric anomaly (two iterations — ample at this precision).
    Mr = rad(M)
    E = Mr + e * math.sin(Mr) * (1.0 + e * math.cos(Mr))
    for _ in range(2):
        E = E - (E - e * math.sin(E) - Mr) / (1.0 - e * math.cos(E))

    # Position in the orbital plane, then true anomaly + radius (Earth radii).
    xv = a * (math.cos(E) - e)
    yv = a * (math.sqrt(1.0 - e * e) * math.sin(E))
    v = math.atan2(yv, xv)
    r = math.hypot(xv, yv)

    # Geocentric ecliptic rectangular coords.
    vN, vi, vw = rad(N), math.radians(i), math.radians(w)
    xh = r * (math.cos(vN) * math.cos(v + vw) - math.sin(vN) * math.sin(v + vw) * math.cos(vi))
    yh = r * (math.sin(vN) * math.cos(v + vw) + math.cos(vN) * math.sin(v + vw) * math.cos(vi))
    zh = r * (math.sin(v + vw) * math.sin(vi))

    lon = math.degrees(math.atan2(yh, xh))
    lat = math.degrees(math.atan2(zh, math.hypot(xh, yh)))

    # Main perturbations (degrees) — Schlyter.
    Dr, Mr2, Msr, Fr = rad(D), rad(M), rad(Ms), rad(F)
    lon += (-1.274 * math.sin(Mr2 - 2 * Dr) + 0.658 * math.sin(2 * Dr)
            - 0.186 * math.sin(Msr) - 0.059 * math.sin(2 * Mr2 - 2 * Dr)
            - 0.057 * math.sin(Mr2 - 2 * Dr + Msr) + 0.053 * math.sin(Mr2 + 2 * Dr)
            + 0.046 * math.sin(2 * Dr - Msr) + 0.041 * math.sin(Mr2 - Msr)
            - 0.035 * math.sin(Dr) - 0.031 * math.sin(Mr2 + Msr)
            - 0.015 * math.sin(2 * Fr - 2 * Dr) + 0.011 * math.sin(Mr2 - 4 * Dr))
    lat += (-0.173 * math.sin(Fr - 2 * Dr) - 0.055 * math.sin(Mr2 - Fr - 2 * Dr)
            - 0.046 * math.sin(Mr2 + Fr - 2 * Dr) + 0.033 * math.sin(Fr + 2 * Dr)
            + 0.017 * math.sin(2 * Mr2 + Fr))

    # Ecliptic -> equatorial.
    lam, bet = math.radians(lon), math.radians(lat)
    eps = math.radians(23.4393 - 3.563e-7 * d)
    xe = math.cos(lam) * math.cos(bet)
    ye = math.sin(lam) * math.cos(bet) * math.cos(eps) - math.sin(bet) * math.sin(eps)
    ze = math.sin(lam) * math.cos(bet) * math.sin(eps) + math.sin(bet) * math.cos(eps)
    ra = math.atan2(ye, xe)
    dec = math.atan2(ze, math.hypot(xe, ye))
    return (math.degrees(ra) % 360.0) / 15.0, math.degrees(dec)


def moon_altaz(lat_deg: float, lon_deg: float,
               unix_time: float | None = None) -> tuple[float, float]:
    """The Moon's (altitude_deg, azimuth_deg) for a site."""
    ra, dec = moon_radec(unix_time)
    return altaz(ra, dec, lat_deg, lon_deg, unix_time)


def moon_illumination(unix_time: float | None = None) -> float:
    """Illuminated fraction of the Moon's disk, 0..1, from the geocentric
    Sun–Moon elongation: k = (1 - cos elong)/2 (new=0, full=1)."""
    sra, sdec = sun_radec(unix_time)
    mra, mdec = moon_radec(unix_time)
    elong = math.radians(angular_sep_deg(sra, sdec, mra, mdec))
    return (1.0 - math.cos(elong)) / 2.0
```

Run (green):
```
server/.venv/Scripts/pytest.exe server/tests/test_coords_moon.py -n0 -q
```
Expected: `5 passed`. (If the `< 1.5` sep test is marginal at one instant, the
theory is correct but sanity-check the epoch constant `2451543.5` and that `d`
uses the Schlyter epoch, not J2000.)

---

### Task 2 — `Schedule` model fields
**Impl tier: Sonnet** (mechanical field additions).

**Files:** `server/astrodeck/sequence/models.py`;
`server/tests/test_moon_ha_fields.py` (new).

**Step 1 (red):**
```python
# server/tests/test_moon_ha_fields.py
from __future__ import annotations
import pytest
from pydantic import ValidationError
from astrodeck.sequence.models import Schedule, Target, SequencePlan


def test_defaults_are_off():
    s = Schedule()
    assert s.min_moon_sep_deg == 0.0
    assert s.max_moon_illum_pct == 0.0
    assert s.max_hour_angle_h == 0.0


def test_legacy_plan_without_fields_deserializes():
    # a saved plan predating PRO-14 has no moon/HA keys — must default, not 422.
    t = Target(name="M31", ra_hours=0.71, dec_deg=41.27,
               schedule={"start_mode": "now", "min_altitude_deg": 30})
    assert t.schedule.min_moon_sep_deg == 0.0
    assert t.schedule.max_hour_angle_h == 0.0


@pytest.mark.parametrize("field,bad", [
    ("min_moon_sep_deg", 200.0), ("min_moon_sep_deg", -1.0),
    ("max_moon_illum_pct", 101.0), ("max_moon_illum_pct", -1.0),
    ("max_hour_angle_h", 13.0), ("max_hour_angle_h", -0.5),
])
def test_out_of_range_rejected(field, bad):
    with pytest.raises(ValidationError):
        Schedule(**{field: bad})
```
Run: `server/.venv/Scripts/pytest.exe server/tests/test_moon_ha_fields.py -n0 -q`
→ red (fields don't exist; defaults test AttributeError, range test passes
vacuously — expect the default/legacy tests to fail).

**Step 2 (green):** add to `Schedule` (models.py, after line 34 `min_altitude_deg`,
keeping the docstring note style). Import `Field` is already present (line 7):
```python
    # --- pro visibility constraints (PRO-14; additive, 0 = off => back-compat) ---
    min_moon_sep_deg: float = Field(0.0, ge=0, le=180)    # ≥ this from the Moon while up
    max_moon_illum_pct: float = Field(0.0, ge=0, le=100)  # skip while Moon > this % lit
    max_hour_angle_h: float = Field(0.0, ge=0, le=12)     # image within ±this h of meridian
```
Run again → `passed` (defaults + legacy + all 6 parametrized rejections).

---

### Task 3 — `schedule.py`: `hour_angle_h` + `constraint_gate` + gating integration
**Impl tier: Opus.** Justification: correctness-sensitive — the HA sign
convention (`ha > lim` = West/closed vs `ha < -lim` = East/waiting) and the
moon-up gating + step ordering directly decide which targets get skipped; a sign
flip silently skips the wrong ones. Numeric, not mechanical.

**Files:** `server/astrodeck/sequence/schedule.py`;
`server/tests/test_constraint_gate.py` (new).

**Step 1 (red):**
```python
# server/tests/test_constraint_gate.py
from __future__ import annotations
import time
from astrodeck.sequence import schedule as sch
from astrodeck.sequence.models import Target
from astrodeck.catalog import coords, lst_hours  # noqa: F401 (lst_hours via coords)
from astrodeck.catalog.coords import lst_hours as _lst

SITE = {"name": "Mid", "latitude": 40.0, "longitude": -74.0,
        "elevation_m": 0.0, "is_default": False}
NOW = time.time()


def _target(ra, dec, **sched):
    return Target(name="T", ra_hours=ra, dec_deg=dec, schedule=sched)


def _ra_at_ha(ha_h: float, now: float) -> float:
    """RA (hours) that yields hour angle `ha_h` at `now` for SITE."""
    lst = _lst(SITE["longitude"], now)
    return (lst - ha_h) % 24.0


def test_hour_angle_sign_matches_meridian_flip():
    ra = 5.0
    ha = sch.hour_angle_h(ra, SITE["longitude"], NOW)
    assert abs(sch.hours_to_meridian_flip(ra, SITE["longitude"], NOW) - (-ha)) < 1e-9


def test_ha_within_window_passes():
    ra = _ra_at_ha(0.0, NOW)            # on the meridian
    assert sch.constraint_gate(_target(ra, 40.0, max_hour_angle_h=3.0), SITE, NOW) is None


def test_ha_east_waits():
    ra = _ra_at_ha(-5.0, NOW)           # 5h east of meridian, limit 3h
    g = sch.constraint_gate(_target(ra, 40.0, max_hour_angle_h=3.0), SITE, NOW)
    assert g is not None and g[0] == "waiting" and g[2] > 0


def test_ha_west_closes():
    ra = _ra_at_ha(5.0, NOW)            # 5h west, past the +3h limit
    g = sch.constraint_gate(_target(ra, 40.0, max_hour_angle_h=3.0), SITE, NOW)
    assert g is not None and g[0] == "window_closed"


def test_all_off_returns_none():
    assert sch.constraint_gate(_target(5.0, 40.0), SITE, NOW) is None


def test_moon_sep_blocks_when_close_and_moon_up():
    # place the target AT the Moon; force a moment the Moon is up.
    t = NOW
    for _ in range(48):                 # scan a day for moon-up at SITE
        if coords.moon_altaz(SITE["latitude"], SITE["longitude"], t)[0] > 10:
            break
        t += 3600.0
    mra, mdec = coords.moon_radec(t)
    g = sch.constraint_gate(_target(mra, mdec, min_moon_sep_deg=30.0), SITE, t)
    assert g is not None and g[0] == "waiting" and "Moon" in g[1]


def test_moon_sep_satisfied_when_moon_down():
    t = NOW
    for _ in range(48):                 # scan for moon BELOW horizon
        if coords.moon_altaz(SITE["latitude"], SITE["longitude"], t)[0] < -5:
            break
        t += 3600.0
    mra, mdec = coords.moon_radec(t)     # even AT the moon, it's down => OK
    assert sch.constraint_gate(_target(mra, mdec, min_moon_sep_deg=30.0), SITE, t) is None


def test_gating_status_ready_when_constraints_off():
    # a default-schedule target is still 'ready' (back-compat through step 4b).
    ra = _ra_at_ha(0.0, NOW)
    gs = sch.gating_status(_target(ra, 80.0), SITE, -12.0, NOW, target_alt=80.0)
    assert gs["state"] == "ready"
```
Run: `server/.venv/Scripts/pytest.exe server/tests/test_constraint_gate.py -n0 -q`
→ red (`hour_angle_h`/`constraint_gate` missing).

**Step 2 (green): `schedule.py` edits.**

(a) Import the moon helpers (extend the import at line 30):
```python
from ..catalog.coords import (altaz, angular_sep_deg, lst_hours, moon_altaz,
                              moon_illumination, moon_radec, sun_altaz)
```

(b) Add `hour_angle_h` and refactor `hours_to_meridian_flip` (replace body at
272-291):
```python
def hour_angle_h(ra_hours: float, lon_deg: float, now: float | None = None) -> float:
    """Signed hour angle HA = LST - RA, wrapped to (-12, 12].

    Negative => target is EAST of the meridian (rising toward transit); positive
    => WEST (past transit). Single source of the HA truth shared by the meridian
    countdown and the PRO-14 hour-angle gate."""
    lst = lst_hours(lon_deg, now)
    return ((lst - ra_hours + 12.0) % 24.0) - 12.0


def hours_to_meridian_flip(ra_hours: float, lon_deg: float,
                           now: float | None = None) -> float:
    """... (keep existing docstring) ..."""
    return -hour_angle_h(ra_hours, lon_deg, now)
```

(c) Add `constraint_gate` (after `hours_to_meridian_flip`):
```python
def constraint_gate(target: "Target", site: dict[str, Any],
                    now: float) -> tuple[str, str, float] | None:
    """Evaluate the PRO-14 pro constraints (hour angle / moon sep / moon illum) at
    ``now``. Returns ``None`` when all are satisfied or off, else
    ``(state, reason, eta_s)`` where state is ``waiting`` or ``window_closed`` —
    the same enforce-and-skip vocabulary as the altitude gate."""
    sched = target.schedule
    lat, lon = _lat_lon(site)

    # --- hour angle (altitude-independent; predictable) ---
    lim = float(getattr(sched, "max_hour_angle_h", 0.0) or 0.0)
    if lim > 0.0:
        ha = hour_angle_h(target.ra_hours, lon, now)
        if ha > lim:
            return ("window_closed", f"past hour-angle limit (+{lim:g}h)", 0.0)
        if ha < -lim:
            return ("waiting", f"before hour-angle window (-{lim:g}h)",
                    (-lim - ha) * 3600.0)

    # --- moon (only while the Moon is up — moon-up gates the constraint) ---
    sep_min = float(getattr(sched, "min_moon_sep_deg", 0.0) or 0.0)
    illum_max = float(getattr(sched, "max_moon_illum_pct", 0.0) or 0.0)
    if sep_min > 0.0 or illum_max > 0.0:
        m_ra, m_dec = moon_radec(now)
        m_alt, _ = altaz(m_ra, m_dec, lat, lon, now)
        if m_alt > 0.0:
            if illum_max > 0.0:
                pct = moon_illumination(now) * 100.0
                if pct > illum_max:
                    return ("waiting",
                            f"moon too bright ({pct:.0f}% > {illum_max:g}%)", 0.0)
            if sep_min > 0.0:
                sep = angular_sep_deg(target.ra_hours, target.dec_deg, m_ra, m_dec)
                if sep < sep_min:
                    return ("waiting",
                            f"too close to the Moon ({sep:.0f} deg < {sep_min:g})",
                            0.0)
    return None
```
(Note: uses `altaz(m_ra, m_dec, …)` directly with the already-computed
`moon_radec` to avoid a second ephemeris eval; `moon_altaz` is retained for
callers/tests.)

(d) Insert step 4b in `gating_status`, between the altitude block (ends line 386)
and the final `return out("ready", "", 0.0)` (389):
```python
    # 4b. pro constraints (moon sep / illumination / hour angle) — PRO-14, same
    #     enforce-and-skip model as the altitude gate above.
    cg = constraint_gate(target, site, now)
    if cg is not None:
        state, reason, eta = cg
        start = None if state == "waiting" else start_ts
        return {"state": state, "reason": reason, "eta_s": max(0.0, eta),
                "start_ts": start, "stop_ts": stop_ts}
```

Run (green):
```
server/.venv/Scripts/pytest.exe server/tests/test_constraint_gate.py server/tests/test_schedule.py -n0 -q
```
Expected: all green (new file passes; `test_schedule.py` — the existing 4-state
suite — still passes, proving no regression to `ready`/`waiting`/`window_closed`/
`never_rises`).

**Full backend regression** (before closing Task 3):
```
server/.venv/Scripts/pytest.exe server/tests -n0 -q
```
Expected: prior green count + the new tests, 0 failed.

---

### Task 4 — UI type, default, and summary chip
**Impl tier: Sonnet.**

**Files:** `ui/src/types.ts`; `ui/src/store.ts`; `ui/src/lib/schedule.ts`;
`ui/src/lib/__tests__/schedule.test.ts`; **plus** any inline `Schedule` literals
the typechecker flags (the new required fields force them).

**Step 1:** add the three fields to the `Schedule` interface (types.ts, after
line 748):
```typescript
  min_moon_sep_deg: number;    // ≥ this from the Moon while it's up (0 = off)
  max_moon_illum_pct: number;  // skip while Moon > this % illuminated (0 = off)
  max_hour_angle_h: number;    // image within ±this h of the meridian (0 = off)
```
and to `defaultSchedule()` (store.ts, inside the return, after `min_altitude_deg`):
```typescript
    min_moon_sep_deg: 0,
    max_moon_illum_pct: 0,
    max_hour_angle_h: 0,
```

**Step 2:** extend `scheduleSummary` (schedule.ts). Append moon/HA fragments to
the `begin` clause so the chip stays readable:
```typescript
export function scheduleSummary(s: Schedule | undefined): string {
  if (!s) return "Runs immediately";
  const start = startLabel(s);
  const gate = s.min_altitude_deg > 0 ? `Alt ≥ ${Math.round(s.min_altitude_deg)}°` : "";
  const ha = s.max_hour_angle_h > 0 ? `HA ±${s.max_hour_angle_h}h` : "";
  const sep = s.min_moon_sep_deg > 0 ? `Moon ≥ ${Math.round(s.min_moon_sep_deg)}°` : "";
  const illum = s.max_moon_illum_pct > 0 ? `Moon ≤ ${Math.round(s.max_moon_illum_pct)}%` : "";
  const begin = [start, gate, ha, sep, illum].filter(Boolean).join(" & ");
  const stop = stopLabel(s);
  const missed = s.on_missed === "skip" ? "skip if missed" : "";
  if (!begin && !stop && !missed) return "Runs immediately";
  const head = begin || "Now";
  const tail = [stop, missed].filter(Boolean).join(" · ");
  return tail ? `${head} → ${tail}` : head;
}
```

**Step 3 (tsx test):** add cases to `schedule.test.ts` (the existing inline
literals there must also gain the three fields = 0, or the file won't compile —
that is itself the back-compat proof). Add e.g.:
```typescript
test("hour-angle + moon constraints in chip", () => {
  const s = scheduleSummary({ start_mode: "now", start_offset_min: 0, start_time: null,
    min_altitude_deg: 0, stop_mode: "none", stop_offset_min: 0, stop_time: null,
    max_run_min: 0, on_missed: "wait",
    min_moon_sep_deg: 40, max_moon_illum_pct: 0, max_hour_angle_h: 3 });
  assert(s === "HA ±3h & Moon ≥ 40°", `got "${s}"`);
});
```

**Run (tsx):**
```
cd ui && npx tsx src/lib/__tests__/schedule.test.ts
```
Expected: `schedule.test: N/N passed` (N = old count + 1).

**Typecheck gate (finds every stale Schedule literal):**
```
cd ui && npx tsc -b
```
Expected: exit 0. Any error points at a literal missing the three fields — add
`min_moon_sep_deg: 0, max_moon_illum_pct: 0, max_hour_angle_h: 0` to it. Likely
touched: `schedule.test.ts` literals, and grep `min_altitude_deg` across
`ui/src` for others (`git grep -n min_altitude_deg -- ui/src`).

---

### Task 5 — `SchedulePanel.tsx` controls
**Impl tier: Sonnet** (thin render; verified by typechecker, mirrors the existing
min-alt block).

**Files:** `ui/src/components/sequence/SchedulePanel.tsx`.

**Step 1:** after the min-altitude block (ends line 130), add three rows cloned
from it — `Stepper` + `InfoDot`, `disabled={disabled}` verbatim:
```tsx
          {/* -------------------------------------------- hour-angle window */}
          <div className="flex items-center gap-2 flex-wrap">
            <Stepper
              label="max HA"
              value={s.max_hour_angle_h}
              onChange={(v) => onChange({ max_hour_angle_h: v })}
              min={0} max={12} step={0.5} unit="h" disabled={disabled}
              format={(v) => (v === 0 ? "no limit" : `±${v}h`)}
            />
            <InfoDot
              label="About the hour-angle window"
              content="Image only within ±this many hours of the meridian (0 = no limit). Before the target rises into the window it waits; once past the west limit it's skipped for the night."
            />
          </div>

          {/* ---------------------------------------- moon separation gate */}
          <div className="flex items-center gap-2 flex-wrap">
            <Stepper
              label="moon sep"
              value={s.min_moon_sep_deg}
              onChange={(v) => onChange({ min_moon_sep_deg: v })}
              min={0} max={180} step={5} unit="°" disabled={disabled}
              format={(v) => (v === 0 ? "off" : `≥ ${v}°`)}
            />
            <InfoDot
              label="About the moon-separation gate"
              content="This target waits while it is closer than this to the Moon AND the Moon is above the horizon (0 = off). A Moon below the horizon imposes no gate."
            />
          </div>

          {/* --------------------------------------- moon illumination gate */}
          <div className="flex items-center gap-2 flex-wrap">
            <Stepper
              label="moon max"
              value={s.max_moon_illum_pct}
              onChange={(v) => onChange({ max_moon_illum_pct: v })}
              min={0} max={100} step={5} unit="%" disabled={disabled}
              format={(v) => (v === 0 ? "off" : `≤ ${v}%`)}
            />
            <InfoDot
              label="About the moon-brightness gate"
              content="This target waits while the Moon is up AND more than this percent illuminated (0 = off) — it resumes once the Moon sets or after this bright Moon's night."
            />
          </div>
```
(Confirm `Stepper` accepts `format` — it's already used for `max run` at
SchedulePanel.tsx:167 and `on_missed`; `unit` used at 124/166. Both props exist.)

**Run:**
```
cd ui && npx tsc -b
```
Expected: exit 0.

---

## 4. Open decisions (each with a recommendation)

**D1 — Hour-angle window: symmetric ±h vs asymmetric east/west.**
Recommend **symmetric `max_hour_angle_h` (±h)** now — one field, covers the
dominant "shoot near transit for low airmass / away from the pier limit" use, and
matches the existing single-number field idiom. An asymmetric `ha_east_h` /
`ha_west_h` pair (e.g. stop earlier on the pier-flip side of a GEM) is a clean
additive follow-up if asked.

**D2 — Moon-sep as point-in-time `waiting` vs a `never_rises`-style pre-flight.**
Recommend **point-in-time `waiting`** (matches the below-altitude gate, step 4)
and rely on the existing stop-boundary / dawn-cutoff machinery to bound a
perpetually-blocked target — with the `start_ts = None` tweak so it rides the
engine's bounded else-sleep (842-845) rather than busy-spinning on a past
`start_ts`. Adding a moon pre-flight "never clears tonight" scan (a
`target_max_altitude`-style loop over the window) is a later refinement; it costs
a per-tick window scan and isn't needed for correctness.

**D3 — Also enforce moon separation at the engine motion boundary
(`hub._check_moon`, like `_check_solar`)?**
Recommend **no, out of scope** for PRO-14 (brief scopes to the gating/scheduler
path). Defense-in-depth at the slew boundary is reasonable later, but unlike the
Sun the Moon is not a safety hazard — it's an image-quality constraint — so the
scheduler gate is the right and sufficient home.

**D4 — Illumination gate keyed on "moon up" (chosen) vs night-integrated.**
Recommend the **moon-up point-in-time** rule (mirrors `_moon_factor`'s `moon_up`
gate, visibility.py:323): a 95%-lit Moon that has set imposes no penalty, so the
target resumes at moonset — more targets get imaged than a blunt night-level
skip. Documented in the InfoDot copy.

**D5 — Moon precision (low-precision `moon_radec` in coords vs importing astropy
into the gating path).** Recommend the **hand-rolled low-precision Moon** (≲0.5°,
astropy-free) to keep `schedule.py`'s "one sky source = coords" invariant and the
gate fast + trivially unit-testable; astropy remains the *test oracle*, not a
runtime dependency of the scheduler. A degrees-wide separation gate does not need
sub-arcminute Moon positions (same argument `_check_solar` makes for the Sun).
