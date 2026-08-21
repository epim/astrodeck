"""The GOES-R ABI fixed grid (stage 2) -- latitude and longitude to pixel.

No I/O, no network, no h5py, no numpy, no config, no rig. Every function total
and deterministic; stdlib ``math`` only, plus stage 1's sphere radius for the
single function that measures a distance on the ground.

Coordinate contract (design section 4), on top of stage 1's:

- scan angles: radians, named ``x_rad`` / ``y_rad``. ``x`` is the east-west
  (column) angle, ``y`` the north-south (row) angle.
- grid indices: ``(row, col)``, both 0-based, row 0 NORTH, col 0 WEST.
- ellipsoid: GRS80, taken from the granule via :class:`GridSpec` and never
  hard-coded in the algorithm.
- satellite radius: ``sat_height_m`` is measured from the EARTH'S CENTRE, i.e.
  ``perspective_point_height + semi_major_axis``. The granule's
  ``perspective_point_height`` alone is the height above the surface and using
  it is a 6378 km error.
- sweep axis: GOES sweeps about x. EUMETSAT sweeps about y. It is a field, not
  an assumption, and only "x" is implemented.

STAGE 2 DOES NOT USE STAGE 1'S SPHERE. The ABI grid is defined on an ellipsoid
by the instrument's own product definition; substituting R=6371.0088 misplaces
pixels by kilometres. Physical geometry and an instrument grid definition are
two different things, and mixing them is the single most likely bug here. The
contract is: stage 1 is a sphere, stage 2 is an ellipsoid, and nothing crosses
between them except latitude, longitude and height. :func:`pixel_size_km` is
the one deliberate exception, and it is measuring the ground, not the grid.

Two kinds of non-answer, and they are not interchangeable:

- ``None`` means "not on the visible earth" -- behind the limb going one way,
  the ray missing the disk going the other. That is a legitimate answer about
  a real place, so it does not raise, unlike stage 1's domain guards where an
  out-of-domain altitude is a caller bug.
- Out of THIS sector is a different fact and a different call. A visible point
  west of the sector gets its (negative) index anyway and :func:`in_grid` says
  no. Folding that into ``None`` would tell stage 4 that the sub-satellite
  point -- the most visible pixel on the hemisphere -- does not exist.

The projection depends only on the satellite, never on the sampling, so the
2 km mask grid and the 10 km cloud-top grid return IDENTICAL scan angles for
the same latitude and longitude and differ only in index. Nothing in
:func:`lonlat_to_scan` or :func:`scan_to_lonlat` may read a sampling field.

Algorithms are GOES-R Product User Guide vol. 5 section 5.1.2.8, with two
deliberate departures from design section 7. Each is argued in full in the
function that makes it, and both were found by verifying the first draft rather
than by writing it:

- :func:`lonlat_to_scan` uses the EXACT limb. Design section 7.1's visibility
  test, which is the PUG's, is an algebraic approximation to the limb, and it
  reports ground the satellite cannot see as visible -- a band 21 km wide at
  the equator, 28 km at latitude 77, and up to 43 km deep.
- :func:`scan_to_lonlat` evaluates the same discriminant in a rearranged form.
  Written as design section 7.2 writes it, ``b*b - 4*a*c`` loses every
  significant digit to cancellation at grazing incidence.

Design sections 7.1, 7.2 and 7.4, test 3 of section 10, and the first invariant
of section 11 all need amending to match.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Stage 1's sphere, for pixel_size_km ONLY -- see its docstring. Imported
# rather than redeclared so the two modules cannot drift apart on the radius.
#
# _normalise_lon_deg is private to stage 1 and imported anyway, deliberately.
# The longitude contract's boundary rule (exactly -180 becomes +180) is shared,
# and design section 7.2 records that this was the one mutation of 26 that
# survived stage 1's first test suite. Two copies of a rule that subtle will
# eventually disagree; promoting it to a public name would mean editing stage 1,
# which stage 2 is explicitly not allowed to touch.
from .geometry import EARTH_RADIUS_KM, _normalise_lon_deg

__all__ = [
    "GridSpec",
    "lonlat_to_scan",
    "scan_to_lonlat",
    "scan_to_index",
    "index_to_scan",
    "lonlat_to_index",
    "index_to_lonlat",
    "in_grid",
    "pixel_size_km",
]


@dataclass(frozen=True)
class GridSpec:
    """Everything the ABI fixed-grid transform needs, all of it from a granule.

    Stage 3 reads every field from the file's own ``goes_imager_projection``
    attributes and its ``x``/``y`` axis packing -- ``x_scale_rad`` is the axis
    variable's ``scale_factor`` and ``x_offset_rad`` its ``add_offset``.
    Nothing here is hard-coded, so one instance serves GOES-18, GOES-19, any
    sector and both resolutions without a branch.

    ``y_scale_rad`` is NEGATIVE for GOES: row increases southward. Do not take
    its absolute value anywhere. A sign error there mirrors the image
    north-south and every check made at the sub-satellite point still passes,
    because the origin row is its own reflection.

    Validation runs at construction (design section 8), so a malformed grid
    cannot be built, passed around and used somewhere far from the mistake.
    """

    sat_height_m: float
    r_eq_m: float
    r_pol_m: float
    lon_origin_deg: float
    sweep_axis: str
    x_offset_rad: float
    x_scale_rad: float
    n_cols: int
    y_offset_rad: float
    y_scale_rad: float
    n_rows: int

    def __post_init__(self) -> None:
        if self.sweep_axis != "x":
            raise ValueError(
                "sweep_axis must be 'x'; got "
                + repr(self.sweep_axis)
                + ". GOES sweeps about x and only that form is implemented; "
                "a y-sweep instrument fed through it returns a plausible "
                "scan angle that is wrong by the difference between the two "
                "conventions"
            )
        if self.n_rows < 1 or self.n_cols < 1:
            raise ValueError(
                "n_rows and n_cols must both be >= 1, got "
                + repr(self.n_rows)
                + " and "
                + repr(self.n_cols)
            )
        if self.x_scale_rad == 0.0 or self.y_scale_rad == 0.0:
            raise ValueError(
                "x_scale_rad and y_scale_rad must be non-zero, got "
                + repr(self.x_scale_rad)
                + " and "
                + repr(self.y_scale_rad)
            )
        if self.r_pol_m > self.r_eq_m:
            raise ValueError(
                "r_pol_m "
                + repr(self.r_pol_m)
                + " exceeds r_eq_m "
                + repr(self.r_eq_m)
                + "; the earth is oblate, not prolate"
            )
        if self.sat_height_m <= self.r_eq_m:
            raise ValueError(
                "sat_height_m "
                + repr(self.sat_height_m)
                + " is at or below r_eq_m "
                + repr(self.r_eq_m)
                + "; it is measured from the earth's CENTRE, so it is "
                "perspective_point_height + semi_major_axis"
            )


def _clamp_unit(x: float) -> float:
    """Clamp to [-1, 1] so rounding cannot push asin out of its domain.

    The argument this guards, ``-sy / |s|``, is a direction cosine and is
    mathematically in [-1, 1] already, so this is a float-rounding guard and
    NOT the silent clamping of an out-of-domain input. Same idiom, and same
    reasoning, as stage 1's.
    """
    if x < -1.0:
        return -1.0
    if x > 1.0:
        return 1.0
    return x


def _great_circle_km(
    lat1_deg: float, lon1_deg: float, lat2_deg: float, lon2_deg: float
) -> float:
    """Haversine separation on STAGE 1'S SPHERE, kilometres.

    Used only by :func:`pixel_size_km`. It is measuring how far apart two
    points on the ground are, which is stage 1's question on stage 1's earth,
    not part of the grid definition.
    """
    p1 = math.radians(lat1_deg)
    p2 = math.radians(lat2_deg)
    dp = p2 - p1
    dl = math.radians(lon2_deg - lon1_deg)
    a = (
        math.sin(dp / 2.0) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


def lonlat_to_scan(
    spec: GridSpec, lat_deg: float, lon_deg: float
) -> tuple[float, float] | None:
    """Scan angles ``(x_rad, y_rad)`` of a point, or ``None`` behind the limb.

    Geodetic latitude goes to geocentric first -- that conversion IS the
    ellipsoid, and dropping it is what "using the sphere" looks like in
    practice. The result depends on ``spec``'s satellite and ellipsoid only,
    never on its sampling, so both products give the same answer.

    The visibility test is not a nicety. Without it the arithmetic returns a
    perfectly plausible scan angle for a point on the far side of the planet:
    (40, -20) comes back as a pixel somewhere over the Pacific.

    DEVIATION FROM DESIGN SECTION 7.1, deliberate. The design's visibility
    test, which is the PUG's, is ``H * (H - s_x) >= s_y^2 + ratio * s_z^2``.
    That is not the limb. Scale z by ``r_eq / r_pol`` and the ellipsoid becomes
    a sphere of radius ``r_eq`` while the satellite, which sits at z = 0, does
    not move; the map is linear, so it carries the line of sight to the line of
    sight, and a point on that sphere is visible exactly when its outward
    normal still faces the satellite. Writing ``p_x = H - s_x`` for the point's
    own distance along the axis toward the satellite, that condition is
    ``H * p_x >= r_eq^2``. For a point ON the ellipsoid the design's right-hand
    side is identically ``r_eq^2 - p_x^2``, so the design's test reads
    ``p_x^2 + H * p_x >= r_eq^2`` and is weaker by exactly that ``p_x^2``: it
    admits ``p_x`` down to 943694.191 m against the true 964815.416 m.
    Confirmed by ray trace rather than by a rival formula -- cast the ray from
    the satellite through (40, -58.20) and intersect it with the ellipsoid, and
    it strikes the earth 29.3 km short of the point being asked about. Measured
    along a parallel the band the design admits is 21.4 km wide at the equator,
    21.5 km at latitude 40 and 28.1 km at latitude 77, and a point on its outer
    edge is occluded by 42.7 km at every latitude. A CONUS sector hides the
    consequence, since none of that band is in sector, but on the full-disk
    grid 2214 sampled points of it come back with an IN-GRID index and nothing
    downstream screens them.

    The form below is the exact one. It costs a term rather than adding one --
    ``ratio`` is no longer needed in this function -- and it leaves all five of
    design section 9.2's golden scan angles BIT-IDENTICAL, every one of them
    being far from the limb. What it changes is the round trip: over the whole
    visible disk the worst disagreement between this function and
    :func:`scan_to_lonlat` falls from 1.24 deg, 41 km on the ground, to
    2.2e-09 deg. Design section 7.1's formula, and design section 10 test 3's
    golden of -58.12 for the 40th parallel's limb, must both be amended. The
    true limb there is at -58.37295.
    """
    # ``e2`` is exactly ``1 - flat``, and ``flat`` is computed in the form the
    # design states rather than derived from its reciprocal, which in floating
    # point is not quite its reciprocal.
    flat = (spec.r_pol_m * spec.r_pol_m) / (spec.r_eq_m * spec.r_eq_m)
    e2 = 1.0 - flat

    phi = math.radians(lat_deg)
    lam = math.radians(lon_deg)
    lam0 = math.radians(spec.lon_origin_deg)

    # Geodetic -> geocentric. THIS conversion is the ellipsoid; drop it and the
    # module has quietly become spherical.
    phi_c = math.atan(flat * math.tan(phi))
    r_c = spec.r_pol_m / math.sqrt(1.0 - e2 * math.cos(phi_c) ** 2)

    s_x = spec.sat_height_m - r_c * math.cos(phi_c) * math.cos(lam - lam0)
    s_y = -r_c * math.cos(phi_c) * math.sin(lam - lam0)
    s_z = r_c * math.sin(phi_c)

    # The exact limb; see the DEVIATION block above. The ellipsoid still
    # reaches this test, through ``r_c`` and ``phi_c`` inside ``s_x``, which is
    # why a sphere of the same equatorial radius moves the limb by 0.016 deg at
    # latitude 40 and 0.084 deg at latitude 70.
    if spec.sat_height_m * (spec.sat_height_m - s_x) < (
        spec.r_eq_m * spec.r_eq_m
    ):
        return None

    y_rad = math.atan(s_z / s_x)
    x_rad = math.asin(
        _clamp_unit(-s_y / math.sqrt(s_x * s_x + s_y * s_y + s_z * s_z))
    )
    return (x_rad, y_rad)


def scan_to_lonlat(
    spec: GridSpec, x_rad: float, y_rad: float
) -> tuple[float, float] | None:
    """Ground point ``(lat_deg, lon_deg)`` under a scan angle, or ``None``.

    ``None`` means the ray missed the earth entirely -- a corner of a full-disk
    array, or any scan angle past the limb.

    THE NEAR ROOT. ``(-b - sqrt(d)) / 2a`` is the satellite-facing
    intersection; ``-b + sqrt(d)`` is the back of the planet and is
    geometrically just as valid, so nothing in the quadratic rejects it. For
    (40, -105) from GOES-18 the two are 38321 km and 45328 km away, and the far
    one sits 116 degrees from the sub-satellite point -- over the horizon of
    the satellite that is supposedly looking at it. Note also that the latitude
    and longitude recovery below assumes the near root: at the far root
    ``sat_height_m - s_x`` goes negative and the ``atan`` lands in the wrong
    quadrant, so a far-root answer is not merely the wrong point, it is a
    nonsense one.

    Longitude comes back normalised to ``(-180, +180]``, stage 1's contract and
    stage 1's boundary rule. The northwest corner of a CONUS sector needs it:
    its raw arithmetic gives -184.3764.

    DEVIATION FROM DESIGN SECTION 7.2, deliberate, and the same number in exact
    arithmetic. Written literally as ``d = b*b - 4*a*c`` the discriminant
    subtracts two quantities near 6.95e+15 whose leading sixteen digits agree
    at grazing incidence: one ULP there is 1.0, and the difference comes out as
    2.0, so every significant digit has cancelled away. Substituting
    ``c = H^2 - r_eq^2`` and collecting gives the identity used below, whose
    largest intermediate is 4.07e+13 with an ULP of 0.0078 and which returns
    2.375 for that same ray. Measured over the whole visible disk the worst
    round trip against :func:`lonlat_to_scan` falls from 1.7e-07 deg to
    2.2e-09 deg. In sector the two forms differ by at most 2.5e-12 deg, which
    is 0.3 mm on the ground, so no golden vector moves at any tolerance this
    module is checked against.

    WHAT STILL DOES NOT HOLD, and design section 11's first invariant has to
    say so. The round trip is exact to 1e-09 deg everywhere except within
    0.032 deg of the limb, and within 2e-05 deg of the limb this function can
    report a genuinely visible point as missing the earth entirely. That
    residue is not a formula error: at tangency the ground position is
    stationary in the scan angle, so the inverse is ill-conditioned there and
    no rearrangement removes it. An independent vector ray/ellipsoid
    formulation lands in the same place to within a factor of two.
    """
    ratio = (spec.r_eq_m * spec.r_eq_m) / (spec.r_pol_m * spec.r_pol_m)
    h = spec.sat_height_m
    cos_x = math.cos(x_rad)
    sin_x = math.sin(x_rad)
    cos_y = math.cos(y_rad)
    sin_y = math.sin(y_rad)

    a = sin_x * sin_x + cos_x * cos_x * (cos_y * cos_y + ratio * sin_y * sin_y)
    b = -2.0 * h * cos_x * cos_y
    # ``b*b - 4*a*c`` with ``c = h*h - r_eq^2``, collected so that it cannot
    # cancel. See the DEVIATION block above.
    d = 4.0 * (
        spec.r_eq_m * spec.r_eq_m * a
        - h * h * (sin_x * sin_x + ratio * cos_x * cos_x * sin_y * sin_y)
    )
    if d < 0.0:
        return None

    r_s = (-b - math.sqrt(d)) / (2.0 * a)
    # A ray pointing away from the earth still has two real intersections, both
    # of them BEHIND the satellite, and the near root is then negative. It
    # takes ``|x|`` or ``|y|`` past 90 degrees to get there -- column 57349 of
    # a 2500-column sector -- so no caller can, but "the ray missed the earth"
    # is what this function promises to say when the ray does not reach the
    # earth in front of the instrument, and without this it answers with the
    # sub-satellite point for a look straight out into space.
    if r_s < 0.0:
        return None
    s_x = r_s * cos_x * cos_y
    s_y = -r_s * sin_x
    s_z = r_s * cos_x * sin_y

    lat_deg = math.degrees(
        math.atan(ratio * s_z / math.sqrt((h - s_x) ** 2 + s_y * s_y))
    )
    lon_deg = math.degrees(
        math.radians(spec.lon_origin_deg) - math.atan(s_y / (h - s_x))
    )
    return (lat_deg, _normalise_lon_deg(lon_deg))


def scan_to_index(spec: GridSpec, x_rad: float, y_rad: float) -> tuple[int, int]:
    """Nearest cell centre to a scan angle. Never ``None``, never clamped.

    ``round`` is Python's, which breaks ties to EVEN rather than upward: a scan
    angle exactly half a cell past column 100 stays at 100 while the same
    displacement past column 101 goes to 102. That only matters on a pixel
    boundary, where either neighbour is defensible, and it is pinned by a named
    test so a future change is a deliberate one.
    """
    row = round((y_rad - spec.y_offset_rad) / spec.y_scale_rad)
    col = round((x_rad - spec.x_offset_rad) / spec.x_scale_rad)
    return (row, col)


def index_to_scan(spec: GridSpec, row: int, col: int) -> tuple[float, float]:
    """Scan angle at the centre of a cell.

    ``scan_to_index(spec, *index_to_scan(spec, row, col))`` returns ``(row,
    col)`` again; the other way round is a quantisation and does not.
    Out-of-range indices extrapolate rather than raise -- the arithmetic is
    defined for them and stage 4 walks off the edge of a sector routinely.
    """
    return (
        spec.x_offset_rad + col * spec.x_scale_rad,
        spec.y_offset_rad + row * spec.y_scale_rad,
    )


def lonlat_to_index(
    spec: GridSpec, lat_deg: float, lon_deg: float
) -> tuple[int, int] | None:
    """Cell containing a point, or ``None`` if it is behind the limb.

    ``None`` here means one thing only: not on the visible earth. A point that
    IS visible but falls outside this sector's rows and columns gets its index
    anyway, and it may be negative or past the end -- ask :func:`in_grid`
    whether to use it. Clamping instead would silently hand back the edge
    pixel's cloud state for a place the sector never saw.
    """
    scan = lonlat_to_scan(spec, lat_deg, lon_deg)
    if scan is None:
        return None
    return scan_to_index(spec, scan[0], scan[1])


def index_to_lonlat(
    spec: GridSpec, row: int, col: int
) -> tuple[float, float] | None:
    """Ground point under a cell centre, or ``None`` if that ray misses.

    Out-of-range indices are extrapolated rather than refused: the arithmetic
    is defined for them and stage 4 walks off the edge of a sector routinely.
    They come back ``None`` once the extrapolated angle leaves the disk.
    """
    x_rad, y_rad = index_to_scan(spec, row, col)
    return scan_to_lonlat(spec, x_rad, y_rad)


def in_grid(spec: GridSpec, row: int, col: int) -> bool:
    """Whether a cell is inside this sector's array bounds."""
    return 0 <= row < spec.n_rows and 0 <= col < spec.n_cols


def pixel_size_km(spec: GridSpec, row: int, col: int) -> tuple[float, float]:
    """Ground spacing ``(ns_km, ew_km)`` of a cell, on stage 1's sphere.

    Great-circle distance between the cell centres either side, halved. This is
    the one place stage 2 legitimately uses the sphere: it is measuring a
    distance on the ground, which is stage 1's job, not defining the grid.

    WHY THIS EXISTS. The "2 km" in the product name is at nadir and nowhere
    else. Over Colorado the same cell is 3.50 km north-south by 2.75 km
    east-west; at (50, -160) it is 4.34 by 2.48. Converting pixel
    displacements with the nominal 2 km understated a measured cloud speed by
    33 percent during the work that produced the design. Any stage that turns
    pixels into kilometres must call this.

    At the edge of the grid the neighbour clamps to the grid and the divisor
    halves with it.

    RAISES in three cases rather than returning a number. The first is outside
    anything the design says. The other two are DEVIATIONS FROM DESIGN SECTION
    7.4, which promises the clamp leaves this function total for every in-grid
    cell -- and the promise is the part that is wrong, because a ground spacing
    is a distance between two neighbouring cell centres and these are the cases
    where no such pair exists:

    - A cell outside the grid. Clamping the neighbours of row -5 gives
      ``row_hi - row_lo`` of -4, so the function would return a NEGATIVE
      distance and stage 5 would turn it into a cloud drifting backwards.
    - A grid one cell across on an axis. Design section 8 rejects only
      ``n_rows`` or ``n_cols`` below 1, so a 1 x 1 grid or a single-row strip
      constructs, ``in_grid`` says yes, and both clamped neighbours collapse
      onto the same cell. Before this guard the design's totality claim came
      out as a ``ZeroDivisionError`` escaping from arithmetic. Stage 3 reading
      a subsetted granule is how such a grid arrives.
    - A cell whose neighbouring centres do not see the earth. Cannot happen on
      a CONUS sector, where every cell is on the disk, but a full-disk grid's
      array corners are off it -- ``ABI-L2-ACMF``, which design section 2.2
      names as a product stage 3 may read. There is no ground distance to
      report there and the signature has no room for ``None``.
    """
    if not in_grid(spec, row, col):
        raise ValueError(
            "pixel_size_km needs an in-grid cell; ("
            + repr(row)
            + ", "
            + repr(col)
            + ") is outside "
            + repr(spec.n_rows)
            + "x"
            + repr(spec.n_cols)
        )

    row_lo = max(0, row - 1)
    row_hi = min(spec.n_rows - 1, row + 1)
    col_lo = max(0, col - 1)
    col_hi = min(spec.n_cols - 1, col + 1)
    if row_hi == row_lo or col_hi == col_lo:
        raise ValueError(
            "pixel_size_km has no ground distance for cell ("
            + repr(row)
            + ", "
            + repr(col)
            + "): a "
            + repr(spec.n_rows)
            + "x"
            + repr(spec.n_cols)
            + " grid is one cell across on an axis, so it has no pair of "
            "neighbouring cell centres to measure between"
        )

    north = index_to_lonlat(spec, row_lo, col)
    south = index_to_lonlat(spec, row_hi, col)
    west = index_to_lonlat(spec, row, col_lo)
    east = index_to_lonlat(spec, row, col_hi)
    if None in (north, south, west, east):
        raise ValueError(
            "pixel_size_km has no ground distance for cell ("
            + repr(row)
            + ", "
            + repr(col)
            + "): a neighbouring cell centre does not see the earth"
        )

    ns_km = _great_circle_km(north[0], north[1], south[0], south[1]) / (
        row_hi - row_lo
    )
    ew_km = _great_circle_km(west[0], west[1], east[0], east[1]) / (
        col_hi - col_lo
    )
    return (ns_km, ew_km)
