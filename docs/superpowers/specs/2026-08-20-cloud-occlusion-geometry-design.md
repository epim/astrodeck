# Cloud occlusion geometry — design

**Status:** stage 1 specified to implementation precision. Stages 2–5 sketched
for seam purposes only and are NOT to be built from this document.

## 1. The problem

"Is a cloud in my line of sight, and when will one be?" Today the rig answers
with a scalar cloud-cover percentage from a forecast over a ~10 km grid cell,
which has no direction in it at all. That gate refused two consecutive clear
nights (2026-08-19, 2026-08-20) and has now been demoted to advisory.

## 2. Two findings that constrain the whole design

### 2.1 Weather radar cannot see clouds

Rayleigh backscatter scales as D⁶. A 15 µm cloud droplet returns **1.1 × 10⁻¹¹**
of a 1 mm raindrop's signal per particle; even allowing ~10⁵ more droplets per
unit volume the deficit is ~10⁻⁶. NEXRAD detects precipitation. A cloud model
built on radar shows an empty sky under solid overcast.

Radar remains correct for the rain gate (`weather.veto_reason`) and must not be
repurposed here.

### 2.2 The right sensor is already fetched, from the wrong satellite

`api/app.py: IEM_SLUGS["satellite"] = "goes_east_fulldisk_ch13"`. Band 13 IR is
the correct instrument, but from the western United States:

| satellite | sub-lon | zenith angle from 40N/105W | 2 km pixel becomes | 9 km cloud geolocated off by |
|---|---|---|---|---|
| GOES-East/16 | −75.2° | 55.5° | 3.5 km | 13.1 km |
| GOES-West/18 | −137.0° | 56.7° | 3.6 km | 13.7 km |

(At the real site the split is wider — East 64.7°, West 46.2°.) Choice of
satellite is a **stage 2** decision; recorded here because stage 1 must expose
the sub-longitude as a parameter rather than hard-coding one.

**Do not parse the IEM tiles.** They are PNGs of a colour ramp; recovering
physical values from them is lossy. NOAA publishes the derived products free and
unauthenticated on S3 (`noaa-goes18`): `ABI-L2-ACMF` (clear-sky mask) and
`ABI-L2-ACHAF` (cloud-top height). Those are stage 2 inputs.

## 3. Why the geometry is necessary, not decorative

The line of sight pierces a cloud layer a long way downrange:

| layer | height | pierce distance at alt 30° | beam footprint there |
|---|---|---|---|
| marine layer | 0.6 km | 1.0 km | 35 m |
| cumulus | 1.5 km | 2.6 km | 88 m |
| cirrus | 9 km | **15.6 km** | 527 m |

"Cloud overhead" and "cloud in the beam" differ by up to 15 km. No scalar can
express that; a projection can.

**And what it can never deliver:** the beam footprint is 35–530 m against a
≥2 km pixel — over 1000× in area. The output is a *probability over a cell*,
never a determination for the column. Any UI implying otherwise is wrong.

## 4. Scope of stage 1

Build `server/astrodeck/cloudmap/geometry.py`: pure coordinate geometry, no I/O,
no config, no network, no rig, no numpy. Every function total and deterministic.

**Non-goals.** No fetching. No advection. No probability model. No rendering.
No changes to any existing module. No new dependencies. Stage 1 adds a package
and a test file and touches nothing else.

## 5. The coordinate contract

Pinned here because every plausible bug in this module is a convention error.

| item | contract |
|---|---|
| azimuth | degrees from **true north**, increasing toward **east**. 0=N, 90=E, 180=S, 270=W. Matches `mount.az` in status. |
| altitude | degrees above the horizon. Valid domain `0 < alt ≤ 90`. |
| longitude | degrees, east positive, normalised to `(−180, +180]`. |
| heights | kilometres above **mean sea level**, for both the site and cloud layers. |
| distances | kilometres. Every parameter and return suffixed `_km`. |
| angles | degrees. Every parameter and return suffixed `_deg`. |
| earth | **sphere**, R = 6371.0088 km (IUGG mean). |

**Ceilometers report AGL.** Conversion is explicit via `agl_to_msl_km`; no
function silently accepts AGL.

**Spherical is sufficient.** The local ellipsoid radius at 40° is ~6369.4 km,
a 2.5 × 10⁻⁴ relative error — 8.6 m over a 34 km slant, against a ≥2000 m pixel.
Do not introduce WGS84; it buys nothing here and costs clarity.

## 6. The design in one line

**One primitive, one constrained inverse, and they validate each other.**

`look_from` converts any (site → target) pair into (alt, az, slant).
`pierce_point` inverts it against a spherical shell.
The satellite is *not a special case in code* — it is `look_from` with the
target placed at the geostationary radius over the equator.

That unification is deliberate: it removes the class of bug where a separate
satellite-geometry routine disagrees with the main one.

## 7. API — exact signatures

```python
# module constants
EARTH_RADIUS_KM: float = 6371.0088
GEOSTATIONARY_RADIUS_KM: float = 42164.0
GEOSTATIONARY_ALT_KM: float = GEOSTATIONARY_RADIUS_KM - EARTH_RADIUS_KM

@dataclass(frozen=True)
class Site:
    lat_deg: float
    lon_deg: float
    elev_km: float = 0.0          # MSL

@dataclass(frozen=True)
class GeoPoint:
    lat_deg: float
    lon_deg: float
    height_msl_km: float

@dataclass(frozen=True)
class LookVector:
    alt_deg: float
    az_deg: float
    slant_km: float


def look_from(site: Site, target: GeoPoint) -> LookVector: ...

def pierce_point(site: Site, alt_deg: float, az_deg: float,
                 layer_msl_km: float) -> GeoPoint: ...

def slant_to_layer_km(alt_deg: float, layer_msl_km: float,
                      site_elev_km: float = 0.0) -> float: ...

def satellite_look(site: Site, sat_lon_deg: float) -> LookVector: ...

def deparallax(reported_lat_deg: float, reported_lon_deg: float,
               cloud_msl_km: float, sat_lon_deg: float) -> GeoPoint: ...

def beam_footprint_km(slant_km: float, fov_deg: float) -> float: ...

def agl_to_msl_km(agl_km: float, ground_elev_km: float) -> float: ...
```

## 8. Algorithms — exact

### 8.1 `slant_to_layer_km`

With `Rs = R + site_elev_km`, `top = R + layer_msl_km`, `a = radians(alt_deg)`,
the positive root of `s² + 2·Rs·sin(a)·s + Rs² − top² = 0`:

```
s = −Rs·sin(a) + sqrt((Rs·sin(a))² + top² − Rs²)
```

Exact on a sphere. Do **not** substitute `h / tan(alt)`; that is a flat-earth
approximation and this costs nothing more.

### 8.2 `pierce_point`

```
s   = slant_to_layer_km(alt, layer, site.elev)
psi = asin(clamp(s·cos(a) / top, -1, 1))            # earth-central angle
lat2 = asin(sin(φ)cos(ψ) + cos(φ)sin(ψ)cos(az))
lon2 = λ + atan2(sin(az)sin(ψ)cos(φ), cos(ψ) − sin(φ)sin(lat2))
```

Return `GeoPoint(lat2, normalise(lon2), layer_msl_km)`.

### 8.3 `look_from`

Both points to ECEF with `r = R + h`; difference vector; project onto the local
ENU basis at the site:

```
up    = (cos φ cos λ, cos φ sin λ, sin φ)
east  = (−sin λ, cos λ, 0)
north = (−sin φ cos λ, −sin φ sin λ, cos φ)

alt = degrees(asin(clamp(v̂·up, -1, 1)))
az  = degrees(atan2(v̂·east, v̂·north)) mod 360
slant = |v|
```

### 8.4 `satellite_look`

```
return look_from(site, GeoPoint(0.0, sat_lon_deg, GEOSTATIONARY_ALT_KM))
```

One line. No separate implementation.

### 8.5 `deparallax`

The satellite product geolocates a pixel by intersecting its line of sight with
the **ground**, so a cloud at height h is reported displaced *away* from the
sub-satellite point. Correct by moving *toward* it:

```
look  = satellite_look(Site(reported_lat, reported_lon, 0.0), sat_lon)
shift = cloud_msl_km · tan(radians(90 − look.alt_deg))
result = move along great circle from (reported) on bearing look.az_deg by shift
```

**Sign is the likely bug: the corrected point moves TOWARD the satellite.**
A named test pins it.

Single pass. Re-evaluating the zenith angle at the corrected point changes the
shift by ~20 m over 9.4 km — far below pixel scale. Do not iterate.

### 8.6 `beam_footprint_km`

```
return 2.0 · slant_km · tan(radians(fov_deg) / 2.0)
```

**Half angle.** `slant · tan(fov)` is wrong by ~2× and nearly indistinguishable
at small angles, so only a test with a large `fov_deg` catches it. One exists.

### 8.7 Domain errors

Raise `ValueError` with a message naming the offending value:

- `alt_deg <= 0` or `> 90` in `pierce_point` / `slant_to_layer_km`
- `layer_msl_km <= site.elev_km` (the layer is at or below the observer)
- `fov_deg <= 0` or `>= 180`

Do not return `None`, do not clamp silently.

## 9. Golden vectors — computed and verified, use verbatim

Site `Site(40.0, -105.0, 0.0)` throughout. **Never use the real observing site
in tests** — the privacy hook rejects the commit.

### 9.1 `slant_to_layer_km`

| alt_deg | layer_msl_km | expected slant_km |
|---|---|---|
| 90 | 9.0 | 9.000000 (exactly the height) |
| 0⁺ | 9.0 | 338.761 (`sqrt(2Rh + h²)`) |
| 30 | 2.0 | 3.998 |
| 30 | 9.0 | 17.962 |
| 45 | 4.0 | 5.655 |
| 15 | 9.0 | 34.438 |

### 9.2 `pierce_point`

| alt | az | layer_km | lat_deg | lon_deg |
|---|---|---|---|---|
| 90 | 0 | 2.0 | 40.00000 | −105.00000 |
| 60 | 0 | 2.0 | 40.01038 | −105.00000 |
| 30 | 90 | 2.0 | 39.99999 | −104.95936 |
| 30 | 90 | 9.0 | 39.99986 | −104.81764 |
| 30 | 270 | 9.0 | 39.99986 | −105.18236 |
| 45 | 45 | 4.0 | 40.02541 | −104.96681 |
| 20 | 180 | 0.6 | 39.98518 | −105.00000 |
| 15 | 315 | 9.0 | 40.21091 | −105.27661 |

Tolerance: `abs=1e-5` degrees.

### 9.3 `satellite_look` from the same site

| satellite | sat_lon_deg | zenith (90−alt) | az_deg |
|---|---|---|---|
| GOES-West/18 | −137.0 | 56.74 | 224.19 |
| GOES-East/16 | −75.2 | 55.49 | 138.30 |

Tolerance: `abs=0.01` degrees.

### 9.4 `deparallax`, sat_lon −137.0, reported point 40.0/−105.0

| cloud_msl_km | shift_km | result lat | result lon |
|---|---|---|---|
| 2.0 | 3.05 | 39.98033 | −105.02495 |
| 9.0 | 13.72 | 39.91145 | −105.11216 |

### 9.5 `beam_footprint_km`, fov 1.682°

| layer_km | alt | slant_km | footprint (m) |
|---|---|---|---|
| 0.6 | 30 | 1.200 | 35.2 |
| 2.0 | 45 | 2.828 | 83.0 |
| 9.0 | 30 | 17.962 | 527.3 |

## 10. Required tests

`server/tests/test_cloudmap_geometry.py`. Each name pins one failure mode; do
not merge or rename them.

1. `test_zenith_slant_is_exactly_the_layer_height` — alt 90 ⇒ `s == h`.
2. `test_horizon_slant_matches_the_closed_form` — alt→0 ⇒ `sqrt(2Rh+h²)`.
3. `test_slant_is_not_the_flat_earth_approximation` — at alt 15, h 9, assert the
   result differs from `h/tan(alt)` by > 0.5 km (flat gives 33.59, exact 34.44).
4. `test_pierce_at_zenith_returns_the_site`.
5. `test_azimuth_zero_goes_north_and_ninety_goes_east` — az 0 raises lat with
   lon unchanged; az 90 raises lon with lat ~unchanged.
6. `test_golden_pierce_points` — parametrized over §9.2.
7. `test_the_round_trip_is_exact` — for alt ∈ {5,15,30,45,60,89.9},
   az ∈ {0,45,90,180,270,359}, h ∈ {0.6,2.0,9.0}:
   `look_from(site, pierce_point(site, alt, az, h))` returns that alt/az.
   Tolerance `abs=1e-6` deg. **This is the load-bearing test.**
8. `test_satellite_look_matches_the_golden_vectors` — §9.3.
9. `test_satellite_look_is_not_a_separate_implementation` — assert
   `satellite_look(site, L)` equals
   `look_from(site, GeoPoint(0.0, L, GEOSTATIONARY_ALT_KM))` exactly.
10. `test_deparallax_moves_toward_the_subsatellite_point` — for a site north and
    east of the sub-satellite point, corrected lat and lon both **decrease**.
11. `test_deparallax_magnitude_is_h_tan_zenith` — §9.4 within 10 m.
12. `test_deparallax_is_zero_at_the_subsatellite_point` — a point at 0°N,
    sat_lon has zenith 0, so the shift is 0.
13. `test_a_target_below_the_horizon_is_refused` — alt 0 and −5 raise ValueError.
14. `test_a_layer_at_or_below_the_observer_is_refused`.
15. `test_longitude_normalises_across_the_antimeridian` — site at lon 179.9,
    az 90, result in `(−180, 180]` and negative.
16. `test_beam_footprint_uses_the_half_angle` — with `fov_deg=90`,
    `slant_km=1`, the result is `2.0` km, not `tan(90°)`.
17. `test_site_elevation_moves_the_pierce_point` — a 2 km site under a 9 km
    layer pierces nearer than a sea-level site does.
18. `test_agl_to_msl_adds_the_ground_elevation`.

## 11. Invariants an implementation must not break

- `pierce_point` and `look_from` are exact inverses (test 7).
- `satellite_look` has no arithmetic of its own (test 9).
- No function reads config, the clock, the network, or the filesystem.
- No function mutates its arguments; all dataclasses frozen.
- Pure stdlib `math`. No numpy, no new dependencies.

## 12. Seams for later stages — do not build

- **Stage 2** fills a `CloudLayer(height_msl_km, mask_lookup)` and needs
  `deparallax` plus a lat/lon → pixel mapping for the GOES fixed grid.
- **Stage 3** (advection) needs two timestamped grids and returns a motion
  vector; it consumes geometry only through `pierce_point`.
- **Stage 4** (dome viewer) will want array versions of `pierce_point`. The
  golden vectors in §9 must then apply unchanged to the vectorised twin.
- **Stage 5** (calibration) joins each captured frame's cloud verdict to its
  pierce point — a labelled dataset the rig already generates nightly for free.
