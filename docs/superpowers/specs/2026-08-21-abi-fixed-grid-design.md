# Cloud occlusion, stage 2: the ABI fixed grid — design

**Status:** stage 2 specified to implementation precision. Section 2 records
measurements taken 2026-08-21 06:00-07:00Z that correct the stage 1 document and
re-plan stages 3-6. Those later stages are sketched for seam purposes only and
are NOT to be built from this document.

**Predecessor:** `2026-08-20-cloud-occlusion-geometry-design.md` (stage 1,
shipped as `ac8c72c`).

## 1. What stage 2 is

Stage 1 answers "given a look direction and a cloud height, where on the earth
is the cloud?" in latitude and longitude. Stage 2 answers "given a latitude and
longitude, which satellite pixel is that?" — the GOES-R ABI fixed grid
projection, and nothing else.

Build `server/astrodeck/cloudmap/abi_grid.py`: pure coordinate math, stdlib
`math` only. No I/O, no network, no h5py, no numpy, no config, no rig.

**Non-goals.** No fetching, no file reading, no cloud logic, no probability, no
rendering, no new dependency, no change to any existing module. Stage 2 adds one
module and one test file and touches nothing else. Same shape as stage 1, for
the same reason: this is the layer where a convention error silently misplaces
every cloud by kilometres, and it is exactly verifiable against published data.

## 2. Measurements taken 2026-08-21, and what they change

Everything in this section was measured against live NOAA data during the
NGC 7129 run, not reasoned about. Numbers are reproducible from the granules
named in section 9.

### 2.1 The stage 1 satellite table is stale — GOES-16 is retired

Listing `noaa-goes16` for 2026/233 returns **zero keys** for every ABI L2
product tried. GOES-East is now **GOES-19**; GOES-West is **GOES-18**. Stage 1
section 2.2 names GOES-East/16 and must be read as GOES-19. Both live buckets
carry `ABI-L2-ACMC`, `ABI-L2-ACHAC` and `ABI-L2-ACMF` at the expected cadence.

**The satellite decision, which stage 1 deferred to here: GOES-18 (West),
sub-longitude -137.0.** From the site the zenith angle is 46.2 deg against
GOES-19's 64.7 deg. That is the difference between a 2 km pixel smearing to
2.9 km and to 4.7 km, and between a 9 km cirrus deck being geolocated 9.4 km off
and 19.1 km off. Nothing about GOES-19 is better from the western United States.

### 2.2 The products, measured

| product | grid | file size | cadence | carries |
|---|---|---|---|---|
| `ABI-L2-ACMC` | 1500 x 2500, 2 km nadir | 4.06 MB | 5 min | `BCM` binary mask, `ACM` 4-level, **`Cloud_Probabilities`**, `DQF` |
| `ABI-L2-ACHAC` | 300 x 500, 10 km nadir | 0.33 MB | 5 min | `HT` cloud-top height (m), `DQF` |
| `ABI-L2-ACMF` | full disk | 24.7 MB | 10 min | as ACMC, whole hemisphere |

Three things here were not known when stage 1 was written:

- **`Cloud_Probabilities` exists.** The mask product ships a per-pixel cloud
  probability, `uint16` scaled by 1.5261e-05 into [0, 1]. Stage 1 assumed a
  binary mask and a probability model built on top of it. NOAA already did that
  work; do not rebuild it.
- **Height is 10 km, mask is 2 km.** Cloud-top height is a *five times coarser*
  grid than the mask. They are the same projection with different sampling, so
  one module serves both, but no code may assume a single grid.
- **CONUS, not full disk.** 4.4 MB per 5-minute cycle for both products,
  measured at 0.8-1.2 s per file. Full disk is 6x the bytes for half the
  cadence and no extra coverage that matters.

### 2.3 Anonymous S3 needs no dependency

`https://noaa-goes18.s3.amazonaws.com/?list-type=2&prefix=...` returns bucket
XML unauthenticated. Listing and GET both work with stdlib `urllib` plus
`xml.etree`. **boto3 is not required.** Reading the netCDF4 payload does require
HDF5 bindings — that is a stage 3 dependency decision, deliberately kept out of
stage 2 so this module stays testable with no install at all.

### 2.4 The observation agrees with the camera; the forecast does not

At 06:52Z, with the rig reporting `clear (200 bright stars, 41x noise)` and
Open-Meteo forecasting 100% cloud cover:

```
GOES-18 ACMC at the site pixel:  ACM = probably_clear,  p(cloud) = 0.19,  DQF = good
```

A 60 km box around the site showed the site sitting in a clear notch with solid
cloud to the west and south. **This is the entire argument for the project in
one reading:** the scalar forecast cannot express "you are in a hole", the
satellite observation can, and the instrument agrees with the satellite.

### 2.5 Cloud motion is real and measurable — but not per layer

Phase correlation on consecutive 5-minute granules, in a window around the site:

| product | variable | grid | result |
|---|---|---|---|
| `ACMC` | `Cloud_Probabilities` | 2 km | 111.0 km/h toward 359.2 deg |
| `ACHAC` | `HT` | 10 km | 112.9 km/h toward 358.6 deg |

**Two independent products, different algorithms and different grids, agree to
2.3 km/h on a 111 km/h vector.** Nine consecutive pairs, per-pair scatter
0.8 km/h. The measured speed matches the 250 hPa model wind (105.4 km/h at
10.9 km) to 5%, and the cloud-top histogram is bimodal with 442 of 1389
retrieved pixels above 8 km — the correlation is locked to the cirrus deck,
which is the deck with the structure.

Four artifact hypotheses were tested and rejected: the vector varies spatially
(-3.07 px at the site, -0.04 px 400 km south, +0.89 px over the ocean, so it is
not a global product artifact); it survives row-demeaning (not scan banding);
the grid definition is byte-identical across all 19 granules examined (not
origin drift); and an injected shift is recovered exactly, so the correlator
itself is sound.

**What failed.** Splitting `HT` into height bands and tracking each gives
126 km/h for cloud below 2 km, which is physically absurd for a marine layer
against a 17 km/h model wind at that level. Only 3% of low pixels were overrun
by cirrus, so simple contamination does not explain it. The honest reading is
that ACHA reports *the top of the topmost cloud*, not a layered atmosphere, and
a low-cloud mask carved out of it is not an observation of the low deck.

**Consequences, binding on stages 4-6:**

1. There is one cloud-top **surface**, not a stack of layers. Model it as a
   height field `h(lat, lon)` defined where cloudy.
2. Occlusion test: a ray is occluded where it **crosses that surface at a
   cloudy pixel** — walk the ray, find where `cloud_top(pierce(h)) - h` changes
   sign. This needs only the top, which is all the data provides.
3. Estimate motion **locally**, in a window around the site, never globally.
4. Estimate **one** motion vector for the surface. Do not claim per-layer winds.
5. Cross-check the measured vector against the model wind column. A vector
   matching no level in the column is not a measurement and the forecast must
   be withheld rather than shown.

### 2.6 The forecast horizon is bounded by decorrelation, not by arithmetic

Consecutive 5-minute pairs correlate at 0.27-0.34. The direct 40-minute pair
correlates at 0.088 and returns a null shift. The pattern does not survive
40 minutes. Any "when will it be in view" answer must carry a horizon of order
30 minutes and degrade explicitly past it.

### 2.7 A test that could not fail

While checking whether height bands share one signal, this document's author
compared the motion of the CLEAR mask against the ANY-CLOUD mask. They are
logical complements, so their phase correlations are identical by construction
and the comparison returned "identical" no matter what the sky was doing.
Recorded because the same shape will be tempting again in stage 5.

## 3. Revised stage plan

Stage 1's plan named stages 2-5 before any of this was measured. Revised:

| stage | deliverable | dependency |
|---|---|---|
| 1 (done) | `geometry.py` — spherical look/pierce/parallax | none |
| **2 (this)** | `abi_grid.py` — ABI fixed grid, both resolutions | none |
| 3 | granule read + S3 discovery + cache | **h5py**, optional extra |
| 4 | cloud-top surface + occlusion probability for (alt, az) | 1, 2, 3 |
| 5 | local motion estimate + bounded forecast + wind cross-check | 4 |
| 6 | dome viewer, and calibration against the rig's own sky verdict | 4, 5 |

Stage 3 will propose `h5py` as an **optional extra** (`cloudmap = ["h5py>=3.10"]`)
with import guards, the same idiom `comhost`/`asiair` already use: absent h5py
the cloud model is simply not offered. h5py is BSD-3-Clause with wheels for
win_amd64, manylinux x86_64 and manylinux aarch64 (the Orange Pi appliance).
That is a stage 3 decision and is recorded, not taken, here.

## 4. The coordinate contract

Stage 1's contract (section 5 of that document) holds unchanged for latitude,
longitude, azimuth, altitude and kilometres. Stage 2 adds:

| item | contract |
|---|---|
| scan angles | radians, named `x_rad` / `y_rad`. `x` is the east-west (column) angle, `y` the north-south (row) angle. |
| grid indices | `(row, col)`, both 0-based, row 0 **north**, col 0 **west**. |
| ellipsoid | **GRS80**, `r_eq = 6378137.0` m, `r_pol = 6356752.31414` m, taken from the granule, never hard-coded in the algorithm. |
| satellite radius | `H = perspective_point_height + semi_major_axis` metres. `perspective_point_height` alone is the height above the surface and using it is a 6378 km error. |
| sweep axis | GOES sweeps about **x**. EUMETSAT sweeps about y. The axis is a field, not an assumption. |

**Stage 2 does not use stage 1's sphere.** The ABI grid is defined on the GRS80
ellipsoid by the instrument's own product definition. Substituting R=6371.0088
misplaces pixels by kilometres. These are two different things — physical
geometry versus an instrument grid definition — and mixing them is the single
most likely bug in this module. The contract is: **stage 1 is a sphere, stage 2
is an ellipsoid, and no value crosses between them except latitude, longitude
and height.**

## 5. The design

One frozen dataclass and two inverse functions, plus index arithmetic.

`GridSpec` carries everything the transform needs, all of it read from a
granule's own metadata by stage 3 and passed in. Nothing is hard-coded, so the
module works for GOES-18, GOES-19, any sector, and both resolutions without a
branch.

Because the projection depends only on the satellite and not the sampling, the
2 km and 10 km products return **identical scan angles for the same latitude and
longitude**, differing only in index. That is an invariant, and test 5 pins it.

## 6. API — exact signatures

```python
@dataclass(frozen=True)
class GridSpec:
    sat_height_m: float       # H = perspective_point_height + semi_major_axis
    r_eq_m: float
    r_pol_m: float
    lon_origin_deg: float
    sweep_axis: str           # "x" or "y"; only "x" supported, "y" raises
    x_offset_rad: float       # x[0]
    x_scale_rad: float        # x[1] - x[0], positive
    n_cols: int
    y_offset_rad: float       # y[0]
    y_scale_rad: float        # y[1] - y[0], NEGATIVE for GOES
    n_rows: int

def lonlat_to_scan(spec, lat_deg, lon_deg) -> tuple[float, float] | None
def scan_to_lonlat(spec, x_rad, y_rad) -> tuple[float, float] | None
def scan_to_index(spec, x_rad, y_rad) -> tuple[int, int]
def index_to_scan(spec, row, col) -> tuple[float, float]
def lonlat_to_index(spec, lat_deg, lon_deg) -> tuple[int, int] | None
def index_to_lonlat(spec, row, col) -> tuple[float, float] | None
def in_grid(spec, row, col) -> bool
def pixel_size_km(spec, row, col) -> tuple[float, float]
```

`lonlat_to_scan` returns `None` when the point is behind the earth's limb.
`scan_to_lonlat` returns `None` when the ray misses the earth. Both are "not
visible", which is a legitimate answer, not an error — unlike stage 1's domain
guards, which raise, because there an out-of-domain altitude is a caller bug.

`lonlat_to_index` returns `None` only for behind-the-limb. **A visible point
outside this sector's rows and columns returns its index anyway, and it may be
negative or past the end.** Range checking is `in_grid`, deliberately separate:
callers need to distinguish "not on the earth" from "on the earth, outside this
sector" and a single `None` conflates them.

## 7. Algorithms — exact

GOES-R Product User Guide vol. 5 section 5.1.2.8. Reproduced here so the
implementer needs no external document.

### 7.1 `lonlat_to_scan`, for `sweep_axis == "x"`

```
e2   = 1 - r_pol^2 / r_eq^2
phi  = radians(lat_deg);  lam = radians(lon_deg);  lam0 = radians(lon_origin_deg)
phic = atan( (r_pol^2 / r_eq^2) * tan(phi) )            # geodetic -> geocentric
rc   = r_pol / sqrt(1 - e2 * cos(phic)^2)
sx   = H - rc * cos(phic) * cos(lam - lam0)
sy   = -rc * cos(phic) * sin(lam - lam0)
sz   = rc * sin(phic)

if H * (H - sx)  <  sy^2 + (r_eq^2/r_pol^2) * sz^2 :  return None   # behind the limb
y_rad = atan( sz / sx )
x_rad = asin( -sy / sqrt(sx^2 + sy^2 + sz^2) )
```

The visibility test is not optional and not a nicety: without it the arithmetic
returns a perfectly plausible scan angle for a point on the far side of the
planet.

### 7.2 `scan_to_lonlat`, for `sweep_axis == "x"`

```
a  = sin(x)^2 + cos(x)^2 * ( cos(y)^2 + (r_eq^2/r_pol^2) * sin(y)^2 )
b  = -2 * H * cos(x) * cos(y)
c  = H^2 - r_eq^2
d  = b^2 - 4ac
if d < 0 : return None                                  # the ray misses the earth
rs = ( -b - sqrt(d) ) / (2a)                            # NEAR root; +sqrt is the far side
sx = rs * cos(x) * cos(y)
sy = -rs * sin(x)
sz = rs * cos(x) * sin(y)
lat_deg = degrees( atan( (r_eq^2/r_pol^2) * sz / sqrt( (H - sx)^2 + sy^2 ) ) )
lon_deg = degrees( lam0 - atan( sy / (H - sx) ) )
```

Normalise the returned longitude into `(-180, +180]`, the same convention and
the same boundary rule as stage 1 — where it was the one mutation of 26 that
survived the first test suite.

**`-b - sqrt(d)` is the likely bug.** The far root is the back of the earth and
is also geometrically valid.

### 7.3 Index arithmetic

```
scan_to_index : row = round( (y_rad - y_offset_rad) / y_scale_rad )
                col = round( (x_rad - x_offset_rad) / x_scale_rad )
index_to_scan : x_rad = x_offset_rad + col * x_scale_rad
                y_rad = y_offset_rad + row * y_scale_rad
in_grid       : 0 <= row < n_rows and 0 <= col < n_cols
```

`y_scale_rad` is **negative** for GOES: row increases southward. Do not take its
absolute value anywhere. A sign error here mirrors the image north-south and
every check still passes at the sub-satellite point, which is why test 4 uses an
asymmetric point.

Use Python's `round`, and note it is banker's rounding — exactly `.5` goes to
even. This only matters on a pixel boundary and either neighbour is defensible;
it is pinned in test 9 so a future change is deliberate.

### 7.4 `pixel_size_km`

Ground spacing of the cell at `(row, col)`, by great-circle distance between the
cell centres either side, on stage 1's sphere:

```
ns_km = haversine( index_to_lonlat(row-1, col), index_to_lonlat(row+1, col) ) / 2
ew_km = haversine( index_to_lonlat(row, col-1), index_to_lonlat(row, col+1) ) / 2
```

This is the one place stage 2 legitimately uses the sphere: it is measuring a
distance on the ground, which is stage 1's job, not defining the grid. Import
`EARTH_RADIUS_KM` from `geometry` rather than redeclaring it.

At the edge of the grid, clamp the neighbour to the grid and halve the divisor
accordingly, so the function is total for every in-grid cell.

**Why this function exists.** The "2 km" in the product name is at nadir. At the
site it is 2.95 km north-south by 2.21 km east-west. Converting pixel
displacements with the nominal 2 km understated a measured cloud speed by 33%
during the work that produced this document. Any stage that turns pixels into
kilometres must call this.

## 8. Domain errors

Raise `ValueError` for: `sweep_axis != "x"`; `n_rows` or `n_cols` < 1;
`x_scale_rad == 0` or `y_scale_rad == 0`; `r_pol_m > r_eq_m`;
`sat_height_m <= r_eq_m`. These are malformed inputs, not geometry, and unlike
"behind the limb" they cannot be a legitimate answer.

## 9. Golden vectors — measured from real granules, use verbatim

Read from
`OR_ABI-L2-ACMC-M6_G18_s20262330606176_e20262330608549_c20262330609298.nc` and
`OR_ABI-L2-ACHAC-M6_G18_s20262330606176_e20262330608549_c20262330610529.nc`,
GOES-18, 2026-08-21.

### 9.1 The two GridSpecs

Shared by both: `sat_height_m = 42164160.0`, `r_eq_m = 6378137.0`,
`r_pol_m = 6356752.31414`, `lon_origin_deg = -137.0`, `sweep_axis = "x"`.

| | ACMC (2 km) | ACHAC (10 km) |
|---|---|---|
| `x_offset_rad` | -0.06997200101613998 | -0.06985999643802643 |
| `x_scale_rad` | 5.6000000768108293e-05 | 0.0002800000074785203 |
| `n_cols` | 2500 | 500 |
| `y_offset_rad` | 0.12821200489997864 | 0.12809999287128448 |
| `y_scale_rad` | -5.6000000768108293e-05 | -0.0002800000074785203 |
| `n_rows` | 1500 | 300 |

### 9.2 `lonlat_to_scan` — identical for both grids

| lat, lon | x_rad | y_rad |
|---|---|---|
| 40.0, -105.0 | 0.0677105022203723 | 0.10686465600808957 |
| 37.0, -125.0 | 0.028365874074141435 | 0.10232676121155931 |
| 0.0, -137.0 | 0.0 | 0.0 |
| 50.0, -160.0 | -0.04146199288596409 | 0.12602032966691368 |
| 20.0, -100.0 | 0.09608491064111407 | 0.05793263512794123 |
| 40.0, -20.0 | `None` — behind the limb | |

### 9.3 Indices, and which are in the sector

| lat, lon | ACMC (row, col) | in grid | ACHAC (row, col) | in grid |
|---|---|---|---|---|
| 40.0, -105.0 | (381, 2459) | yes | (76, 491) | yes |
| 37.0, -125.0 | (462, 1756) | yes | (92, 351) | yes |
| 50.0, -160.0 | (39, 509) | yes | (7, 101) | yes |
| 0.0, -137.0 | (2290, 1250) | **no** | (457, 249) | **no** |
| 20.0, -100.0 | (1255, 2965) | **no** | (251, 593) | **no** |

The sub-satellite point being outside a CONUS sector is the case that proves
`in_grid` is separate from `None`: it is the most visible point on the disk.

### 9.4 `pixel_size_km` — tolerance 0.001 km

| lat, lon | ACMC N-S | ACMC E-W | ACHAC N-S | ACHAC E-W |
|---|---|---|---|---|
| 40.0, -105.0 | 3.500622 | 2.754963 | 17.473834 | 13.753146 |
| 37.0, -125.0 | 2.899210 | 2.159355 | 14.497235 | 10.798411 |
| 50.0, -160.0 | 4.343845 | 2.483640 | 21.797253 | 12.440205 |

Nowhere near the nominal 2 km and 10 km. That is the point of the function.

### 9.5 Sector corners, ACMC — reproduce the granule's own declared bounds

| corner | (row, col) | lat | lon |
|---|---|---|---|
| NW | (0, 0) | 53.5001 | +175.6236 (normalised from -184.3764) |
| NE | (0, 2499) | 53.5001 | -89.6236 |
| SW | (1499, 0) | 14.8052 | -161.5694 |
| SE | (1499, 2499) | 14.8052 | -112.4306 |

The granule declares `geospatial_northbound_latitude = 53.50006`,
`westbound = 175.62358`, `eastbound = -89.62357`, matching to five decimals.
Its `southbound = 14.5713` is **not** at a corner — the southern edge bows, and
the minimum over the bottom row is 14.5713 at column 1250, longitude -136.9906,
directly beneath the satellite. Test 6 asserts the corners and test 7 the bow;
asserting only the corners against the declared southbound would fail correct
code.

## 10. Required tests

`server/tests/test_abi_grid.py`. Every name states the property, not the
function.

1. `test_the_sub_satellite_point_maps_to_the_grid_origin` — (0, -137) gives
   exactly (0.0, 0.0), and back.
2. `test_scan_and_lonlat_are_exact_inverses` — sweep lat -20..60 step 5, lon
   -175..-80 step 5, both GridSpecs; round trip within 1e-9 deg. Assert at
   least 200 points actually ran, or a broken visibility test that returns
   `None` everywhere passes vacuously.
3. `test_a_point_behind_the_limb_is_not_visible` — (40, -20) gives `None`; and
   the nearest visible point on the same parallel does not.
4. `test_row_increases_southward_and_column_eastward` — from an asymmetric
   in-grid cell, row+1 is south and col+1 is east. Pins the negative
   `y_scale_rad`.
5. `test_both_products_agree_on_scan_angle_and_differ_only_in_index` — section
   9.2 and 9.3 for both GridSpecs.
6. `test_the_sector_corners_match_the_granules_declared_bounds` — section 9.5.
7. `test_the_southern_edge_bows_below_its_corners` — the bottom-row minimum is
   14.5713 at column 1250, and is south of both bottom corners.
8. `test_pixel_size_is_not_the_nominal_resolution` — section 9.4 to 0.001 km,
   and assert the 2 km product exceeds 2.5 km north-south at all three points,
   so a stub returning the nominal value fails.
9. `test_index_rounding_is_pinned_at_a_cell_boundary` — a scan angle exactly
   half a cell past a centre lands on the documented neighbour.
10. `test_a_visible_point_outside_the_sector_still_gets_an_index` — the
    sub-satellite point returns (2290, 1250) and `in_grid` is False.
11. `test_longitude_comes_back_in_the_half_open_range` — including a point whose
    raw arithmetic gives -184.3764, which must normalise to +175.6236.
12. `test_the_far_root_is_not_chosen` — a scan angle with two real intersections
    returns the near one; the returned point is on the satellite-facing side.
13. `test_a_malformed_grid_is_rejected` — each condition in section 8 raises
    `ValueError`, parametrised.
14. `test_a_y_sweep_instrument_is_refused_not_silently_mishandled` —
    `sweep_axis="y"` raises rather than returning EUMETSAT-shaped nonsense.
15. `test_pixel_size_is_total_at_the_grid_edge` — row 0 and the last row return
    finite positive sizes.
16. `test_nothing_here_uses_the_sphere_for_the_projection` — construct a
    GridSpec with `r_pol_m == r_eq_m` and assert the scan angle for
    (40, -105) differs from the GRS80 answer by more than 1e-4 rad, proving
    the ellipsoid is actually in the arithmetic.

## 11. Invariants an implementation must not break

- `lonlat_to_scan` and `scan_to_lonlat` are exact inverses to 1e-9 deg.
- The scan angle for a latitude and longitude is independent of grid sampling.
- `y_scale_rad` stays negative; row 0 is north.
- The projection uses the ellipsoid from the `GridSpec`. It never uses
  `EARTH_RADIUS_KM`, and `pixel_size_km` never uses anything else.
- Longitude is returned in `(-180, +180]`; exactly -180 normalises to +180.
- No function reads config, the clock, the network, or the filesystem.
- No function mutates its arguments; `GridSpec` is frozen.
- Pure stdlib `math`. No numpy, no h5py, no new dependency.

## 12. Seams for later stages — do not build

- **Stage 3** constructs `GridSpec` from a granule's `goes_imager_projection`
  attributes and its `x`/`y` axis packing. `x_scale_rad` is the axis variable's
  `scale_factor` and `x_offset_rad` its `add_offset`. Note that h5py does
  **not** apply `scale_factor`, `add_offset` or `_FillValue` — unlike netCDF4
  and xarray, which do. Unpacking is the caller's job and forgetting it yields
  raw `uint16` counts that look like plausible data.
- **Stage 4** walks a ray by calling stage 1's `pierce_point` for a ladder of
  heights and this module's `lonlat_to_index` at each, looking for the sign
  change in `cloud_top - h` described in section 2.5.
- **Stage 5** needs `pixel_size_km` to turn a correlation peak into km/h, and
  the model wind column as the cross-check that permits the forecast to be
  shown at all.
- **Stage 6** joins each captured frame's own cloud verdict to its pierce point.
  The rig has been generating that labelled dataset nightly, for free, all along.
