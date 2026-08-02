"""Where the "Guide cam" panel's picture actually comes from.

Field report, 2026-07-31: turning on Guide cam under Capture did nothing. The
rig's ZWO ASI guide camera was connected and idle the whole time — the route
just never asked it. ``/api/guide/frame.png`` gated on ``hub.guider``, and
``hub.guider`` is None on a native rig unless a profile explicitly overrides the
``guider`` role: ``NativeBackend.roles`` deliberately excludes it, and the
Equipment screen even tells the user there is "no guider device to assign here".
So the panel 404'd forever on a rig that had everything it needed.

Second half, measured on the rig 2026-08-01: the route now answers, and what it
answers with is HTTP 200, 223 bytes, a valid 512x288 PNG that renders black,
byte-identical across three calls four seconds apart. The camera was connected,
no guide loop was running, and nothing about the request reached the run log. So
the source selection reached its SUCCESS branch and encoded a CONSTANT array:
``auto_stretch`` maps any constant array below full scale to the same black
image (median 0, MAD 0), and a black rectangle with no explanation is the same
defect the user first reported, one layer down.

What the 223 bytes do NOT say is which constant, or what shape — see
``test_the_served_bytes_identify_a_constant_array_and_nothing_more``. The first
repair read them as "all zero at 1920x1080, so nothing wrote the buffer" and
shipped a refusal that asserted that cause and prescribed a reconnect; the tests
below hold it to what it can actually see.

``Hub.guide_preview_png`` is the source-selection rule, and these are its edges.
Nothing here needs hardware.

NOTE ON OWNERSHIP: this is a NEW file rather than an addition to
tests/test_guide_frame.py, which belongs to another lane this run.
"""
from __future__ import annotations

import numpy as np
import pytest

from astrodeck.devices.base import Camera, CameraFrame
from astrodeck.hub import (GUIDE_PREVIEW_EXPOSURE_S, GUIDE_PREVIEW_GAIN,
                           GUIDE_PREVIEW_NOTE_TTL_S, Hub)


class _FakeGuideCam(Camera):
    """The smallest thing that can hand back a frame."""

    def __init__(self, name="ZWO ASI guide"):
        super().__init__(name)
        self.connected = True
        self.exposures: list[float] = []
        self.gains: list[int] = []
        self.fail: str | None = None
        #: incremented the moment expose() is ENTERED, before any await — a
        #: second start is the overlap we must never produce, and counting on
        #: return would miss it.
        self.started = 0
        #: when set, expose blocks on it: a real 1s exposure + USB readout + PNG
        #: encode can outlast the panel's 2.5s poll, and instant fakes are the
        #: reason that overlap was invisible.
        self.gate: "asyncio.Event | None" = None
        #: exact pixels to hand back. None = the default frame below (a flat
        #: background with one star), which is what a working camera looks like.
        #: Set it to model what the rig actually returned on 2026-08-01.
        self.data = None

    async def connect(self) -> None: self.connected = True
    async def disconnect(self) -> None: self.connected = False
    async def abort_exposure(self) -> None: pass
    async def get_temperature(self): return None
    async def set_temperature(self, c): pass
    async def set_cooler(self, on): pass

    async def expose(self, seconds, gain, offset, binning=1, light=True,
                     save=False, target="") -> CameraFrame:
        import numpy as np
        self.started += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError(self.fail)
        self.exposures.append(seconds)
        self.gains.append(gain)
        if self.data is not None:
            data = self.data
        else:
            data = np.full((64, 64), 300, dtype="uint16")
            data[32, 32] = 40000
        return CameraFrame(data=data, exposure_s=seconds, gain=gain,
                           offset=offset, binning=binning, bayer_pattern=None,
                           temperature_c=None, timestamp=0.0)


class _Guider:
    name = "PHD2"
    connected = True

    def __init__(self, png: bytes | None): self.png = png
    async def guide_frame(self): return self.png

    def stats(self):
        # poll_status publishes the guider's own stats block; nothing in this
        # file asserts on it, it just has to exist for status to build.
        import types
        return types.SimpleNamespace(state="idle")


def _guider_png(data) -> bytes:
    """What a guider hands back: an image it has already stretched and encoded
    itself. The hub gets no ADU from this path, only bytes."""
    from astrodeck.imaging.processing import to_png
    return to_png(np.asarray(data), stretch=True, max_width=256)


async def test_a_connected_guide_camera_serves_the_preview_with_no_guider():
    """THE bug. No guider role assigned — which is the normal state of a native
    rig — and the panel must still show the guide field."""
    hub = Hub()
    cam = _FakeGuideCam()
    hub.devices["guide_camera"] = cam

    png, reason = await hub.guide_preview_png()
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n", reason
    assert cam.exposures == [GUIDE_PREVIEW_EXPOSURE_S]


async def test_a_running_guider_wins_over_the_camera():
    """While a guide loop owns the sensor, its own last frame is the truth and
    exposing underneath it would be a bug, not a preview."""
    hub = Hub()
    cam = _FakeGuideCam()
    hub.devices["guide_camera"] = cam
    hub.guider = _Guider(b"\x89PNG\r\n\x1a\nfrom-the-guider")

    png, _ = await hub.guide_preview_png()
    assert png == b"\x89PNG\r\n\x1a\nfrom-the-guider"
    assert cam.exposures == [], "must not touch a camera the guider is driving"


async def test_a_guider_with_no_image_says_which_guider():
    """PHD2 in NINA mode exposes no raw frame. That is a fact about the guider,
    so name it — "unavailable" was the entire complaint."""
    hub = Hub()
    hub.guider = _Guider(None)
    png, reason = await hub.guide_preview_png()
    assert png is None
    assert "PHD2" in reason


async def test_one_camera_in_both_roles_is_left_to_the_imaging_train():
    """An OAG rig can assign the same physical camera to both roles. Grabbing a
    preview frame there would take the sensor out from under a running
    sequence — so refuse, and say where its frames DO appear."""
    hub = Hub()
    cam = _FakeGuideCam("Player One")
    hub.devices["guide_camera"] = cam
    hub.devices["camera"] = cam

    png, reason = await hub.guide_preview_png()
    assert png is None
    assert cam.exposures == []
    assert "imaging camera" in reason and "Player One" in reason


async def test_a_running_sequence_does_not_block_the_guide_preview(monkeypatch):
    """``busy_label`` is a HUB-WIDE label for the IMAGING train, and by the time
    we get here the guide camera is known to be a DIFFERENT device from the
    imaging one — so refusing on it printed "ZWO ASI is busy (capturing)" about a
    camera that was sitting idle, which is the instrument-lying-about-itself
    defect this whole run exists to remove.

    It also disabled the panel at the one moment it is wanted: you check the
    guide field for dew or cloud precisely WHILE a sequence is running."""
    hub = Hub()
    cam = _FakeGuideCam()
    hub.devices["guide_camera"] = cam
    hub.devices["camera"] = _FakeGuideCam("imaging cam")     # a different sensor
    monkeypatch.setattr(type(hub), "busy_label", property(lambda self: "capturing"))

    png, reason = await hub.guide_preview_png()
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n", reason
    assert cam.exposures == [GUIDE_PREVIEW_EXPOSURE_S]


async def test_the_preview_gain_is_clamped_to_what_the_camera_accepts():
    """GUIDE_PREVIEW_GAIN is a brightness wish, not a hardware fact. An ASI120MM
    Mini — the commonest ZWO guide camera there is — tops out at 100, and the ZWO
    SDK RAISES on an out-of-range control value instead of clipping it. Sending
    200 unclamped turns the preview back into "could not deliver a frame" on
    exactly the rigs this fix is for."""
    hub = Hub()
    cam = _FakeGuideCam("ASI120MM Mini")
    cam.max_gain = 100                       # what the SDK's gain_range reported
    hub.devices["guide_camera"] = cam

    png, reason = await hub.guide_preview_png()
    assert png, reason
    assert cam.gains == [100]


async def test_a_camera_that_reports_no_gain_ceiling_gets_the_wish_unchanged():
    """0 means "this backend does not report a ceiling" (NINA with no GainMax, an
    Alpaca camera without gainmax) — clamping to it would send gain 0, and
    inventing a limit is the guess the codebase's own rule forbids. Let the
    driver refuse it by name instead."""
    hub = Hub()
    cam = _FakeGuideCam()
    cam.max_gain = 0
    hub.devices["guide_camera"] = cam

    png, reason = await hub.guide_preview_png()
    assert png, reason
    assert cam.gains == [GUIDE_PREVIEW_GAIN]


async def test_two_overlapping_polls_share_one_exposure():
    """The panel swaps a cache-busted <img src> every 2.5s whether or not the
    previous one landed, and a 1s exposure plus a full-sensor USB readout plus a
    PNG encode can outlast that. A second ``start_exposure`` on a camera that is
    already exposing races two readouts over one buffer — torn or errored frames,
    which the panel renders as the same uninformative "unavailable", sending the
    next debugging session back to the beginning."""
    import asyncio

    hub = Hub()
    cam = _FakeGuideCam()
    cam.gate = asyncio.Event()
    hub.devices["guide_camera"] = cam

    first = asyncio.create_task(hub.guide_preview_png())
    await asyncio.sleep(0.02)
    second = asyncio.create_task(hub.guide_preview_png())
    await asyncio.sleep(0.02)
    assert cam.started == 1, "the second poll must JOIN the exposure in flight"

    cam.gate.set()
    (png1, _), (png2, _) = await asyncio.gather(first, second)
    assert png1 and png1 == png2
    assert cam.started == 1 and cam.exposures == [GUIDE_PREVIEW_EXPOSURE_S]


async def test_a_client_that_walks_away_does_not_abort_the_exposure():
    """The request task is cancelled when the browser drops the <img> load. That
    must cancel the WAIT, not the exposure: killing a camera mid-readout to serve
    a panel nobody is watching is how a guide camera ends up wedged for the run
    that follows."""
    import asyncio

    hub = Hub()
    cam = _FakeGuideCam()
    cam.gate = asyncio.Event()
    hub.devices["guide_camera"] = cam

    first = asyncio.create_task(hub.guide_preview_png())
    await asyncio.sleep(0.02)
    first.cancel()
    second = asyncio.create_task(hub.guide_preview_png())
    await asyncio.sleep(0.02)
    cam.gate.set()

    png, reason = await second
    assert png, reason
    assert cam.started == 1, "the surviving poll rode the exposure already running"


async def test_a_camera_that_refuses_reports_the_refusal_not_a_shrug():
    """A blank "unavailable" note is what made this look like nothing happened.
    The camera's own error is the only thing that tells the user whether to
    re-seat a USB cable or go and look at the sky."""
    hub = Hub()
    cam = _FakeGuideCam()
    cam.fail = "camera not responding"
    hub.devices["guide_camera"] = cam
    png, reason = await hub.guide_preview_png()
    assert png is None
    assert "camera not responding" in reason


async def test_nothing_connected_says_what_to_connect():
    hub = Hub()
    png, reason = await hub.guide_preview_png()
    assert png is None
    # A dead end with no exit is the thing house style forbids: name the screen.
    assert "Equipment" in reason or "PHD2" in reason


# --------------------------------------------------- the reason reaches the UI

async def test_the_refusal_reaches_the_panel_through_status():
    """The 404 detail is careful and specific and NOBODY READS IT: the preview
    panel assigns an ``<img src>``, so all it can observe is that the load
    errored, and every named refusal collapses into one generic sentence on the
    way. So the reason also rides status.guide_camera, the same additive way
    filterwheel.moving does."""
    hub = Hub()
    cam = _FakeGuideCam()
    cam.fail = "camera not responding"
    hub.devices["guide_camera"] = cam

    await hub.guide_preview_png()
    st = await hub.poll_status()
    assert "camera not responding" in st["guide_camera"]["preview_reason"]


async def test_a_working_preview_publishes_no_reason():
    """Absent, not empty: a key that is always there invites a UI that always
    renders a line."""
    hub = Hub()
    hub.devices["guide_camera"] = _FakeGuideCam()

    png, _ = await hub.guide_preview_png()
    assert png
    st = await hub.poll_status()
    assert "preview_reason" not in st["guide_camera"]


async def test_the_reason_expires_rather_than_describing_a_fixed_camera(monkeypatch):
    """A reason survives only as long as the panel that produced it is plausibly
    still polling. Left to sit, it would describe a camera that has since been
    reconnected — a confident stale claim, which is the class of bug this run is
    about, not a fix for it."""
    import astrodeck.hub as hub_mod

    hub = Hub()
    cam = _FakeGuideCam()
    cam.fail = "USB read failed"
    hub.devices["guide_camera"] = cam
    await hub.guide_preview_png()
    assert hub.guide_preview_note()

    base = hub_mod.time.monotonic()
    monkeypatch.setattr(hub_mod.time, "monotonic",
                        lambda: base + GUIDE_PREVIEW_NOTE_TTL_S + 1)
    assert hub.guide_preview_note() == ""


@pytest.mark.parametrize("detail", ["Equipment", "guide camera"])
def test_the_endpoint_passes_the_reason_through_as_the_404_detail(detail, tmp_path,
                                                                 monkeypatch):
    """Still 404, never 500 — but a 404 that says which of the several possible
    nothings this is."""
    import astrodeck.api.app as app_module
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_module
    from astrodeck.config import ConfigStore
    from fastapi.testclient import TestClient

    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")

    app = app_module.create_app()
    with TestClient(app) as c:
        r = c.get("/api/guide/frame.png")
        assert r.status_code == 404
        assert detail in r.json()["detail"]


# ------------------------------------- a success that is not a picture (#115)
#
# Everything above asks "did we ask the right device". This block asks the next
# question, the one the rig answered on 2026-08-01: what came back was a 200 with
# a PNG that renders black. An exposure call that returns is not the same event
# as a frame, and the encoder cannot tell the difference — a constant array
# stretches to a constant image and looks exactly like a legitimately dark one.


def test_the_served_bytes_identify_a_constant_array_and_nothing_more():
    """The evidence, pinned, because the first repair over-read it and the
    over-reading was load-bearing: it chose the refusal's wording.

    223 bytes at 512x288 says CONSTANT. It does not say which constant (0 and
    700 encode identically) and it does not say what shape (1920x1080 and
    512x288 encode identically). The only value it rules out is full scale.
    Anyone tempted to write "the buffer was all zero" back into this module gets
    stopped here."""
    from astrodeck.imaging.processing import to_png

    def enc(a):
        return to_png(a, stretch=True, max_width=512)

    served = enc(np.zeros((1080, 1920), dtype="uint16"))
    assert len(served) == 223
    assert enc(np.full((1080, 1920), 700, dtype="uint16")) == served, \
        "the value of the constant is not recoverable from the response"
    assert enc(np.zeros((288, 512), dtype="uint16")) == served, \
        "the shape of the frame is not recoverable either"
    assert enc(np.full((1080, 1920), 65535, dtype="uint16")) != served, \
        "full scale is the one value the bytes exclude"


async def test_a_constant_frame_is_refused_instead_of_encoded_as_black():
    """MEASURED: a 512x288 PNG that renders black, byte-identical across three
    calls four seconds apart. A frame with no variation carries no picture, and
    encoding it produced a black rectangle the panel could not explain — which is
    indistinguishable from the "nothing happens" the whole ticket is about."""
    hub = Hub()
    cam = _FakeGuideCam()
    cam.data = np.zeros((1080, 1920), dtype="uint16")
    hub.devices["guide_camera"] = cam

    png, reason = await hub.guide_preview_png()
    assert png is None, "a frame with no variation must not be served as a picture"
    assert "ZWO ASI guide" in reason        # which instrument
    assert "1920x1080" in reason            # what IT returned, measured here
    assert "reads 0" in reason
    assert "Equipment" in reason, "a blocked panel has to name a way forward"


async def test_the_refusal_reports_the_measurement_and_declines_the_diagnosis():
    """#114, one screen over: the shipped text said "A sensor read always carries
    read noise, so nothing was exposed or nothing was downloaded — reconnect the
    guide camera under Equipment", which is a cause and a cure invented from a
    signal that cannot distinguish them.

    Two ways to get here with nothing broken. The preview passes offset 0, which
    ZWO writes to ASI_OFFSET (the black level), so a capped sensor clips its read
    noise against zero and legitimately comes back flat — cap the scope indoors to
    check the camera and the old text told you the camera never exposed and sent
    you to reconnect working hardware. And a sensor saturated in daylight WAS
    exposed and WAS downloaded, so every clause of that sentence was false.

    The measurement is the finding. The cause is not visible from here."""
    for name, data in (("dust cap on, black level 0",
                        np.zeros((1080, 1920), dtype="uint16")),
                       ("daylight, fully saturated",
                        np.full((1080, 1920), 65535, dtype="uint16"))):
        hub = Hub()
        cam = _FakeGuideCam()
        cam.data = data
        hub.devices["guide_camera"] = cam
        png, reason = await hub.guide_preview_png()

        assert png is None, name
        assert "no variation" in reason, name          # what was measured
        assert "does not say why" in reason, name      # and what was not
        for invented in ("nothing was exposed", "nothing was downloaded",
                         "always carries read noise", "reported success"):
            assert invented not in reason, f"{name}: {invented!r} is a guess"


async def test_a_uniformly_saturated_frame_is_refused_on_the_same_evidence():
    """65535 everywhere carries the same information as 0 everywhere: zero
    variance across a megapixel. A brightness test would have passed this one
    straight through — the signal is that nothing in the buffer varies."""
    hub = Hub()
    cam = _FakeGuideCam()
    cam.data = np.full((1080, 1920), 65535, dtype="uint16")
    hub.devices["guide_camera"] = cam

    png, reason = await hub.guide_preview_png()
    assert png is None
    assert "reads 65535" in reason


async def test_a_faint_frame_with_real_noise_is_still_served():
    """The refusal keys on variance, not on level. A short guide exposure at low
    gain under a genuinely dark sky is near-black and is a perfectly good frame;
    refusing it would trade one silent lie for another."""
    hub = Hub()
    cam = _FakeGuideCam()
    rng = np.random.default_rng(7)
    cam.data = rng.integers(4, 12, size=(64, 64), dtype=np.uint16)
    hub.devices["guide_camera"] = cam

    png, reason = await hub.guide_preview_png()
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n", reason
    assert reason == ""


async def test_a_frame_that_is_not_two_dimensional_is_named_not_encoded():
    """``to_png`` refuses a non-2-D array by raising, which would have surfaced
    as the generic "could not deliver a frame: preview encode needs…". Say what
    the camera returned instead, in the camera's own terms."""
    hub = Hub()
    cam = _FakeGuideCam()
    cam.data = np.zeros((3, 64, 64), dtype="uint16")
    hub.devices["guide_camera"] = cam

    png, reason = await hub.guide_preview_png()
    assert png is None
    assert "not an image" in reason and "ZWO ASI guide" in reason


async def test_the_empty_buffer_reaches_the_panel_through_status():
    """The panel renders an ``<img>`` and can observe only that the load failed,
    so the refusal has to ride status like every other named one — otherwise
    this fix just swaps a black rectangle for a broken-image icon."""
    hub = Hub()
    cam = _FakeGuideCam()
    cam.data = np.zeros((1080, 1920), dtype="uint16")
    hub.devices["guide_camera"] = cam

    await hub.guide_preview_png()
    st = await hub.poll_status()
    assert "no variation" in st["guide_camera"]["preview_reason"]
    assert "preview_ok" not in st["guide_camera"]


# ----------------------------------------------- three states, not two (#115)


async def test_a_camera_nobody_has_asked_yet_publishes_neither_verdict():
    """Assigned and connected, panel never opened. There is no reason to report
    and no frame to report either — and saying nothing is only honest as long as
    a delivered frame says something."""
    hub = Hub()
    hub.devices["guide_camera"] = _FakeGuideCam()

    gc = (await hub.poll_status())["guide_camera"]
    assert "preview_reason" not in gc and "preview_ok" not in gc


async def test_a_delivered_frame_is_distinguishable_from_a_camera_nobody_asked():
    """Recording an outcome only on refusal made success carry no information at
    all: a working preview and a camera that had never been asked published the
    identical empty ``guide_camera`` block, so the panel had nothing to check the
    picture against when the picture was black."""
    hub = Hub()
    hub.devices["guide_camera"] = _FakeGuideCam()

    png, _ = await hub.guide_preview_png()
    assert png
    gc = (await hub.poll_status())["guide_camera"]
    assert gc["preview_ok"] is True
    assert "preview_reason" not in gc


async def test_the_delivered_frame_verdict_expires_like_the_reason(monkeypatch):
    """Same rule as the refusal note, for the same reason: "a frame arrived"
    from five minutes ago describes a camera that may since have been unplugged.
    An expired verdict falls back to "nobody has asked recently", not to a
    claim."""
    import astrodeck.hub as hub_mod

    hub = Hub()
    hub.devices["guide_camera"] = _FakeGuideCam()
    await hub.guide_preview_png()
    assert hub.guide_preview_ok()

    base = hub_mod.time.monotonic()
    monkeypatch.setattr(hub_mod.time, "monotonic",
                        lambda: base + GUIDE_PREVIEW_NOTE_TTL_S + 1)
    assert hub.guide_preview_ok() is False


# ------------------------------------- the exposure leaves a trace (#115)


async def test_the_preview_writes_one_log_line_per_change_of_outcome(bus_lines):
    """Half the cost of diagnosing the black frame was that the run log had
    NOTHING about the request — no exposure, no error — so "was the camera even
    asked?" could only be answered from the response bytes. One line per
    transition answers it; a line per request would bury the log, since the
    panel re-requests every 2.5s."""
    hub = Hub()
    cam = _FakeGuideCam()
    hub.devices["guide_camera"] = cam

    def guide_lines():
        return [m for _lvl, m, src in bus_lines if src == "guide"]

    await hub.guide_preview_png()
    await hub.guide_preview_png()
    lines = guide_lines()
    assert len(lines) == 1, "a repeated poll with the same outcome is not news"
    # The ADU range is the one number that separates a read from an allocation.
    assert "ZWO ASI guide" in lines[0] and "64x64" in lines[0]
    assert "300..40000 ADU" in lines[0]

    cam.data = np.zeros((64, 64), dtype="uint16")
    await hub.guide_preview_png()
    lines = guide_lines()
    assert len(lines) == 2, "the moment it stops being a frame IS news"
    assert "no variation" in lines[1]
    # The exposure REQUEST belongs in the log, not in the user-facing refusal:
    # offset 0 is the ZWO black level, and "flat at offset 0" is the difference
    # between a capped sensor and a dead one for whoever reads this later.
    assert "offset 0" in lines[1] and "gain" in lines[1]


# ------------------------------- the shared exposure vs a reconnect (#115)


async def test_a_reconnected_camera_is_exposed_rather_than_joining_the_old_one():
    """The single-flight task is shared by everyone who asks while it runs, and
    a reconnect swaps ``devices['guide_camera']`` for a NEW object with a fresh
    SDK handle. Joining the exposure started on the closed one would answer for
    a camera that no longer exists — the same class of stale confident claim as
    serving the frame before last."""
    import asyncio

    hub = Hub()
    old = _FakeGuideCam("ZWO ASI guide")
    old.gate = asyncio.Event()
    hub.devices["guide_camera"] = old

    first = asyncio.create_task(hub.guide_preview_png())
    await asyncio.sleep(0.02)
    assert old.started == 1

    new = _FakeGuideCam("ZWO ASI guide")          # what a reconnect leaves behind
    hub.devices["guide_camera"] = new
    png, reason = await hub.guide_preview_png()
    assert png, reason
    assert new.started == 1, "the live handle must be the one that is asked"

    old.gate.set()
    await first


# ----------------------- the OTHER source has the same defect (#115 review)
#
# Everything above drives the guide CAMERA. Every rig running PHD2, NINA, the
# sim or the native guider takes the branch above it, and the first repair left
# that branch at "if png: return png" — no inspection, no log line — while the
# same commit began publishing preview_ok for it. So on those rigs the black
# rectangle survived AND acquired a positive claim that a frame had arrived,
# which is worse than the silence it replaced.


async def test_a_guider_that_returns_a_blank_image_is_refused_like_the_camera(bus_lines):
    """PHD2 handing back a uniform star image is the same event as an empty
    camera buffer, one encode later: bytes that render as a rectangle. The hub
    only ever sees a guider's frame already stretched and encoded, so it asks
    the same question of the pixels it can decode."""
    hub = Hub()
    hub.guider = _Guider(_guider_png(np.zeros((60, 60), dtype="uint16")))

    png, reason = await hub.guide_preview_png()
    assert png is None, "an image with no variation is not a guide field"
    assert "PHD2" in reason and "no variation" in reason
    assert "reads 0" in reason
    guide_lines = [m for _lvl, m, src in bus_lines if src == "guide"]
    assert guide_lines, "the guider path used to leave no trace at all"


async def test_a_blank_guider_image_never_publishes_that_a_frame_arrived():
    """The exact repro from review: a guider whose ``guide_frame()`` returns an
    all-black PNG got ``preview_ok: True`` on status. A panel checking that key
    would have been told the preview was working while it showed a black
    rectangle."""
    hub = Hub()
    hub.guider = _Guider(_guider_png(np.zeros((60, 60), dtype="uint16")))

    await hub.guide_preview_png()
    gc = (await hub.poll_status())["guide_camera"]
    assert "preview_ok" not in gc
    assert "no variation" in gc["preview_reason"]


async def test_a_guider_image_with_a_star_in_it_is_served_and_vouched_for(bus_lines):
    """The other half of the same rule: a real guider frame must still reach the
    panel, and must be distinguishable from a guider nobody has asked yet."""
    from astrodeck.events import bus

    star = np.full((60, 60), 300, dtype="uint16")
    star[30, 30] = 40000
    hub = Hub()
    hub.guider = _Guider(_guider_png(star))

    png, reason = await hub.guide_preview_png()
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n", reason
    assert reason == ""
    gc = (await hub.poll_status())["guide_camera"]
    assert gc["preview_ok"] is True
    lines = [m for _lvl, m, src in bus_lines if src == "guide"]
    # The level SPAN is what separates a picture from a rectangle, the same way
    # the ADU range does on the camera path. Its exact ends belong to the display
    # stretch, so assert the span exists rather than pinning the pipeline.
    assert len(lines) == 1 and "PHD2" in lines[0]
    lo, hi = (int(x) for x in lines[0].split("display levels ")[1].split(".."))
    assert lo < hi


async def test_bytes_the_server_cannot_decode_are_served_but_not_vouched_for():
    """Pillow failing to open the bytes is a fact about this server, not about
    the guider's image — a browser may well render what we could not read. So
    forward them (refusing on our own blindness would blank a working panel) and
    do NOT publish preview_ok, which would be a verdict on pixels nobody looked
    at. Neither key is the honest answer here: nothing recent is known."""
    hub = Hub()
    hub.guider = _Guider(b"\x89PNG\r\n\x1a\nnot really a png")

    png, reason = await hub.guide_preview_png()
    assert png == b"\x89PNG\r\n\x1a\nnot really a png"
    assert reason == ""
    gc = (await hub.poll_status())["guide_camera"]
    assert "preview_ok" not in gc and "preview_reason" not in gc
