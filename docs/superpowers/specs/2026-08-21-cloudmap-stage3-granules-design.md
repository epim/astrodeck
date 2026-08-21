# Cloud occlusion, stage 3: getting a granule — design

**Status:** stage 3 specified to implementation precision.

**Predecessors:** `2026-08-20-cloud-occlusion-geometry-design.md` (stage 1,
`ac8c72c`), `2026-08-21-abi-fixed-grid-design.md` (stage 2, `328b69d`).

## 1. What stage 3 is

Stages 1 and 2 are pure arithmetic and touch nothing. Stage 3 is the first
stage with I/O: find the newest GOES granule, put it on disk, and read a window
of it into numpy. Nothing interprets the numbers — that is stage 4.

Two modules, split on the network boundary so the file reader stays testable
without a socket:

- `server/astrodeck/cloudmap/granule.py` — read a local `.nc` into arrays. No
  network. Needs `h5py` and `numpy`.
- `server/astrodeck/cloudmap/source.py` — discover keys on S3, download, cache,
  evict. Needs `httpx` (already a dependency) and stdlib `xml.etree`.

**Non-goals.** No cloud logic, no probability, no occlusion, no motion, no
rendering, no API route, no UI, no poller wired into the app lifespan. Stage 3
is a library the later stages call.

## 2. The dependency, and the platform question it raises

Stage 3 adds **`h5py`** as an optional extra:

```toml
cloudmap = ["h5py>=3.10"]
```

BSD-3-Clause. Its only requirement is `numpy`, already a hard dependency.

**It costs no platform reach.** Measured against PyPI 2026-08-21, h5py's wheel
coverage is numpy's coverage:

| platform | numpy (already required) | h5py |
|---|---|---|
| win_amd64 | yes | yes |
| manylinux + musllinux x86_64 | yes | yes |
| manylinux + musllinux aarch64 | yes | yes |
| macOS arm64 + x86_64 | yes | yes |
| **linux armv7l / armv6l** | **no** | **no** |

So 64-bit Raspberry Pi OS, Ubuntu arm64 and the Orange Pi appliance all work,
and 32-bit ARM was already out of reach before this stage existed — numpy put it
there. The one asymmetry is `win32`, which numpy ships and h5py does not; 32-bit
Windows is not a supported AstroDeck target (the vendored camera DLLs are x64).

**Import-guard idiom, the same one `comhost` and `asiair` already use.** Absent
h5py, `granule.py` still imports, `HAVE_H5PY` is `False`, and every entry point
raises `CloudmapUnavailable` with a message naming the extra. Nothing else in
the product changes: no route disappears, no config key breaks, no traceback at
startup. A test asserts the module imports with `h5py` masked out of
`sys.modules`.

## 3. What the data actually looks like

Measured from real granules, 2026-08-21 (design 2 of this series, section 2.2).
Reproduced here because the implementer must not have to guess.

| | `ABI-L2-ACMC` | `ABI-L2-ACHAC` |
|---|---|---|
| shape | 1500 x 2500 | 300 x 500 |
| size on disk | 4.06 MB | 0.33 MB |
| cadence | 5 min | 5 min |
| variables used | `BCM`, `ACM`, `Cloud_Probabilities`, `DQF` | `HT`, `DQF` |

Key naming: `ABI-L2-ACMC/<YYYY>/<DDD>/<HH>/OR_ABI-L2-ACMC-M6_G18_sYYYYDDDHHMMSSS_e..._c....nc`,
where `DDD` is the zero-padded day of year and `HH` the UTC hour. The `s` field
is scan start; **sort lexicographically and the newest key is last**, because
the timestamp is fixed-width.

### 3.1 h5py does not unpack. netCDF4 and xarray do.

This is the defect this module exists to prevent. `h5py` returns the raw stored
integers and leaves `scale_factor`, `add_offset` and `_FillValue` as attributes
for the caller to apply. Read `Cloud_Probabilities` with h5py and no unpacking
and you get `uint16` counts up to 65535 that look like plausible data.

Unpacking rule, applied in this exact order:

```
raw   = dataset[slice]                       # integer dtype
out   = raw.astype(float64)
out[raw == _FillValue] = NaN                 # BEFORE scaling; the fill is a raw value
out = out * scale_factor + add_offset        # only if the attributes are present
```

`_FillValue`, `scale_factor` and `add_offset` are each stored as a length-1
array; read element `[0]`. A variable may have any subset of them — `BCM`,
`ACM` and `DQF` have `_FillValue` but no scaling; `HT` and
`Cloud_Probabilities` have all three.

Measured values, to be asserted as golden:
`Cloud_Probabilities.scale_factor = 1.5260999134625308e-05`,
`_FillValue = 65535`; `HT.scale_factor = 0.30520370602607727`,
`add_offset = 0.0`, `_FillValue = 65535`, units metres;
`BCM/ACM/DQF._FillValue = 255`.

### 3.2 The projection comes from the granule, never from a constant

`GridSpec` is built from the file's own `goes_imager_projection` attributes and
its `x`/`y` axis packing:

```
sat_height_m   = perspective_point_height[0] + semi_major_axis[0]
r_eq_m         = semi_major_axis[0]
r_pol_m        = semi_minor_axis[0]
lon_origin_deg = longitude_of_projection_origin[0]
sweep_axis     = sweep_angle_axis            (bytes; decode to str)
x_offset_rad   = x.attrs["add_offset"][0]      x_scale_rad = x.attrs["scale_factor"][0]
y_offset_rad   = y.attrs["add_offset"][0]      y_scale_rad = y.attrs["scale_factor"][0]
n_cols, n_rows = x.shape[0], y.shape[0]
```

**`perspective_point_height` alone is the height above the surface**; using it
unadded is a 6378 km error that stage 2's guards cannot catch, because 35786 km
is a legal satellite height. Stage 2 section 4 says so; this is the call site
that must get it right.

`sweep_angle_axis` is stored as bytes and must be decoded before it reaches
`GridSpec`, whose validator compares against the string `"x"`.

### 3.3 Time

`t` is a scalar `float64`, "seconds since 2000-01-01 12:00:00" (J2000 epoch,
UTC). Convert with `datetime(2000, 1, 1, 12, tzinfo=timezone.utc) +
timedelta(seconds=float(t))`. Do **not** use the filename's `s` field for the
observation time; it is the scan start, and `t` is the mid-point.

## 4. API — exact signatures

### 4.1 `granule.py`

```python
class CloudmapUnavailable(RuntimeError): ...

HAVE_H5PY: bool

@dataclass(frozen=True)
class GranuleWindow:
    spec: GridSpec              # stage 2's, built from this file
    observed_at: datetime       # tz-aware UTC, from `t`
    product: str                # "ABI-L2-ACMC"
    platform: str               # "G18"
    row0: int                   # window origin in FULL-grid indices
    col0: int
    data: dict[str, "np.ndarray"]   # float64, NaN where fill, unpacked

    def value_at(self, name: str, row: int, col: int) -> float
    """Full-grid indices in, value out. NaN if fill; raises KeyError for an
    unread variable and IndexError outside the window."""

def read_window(path, variables, *, centre_lat_deg, centre_lon_deg,
                half_rows, half_cols) -> GranuleWindow
def read_grid_spec(path) -> GridSpec
def observed_at(path) -> datetime
```

`read_window` opens the file, builds the `GridSpec`, maps the centre through
stage 2's `lonlat_to_index`, clips the window to the grid, slices, unpacks, and
closes the file before returning. **No h5py handle escapes**; a test asserts the
file can be deleted immediately after the call on Windows, where an open handle
would make that fail.

A centre behind the limb raises `ValueError`. A centre visible but outside this
sector's rows and columns is **not** an error — the window clips, and if the
clipped window is empty `read_window` raises `ValueError` naming the sector.

### 4.2 `source.py`

```python
BUCKETS = {"G18": "noaa-goes18", "G19": "noaa-goes19"}
PRODUCTS = ("ABI-L2-ACMC", "ABI-L2-ACHAC")

@dataclass(frozen=True)
class GranuleRef:
    bucket: str; key: str; product: str; platform: str
    scan_start: datetime        # parsed from the key's `s` field
    size_bytes: int

async def list_hour(client, bucket, product, when) -> list[GranuleRef]
async def latest_ref(client, bucket, product, now) -> GranuleRef | None
async def ensure_cached(client, ref, cache_dir) -> Path
def evict(cache_dir, keep_per_product=6) -> int
def cache_dir() -> Path            # CAPTURE_DIR / "_cloudmap"
```

`latest_ref` lists the current UTC hour and, if that is empty or every key is
older than `now`, falls back to the previous hour. **Both hours, never more** —
a product that has been dead for two hours is a fault to report, not a gap to
paper over by walking back through the day.

## 5. Algorithms — exact

### 5.1 Listing

```
GET https://<bucket>.s3.amazonaws.com/?list-type=2&max-keys=400&prefix=<product>/<YYYY>/<DDD>/<HH>/
```

Anonymous; no credentials, no boto3. The response is S3 ListBucketResult XML in
namespace `http://s3.amazonaws.com/doc/2006-03-01/`. Each `<Contents>` carries
`<Key>` and `<Size>`.

**Handle `<IsTruncated>true</IsTruncated>`** by following
`<NextContinuationToken>` — at `max-keys=400` against 12 granules an hour it
will not happen, and an implementation that silently ignores truncation is the
kind that breaks when a product's cadence changes. One follow-up page is
enough; log and stop if a second is offered.

Parse the scan start from the key with:

```
r"_s(?P<y>\d{4})(?P<doy>\d{3})(?P<h>\d{2})(?P<m>\d{2})(?P<s>\d{2})(?P<ds>\d)_"
```

The final digit is tenths of a second; discard it. A key that does not match is
skipped, not fatal — NOAA occasionally leaves other objects under a prefix.

### 5.2 Caching

Cache path: `cache_dir() / product / <basename of key>`. Download to
`<name>.part` and `os.replace` into place, so a killed process never leaves a
truncated file that a later run treats as complete. If the destination already
exists and its size equals `ref.size_bytes`, return it without a request —
these objects are immutable once written.

**A size mismatch means re-download, not repair.** Delete and fetch again.

`evict` keeps the newest `keep_per_product` files per product directory by
filename sort (which is time order) and unlinks the rest. It never touches
`.part` files younger than an hour, and never removes a directory.

### 5.3 Bandwidth, measured

4.06 MB + 0.33 MB per 5-minute cycle, at 0.8-1.2 s per file. That is 53 MB an
hour if polled every cycle. **Poll every 10 minutes, not every 5** — the cloud
field's own pattern lifetime is under 40 minutes (design 2 section 2.6), so a
10-minute cadence loses nothing a 5-minute one would have caught, and halves
the traffic on a rig that is also carrying the relay tunnel. That is a stage 4
decision to make; recorded here so it is made deliberately.

## 6. Failure behaviour

Everything here is best-effort and must never take the rig down:

- **Any network failure** raises `CloudmapUnavailable` from `latest_ref` /
  `ensure_cached`. Callers degrade; nothing retries in a loop inside this
  module.
- **Logging is outcome-only**: the exception type, the product, and the
  bucket. **Never a URL and never coordinates** — the same rule
  `weather.py` follows, for the same reason (a lat/lon in a log is a
  geolocation leak).
- **Zero outbound requests when the caller does not ask.** There is no poller
  here and no module-level singleton; nothing in stage 3 fires on import.

## 7. Domain errors

`ValueError` for: a centre behind the limb; an empty clipped window; a
`half_rows`/`half_cols` below zero; an unknown variable name for the product;
an unknown platform key. `CloudmapUnavailable` for: h5py missing; any network
or S3 failure. `FileNotFoundError` propagates unchanged from a missing path.

## 8. Tests

`server/tests/test_cloudmap_granule.py` and `test_cloudmap_source.py`.

**Fixtures are built, not committed.** Each test writes a tiny HDF5 file with
h5py into `tmp_path`, shaped exactly like a real granule: a
`goes_imager_projection` scalar carrying the nine attributes of section 3.2,
packed `x`/`y` int16 axes with the measured offsets and scales, and small
integer variables with the measured `scale_factor` / `_FillValue`. A 40 x 40
grid is enough. Committing a 4 MB binary would be worse in every way, and a
built fixture fails loudly if the shape assumption drifts.

Every test name states the property.

1. `test_a_packed_variable_comes_back_in_physical_units` — a stored `uint16`
   of 32768 with the measured `Cloud_Probabilities` scale returns 0.50007…,
   not 32768.
2. `test_the_fill_value_becomes_nan_and_is_not_scaled` — a stored 65535 is
   NaN, **not** 65535 x scale (which would be 1.0002, a plausible-looking
   probability). This is the ordering bug the section 3.1 rule prevents.
3. `test_a_variable_without_scaling_is_returned_as_stored` — `BCM` values 0
   and 1 survive; its 255 fill becomes NaN.
4. `test_the_grid_spec_is_built_from_the_file_not_from_constants` — write a
   fixture with a deliberately odd `longitude_of_projection_origin` of −75.0
   and assert the returned spec carries it.
5. `test_the_satellite_height_adds_the_equatorial_radius` — assert
   `spec.sat_height_m == perspective_point_height + semi_major_axis` against
   the fixture's own numbers, and that it is not the bare
   `perspective_point_height`.
6. `test_the_sweep_axis_is_decoded_from_bytes` — the fixture stores
   `b"x"`; a spec built without decoding would fail `GridSpec`'s validator, so
   assert construction succeeds and `sweep_axis == "x"`.
7. `test_the_observation_time_is_the_j2000_midpoint` — `t = 840564456.2797`
   returns 2026-08-21T06:07:36Z (±1 s), tz-aware.
8. `test_the_window_is_clipped_at_the_grid_edge` — a centre two cells from the
   corner with `half_rows=10` returns a window that starts at row 0 and
   reports `row0 == 0`.
9. `test_value_at_takes_full_grid_indices_not_window_indices` — read a window
   at an offset, then assert `value_at` returns the same number the full-grid
   index should, and raises `IndexError` one cell outside.
10. `test_a_centre_behind_the_limb_is_refused` — `ValueError`.
11. `test_the_file_is_closed_before_the_call_returns` — `read_window`, then
    `Path.unlink()` in the same test. On Windows an escaped handle makes this
    raise `PermissionError`.
12. `test_the_module_imports_without_h5py` — with `h5py` masked in
    `sys.modules`, import succeeds, `HAVE_H5PY` is False, and `read_window`
    raises `CloudmapUnavailable` whose message names the `cloudmap` extra.
13. `test_a_key_is_parsed_into_its_scan_start` — the real key
    `OR_ABI-L2-ACMC-M6_G18_s20262330606176_e20262330608549_c20262330609298.nc`
    gives 2026-08-21T06:06:17Z, product `ABI-L2-ACMC`, platform `G18`.
14. `test_an_unrecognised_key_is_skipped_not_fatal` — a listing containing one
    junk key and two good ones returns two refs.
15. `test_the_newest_key_is_the_last_in_sort_order` — three keys out of order
    in the XML; `latest_ref` returns the newest.
16. `test_a_truncated_listing_follows_its_continuation_token` — a stubbed
    client returning `IsTruncated` then a second page yields keys from both.
17. `test_an_empty_current_hour_falls_back_one_hour_and_no_further` — assert
    exactly two requests were made and the result comes from the older hour;
    with both empty, `latest_ref` returns None having made exactly 2 requests.
18. `test_a_cached_file_of_the_right_size_is_not_refetched` — the stub client
    records zero GETs.
19. `test_a_cached_file_of_the_wrong_size_is_refetched` — one GET, and the
    file ends the right size.
20. `test_a_download_is_atomic` — make the stub raise mid-body; assert no file
    at the destination and no `.part` left readable as complete.
21. `test_eviction_keeps_the_newest_and_counts_what_it_removed`.
22. `test_nothing_reaches_the_network_unless_asked` — import both modules and
    construct everything constructible with a client that raises on any
    request (`weather.py`'s `_Boom` pattern); assert no call.
23. `test_a_failure_logs_no_url_and_no_coordinates` — force a failure, capture
    the log, assert the message contains neither "amazonaws" nor a decimal
    degree.

## 9. Invariants

- No h5py handle outlives `read_window`.
- `_FillValue` is compared against the RAW array, before scaling.
- The `GridSpec` comes from the file; no projection constant is hard-coded in
  either module.
- Nothing at module scope opens a socket, reads config, or touches the clock.
- Logs carry no URL and no coordinates.
- Both modules import with h5py absent.
- Cache writes are atomic (`.part` then `os.replace`).

## 10. Seams for stage 4

Stage 4 asks for two windows around the site — `ABI-L2-ACMC` for
`Cloud_Probabilities` and `ABI-L2-ACHAC` for `HT` — on their own grids, and
walks a ray through them with stage 1's `pierce_point` and stage 2's
`lonlat_to_index`. It owns the poll cadence (section 5.3), the decision of
which platform to use (GOES-18 from the western United States, design 2
section 2.1), and every judgement about what the numbers mean. Stage 3 knows
nothing about clouds.
