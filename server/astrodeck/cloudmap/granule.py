"""Reading a GOES ABI granule off disk (stage 3) -- raw counts to physical units.

No network, no config, no rig, no clock. One file in, numpy arrays out. Nothing
here interprets a number; what a cloud probability MEANS is stage 4's business.

THIS MODULE EXISTS BECAUSE H5PY DOES NOT UNPACK. ``netCDF4`` and ``xarray``
apply ``scale_factor``, ``add_offset`` and ``_FillValue`` for you; ``h5py``
hands back the stored integers and leaves the attributes sitting there. Read
``Cloud_Probabilities`` with h5py and no unpacking and you get uint16 counts up
to 65535 which look exactly like plausible data. The rule, in this order and no
other (design section 3.1):

    raw = dataset[slice]                  # integer dtype
    out = raw.astype(float64)
    out[raw == _FillValue] = NaN          # BEFORE scaling; the fill is RAW
    out = out * scale_factor + add_offset # each, only if the file carries it

Scale first and the fill becomes 65535 * 1.5261e-05 = 1.0002 -- a probability
a hair over 1, which reads as a rounding artefact rather than as the absence of
data, and which any clip to [0, 1] downstream turns into a confident
"overcast". The ordering is the whole module.

The projection is likewise read from the granule and never from a constant, so
one code path serves GOES-18, GOES-19, both sectors and both resolutions.
``sat_height_m`` is ``perspective_point_height + semi_major_axis``: the
granule's ``perspective_point_height`` alone is the height above the SURFACE,
and using it is a 6378 km error that stage 2 cannot catch, because 35786 km is
a perfectly legal satellite height.

Absent ``h5py`` -- the optional ``cloudmap`` extra -- this module still
imports, ``HAVE_H5PY`` is False, and every entry point raises
:class:`CloudmapUnavailable` naming the extra. Same idiom as
``comhost.device``'s comtypes guard, for the same reason: an optional
dependency may not cost a traceback at startup.

TWO ADDITIONS TO DESIGN SECTION 3, both argued at the code that makes them:
:func:`observed_at` refuses a granule whose ``t`` names an epoch other than
J2000 rather than silently shifting the observation by decades, and
:func:`_fill_mask` compares a fill that cannot be represented in the raw dtype
against the unsigned reading of it, which is the one case where h5py's answer
and netCDF's provably differ.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .abi_grid import GridSpec, lonlat_to_index

try:  # h5py is the optional `cloudmap` extra; guarded so import never fails.
    import h5py
    HAVE_H5PY = True
except ImportError:  # pragma: no cover - exercised by a fresh masked import
    h5py = None  # type: ignore[assignment]
    HAVE_H5PY = False

__all__ = [
    "CloudmapUnavailable",
    "SiteOutsideSector",
    "HAVE_H5PY",
    "GranuleWindow",
    "parse_granule_name",
    "read_window",
    "read_grid_spec",
    "observed_at",
]

#: ``t``'s epoch: seconds since 2000-01-01 12:00:00 UTC (design section 3.3).
J2000 = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
#: The part of ``t.units`` that pins the epoch above. Compared as a substring
#: so "...12:00:00" and "...12:00:00.0" both pass.
_J2000_UNITS = "2000-01-01 12:00:00"

#: ``OR_ABI-L2-ACMC-M6_G18_s20262330606176_e..._c....nc``. The product is
#: non-greedy up to the ``-M<mode>`` field, which is what lets it contain the
#: hyphens it does. The final digit of the ``s`` field is TENTHS of a second,
#: not part of the seconds, and is discarded (design section 5.1).
_NAME_RE = re.compile(
    r"^OR_(?P<product>[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*?)-M\d[A-Za-z0-9]*"
    r"_(?P<platform>G\d{2})"
    r"_s(?P<year>\d{4})(?P<doy>\d{3})(?P<hh>\d{2})(?P<mm>\d{2})(?P<ss>\d{2})"
    r"(?P<tenths>\d)_"
)


class CloudmapUnavailable(RuntimeError):
    """Cloud data cannot be had right now: no h5py, or the fetch failed.

    One type for the whole stage, so a caller that only wants to degrade
    gracefully catches it once and never learns what an S3 listing is.
    """


class SiteOutsideSector(ValueError):
    """The satellite sees the site, but this product's sector stops short of it.

    THE TYPE IS THE MESSAGE, and that is not a stylistic preference. The one
    caller in the tree -- ``cloudmap.service``'s poll -- catches everything a
    fetch can raise and keeps ``type(exc).__name__`` and nothing else, because
    httpx puts the full request URL in its exception text and an S3 URL will
    carry a site coordinate the day somebody adds a point query. So the class
    name is the whole of what reaches the operator and the panel that renders
    ``status.last_error``. Before this type existed that panel said
    "ValueError", which is true of a typo in a filter name too. The name is
    written to be read in that slot as a statement of fact: the site is
    outside the sector.

    THE MESSAGE IS SAFE TO SHOW AS WELL, so a caller that can echo more is not
    forced to choose between silence and a leak. It carries no coordinate, no
    grid index, no array shape and no URL. A cell index is a position wearing
    a hat: the fixed-grid transform is a bijection, so a centre cell run back
    through it is a latitude and a longitude in two steps, ambiguous only to
    the width of a cell -- about 2 km. This project has already had a viewer
    geolocate the rig to 2.9 km from a derived value that nobody had thought
    of as a coordinate, which is the whole reason this rule is written down
    rather than assumed. The sector's dimensions leak nothing by themselves;
    they are excluded anyway because they tell an operator nothing they can
    act on, and a sentence that already prints two numbers is where the third
    one gets added. No worked example of the arithmetic appears here either:
    any plausible one would be the rig's own position.

    Subclasses ``ValueError`` because design section 7 promises ValueError for
    every argument this module refuses, and callers were written against that
    promise before this type existed.

    Constructed with keyword-only, required indices. That makes it unpicklable
    -- ``BaseException.__reduce__`` replays ``args``, which is the message
    alone -- and that is fine here: the read runs on a worker THREAD, not in a
    process pool, so no exception in this stage crosses a process boundary. If
    one ever does, this needs a ``__reduce__``, not a looser signature.
    """

    #: OPT-IN PERMISSION TO ECHO ``str(exc)``, read by the service's error
    #: filter. Absent on every other exception in this package and that is the
    #: point: ``source.py`` builds its CloudmapUnavailable text by
    #: concatenating the failing request's own error, which is where the URL
    #: lives, so a blanket "echo our own exceptions" rule would leak the thing
    #: the filter exists to withhold. Set this only on a class whose message is
    #: a CONSTANT, as MESSAGE below is.
    SAFE_TO_ECHO = True

    #: The only sentence this exception ever says. A constant and not a format
    #: string, deliberately: a format string is where a coordinate gets
    #: interpolated in six months by someone debugging a clipped window.
    MESSAGE = (
        "this satellite's sector does not reach the site: the granule itself "
        "read fine, but the site falls outside the ground this product "
        "scans, so no granule of it will ever hold the sky overhead -- only "
        "a wider sector, or a satellite that covers the site, will"
    )

    def __init__(
        self,
        *,
        centre_row: int,
        centre_col: int,
        n_rows: int,
        n_cols: int,
    ) -> None:
        super().__init__(self.MESSAGE)
        # DEBUGGING ONLY, NEVER FOR DISPLAY. These four are precisely what the
        # message above may not contain: centre_row/centre_col ARE the site's
        # position to about 2 km (see the class docstring). Read them in a
        # debugger or a local traceback; do not log them, do not render them,
        # do not fold them back into str(self).
        self.centre_row = centre_row
        self.centre_col = centre_col
        self.n_rows = n_rows
        self.n_cols = n_cols


def _require_h5py() -> None:
    if not HAVE_H5PY:
        raise CloudmapUnavailable(
            "reading a GOES granule needs h5py, which ships as the optional "
            "'cloudmap' extra: pip install 'astrodeck[cloudmap]'"
        )


def parse_granule_name(name: str) -> tuple[str, str, datetime] | None:
    """``(product, platform, scan_start)`` from a key or path, or ``None``.

    Accepts a full S3 key, a filesystem path or a bare basename; only the last
    component is read. ``None`` means "not a granule name" and is never fatal:
    NOAA occasionally leaves other objects under a prefix, and one of them must
    not cost the caller the whole hour.

    THIS LIVES HERE, in the module with no socket, because both stage-3 modules
    need it -- :func:`read_window` for the window's identity and ``source`` for
    every listed key. Two copies of a rule with a zero-padded day of year and a
    trailing tenth of a second in it would eventually disagree.

    ``scan_start`` is the SCAN START, which is not the observation time. Use
    :func:`observed_at`, which reads the granule's own mid-point ``t``; for the
    granules measured in design section 3 the two differ by 79 s, a third of
    the way to the next frame.
    """
    match = _NAME_RE.match(Path(str(name)).name)
    if match is None:
        return None
    doy = int(match["doy"])
    if not 1 <= doy <= 366:
        return None
    scan_start = datetime(int(match["year"]), 1, 1, tzinfo=timezone.utc) + timedelta(
        days=doy - 1,
        hours=int(match["hh"]),
        minutes=int(match["mm"]),
        seconds=int(match["ss"]),
    )
    return (match["product"], match["platform"], scan_start)


@dataclass(frozen=True)
class GranuleWindow:
    """A rectangle of one granule, unpacked, with the grid it came from.

    ``row0``/``col0`` are the window's origin in FULL-GRID indices, and they
    are load-bearing: the window clips at the sector edge, so a window that
    reported ``centre - half`` as its origin regardless would place every value
    in it several cells from where it was measured -- and every one of those
    values would still be a real measurement of a real place.
    """

    spec: GridSpec
    observed_at: datetime
    product: str
    platform: str
    row0: int
    col0: int
    data: dict[str, "np.ndarray"]

    def value_at(self, name: str, row: int, col: int) -> float:
        """One cell, addressed in FULL-GRID indices. NaN where the fill was.

        ``KeyError`` for a variable this window did not read, ``IndexError``
        outside it. The negative check is not redundant with numpy's bounds:
        numpy would take ``row0 - 1`` as an index from the far end of the
        window and answer with a real number from the wrong place.
        """
        array = self.data[name]
        r = row - self.row0
        c = col - self.col0
        if r < 0 or c < 0 or r >= array.shape[0] or c >= array.shape[1]:
            raise IndexError(
                "cell ("
                + repr(row)
                + ", "
                + repr(col)
                + ") is outside this window, which covers rows "
                + repr(self.row0)
                + ".."
                + repr(self.row0 + array.shape[0] - 1)
                + " and columns "
                + repr(self.col0)
                + ".."
                + repr(self.col0 + array.shape[1] - 1)
                + " of the full grid"
            )
        return float(array[r, c])


def _scalar(value: Any) -> Any:
    """The one element of an attribute stored as a length-1 array.

    ``_FillValue``, ``scale_factor`` and ``add_offset`` are length-1 arrays in
    a real granule; a hand-built file may store the bare number. Both read the
    same way here rather than at four call sites.
    """
    flat = np.asarray(value).reshape(-1)
    return flat[0]


def _text(value: Any) -> str | None:
    """An NC_CHAR attribute as a str. h5py returns those as bytes.

    ``sweep_angle_axis`` reaches ``GridSpec``, whose validator compares against
    the string "x" -- so an undecoded ``b"x"`` does not build a spec at all,
    which is the good case. ``t.units`` is the same shape of value and the same
    decode.
    """
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("ascii", "replace")
    return str(value)


def _fill_mask(raw: "np.ndarray", fill: Any) -> "np.ndarray":
    """Where ``raw`` holds the fill, compared in the RAW dtype.

    ADDITION TO DESIGN SECTION 3.1. A fill that cannot be represented in the
    array's dtype is the netCDF unsigned-byte convention seen through h5py: the
    file stores 255 in a SIGNED byte, netCDF4 reports 255 and h5py reports -1,
    and ``raw == 255`` on an int8 array is silently all-False under NEP 50 --
    no error, no warning, and the fill survives into the output as -1.0, which
    reads downstream as an ordinary quality flag. Wrapping the comparison into
    the unsigned reading costs one modulo and is a no-op for every
    self-consistent file, where the fill fits and the mask is unchanged.

    Only the COMPARISON is wrapped. The values themselves come back as h5py
    read them, because the fill is the one place where the two readings
    provably differ -- everywhere else, reinterpreting would be a guess.
    """
    if raw.dtype.kind not in "iu":
        return raw == fill
    fill_int = int(fill)
    values = raw.astype(np.int64)
    if raw.dtype.kind == "i" and fill_int > np.iinfo(raw.dtype).max:
        values = values % (1 << (8 * raw.dtype.itemsize))
    return values == fill_int


def _unpack(dataset: Any, row0: int, row1: int, col0: int, col1: int) -> "np.ndarray":
    """A slice of one variable in physical units, NaN where the fill was.

    The slice bounds are INCLUSIVE, because they are grid cells rather than
    Python indices everywhere else in this stage.
    """
    raw = dataset[row0:row1 + 1, col0:col1 + 1]
    out = raw.astype(np.float64)

    fill = dataset.attrs.get("_FillValue")
    if fill is not None:
        # BEFORE scaling. See the module docstring: this line is the module.
        out[_fill_mask(raw, _scalar(fill))] = np.nan

    # Each attribute applied only if the file carries it. BCM, ACM and DQF have
    # a fill and no scaling; HT and Cloud_Probabilities have all three. A
    # scale of 1.0 invented for the first group is harmless right up until a
    # variable's real scale is not 1.0.
    scale = dataset.attrs.get("scale_factor")
    if scale is not None:
        out = out * float(_scalar(scale))
    offset = dataset.attrs.get("add_offset")
    if offset is not None:
        out = out + float(_scalar(offset))
    return out


def _grid_spec_of(handle: Any) -> GridSpec:
    """Stage 2's :class:`GridSpec`, entirely from this file's own attributes."""
    projection = handle["goes_imager_projection"].attrs
    x_axis = handle["x"]
    y_axis = handle["y"]
    return GridSpec(
        # From the earth's CENTRE. perspective_point_height alone is the height
        # above the surface, and stage 2 cannot tell the difference.
        sat_height_m=float(_scalar(projection["perspective_point_height"]))
        + float(_scalar(projection["semi_major_axis"])),
        r_eq_m=float(_scalar(projection["semi_major_axis"])),
        r_pol_m=float(_scalar(projection["semi_minor_axis"])),
        lon_origin_deg=float(
            _scalar(projection["longitude_of_projection_origin"])
        ),
        sweep_axis=_text(_scalar(projection["sweep_angle_axis"])),
        x_offset_rad=float(_scalar(x_axis.attrs["add_offset"])),
        x_scale_rad=float(_scalar(x_axis.attrs["scale_factor"])),
        n_cols=int(x_axis.shape[0]),
        y_offset_rad=float(_scalar(y_axis.attrs["add_offset"])),
        # NEGATIVE for GOES: row increases southward. Never abs() it.
        y_scale_rad=float(_scalar(y_axis.attrs["scale_factor"])),
        n_rows=int(y_axis.shape[0]),
    )


def _observed_at_of(handle: Any) -> datetime:
    """The granule's mid-scan time, tz-aware UTC, from its scalar ``t``."""
    dataset = handle["t"]
    units = _text(dataset.attrs.get("units"))
    if units is not None and _J2000_UNITS not in units:
        # ADDITION TO DESIGN SECTION 3.3, which hard-codes the J2000 epoch. A
        # granule counting from the Unix epoch through that constant returns a
        # date in 2052, and a datetime 26 years out looks like nothing in
        # particular at the call site. The file states its own epoch; if it
        # disagrees, this is not a granule this module knows how to read.
        raise ValueError(
            "granule time is in "
            + repr(units)
            + ", not seconds since "
            + _J2000_UNITS
            + " -- this reader only knows the J2000 epoch"
        )
    return J2000 + timedelta(seconds=float(_scalar(dataset[()])))


def read_grid_spec(path: "str | Path") -> GridSpec:
    """The ABI fixed-grid transform this granule was sampled on."""
    _require_h5py()
    with h5py.File(Path(path), "r") as handle:
        return _grid_spec_of(handle)


def observed_at(path: "str | Path") -> datetime:
    """When this granule was observed: tz-aware UTC, from its own ``t``.

    NOT the filename's ``s`` field, which is the scan start.
    """
    _require_h5py()
    with h5py.File(Path(path), "r") as handle:
        return _observed_at_of(handle)


def read_window(
    path: "str | Path",
    variables: Sequence[str],
    *,
    centre_lat_deg: float,
    centre_lon_deg: float,
    half_rows: int,
    half_cols: int,
) -> GranuleWindow:
    """A rectangle around a ground point, unpacked into float64 arrays.

    The centre is mapped through stage 2's :func:`lonlat_to_index` on the grid
    this file declares, and the window is clipped to the sector. Clipping is
    not an error -- a site near the edge of a CONUS sector legitimately gets a
    lopsided window -- but a window that clips away to nothing is, because an
    empty array reads downstream as "nothing up there".

    A centre behind the limb raises ``ValueError``: the satellite cannot see
    that place, which is a different fact from having no data about it.

    NO H5PY HANDLE ESCAPES. Every array is materialised inside the ``with`` and
    the file is closed before this returns, so the caller can evict the cache
    file immediately. On POSIX a leaked handle would let the unlink succeed and
    hide the bug; on Windows it raises PermissionError.

    The message on the limb error deliberately omits the coordinates it was
    given. An exception text reaches the same journal that design section 6
    keeps latitudes out of, and a site's position is the one thing in this
    stage worth withholding.

    The empty clip goes one step further and raises :class:`SiteOutsideSector`
    -- same rule, applied to the TYPE, because the caller that shows this to
    an operator keeps only ``type(exc).__name__``. The limb refusal is still a
    bare ValueError: it is a misconfigured site rather than a sector edge, and
    nothing in the tree yet distinguishes the two on the way out.
    """
    _require_h5py()
    if half_rows < 0 or half_cols < 0:
        raise ValueError(
            "half_rows and half_cols must be >= 0, got "
            + repr(half_rows)
            + " and "
            + repr(half_cols)
        )

    path = Path(path)
    with h5py.File(path, "r") as handle:
        spec = _grid_spec_of(handle)
        when = _observed_at_of(handle)

        index = lonlat_to_index(spec, centre_lat_deg, centre_lon_deg)
        if index is None:
            raise ValueError(
                "the window centre is behind the limb: it is not on the earth "
                "visible from a satellite at longitude "
                + repr(spec.lon_origin_deg)
            )
        centre_row, centre_col = index
        row0 = max(0, centre_row - half_rows)
        row1 = min(spec.n_rows - 1, centre_row + half_rows)
        col0 = max(0, centre_col - half_cols)
        col1 = min(spec.n_cols - 1, centre_col + half_cols)
        if row1 < row0 or col1 < col0:
            # A NAMED TYPE, because this is the one refusal here that is the
            # OPERATOR'S to act on -- the fetch worked, the file is good, the
            # site is simply not in this satellite's picture -- and the caller
            # that shows it to them can only safely echo the class name. The
            # four indices ride along as attributes instead of in the text:
            # the centre cell is the site's position to about 2 km. See
            # SiteOutsideSector.
            raise SiteOutsideSector(
                centre_row=centre_row,
                centre_col=centre_col,
                n_rows=spec.n_rows,
                n_cols=spec.n_cols,
            )

        data: dict[str, np.ndarray] = {}
        for name in variables:
            if name not in handle:
                raise ValueError(
                    "no variable "
                    + repr(name)
                    + " in this granule; it carries "
                    + ", ".join(sorted(handle))
                )
            data[name] = _unpack(handle[name], row0, row1, col0, col1)

    identity = parse_granule_name(path.name)
    # Empty rather than a guess or a refusal. Nothing above depends on either
    # field -- the projection came from the file, not from its name -- so a
    # granule someone renamed still reads, and says it does not know what it is.
    product, platform = (identity[0], identity[1]) if identity else ("", "")
    return GranuleWindow(
        spec=spec,
        observed_at=when,
        product=product,
        platform=platform,
        row0=row0,
        col0=col0,
        data=data,
    )
