"""Reading a GOES ABI granule off disk, stage 3.

Every test here pins one specific way the reader can be plausibly wrong: raw
counts handed back as if they were physical units, a fill scaled into a
plausible-looking probability, an ``add_offset`` never applied, a projection
taken from a constant instead of from the file, ``perspective_point_height``
used as the satellite's distance from the earth's CENTRE, a sweep axis left as
bytes, a window that reports its own indices instead of the grid's, a window
whose rows and columns have been swapped somewhere, an h5py handle that
outlives the call.

Fixtures are BUILT, never committed (design section 8). Each test writes a
40 x 48 granule into ``tmp_path`` packed the way the real 1500 x 2500 files
are. A 4 MB binary in git would be unreadable and unfixable, and a built
fixture fails loudly if the shape assumption drifts.

THE FIXTURE IS NOT SQUARE, which is a deliberate departure from design section
8's 40 x 40. A square grid with symmetric windows makes four independent
row/column transpositions invisible at once -- ``n_rows``/``n_cols`` read off
the wrong axis variable, ``row0``/``col0`` swapped on the returned window, the
slice bounds swapped, and ``half_rows`` applied to the columns. All four were
measured surviving the square version of this suite. Real granules are
1500 x 2500 and stage 4 asks for asymmetric windows, which is exactly why the
API takes ``half_rows`` and ``half_cols`` separately.

The packing constants below are design section 3's measured values, and they
are exactly the float32 widenings of what a real granule stores -- float32
1.5261e-05 read back as float64 IS 1.5260999134625308e-05 -- so the goldens
can be asserted exactly rather than to a tolerance. The fixture writes them as
float32 attributes for that reason; storing them as float64 would quietly make
this suite check arithmetic no granule performs.

The 40 x 48 grid is the northwest corner of the real GOES-18 CONUS sector, so
it sits out over the Aleutians and crosses the antimeridian. The lat/lon
literals below were computed ONCE from that packing and are pinned here: a
test that re-derives its own input through the module it is testing moves
whenever the module does. They are unchanged by the widening, because the
packing is index-based and a column's ground point does not depend on how many
columns come after it.
"""
from __future__ import annotations

import importlib.util
import math
import re
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from astrodeck.cloudmap import granule as granule_mod
from astrodeck.cloudmap.granule import (
    CloudmapUnavailable,
    SiteOutsideSector,
    observed_at,
    parse_granule_name,
    read_grid_spec,
    read_window,
)

# --------------------------------------------------------------- the fixture

N_ROWS = 40
N_COLS = 48

# goes_imager_projection, design section 3.2. GRS80 + the GOES-R orbit.
PERSPECTIVE_HEIGHT_M = 35786023.0
R_EQ_M = 6378137.0
R_POL_M = 6356752.31414
LON_ORIGIN_DEG = -137.0            # GOES-18
SAT_HEIGHT_M = PERSPECTIVE_HEIGHT_M + R_EQ_M      # 42164160.0, from the CENTRE

# x/y axis packing of the real 2 km CONUS sector, float32. Stage 3's design
# says WHICH attributes the axes are built from (section 3.2) but does not
# tabulate their values the way section 3.1 tabulates the variable packing;
# these are the measured ones, from the same 06:06 granule as `t` below.
X_OFFSET = -0.06997200101613998
X_SCALE = 5.6000000768108293e-05
Y_OFFSET = 0.12821200489997864
Y_SCALE = -5.6000000768108293e-05  # NEGATIVE: row increases southward

# Cloud_Probabilities packing, design section 3.1.
CP_SCALE = 1.5260999134625308e-05
CP_OFFSET = 0.0
FILL_U16 = 65535
FILL_U8 = 255

# HT packing, design section 3.1 -- scale and fill measured, units metres.
HT_SCALE = 0.30520370602607727
#: SYNTHETIC, and the one number in this fixture that is. The real HT carries
#: ``add_offset = 0.0``, and so does Cloud_Probabilities: NO variable this
#: stage reads has a non-zero offset. An attribute that is zero in every
#: fixture is an attribute whose application line can be deleted outright and
#: nothing notices -- measured, it survived the whole suite -- so HT is given
#: one here purely to prove the file is read rather than assumed.
HT_OFFSET = 1000.0

#: ``t`` of the granule this fixture imitates: 2026-08-21T06:07:36.2797Z.
T_SECONDS = 840564456.2797
T_UNITS = "seconds since 2000-01-01 12:00:00"

#: Ground points of fixture cells, computed once from the packing above.
CELL_2_2 = (53.39175461331048, 175.9140278124869)
CELL_20_20 = (52.45650430291552, 178.31618849052433)
#: The far corner: row 37 of 40 and column 45 of 48, so a window around it is
#: limited by BOTH ``n_rows - 1`` and ``n_cols - 1``.
CELL_37_45 = (51.58617710307323, -179.2699398507478)
#: The same cell (20, 20) when the fixture's projection origin is moved to -75.
CELL_20_20_AT_75 = (52.45650430291552, -119.68381150947566)
#: Visible from GOES-18 -- it is the sub-satellite point, the most visible
#: pixel on the hemisphere -- but at cell (2290, 1250), nowhere near this
#: sector: outside on BOTH axes.
SUB_SATELLITE = (0.0, -137.0)
#: Cell (20, 200): the ROW is inside this sector and only the column is out.
ROW_INSIDE_COLUMN_OUT = (51.59790663582933, -172.57255780177576)
#: Cell (200, 20): the COLUMN is inside this sector and only the row is out.
COLUMN_INSIDE_ROW_OUT = (45.62253928972488, -173.84364927066613)
#: Behind the limb from -137: the arithmetic still returns a plausible pixel
#: without stage 2's visibility test.
BEHIND_THE_LIMB = (0.0, 40.0)

#: A real key's basename. ``product`` and ``platform`` come from it.
GRANULE_NAME = (
    "OR_ABI-L2-ACMC-M6_G18_s20262330606176"
    "_e20262330608549_c20262330609298.nc"
)
#: The scan START in that name. NOT the observation time; see design 3.3.
SCAN_START = datetime(2026, 8, 21, 6, 6, 17, tzinfo=timezone.utc)


@pytest.fixture
def make_granule(tmp_path):
    """Writes a tiny ABI granule and returns its path.

    h5py is the optional ``cloudmap`` extra, so every test that needs a file
    skips without it -- and ``test_the_module_imports_without_h5py``
    deliberately does NOT take this fixture, because it is the one test whose
    whole subject is h5py being absent.
    """
    h5py = pytest.importorskip(
        "h5py", reason="h5py is the optional 'cloudmap' extra"
    )

    def _make(
        name: str = GRANULE_NAME,
        *,
        lon_origin_deg: float = LON_ORIGIN_DEG,
        t_seconds: float = T_SECONDS,
        t_units: str = T_UNITS,
    ) -> Path:
        path = tmp_path / name
        with h5py.File(path, "w") as f:
            proj = f.create_dataset(
                "goes_imager_projection", data=np.int32(-2147483647)
            )
            # A real granule's nine projection attributes -- the five design
            # section 3.2 reads, plus the four it does not, so that reading a
            # neighbour by mistake finds something. Text is written as
            # np.bytes_ because that is what netCDF's NC_CHAR looks like
            # through h5py -- writing a str would return a str and quietly
            # retire the decode this suite is here to check.
            proj.attrs["long_name"] = np.bytes_("GOES-R ABI fixed grid projection")
            proj.attrs["grid_mapping_name"] = np.bytes_("geostationary")
            proj.attrs["perspective_point_height"] = np.array(
                [PERSPECTIVE_HEIGHT_M]
            )
            proj.attrs["semi_major_axis"] = np.array([R_EQ_M])
            proj.attrs["semi_minor_axis"] = np.array([R_POL_M])
            proj.attrs["inverse_flattening"] = np.array([298.257222096])
            proj.attrs["latitude_of_projection_origin"] = np.array([0.0])
            proj.attrs["longitude_of_projection_origin"] = np.array(
                [lon_origin_deg]
            )
            proj.attrs["sweep_angle_axis"] = np.bytes_("x")

            # x is the COLUMN axis and y the ROW axis, and they are different
            # lengths here so that reading either off the other is visible.
            x = f.create_dataset("x", data=np.arange(N_COLS, dtype=np.int16))
            x.attrs["scale_factor"] = np.array([X_SCALE], dtype=np.float32)
            x.attrs["add_offset"] = np.array([X_OFFSET], dtype=np.float32)
            x.attrs["units"] = np.bytes_("rad")
            y = f.create_dataset("y", data=np.arange(N_ROWS, dtype=np.int16))
            y.attrs["scale_factor"] = np.array([Y_SCALE], dtype=np.float32)
            y.attrs["add_offset"] = np.array([Y_OFFSET], dtype=np.float32)
            y.attrs["units"] = np.bytes_("rad")

            t = f.create_dataset("t", data=np.float64(t_seconds))
            t.attrs["units"] = np.bytes_(t_units)

            # Every cell distinguishable: raw count = row*100 + col, so a
            # window that slices the wrong rectangle cannot come back looking
            # right, and so can a transposed one (row*100 + col is not
            # symmetric under a swap).
            counts = (
                np.arange(N_ROWS, dtype=np.uint16)[:, None] * 100
                + np.arange(N_COLS, dtype=np.uint16)[None, :]
            ).astype(np.uint16)
            counts[10, 10] = 32768
            counts[11, 11] = FILL_U16
            cp = f.create_dataset("Cloud_Probabilities", data=counts)
            cp.attrs["scale_factor"] = np.array([CP_SCALE], dtype=np.float32)
            cp.attrs["add_offset"] = np.array([CP_OFFSET], dtype=np.float32)
            cp.attrs["_FillValue"] = np.array([FILL_U16], dtype=np.uint16)
            cp.attrs["units"] = np.bytes_("1")

            # HT, the one variable here with a NON-ZERO add_offset. See the
            # HT_OFFSET comment: nothing else in either product has one, so
            # without it the offset line is dead code.
            ht = f.create_dataset("HT", data=counts)
            ht.attrs["scale_factor"] = np.array([HT_SCALE], dtype=np.float32)
            ht.attrs["add_offset"] = np.array([HT_OFFSET], dtype=np.float32)
            ht.attrs["_FillValue"] = np.array([FILL_U16], dtype=np.uint16)
            ht.attrs["units"] = np.bytes_("m")

            bcm = (
                (np.arange(N_ROWS)[:, None] + np.arange(N_COLS)[None, :]) % 2
            ).astype(np.uint8)
            bcm[3, 4] = FILL_U8
            b = f.create_dataset("BCM", data=bcm)
            b.attrs["_FillValue"] = np.array([FILL_U8], dtype=np.uint8)

            # DQF as a SIGNED byte whose _FillValue attribute is 255, which
            # int8 cannot hold. That is the netCDF unsigned-byte convention
            # seen through h5py, and `raw == 255` on an int8 array is silently
            # all-False -- so the fill would survive as -1.0, which reads as a
            # perfectly ordinary quality flag downstream.
            dqf = np.zeros((N_ROWS, N_COLS), dtype=np.int8)
            dqf[0, 1] = 1
            dqf[3, 5] = -1
            d = f.create_dataset("DQF", data=dqf)
            d.attrs["_FillValue"] = np.array([FILL_U8], dtype=np.uint8)
        return path

    return _make


def _whole_grid(path, variables, centre=CELL_20_20):
    """The window that clips to the entire fixture grid, origin (0, 0)."""
    return read_window(
        path,
        variables,
        centre_lat_deg=centre[0],
        centre_lon_deg=centre[1],
        half_rows=N_ROWS,
        half_cols=N_COLS,
    )


# ------------------------------------------------------------- the unpacking


def test_a_packed_variable_comes_back_in_physical_units(make_granule):
    """h5py does not unpack; this module does.

    The stored count at (10, 10) is 32768. Handed back unscaled it is a number
    54000 times too large that nothing downstream can tell from data.
    """
    win = _whole_grid(make_granule(), ("Cloud_Probabilities",))

    assert win.data["Cloud_Probabilities"].dtype == np.float64
    assert win.data["Cloud_Probabilities"].shape == (N_ROWS, N_COLS)
    assert win.value_at("Cloud_Probabilities", 10, 10) == 0.5000724196434021
    assert win.value_at("Cloud_Probabilities", 0, 1) == 1.5260999134625308e-05


def test_the_fill_value_becomes_nan_and_is_not_scaled(make_granule):
    """The fill is compared against the RAW array, before any scaling.

    Scale first and the fill at (11, 11) comes out as 65535 * 1.5261e-05 =
    1.0001295782876696 -- a probability just over 1, which looks like a
    rounding artefact rather than the absence of data, and which a clip to
    [0, 1] downstream would turn into a confident "overcast".
    """
    win = _whole_grid(make_granule(), ("Cloud_Probabilities",))

    assert math.isnan(win.value_at("Cloud_Probabilities", 11, 11))
    # Its neighbours are untouched: the mask is the fill, not a region.
    assert win.value_at("Cloud_Probabilities", 11, 12) == pytest.approx(
        1112 * CP_SCALE
    )
    assert np.count_nonzero(np.isnan(win.data["Cloud_Probabilities"])) == 1


def test_a_variable_without_scaling_is_returned_as_stored(make_granule):
    """``BCM`` and ``DQF`` carry a fill and no scaling; the flags survive.

    A ``scale_factor`` invented for them (1.0 is the tempting default) is
    harmless here and catastrophic the day a variable's real scale differs
    from it, so the two attributes are read independently and each is applied
    only when the file carries it.
    """
    win = _whole_grid(make_granule(), ("BCM", "DQF"))

    assert win.value_at("BCM", 0, 0) == 0.0
    assert win.value_at("BCM", 0, 1) == 1.0
    assert math.isnan(win.value_at("BCM", 3, 4))

    # The signed-byte fill: -1 stored, 255 declared. Compared naively it is
    # never found, and -1.0 flows on as a quality flag.
    assert win.value_at("DQF", 0, 1) == 1.0
    assert win.value_at("DQF", 0, 0) == 0.0
    assert math.isnan(win.value_at("DQF", 3, 5))


def test_the_add_offset_is_applied_after_the_scale_and_never_to_the_fill(
    make_granule,
):
    """``out = raw * scale + offset``, in that order, and NaN stays NaN.

    Nothing in either real product carries a non-zero ``add_offset``, so
    without the fixture's synthetic one on HT the line that applies it is
    unreachable and deleting it changes no assertion in this file -- measured.
    The three numbers below are each other's near misses: the offset dropped
    gives 616.51, the offset added before the scale gives 921.72, and the fill
    left to the arithmetic gives 21001.52 metres, a cloud top well above the
    tropopause that reads downstream as a real measurement.
    """
    win = _whole_grid(make_granule(), ("HT",))

    assert win.value_at("HT", 20, 20) == 1616.511486172676
    assert win.value_at("HT", 20, 20) != pytest.approx(2020 * HT_SCALE)
    assert win.value_at("HT", 10, 10) == 11000.9150390625
    assert math.isnan(win.value_at("HT", 11, 11))
    assert np.count_nonzero(np.isnan(win.data["HT"])) == 1


def test_the_fill_mask_is_read_in_the_arrays_own_dtype():
    """``_fill_mask`` head on, because one of its branches has no granule.

    ABI packs every 2-D variable as an integer, so the non-integer branch
    cannot be reached through :func:`read_window` by any real file, and a
    float variable added to the fixture to reach it would be a granule shape
    that does not exist. Testing the helper directly is the honest way to keep
    the branch from being a claim nothing keeps: inverting its comparison
    survives every other test in this file.

    The signed case is the one that matters in production: the file declares a
    ``_FillValue`` of 255 on an int8 array, which h5py reads back as -1, and
    ``raw == 255`` is silently all-False under NEP 50 -- no error, no warning,
    and the fill flows on as an ordinary quality flag.
    """
    unsigned = np.array([[0, FILL_U16], [7, 7]], dtype=np.uint16)
    assert granule_mod._fill_mask(unsigned, np.uint16(FILL_U16)).tolist() == [
        [False, True],
        [False, False],
    ]

    signed = np.array([[-1, 0, 1]], dtype=np.int8)
    assert not (signed == FILL_U8).any()          # the naive comparison
    assert granule_mod._fill_mask(signed, np.uint8(FILL_U8)).tolist() == [
        [True, False, False]
    ]

    floats = np.array([[1.5, -999.0]], dtype=np.float32)
    assert granule_mod._fill_mask(floats, np.float32(-999.0)).tolist() == [
        [False, True]
    ]


# ------------------------------------------------------------- the grid spec


def test_the_grid_spec_is_built_from_the_file_not_from_constants(make_granule):
    """A granule that says -75.0 gets -75.0, not GOES-18's -137.0.

    The fixture is also named something no NOAA key could be, which is the
    other half of the same rule: nothing about the projection may come from
    the file's NAME either.
    """
    path = make_granule("subset-of-a-granule.nc", lon_origin_deg=-75.0)

    spec = read_grid_spec(path)
    assert spec.lon_origin_deg == -75.0
    # Rows off the y axis and columns off the x axis, which are different
    # lengths here precisely so that reading either off the other shows up.
    assert (spec.n_rows, spec.n_cols) == (40, 48)
    assert spec.x_scale_rad == X_SCALE
    assert spec.y_scale_rad == Y_SCALE          # negative, never abs()

    # And it reads, with an empty identity rather than a refusal or a guess.
    win = _whole_grid(path, ("BCM",), centre=CELL_20_20_AT_75)
    assert (win.product, win.platform) == ("", "")
    assert win.spec.lon_origin_deg == -75.0


def test_the_satellite_height_adds_the_equatorial_radius(make_granule):
    """``perspective_point_height`` alone is a 6378 km error.

    Stage 2 cannot catch it: 35786023 m is a legal satellite height, so its
    ``sat_height_m > r_eq_m`` guard passes and every pixel lands somewhere
    plausible and wrong.
    """
    spec = read_grid_spec(make_granule())

    assert spec.sat_height_m == PERSPECTIVE_HEIGHT_M + R_EQ_M
    assert spec.sat_height_m == 42164160.0
    assert spec.sat_height_m != PERSPECTIVE_HEIGHT_M


def test_the_sweep_axis_is_decoded_from_bytes(make_granule):
    """``sweep_angle_axis`` is NC_CHAR, i.e. bytes through h5py.

    ``GridSpec`` compares it against the string "x", so an undecoded
    ``b"x"`` does not build at all -- construction succeeding IS the
    assertion, and the two below say which way it succeeded.
    """
    spec = read_grid_spec(make_granule())

    assert spec.sweep_axis == "x"
    assert isinstance(spec.sweep_axis, str)


# ------------------------------------------------------------------ the time


def test_the_observation_time_is_the_j2000_midpoint(make_granule):
    """``t`` is seconds since 2000-01-01 12:00 UTC, and it is tz-aware.

    Not the filename's ``s`` field, which is the scan START: for this granule
    they differ by 79 s, which is a third of the way to the next granule.

    The second half pins the epoch itself. A granule counting from the Unix
    epoch through this arithmetic returns 2052, and nothing about a datetime
    26 years out looks like a unit error at the call site -- so it is refused
    here instead.
    """
    when = observed_at(make_granule())

    assert when.tzinfo is not None
    assert when.utcoffset() == timedelta(0)
    expected = datetime(2026, 8, 21, 6, 7, 36, tzinfo=timezone.utc)
    assert abs((when - expected).total_seconds()) <= 1.0

    other_epoch = make_granule(
        "other-epoch.nc", t_units="seconds since 1970-01-01 00:00:00"
    )
    with pytest.raises(ValueError):
        observed_at(other_epoch)


def test_a_key_is_parsed_into_its_scan_start_by_the_module_that_owns_it():
    """``_s2026233 0606176`` is day 233 of 2026 at 06:06:17.6 UTC.

    ``source`` has its own copy of this assertion, and both are wanted: the
    parser lives HERE, in the module with no socket, and a rule this fiddly --
    a zero-padded day of year that is one-based, and a trailing tenth of a
    second that is NOT part of the seconds field -- should be pinned beside
    the code as well as beside the caller. Off-by-one on the day of year and
    the tenth folded into the seconds both survive the rest of this file.
    """
    assert parse_granule_name(GRANULE_NAME) == (
        "ABI-L2-ACMC", "G18", SCAN_START,
    )
    # A full S3 key parses identically: only the last component is read.
    assert parse_granule_name(
        "ABI-L2-ACMC/2026/233/06/" + GRANULE_NAME
    ) == parse_granule_name(GRANULE_NAME)

    # Not a granule name is None and never fatal -- NOAA leaves other objects
    # under a prefix. Day 000 exercises the range check, which is what stops a
    # zero-based day being read as a valid one.
    assert parse_granule_name("index.html") is None
    assert parse_granule_name(
        "OR_ABI-L2-ACMC-M6_G18_s20260000606176_e_c.nc"
    ) is None


# ---------------------------------------------------------------- the window


def test_the_window_is_clipped_at_the_grid_edge(make_granule):
    """Two cells from the corner with half 10: the window starts at row 0.

    ``row0`` has to say so. A window that clipped its data but kept reporting
    ``centre - half`` as its origin would put every value 8 cells north of
    where it is, and every value in it would still be a real measurement from
    a real place.
    """
    path = make_granule()

    win = read_window(
        path,
        ("BCM",),
        centre_lat_deg=CELL_2_2[0],      # cell (2, 2)
        centre_lon_deg=CELL_2_2[1],
        half_rows=10,
        half_cols=10,
    )

    assert (win.row0, win.col0) == (0, 0)
    assert win.data["BCM"].shape == (13, 13)     # rows 0..12, not 21
    assert win.value_at("BCM", 0, 0) == 0.0
    assert win.value_at("BCM", 12, 12) == 0.0

    # Visible, but at cell (2290, 1250) of a 40 x 48 sector: the clip is
    # empty, and that is an error rather than an empty array, because an
    # empty array reads downstream as "no cloud".
    #
    # THREE CENTRES, because the guard is an `or` and one centre outside on
    # both axes lets either half of it be deleted unnoticed. The second is
    # outside on the column only, the third on the row only -- a site off the
    # east edge of a sector, and a site off its south edge.
    #
    # SiteOutsideSector and not ValueError: all three are the same fact about
    # the site and the sector, and the caller that shows that fact to an
    # operator has only the class name to show. The two tests below are about
    # that name and that message; this loop is about all three centres
    # reaching them.
    for centre in (SUB_SATELLITE, ROW_INSIDE_COLUMN_OUT, COLUMN_INSIDE_ROW_OUT):
        with pytest.raises(SiteOutsideSector):
            read_window(
                path,
                ("BCM",),
                centre_lat_deg=centre[0],
                centre_lon_deg=centre[1],
                half_rows=2,
                half_cols=2,
            )


def test_a_site_outside_the_sector_is_refused_by_a_type_that_says_so(
    make_granule,
):
    """The operator sees the CLASS NAME of this refusal and nothing else.

    ``cloudmap.service``'s poll catches everything a fetch can raise and keeps
    ``type(exc).__name__``, on purpose: httpx puts the full request URL in its
    exception text and an S3 URL will carry a site coordinate the day somebody
    adds a point query. So the panel that renders ``status.last_error`` used
    to say "ValueError" here -- the same word it says for a mistyped variable
    name and for a granule stamped in the wrong epoch. One word, three
    unrelated facts, and no next move for the person reading it.

    Hence the exact-name assertion below, which looks like a tautology and is
    not: that string IS the operator-facing copy, so renaming the class is a
    copy change and has to be a deliberate one.

    ``ValueError`` stays in the bases because design section 7 promises it for
    every argument this module refuses, and callers -- including the clipping
    test above -- were written against that promise.

    The indices are asserted too. The point of the exercise is to stop
    PRINTING them, not to stop knowing them: a developer holding the exception
    still needs the cell that missed and the sector it missed, or the next
    person debugging a clipped window puts them back into the message.
    """
    with pytest.raises(SiteOutsideSector) as caught:
        read_window(
            make_granule(),
            ("BCM",),
            centre_lat_deg=SUB_SATELLITE[0],
            centre_lon_deg=SUB_SATELLITE[1],
            half_rows=2,
            half_cols=2,
        )

    assert type(caught.value).__name__ == "SiteOutsideSector"
    assert isinstance(caught.value, ValueError)
    # Reachable for debugging, by a route that is not str(): the sub-satellite
    # point is cell (2290, 1250) of this 40 x 48 fixture.
    assert (caught.value.centre_row, caught.value.centre_col) == (2290, 1250)
    assert (caught.value.n_rows, caught.value.n_cols) == (N_ROWS, N_COLS)


def test_the_out_of_sector_refusal_names_no_position(make_granule):
    """No digit in the sentence, because every digit here is a position.

    ``lonlat_to_index`` is a bijection on the fixed grid, so a centre cell is
    the site's latitude and longitude with two steps of stage 2 run backwards
    -- ambiguous only to the width of a cell, about 2 km. A viewer has already
    geolocated this rig to 2.9 km out of a value nobody had thought of as a
    coordinate, and this message is bound for the operator journal and a UI
    panel.

    ASSERTED BY PATTERN rather than by reading the sentence, because the
    failure mode is somebody appending a helpful clause to a message that was
    safe when it was written. A reviewer eyeballing the constant catches that
    once; a regex catches it every run. The centre used here is cell
    (20, 200), whose two indices are digit strings the old message printed
    verbatim.

    The last two assertions stop the regex being satisfied by the empty
    string, which would pass everything above and tell the operator nothing.
    ``repr`` is checked as well as ``str``: a log line written ``{exc!r}``
    renders the args tuple rather than the message.
    """
    with pytest.raises(SiteOutsideSector) as caught:
        read_window(
            make_granule(),
            ("BCM",),
            centre_lat_deg=ROW_INSIDE_COLUMN_OUT[0],
            centre_lon_deg=ROW_INSIDE_COLUMN_OUT[1],
            half_rows=2,
            half_cols=2,
        )

    sentence = str(caught.value)
    assert re.search(r"\d", sentence) is None, sentence
    assert re.search(r"\d", repr(caught.value)) is None, repr(caught.value)
    # And it still says the thing: the site, and the sector that misses it.
    assert "site" in sentence and "sector" in sentence
    assert len(sentence.split()) > 10


def test_the_window_is_clipped_at_the_far_edge_of_the_sector(make_granule):
    """The SOUTH and EAST bounds, where ``row0`` and ``col0`` are not zero.

    The near-edge test above clips at 0, where ``n_rows - 1`` and
    ``n_cols - 1`` are both inactive -- so both can quietly become ``- 2``,
    dropping the last row and the last column of every sector, and this suite
    stays green. Cell (37, 45) with half 5 is limited by both.
    """
    win = read_window(
        make_granule(),
        ("BCM",),
        centre_lat_deg=CELL_37_45[0],
        centre_lon_deg=CELL_37_45[1],
        half_rows=5,
        half_cols=5,
    )

    assert (win.row0, win.col0) == (32, 40)
    assert win.data["BCM"].shape == (8, 8)       # rows 32..39, cols 40..47
    assert win.value_at("BCM", 39, 47) == 0.0    # the very last cell
    with pytest.raises(IndexError):
        win.value_at("BCM", 40, 47)              # one row past the sector
    with pytest.raises(IndexError):
        win.value_at("BCM", 39, 48)              # one column past it


def test_value_at_takes_full_grid_indices_not_window_indices(make_granule):
    """The window is a view on the grid, and its coordinates are the grid's.

    Stage 4 walks a ray through full-grid indices from stage 2; if
    ``value_at`` silently took window-relative ones, every lookup would be
    off by the window origin and still return a number.

    ``observed_at`` is asserted here rather than in its own test because this
    is where a window exists to carry it. It is the field stage 4 times every
    frame against, and taking it from the filename's ``s`` field -- the exact
    thing design section 3.3 forbids -- shifts every window by 79 s and
    follows any renamed or reprocessed granule.
    """
    path = make_granule()
    win = read_window(
        path,
        ("Cloud_Probabilities",),
        centre_lat_deg=CELL_20_20[0],     # cell (20, 20)
        centre_lon_deg=CELL_20_20[1],
        half_rows=3,
        half_cols=3,
    )

    assert (win.row0, win.col0) == (17, 17)
    assert win.data["Cloud_Probabilities"].shape == (7, 7)
    assert (win.product, win.platform) == ("ABI-L2-ACMC", "G18")

    assert win.observed_at == observed_at(path)
    assert win.observed_at.second == 36
    assert (win.observed_at - SCAN_START).total_seconds() == pytest.approx(
        79.2797, abs=1e-4
    )

    assert win.value_at("Cloud_Probabilities", 20, 20) == 0.030827218251943123
    # The window's own (0, 0) is the grid's (17, 17), not (0, 0).
    assert win.value_at("Cloud_Probabilities", 17, 17) == pytest.approx(
        win.data["Cloud_Probabilities"][0, 0]
    )

    with pytest.raises(IndexError):
        win.value_at("Cloud_Probabilities", 24, 20)
    # One cell NORTH of the window. numpy would wrap a negative index round to
    # the far edge and answer with a real number from the wrong place.
    with pytest.raises(IndexError):
        win.value_at("Cloud_Probabilities", 16, 20)
    with pytest.raises(KeyError):
        win.value_at("BCM", 20, 20)


def test_an_asymmetric_window_keeps_its_rows_and_columns_apart(make_granule):
    """``half_rows`` is rows and ``half_cols`` is columns, all the way down.

    Stage 4 asks for windows that are not square -- the two products are on
    grids five times apart in resolution -- which is why the API takes the two
    halves separately at all. Four independent transpositions were measured
    surviving a suite whose every window was symmetric: the axis lengths read
    off each other, the origin pair swapped, the slice bounds swapped, and the
    halves applied to the wrong axis. Each of them changes one of the three
    assertions below.
    """
    win = read_window(
        make_granule(),
        ("Cloud_Probabilities",),
        centre_lat_deg=CELL_20_20[0],     # cell (20, 20)
        centre_lon_deg=CELL_20_20[1],
        half_rows=3,
        half_cols=5,
    )

    assert (win.row0, win.col0) == (17, 15)
    assert win.data["Cloud_Probabilities"].shape == (7, 11)
    # Off the diagonal, so a transposed read cannot land on the same count:
    # the raw value at (18, 22) is 1822 and at (22, 18) it is 2218.
    assert win.value_at("Cloud_Probabilities", 18, 22) == 0.02780554042328731


def test_an_unknown_variable_name_is_a_value_error(make_granule):
    """Design section 7 says ValueError, and h5py's own answer is KeyError.

    A caller written against section 7 catches ValueError for every bad
    argument this module takes. Drop the guard and the same mistake arrives as
    a KeyError from inside h5py, which that caller does not catch and which
    names the HDF5 object rather than the variable list the granule carries.
    """
    with pytest.raises(ValueError) as caught:
        read_window(
            make_granule(),
            ("Cloud_Top_Height",),           # HT's long name, not its key
            centre_lat_deg=CELL_20_20[0],
            centre_lon_deg=CELL_20_20[1],
            half_rows=1,
            half_cols=1,
        )

    assert "Cloud_Top_Height" in str(caught.value)
    assert "HT" in str(caught.value)          # and what it could have asked for


def test_a_negative_half_is_refused_by_name(make_granule):
    """A caller-side sign error must not be reported as a sector problem.

    Without the guard a negative half still raises ValueError -- ``row0``
    overshoots ``row1`` and the empty-clip refusal fires -- so the type alone
    proves nothing. That message says the granule never sampled the cell,
    which sends the reader to NOAA's sector definition for a bug in their own
    argument list. The ``match`` is the whole test.

    The empty-clip refusal is now :class:`SiteOutsideSector`, which makes that
    confusion worse without this guard rather than better: the caller would
    show the operator a name asserting their site is outside the satellite's
    sector when the site is dead centre of it and the argument list is what is
    wrong. So the type is asserted negatively as well as the message
    positively: drop the guard and the ``match`` fails first, quoting the
    sector sentence back at you, and the ``isinstance`` line is what still
    holds if that message is ever reworded into something ``>= 0`` matches.
    """
    path = make_granule()
    for half_rows, half_cols in ((-1, 3), (3, -1)):
        with pytest.raises(ValueError, match=">= 0") as caught:
            read_window(
                path,
                ("BCM",),
                centre_lat_deg=CELL_20_20[0],
                centre_lon_deg=CELL_20_20[1],
                half_rows=half_rows,
                half_cols=half_cols,
            )
        assert not isinstance(caught.value, SiteOutsideSector)


def test_a_centre_behind_the_limb_is_refused(make_granule):
    """A place the satellite cannot see is not a window with no data in it."""
    with pytest.raises(ValueError):
        read_window(
            make_granule(),
            ("BCM",),
            centre_lat_deg=BEHIND_THE_LIMB[0],
            centre_lon_deg=BEHIND_THE_LIMB[1],
            half_rows=2,
            half_cols=2,
        )


def test_the_file_is_closed_before_the_call_returns(make_granule, monkeypatch):
    """No h5py handle outlives a call, and Windows is the one that proves it.

    On POSIX an escaped handle unlinks happily and this test passes while the
    cache slowly fills with files nothing can evict; on Windows the unlink
    raises PermissionError. Same bug, and only one platform says so.

    The unlink is not enough on its own, which is why every handle is recorded
    and asked whether it is closed. Replacing the ``with`` with a bare open
    and no close passes the unlink on CPython -- refcounting closes the local
    as the frame dies -- so the unlink pins "nothing holds a reference
    afterwards", not "the file is closed before the call returns". A traceback
    frame, a logger holding an exception or a non-refcounting runtime is
    enough to make those two different facts. ``id.valid`` is the second one.
    """
    path = make_granule()
    opened = []
    real_file = granule_mod.h5py.File

    def _recording(*args, **kwargs):
        handle = real_file(*args, **kwargs)
        opened.append(handle)
        return handle

    monkeypatch.setattr(granule_mod.h5py, "File", _recording)

    win = _whole_grid(path, ("BCM", "Cloud_Probabilities"))
    read_grid_spec(path)
    observed_at(path)

    assert len(opened) == 3
    assert [handle.id.valid for handle in opened] == [0, 0, 0]

    path.unlink()
    assert not path.exists()
    # The arrays are in memory, not a lazy view onto a file that is now gone.
    assert win.value_at("Cloud_Probabilities", 10, 10) == 0.5000724196434021


# ------------------------------------------------------------------ the extra


def test_the_module_imports_without_h5py(monkeypatch, tmp_path):
    """Absent the extra, the module still imports and every door says why.

    The module is loaded FRESH from its own file under a throwaway name rather
    than reloaded in place, so masking h5py cannot leave the real module
    object degraded for whatever test runs next in this worker.
    """
    monkeypatch.setitem(sys.modules, "h5py", None)
    name = "astrodeck.cloudmap._granule_without_h5py"
    spec = importlib.util.spec_from_file_location(name, granule_mod.__file__)
    masked = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass resolves its string annotations
    # through sys.modules[cls.__module__], and an unregistered module makes
    # that lookup return None. monkeypatch drops the entry again afterwards.
    monkeypatch.setitem(sys.modules, name, masked)
    spec.loader.exec_module(masked)

    assert masked.HAVE_H5PY is False
    path = tmp_path / GRANULE_NAME

    with pytest.raises(masked.CloudmapUnavailable) as caught:
        masked.read_window(
            path,
            ("BCM",),
            centre_lat_deg=CELL_20_20[0],
            centre_lon_deg=CELL_20_20[1],
            half_rows=1,
            half_cols=1,
        )
    assert "cloudmap" in str(caught.value)
    with pytest.raises(masked.CloudmapUnavailable):
        masked.read_grid_spec(path)
    with pytest.raises(masked.CloudmapUnavailable):
        masked.observed_at(path)

    # A RuntimeError, so a caller that only wants "cloudmap is not available
    # today" can catch it without importing this module at all.
    assert issubclass(masked.CloudmapUnavailable, RuntimeError)
    assert issubclass(CloudmapUnavailable, RuntimeError)


def test_the_extra_that_the_error_message_names_exists(monkeypatch):
    """The remedy the module prints has to be a remedy.

    For one review round it was not: the message told the user to run
    ``pip install 'astrodeck[cloudmap]'`` against an extra nobody had added to
    pyproject.toml. pip answers that with "astrodeck does not provide the
    extra 'cloudmap'", installs nothing, exits 0 -- and the next run prints the
    identical instruction. The h5py already sitting in this venv is exactly
    why the whole granule suite passed anyway.

    So the extra name is taken OUT of the live message rather than written
    twice, and looked up in the packaging. The two can only drift apart by
    breaking this.
    """
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if not pyproject.is_file():
        pytest.skip("no pyproject.toml: an installed tree, not a checkout")
    extras = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ]

    monkeypatch.setattr(granule_mod, "HAVE_H5PY", False)
    with pytest.raises(CloudmapUnavailable) as caught:
        granule_mod._require_h5py()
    named = re.search(r"astrodeck\[([A-Za-z0-9._-]+)\]", str(caught.value))

    assert named is not None, "the message no longer names an extra to install"
    assert named.group(1) in extras
    assert any(
        requirement.replace(" ", "").startswith("h5py")
        for requirement in extras[named.group(1)]
    )
