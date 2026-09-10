"""Wave S7's additions to the 2 s status frame, each one a fact the UI was
previously guessing at.

Everything here rides ``Hub.poll_status``. That matters: these are not new
endpoints to poll, they are values the client already receives every two
seconds, and a value that only exists behind a second fetch is a value the
screen renders stale.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from astrodeck.api.redact import _redact_site_for
from astrodeck.auth import admin_principal, principal_for_role
from astrodeck.config import Optics, config_store

from _simhub import sim_hub  # noqa: F401 (fixture import)


# ------------------------------------------------------------- mount ceiling

async def test_the_mount_publishes_the_rate_it_can_actually_slew(sim_hub):
    """D-RIG-4. The slew pad used to clamp against a hard-coded 0.6 deg/s -- a
    guess about somebody else's gearbox, applied to an AM5 measured at 1.44.
    The driver now says, and null means "this one cannot".

    Sabotage: publish ``TOUCH_MAX_RATE_DEG_S`` instead of the driver value."""
    hub = sim_hub
    tel = hub.devices["telescope"]

    # THE MUTE BACKEND IS BUILT, not assumed. The sim mount DECLARES 1.44 since
    # S7L wired the ceiling through, so reading "null" off the untouched sim was
    # grading the simulator's silence rather than the hub's rule. The real mute
    # backends are Alpaca and NINA, and neither is instantiable here.
    tel.max_rate_deg_s = None
    status = await hub.poll_status()
    assert "max_rate_deg_s" in status["mount"], "the key must be PRESENT"
    assert status["mount"]["max_rate_deg_s"] is None, "a mute backend says null"

    tel.max_rate_deg_s = 1.44
    status = await hub.poll_status()
    assert status["mount"]["max_rate_deg_s"] == pytest.approx(1.44)


# --------------------------------------------------------------------- optics

async def test_f_ratio_is_derived_and_null_until_the_aperture_is_filled_in(sim_hub):
    """D-SET-1. An f/5.3 printed next to a frame is read as a fact about the
    rig, so an unset aperture has to read as "we do not know" rather than as a
    number derived from a guess. There is no camera fallback and none is
    possible.

    Sabotage: apply the reducer to the ratio."""
    hub = sim_hub
    config_store.set_optics(Optics(focal_length_mm=530.0, aperture_mm=0.0))
    assert hub.effective_optics()["f_ratio"] is None
    assert hub.effective_optics()["aperture_mm"] == 0.0

    config_store.set_optics(Optics(focal_length_mm=530.0, aperture_mm=100.0))
    assert hub.effective_optics()["f_ratio"] == pytest.approx(5.3)


async def test_a_reducer_is_recorded_and_changes_nothing(sim_hub):
    """``reducer`` is a RECORD, not a multiplier. If USE THE REDUCED FOCAL
    LENGTH was pressed then ``focal_length_mm`` already carries it, and
    applying it again here would double-count -- silently mis-framing every
    plan and mis-hinting every solve.

    Sabotage: multiply ``focal_length_mm`` (or ``f_ratio``) by the reducer."""
    hub = sim_hub
    config_store.set_optics(Optics(focal_length_mm=530.0, aperture_mm=100.0,
                                   reducer=0.5))
    opt = hub.effective_optics()
    assert opt["reducer"] == pytest.approx(0.5)
    assert opt["focal_length_mm"] == pytest.approx(530.0)
    assert opt["f_ratio"] == pytest.approx(5.3)


# ------------------------------------------------------------ temperature comp

async def test_temp_comp_rides_the_status_frame_on_a_default_config(sim_hub):
    """D-RIG-2. The Focus screen has to be able to show what compensation would
    do BEFORE it does it, and it has to show the setting to the person
    configuring it -- which means the node exists on a rig with no run loaded
    and the feature switched off.

    Sabotage: publish the node only while a plan is running."""
    hub = sim_hub
    status = await hub.poll_status()
    tc = status["focuser"]["temp_comp"]
    assert tc["enabled"] is False
    assert tc["steps_per_c"] == pytest.approx(0.0)
    for key in ("reference_temp_c", "reference_position", "predicted_position",
                "last_move_steps", "last_reason"):
        assert key in tc, key


async def test_a_dead_temperature_probe_does_not_cost_the_position(sim_hub):
    """The position is the one number the focus screen cannot work without, and
    it used to share a try/except with the temperature -- so an EAF with an
    unplugged probe took the whole focuser node off the status frame.

    Sabotage: read the temperature back inside the position's own try."""
    hub = sim_hub
    foc = hub.devices["focuser"]

    async def _raises():
        raise RuntimeError("probe unplugged")
    foc.get_temperature = _raises

    status = await hub.poll_status()
    assert status["focuser"]["position"] is not None
    assert status["focuser"]["max"] > 0
    assert status["focuser"]["temperature"] is None
    # and the compensation node still answers, from the config
    assert status["focuser"]["temp_comp"]["enabled"] is False


# ------------------------------------------------------------------- dew loop

class _StubDew:
    """A dew controller that has ticked once."""

    def __init__(self, snap):
        self._snap = snap

    def snapshot(self):
        return self._snap


async def test_the_dew_node_is_absent_until_the_loop_has_something_to_say(sim_hub):
    """D-RIG-3. ABSENT, not a node full of nulls: "the loop ran and found
    nothing" and "the loop has not run" are different sentences, and only one
    of them should make a screen draw a dew row.

    Sabotage: publish ``{}`` when there is no controller (or no snapshot)."""
    hub = sim_hub
    assert "dew" not in await hub.poll_status()

    # attached but not yet ticked
    hub.dew_controller = _StubDew(None)
    assert "dew" not in await hub.poll_status()

    hub.dew_controller = _StubDew({"enabled": True, "following": True,
                                   "margin_c": 3.0, "power_pct": 50,
                                   "reason": "following the margin"})
    status = await hub.poll_status()
    assert status["dew"]["power_pct"] == 50
    assert status["dew"]["margin_c"] == pytest.approx(3.0)


async def test_a_broken_dew_controller_cannot_break_the_status_frame(sim_hub):
    """Guarded like every other block on this hot path: the newest thing here
    must never be able to 500 the poll every client depends on.

    Sabotage: drop the try/except around the snapshot call."""
    hub = sim_hub

    class _Angry:
        def snapshot(self):
            raise RuntimeError("no")

    hub.dew_controller = _Angry()
    status = await hub.poll_status()
    assert "dew" not in status
    assert status["camera"]["width"] > 0        # the frame still arrives


# ------------------------------------------------------------ colour identity

@pytest.mark.parametrize("reported,expected", [
    ("RGGB", "RGGB"),
    # the native ZWO / Player One bindings report the TOP-LEFT PAIR only
    ("RG", "RGGB"),
    ("bg", "BGGR"),
    (None, None),
    ("", None),
    ("XYZW", None),
])
async def test_the_camera_says_what_colour_it_is_in_one_spelling(
        sim_hub, reported, expected):
    """Normalised, never raw. The same camera reports "RGGB" over Alpaca and
    "RG" through the native binding, and a client comparing against four
    letters would decide it was mono on one backend and colour on the other.
    An unrecognised pattern is None: guessing would swap red for blue over the
    whole session.

    Sabotage: publish ``cam.bayer_pattern`` raw."""
    hub = sim_hub
    hub.devices["camera"].bayer_pattern = reported

    status = await hub.poll_status()
    assert status["camera"]["bayer_pattern"] == expected
    assert status["camera"]["is_color"] is (expected is not None)


async def test_binning_does_not_change_what_colour_the_sensor_is(sim_hub):
    """A sensor keeps its colour filter array whatever the readout does.
    ``effective_bayer`` answers the DIFFERENT question of whether a particular
    FRAME can still be debayered -- a 2x2 bin has already mixed the colours in
    the pixels that arrive -- and deriving this node from it would make a
    camera stop being colour because somebody changed a dropdown.

    Sabotage: derive the node from ``effective_bayer(pattern, binning)``."""
    hub = sim_hub
    cam = hub.devices["camera"]
    cam.bayer_pattern = "RGGB"

    first = await hub.poll_status()
    await hub.capture(0.2, 100, 30, 4, save=False, target="")
    second = await hub.poll_status()

    assert first["camera"]["bayer_pattern"] == "RGGB"
    assert second["camera"]["bayer_pattern"] == "RGGB"
    assert second["camera"]["is_color"] is True


# --------------------------------------------------------- video capability

class _Caps:
    """Stand-in for a native adapter's ``CameraCapabilities``."""

    def __init__(self, *, burst_supported=False, roi_align=(8, 2), max_fps=60.0):
        self.burst_supported = burst_supported
        self.roi_align = roi_align
        self.max_fps = max_fps


async def test_the_camera_says_whether_it_can_record_before_the_press(sim_hub):
    """D-RIG-1. Without this the only way to learn a camera has no video path
    is to start a recording and read a 409 back -- a control that looks live,
    costs a round trip, and then explains it was never available.

    Two ways to have a path, and both must count: a camera that DECLARES burst
    support, and any natively-driven camera (which gets the generic
    expose-in-a-loop fallback). This mirrors the refusal in
    imaging/video_routes.py.

    Sabotage: publish ``video_path`` from ``burst_supported`` alone, so every
    natively-driven camera reads as unable to record."""
    from astrodeck.devices.cameras.engine import NativeCamera

    hub = sim_hub
    cam = hub.devices["camera"]

    status = await hub.poll_status()
    assert status["camera"]["video_path"] == "none"
    assert status["camera"]["burst_supported"] is False
    assert status["camera"]["roi_align"] == [1, 1], "no constraint = a 1x1 grid"
    assert status["camera"]["max_fps"] is None

    # half one: the camera declares burst support
    cam._caps = _Caps(burst_supported=True)
    status = await hub.poll_status()
    assert status["camera"]["video_path"] == "native"
    assert status["camera"]["burst_supported"] is True
    assert status["camera"]["roi_align"] == [8, 2]
    assert status["camera"]["max_fps"] == pytest.approx(60.0)

    # half two: a natively-driven camera with NO burst support still has the
    # generic fallback, so it can record.
    class _FakeNative(NativeCamera):
        def __init__(self):
            super().__init__(adapter=object(), index=0, name="Fake Native")
            self.connected = True
            self.sensor_width = 100
            self.sensor_height = 100
            self._caps = _Caps(burst_supported=False, roi_align=(4, 4),
                               max_fps=None)

        async def get_temperature(self):
            return -10.0

    hub.devices["camera"] = _FakeNative()
    status = await hub.poll_status()
    assert status["camera"]["burst_supported"] is False
    assert status["camera"]["video_path"] == "native"
    assert status["camera"]["roi_align"] == [4, 4]


# --------------------------------------------------------- meridian countdown

async def test_a_disabled_flip_still_counts_down_for_a_holder(sim_hub):
    """Switching the flip off does not stop a GEM reaching its meridian; it
    means nobody will move the tube when it does -- which is exactly when the
    operator needs the number. It was being withheld from the one person the
    strip was the only warning for.

    The value is still site-derived (it comes from the hour angle, which comes
    from the longitude), so the redaction seam nulls it for a viewer. That is
    the ONLY thing that should null it.

    Sabotage: leave ``hours_to_flip`` unassigned in the ``flip_disabled``
    branch."""
    hub = sim_hub
    status = await hub.poll_status()
    meridian = status["meridian"]
    assert meridian["status"] == "flip_disabled"
    assert meridian["hours_to_flip"] is not None

    admin = _redact_site_for({"meridian": dict(meridian)}, admin_principal())
    assert admin["meridian"]["hours_to_flip"] is not None
    assert admin["meridian"]["status"] == "flip_disabled"

    viewer = _redact_site_for({"meridian": dict(meridian)},
                              principal_for_role("viewer"))
    assert viewer["meridian"]["hours_to_flip"] is None
    # the status word is NOT site-derived, so it survives: the client still
    # knows the flip is switched off, it just cannot time it.
    assert viewer["meridian"]["status"] == "flip_disabled"
