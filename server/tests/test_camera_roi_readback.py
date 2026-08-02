"""A requested ROI is not an applied ROI, and the adapters used to treat it as
one.

Audit of #110. Both vendor adapters used to do this:

    w, h = roi.w // roi.bin, roi.h // roi.bin
    sdk.set_<roi>(cam_id, w, h, roi.bin, RAW16)
    self._nbytes = w * h * 2                     # <- from the REQUEST

so the download buffer, its byte count and the row length the engine laid it out
at all came from the same unverified number. Three consequences, each with a
test below:

* the length check in ``engine._shape`` can never fire for these adapters. The
  buffer is ours; ASIGetDataAfterExp/POAGetImageData succeed for any buffer that
  is big ENOUGH and fill the front of it; ``read_frame`` returns the whole
  allocation. ``len(raw)`` therefore equals the requested size by construction,
  whatever the sensor did — a wrong row length arrives at exactly the right byte
  count and the guard sees nothing.
* both SDKs can be asked what they applied and neither was asking.
  ``ASIGetROIFormat`` was even already bound and already required of a candidate
  DLL, and was never called. ``POAGetImageSize``/``Bin``/``Format``/
  ``StartPos`` were not bound at all; all four are exported by the vendored
  V3.10.1 build.
* ``roi.x``/``roi.y`` were discarded outright. Player One pinned the origin to
  (0, 0); ZWO never set one, and ASISetROIFormat re-centres the ROI, so the
  frame came from wherever the SDK left it. Nothing today asks for a subframe,
  which is why this had not bitten — but the ROI type advertises an origin and
  the adapters were quietly throwing it away.

What the sheared picture looks like, and the arithmetic tying it to the row
length, is in test_camera_roi_shear.py.
"""
from __future__ import annotations

import numpy as np
import pytest

from astrodeck.devices.base import DeviceError
from astrodeck.devices.cameras.adapter import ROI
from astrodeck.devices.cameras.engine import NativeCamera
from astrodeck.devices.cameras.player_one import PlayerOneAdapter
from astrodeck.devices.cameras.player_one_sdk import (
    POA_RAW16, POA_RAW8, PlayerOneSdk)
from astrodeck.devices.cameras.zwo_asi import AsiCameraAdapter
from astrodeck.devices.cameras.zwo_asi_sdk import ASI_IMG_RAW8, ASI_IMG_RAW16

from test_camera_contract import CONTRACT_ADAPTERS

SENSOR_W, SENSOR_H = 128, 64

#: The quantization the vendored Player One DLL performs, taken from the module
#: that cites the instructions rather than retyped here (see PoseidonSdk).
ALIGN_W, ALIGN_H = PlayerOneSdk.ALIGN_W, PlayerOneSdk.ALIGN_H


class _Sensor:
    """A camera that rounds the requested width DOWN to ``align`` and then fills
    the caller's buffer with rows of the width it actually used.

    Rounding down is the only direction that can go unnoticed: a sensor that
    rounded UP would need more bytes than the adapter allocated and the SDK
    would fail the download outright (ASI_ERROR_BUFFER_TOO_SMALL /
    POA_ERROR_SIZE_LESS), which is a loud failure and not this bug."""

    def __init__(self, align: int = 8, fmt: int | None = None,
                 sensor=(SENSOR_W, SENSOR_H)):
        self.align = align
        self.sw, self.sh = sensor
        self.applied_w = self.sw
        self.applied_h = self.sh
        self.applied_bin = 1
        self.fmt = fmt
        self.pos = (0, 0)
        self.calls: list[str] = []

    # -- what the "hardware" does -------------------------------------------
    def _apply(self, w, h, b, fmt):
        self.applied_w = w - (w % self.align)
        self.applied_h = h
        self.applied_bin = b
        if self.fmt is None:
            self.fmt = fmt
        self.calls.append(f"set:{w}x{h}/{b}->{self.applied_w}x{self.applied_h}")

    def _image(self, nbytes: int) -> bytes:
        rows = np.arange(self.applied_h, dtype="<u2").repeat(self.applied_w)
        buf = np.zeros(nbytes // 2, dtype="<u2")
        n = min(rows.size, buf.size)
        buf[:n] = rows[:n]
        return buf.tobytes()

    def _place(self, x, y):
        self.pos = (x, y)
        self.calls.append(f"pos:{x},{y}")


class FakeRoundingAsiSdk(_Sensor):
    def count(self): return 1

    def get_property(self, i):
        from astrodeck.devices.cameras.zwo_asi import AsiProperty
        return AsiProperty(name="ZWO ASI (rounding)", width=self.sw, height=self.sh,
                           pixel_size_um=4.0, is_color=False, bayer=None,
                           bit_depth=16, bin_modes=(1, 2), max_gain=300,
                           max_offset=255, has_cooler=False, camera_id=0, egain=1.0)

    def open(self, cid): pass
    def close(self, cid): pass
    def control_range(self, cid, ctrl): return (0, 300)
    def set_control(self, cid, ctrl, v, auto=False): pass
    def get_control(self, cid, ctrl): return 200
    def set_roi(self, cid, w, h, b, t): self._apply(w, h, b, t)
    def get_roi(self, cid):
        return self.applied_w, self.applied_h, self.applied_bin, self.fmt
    def set_start_pos(self, cid, x, y): self._place(x, y)
    def get_start_pos(self, cid): return self.pos
    def start_exposure(self, cid, dark): pass
    def exp_status(self, cid): return 2
    def get_data(self, cid, nbytes): return self._image(nbytes)
    def stop_exposure(self, cid): pass


class FakeRoundingPoaSdk(_Sensor):
    def count(self): return 1

    def get_properties(self, i):
        from astrodeck.devices.cameras.player_one import PoaProperty
        return PoaProperty(camera_id=0, name="Poseidon (rounding)", width=self.sw,
                           height=self.sh, pixel_size_um=3.76, is_color=False,
                           bayer=None, bit_depth=16, is_cooled=True, max_bin=4,
                           max_gain=600, max_offset=1000)

    def open(self, cid): pass
    def close(self, cid): pass
    def config_range(self, cid, c): return (0, 600)
    def sensor_modes(self, cid): return ["Normal"]
    def get_egain(self, cid): return 0.25
    def set_config(self, cid, k, v, is_auto=False): pass
    def get_config(self, cid, k): return 2000
    def set_image_format(self, cid, w, h, b, fmt): self._apply(w, h, b, fmt)
    def get_roi(self, cid):
        return self.applied_w, self.applied_h, self.applied_bin, self.fmt
    def set_start_pos(self, cid, x, y): self._place(x, y)
    def get_start_pos(self, cid): return self.pos
    def start_exposure(self, cid, single=True): pass
    def image_ready(self, cid): return True
    def get_image_data(self, cid, nbytes, timeout_ms=5000): return self._image(nbytes)
    def stop_exposure(self, cid): pass


#: The Poseidon-M Pro's real sensor, read off the camera itself during the
#: 2026-07-21 at-scope validation (docs/hardware/native-cameras-validation.md:
#: "Poseidon 6252x4176/3.76um/16-bit/cooled"). The width matters: 6252 is a
#: multiple of 4 and 6252//2 = 3126 is not.
POSEIDON_W, POSEIDON_H = 6252, 4176


class PoseidonSdk(FakeRoundingPoaSdk):
    """The Poseidon-M Pro, rounding the way the vendored SDK is MEASURED to.

    ``_apply`` is not a plausible-looking invention: it is
    ``PlayerOneSdk.ALIGN_W``/``ALIGN_H``, which are the widths quantized by two
    instructions read out of astrodeck/vendor/playerone/PlayerOneCamera.dll and
    cited by address in player_one_sdk.py -- ``and esi, 0FFFFFFFCh`` on the
    width, ``and edi, 0FFFFFFFEh`` on the height, both followed by a store of
    the rounded value and NO error return. Importing the constants rather than
    typing 4 and 2 here is deliberate: if the citation is ever corrected, this
    fake changes with it instead of quietly preserving a rule the DLL no longer
    has."""

    def __init__(self, **kw):
        kw.setdefault("sensor", (POSEIDON_W, POSEIDON_H))
        super().__init__(**kw)
        #: the binned size the adapter asked the SDK for, before rounding
        self.requested: tuple[int, int] | None = None

    def get_properties(self, i):
        p = super().get_properties(i)
        p.name = "Poseidon-M Pro"
        return p

    def _apply(self, w, h, b, fmt):
        self.requested = (w, h)
        # clamp to sensor/bin first, then quantize down -- the DLL's order
        w, h = min(w, self.sw // b), min(h, self.sh // b)
        self.applied_w = max(ALIGN_W, w - w % ALIGN_W)
        self.applied_h = max(ALIGN_H, h - h % ALIGN_H)
        self.applied_bin = b
        if self.fmt is None:
            self.fmt = fmt


def test_the_rule_this_module_reproduces_is_the_one_the_shipped_sdk_documents():
    """The fake is only evidence if it rounds like the DLL. This pins the two
    constants so a drive-by edit to either cannot silently turn every Poseidon
    test below into a test of a camera that does not exist."""
    assert (ALIGN_W, ALIGN_H) == (4, 2)


async def test_the_poseidon_at_bin_2_asks_for_a_width_this_sdk_will_not_give():
    """THE REACHABLE CALL SEQUENCE, with no subframe and no unusual request in
    it. Full frame, bin 2 -- what autofocus does by default (binning=2), and
    what plate solve and rotate-to-PA do on every run. 6252//2 = 3126, which is
    not a multiple of 4, so the sensor reads out 3124 and says so only if asked.

    This is the exposure the whole of #110's third candidate rests on, and until
    the DLL was read it was arithmetic about a rule nobody had checked."""
    a = PlayerOneAdapter(sdk=PoseidonSdk())
    cam = NativeCamera(a)
    await cam.connect()
    assert (cam.sensor_width, cam.sensor_height) == (POSEIDON_W, POSEIDON_H)

    f = await cam.expose(0.01, gain=0, offset=0, binning=2)

    assert a._sdk.requested == (3126, 2088), "what the adapter asked for"
    assert a._sdk.applied_w == 3124, "what the sensor settled on"
    assert a.applied_roi() == ROI(x=0, y=0, w=6248, h=4176, bin=2)
    assert a._nbytes == 3124 * 2088 * 2
    assert f.data.shape == (2088, 3124)
    for y, row in enumerate(f.data):
        assert row.min() == row.max() == y, f"row {y} is not the sensor's row {y}"


async def test_without_the_read_back_that_same_exposure_is_the_reported_picture(said):
    """The adapter exactly as it shipped on 2026-07-31, driven by the call
    sequence above: the buffer is the size the request implies, nothing errors,
    and the frame that reaches the preview, the star detector and the FITS
    writer is sheared two pixels per row and wrapped.

    Two pixels is small and that is the point -- the damage is a lean across the
    whole field, not a tear anyone would mistake for a crash. The last 4176
    pixels are buffer the sensor never wrote."""
    a = PlayerOneAdapter(sdk=PoseidonSdk())
    a._read_back = lambda roi: None
    cam = NativeCamera(a)
    await cam.connect()

    f = await cam.expose(0.01, gain=0, offset=0, binning=2)

    assert a._nbytes == 3126 * 2088 * 2, "sized from the request, as it used to be"
    assert f.data.shape == (2088, 3126), "laid out at the request, as it used to be"
    assert not said, "and not one word of complaint anywhere"

    walked = [y for y, row in enumerate(f.data) if row.min() != row.max()]
    assert len(walked) > 2000, f"only {len(walked)} of 2088 rows are mixed"
    # sensor row r begins at output column (-2r) % 3126: the lean, and the wrap
    for r in (1, 2, 100, 1563):
        lin = r * 3124
        assert f.data[lin // 3126, lin % 3126] == r
        assert lin % 3126 == (-2 * r) % 3126
    assert not f.data.ravel()[-4176:].any(), "the tail is unwritten buffer"
    assert f.data.max() == 2087, "which is not the same as an empty frame"


@pytest.mark.parametrize("bin_, asked, applied", [
    (1, 6252, 6252),     # the sensor width is already a multiple of 4
    (2, 3126, 3124),     # autofocus / plate solve / rotate-to-PA
    (3, 2084, 2084),     # divides clean again
    (4, 1563, 1560),     # and bites hardest here
])
def test_which_bins_of_this_sensor_the_quantization_bites(bin_, asked, applied):
    """So nobody has to redo the arithmetic to know whether a report is this
    bug. Only the bins whose full-frame width is not a multiple of 4 diverge,
    which on a 6252 px sensor is 2 and 4 -- and bin 2 is the one the product
    reaches for on its own."""
    a = PlayerOneAdapter(sdk=PoseidonSdk())
    a.open(0)
    a.start_exposure(seconds=0.01, gain=0, offset=0, light=True,
                     roi=ROI(x=0, y=0, w=POSEIDON_W, h=POSEIDON_H, bin=bin_))

    assert a._sdk.requested[0] == asked
    got = a.applied_roi()
    assert got.w // got.bin == applied
    assert (got.w == POSEIDON_W) == (asked == applied), (
        "a divergence must change the reported field of view, and a "
        "non-divergence must leave it alone")


#: (brand, adapter factory over the rounding SDK, its RAW16 id, its RAW8 id).
#: Every test below runs against BOTH, because the gap was in both.
BRANDS = [
    ("zwo-asi", lambda **kw: AsiCameraAdapter(sdk=FakeRoundingAsiSdk(**kw)),
     ASI_IMG_RAW16, ASI_IMG_RAW8),
    ("player-one", lambda **kw: PlayerOneAdapter(sdk=FakeRoundingPoaSdk(**kw)),
     POA_RAW16, POA_RAW8),
]


@pytest.fixture(params=BRANDS, ids=lambda b: b[0])
def brand(request):
    return request.param


@pytest.fixture(autouse=True)
def said(monkeypatch) -> list[tuple[str, str, str]]:
    """Every log line this module provokes, and NONE of them on the real bus.

    Half these tests drive a geometry mismatch, which the engine announces. Left
    on the process-global bus those entries land in ``bus._history``, a
    ``deque(maxlen=200)`` shared by the whole test process — and several suites
    read new lines as ``bus.log_history[at:]`` with ``at = len(bus.log_history)``,
    a slice that is empty forever once the ring reaches its cap. The first
    version of this module leaked four entries and turned three tests in
    test_guide_preview_source.py red without touching a line of their code."""
    out: list[tuple[str, str, str]] = []
    from astrodeck import events
    monkeypatch.setattr(events.bus, "log",
                        lambda level, message, source="hub": out.append(
                            (level, message, source)))
    return out


async def test_the_download_is_sized_from_what_the_camera_applied(brand):
    """122 rounded down to 120 means 120*64*2 bytes, not 122*64*2. Sizing the
    buffer from the request is what lets a wrong row length arrive at a byte
    count nothing can object to."""
    _name, make, _r16, _r8 = brand
    a = make()
    cam = NativeCamera(a)
    await cam.connect()
    roi = ROI(x=0, y=0, w=122, h=64, bin=1)
    await cam.expose(0.01, gain=0, offset=0, roi=roi)
    assert a._sdk.applied_w == 120
    assert a._nbytes == 120 * 64 * 2


async def test_the_frame_is_laid_out_at_the_applied_width_not_the_requested_one(brand):
    """The fix, stated as the shape of the array. Every row of the returned
    frame is one constant value — the row index the fake sensor wrote into it —
    which is only true if the layout width matches the sensor's."""
    _name, make, _r16, _r8 = brand
    cam = NativeCamera(make())
    await cam.connect()
    f = await cam.expose(0.01, gain=0, offset=0, roi=ROI(x=0, y=0, w=122, h=64, bin=1))

    assert f.data.shape == (64, 120)
    for y, row in enumerate(f.data):
        assert row.min() == row.max() == y, f"row {y} is not the sensor's row {y}"


async def test_a_geometry_the_camera_changed_is_announced_not_absorbed(brand, said):
    """Shaping at the applied width fixes the picture but not the pointing: the
    frame is a different field of view from the one that was asked for, and the
    FITS header, the plate solve and the star marks are all placed from it."""
    _name, make, _r16, _r8 = brand
    cam = NativeCamera(make())
    await cam.connect()
    await cam.expose(0.01, gain=0, offset=0, roi=ROI(x=0, y=0, w=122, h=64, bin=1))

    warnings = [m for lvl, m, src in said if lvl == "warning" and src == "camera"]
    assert warnings, "a geometry the camera changed must not pass in silence"
    msg = warnings[-1]
    assert "120x64" in msg and "122x64" in msg, msg


async def test_a_geometry_the_camera_honoured_says_nothing(brand, said):
    """The counterweight: a warning on every well-behaved exposure would train
    the user to ignore it, and 128 is already a multiple of 8."""
    _name, make, _r16, _r8 = brand
    cam = NativeCamera(make())
    await cam.connect()
    f = await cam.expose(0.01, gain=0, offset=0)
    assert f.data.shape == (SENSOR_H, SENSOR_W)
    assert not said, said


async def test_the_requested_subframe_origin_reaches_the_sensor(brand):
    """It used to be dropped: Player One pinned the origin to (0,0) and ZWO
    never set one at all, so a subframe request returned a different patch of
    the sensor and said nothing. The origin goes in binned pixels."""
    _name, make, _r16, _r8 = brand
    a = make()
    cam = NativeCamera(a)
    await cam.connect()
    await cam.expose(0.01, gain=0, offset=0, roi=ROI(x=16, y=8, w=64, h=32, bin=2))
    assert a._sdk.pos == (8, 4)
    assert a.applied_roi().x == 16 and a.applied_roi().y == 8


async def test_an_applied_bit_depth_that_is_not_raw16_is_refused(brand):
    """_shape decodes little-endian uint16 unconditionally. A RAW8 frame read
    that way pairs adjacent pixels into one — a half-width picture of the same
    sky, which is the artefact again by a different route. The adapters request
    RAW16; only the read-back can tell whether they got it."""
    _name, make, _r16, r8 = brand
    cam = NativeCamera(make(fmt=r8))
    await cam.connect()
    with pytest.raises(DeviceError) as ei:
        await cam.expose(0.01, gain=0, offset=0)
    assert "RAW16" in str(ei.value)


async def test_an_sdk_that_cannot_be_asked_reports_nothing_rather_than_the_request(brand):
    """``applied_roi`` returning the request would be the original bug wearing
    the fix's clothes: the engine would believe it had confirmation it never
    got. None means "unverified", and the engine falls back knowingly."""
    from test_zwo_asi_adapter import FakeAsiSdk
    from test_player_one_adapter import FakePoaSdk
    name, _make, _r16, _r8 = brand
    a = (AsiCameraAdapter(sdk=FakeAsiSdk(8, 6)) if name == "zwo-asi"
         else PlayerOneAdapter(sdk=FakePoaSdk(8, 6)))
    cam = NativeCamera(a)
    await cam.connect()
    f = await cam.expose(0.01, gain=0, offset=0)
    assert a.applied_roi() is None
    assert f.data.shape == (6, 8)


async def test_an_sdk_that_cannot_place_a_subframe_refuses_instead_of_centring(brand):
    """A camera that cannot honour the origin must say so. Returning a centred
    frame labelled with the requested corner is the defect this whole file is
    about."""
    from test_zwo_asi_adapter import FakeAsiSdk
    from test_player_one_adapter import FakePoaSdk
    name, _make, _r16, _r8 = brand
    a = (AsiCameraAdapter(sdk=FakeAsiSdk(64, 64)) if name == "zwo-asi"
         else PlayerOneAdapter(sdk=FakePoaSdk(64, 64)))
    cam = NativeCamera(a)
    await cam.connect()
    with pytest.raises(DeviceError) as ei:
        await cam.expose(0.01, gain=0, offset=0, roi=ROI(x=8, y=8, w=32, h=32, bin=1))
    assert "origin" in str(ei.value)


async def test_the_byte_count_alone_could_never_have_caught_this(brand):
    """Why the guard in _shape is not the fix. Run the adapter with the
    read-back removed — the state it shipped in — and the buffer the engine gets
    is EXACTLY the size the request implies, while its rows are 8 px shorter.
    Nothing about the length is wrong; only the content is."""
    _name, make, _r16, _r8 = brand
    a = make()
    a._read_back = lambda roi: None          # the pre-fix adapter, exactly
    cam = NativeCamera(a)
    await cam.connect()
    roi = ROI(x=0, y=0, w=122, h=64, bin=1)
    f = await cam.expose(0.01, gain=0, offset=0, roi=roi)

    assert a._nbytes == 122 * 64 * 2, "sized from the request, as it used to be"
    assert f.data.shape == (64, 122), "laid out at the request, as it used to be"
    walked = [y for y, row in enumerate(f.data) if row.min() != row.max()]
    assert walked, "and the rows are sheared, with no error raised anywhere"


# --------------------------------------------------------------------------
# The rule bound to the ROSTER, not to the two adapters audited today
# --------------------------------------------------------------------------
#: Entries in the keystone contract that are not a vendor brand and therefore
#: have no SDK that could be asked what it applied.
NOT_A_BRAND = {"reference-fake"}


def test_every_bundled_brand_is_audited_for_the_geometry_read_back():
    """``CONTRACT_ADAPTERS`` in test_camera_contract.py is the roster every
    bundled brand joins, and this rule has to bind on the roster rather than on
    the two adapters that happened to be read on the day it was written.

    The failure this guards is specific: a third adapter whose ``applied_roi()``
    returns its own request — the original defect wearing the fix's clothes. A
    compliant fake cannot expose that, because request and read-back coincide;
    it takes a sensor that DISAGREES, which is what ``BRANDS`` above supplies.
    So a brand appending itself to the contract list fails here until it brings
    one."""
    audited = {name for name, *_ in BRANDS}
    missing = sorted(n for n, _f in CONTRACT_ADAPTERS
                     if n not in NOT_A_BRAND
                     and n.removesuffix("-fake") not in audited)
    assert not missing, (
        f"{missing} is in the keystone camera contract but has no rounding-SDK "
        "fake in BRANDS here, so nothing checks that its applied_roi() reports "
        "what the sensor did rather than what it was asked for. Add one: a fake "
        "that rounds the requested width down and fills the buffer at ITS width.")


@pytest.mark.parametrize("entry", CONTRACT_ADAPTERS, ids=lambda e: e[0])
async def test_a_reported_geometry_is_one_the_delivered_frame_actually_has(entry):
    """The half of the rule that a compliant sensor CAN check, run over the
    whole contract roster: ``applied_roi()`` is either None — "this brand cannot
    be asked", and the engine falls back to the request knowingly — or a
    geometry the returned array really has. An adapter that reports a shape its
    own picture does not have has invented a measurement, which is the whole
    family of bugs this slice exists for."""
    _name, factory = entry
    a = factory()
    cam = NativeCamera(a)
    await cam.connect()
    f = await cam.expose(0.01, gain=0, offset=0)
    applied = a.applied_roi()
    if applied is None:
        assert f.data.shape == (cam.sensor_height, cam.sensor_width)
        return
    assert f.data.shape == (applied.h // applied.bin, applied.w // applied.bin)
