"""A camera that claims to be connected and produces nothing is dropped (#16).

2026-09-12: an ASI guide camera stranded in `VIDEO_MODE_ACTIVE` went on
reporting `connected: true`. `_reconnect_gate` keys off that flag, saw a healthy
device, and never attempted recovery; the rig sat broken until a human ran a
profile activate.

The class, and the reason this is worth a file of its own: a liveness flag that
cannot go false is not a liveness flag, and every recovery path hanging off it
is inert. It is the same shape as the dead-serial-link night, where `connected`
was a memory rather than a measurement.

These drive `_camera_is_silent` and the gate that consults it. What they do NOT
cover is the issue's other ask - that `connected` be backed by something the
device must actually answer - which is a change in every camera backend and
cannot be validated without the hardware.
"""
from __future__ import annotations

import time

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck.sequence import SequenceEngine


class _Hub:
    def __init__(self):
        self.devices: dict = {}


def _engine(*, expecting: bool, idle_s: float,
            exposure_s: float = 10.0, overhead_s: float = 20.0) -> SequenceEngine:
    e = SequenceEngine(_Hub())
    e._progress_expected = expecting
    e._cur_exposure_s = exposure_s
    e._overhead_ema = overhead_s
    e._last_frame_at = time.time() - idle_s
    return e


def test_a_camera_that_has_missed_three_frames_is_silent():
    """The defect, at the seam. 3 x (10 + 20) is 90 s, floored at 180, so 400 s
    of nothing is well past it.

    MUTATION: `return False` from `_camera_is_silent`. Observed: this fails, and
    so does the gate case below - the two together are the whole fix.
    """
    assert _engine(expecting=True, idle_s=400)._camera_is_silent() is True


def test_a_camera_within_its_own_frame_time_is_not_silent():
    """The other half, and the one a hasty bound breaks: a 600 s sub is not a
    dead camera, and reconnecting mid-exposure would destroy the frame.

    MUTATION: drop `_cur_exposure_s` from the expected time, leaving the
    overhead alone. Observed: a legitimate long exposure is called silent and
    this fails.
    """
    e = _engine(expecting=True, idle_s=700, exposure_s=600.0, overhead_s=30.0)
    assert e._camera_is_silent() is False, (
        "a 600 s exposure 700 s in was treated as a dropped camera")


def test_the_floor_protects_a_burst_of_short_frames():
    """Sub-second calibration frames make 3 x (exposure + overhead) a couple of
    seconds, and a camera is allowed to pause for longer than that between
    darks without being torn down.

    MUTATION: drop the `max(_CAMERA_SILENT_FLOOR_S, ...)`. Observed: 60 s of
    quiet during a bias burst reads as a dropped camera and this fails.
    """
    e = _engine(expecting=True, idle_s=60, exposure_s=0.2, overhead_s=0.5)
    assert e._camera_is_silent() is False


def test_nothing_is_silent_when_no_frames_are_expected():
    """The engine sits at `running` with no frames through a scheduled wait, a
    slew, a centre, an autofocus and the cooling ramp. Nobody has asked the
    camera for anything, so it is not failing to answer.

    This is the same gate the no-progress watchdog uses, and for the same
    reason: firing there paged a false 'UNSAFE: no progress' at 1am mid-plan.

    MUTATION: drop the `_progress_expected` check. Observed: an hour of
    legitimate cooling reads as a dropped camera and this fails.
    """
    assert _engine(expecting=False, idle_s=3600)._camera_is_silent() is False


def test_the_clock_is_restamped_so_it_does_not_reconnect_every_frame():
    """Without this the next frame boundary reads the same stale clock and
    reconnects again, and again - a reconnect loop out of one stall.

    MUTATION: delete the `self._last_frame_at = time.time()`. Observed: the
    second call still reports silent and this fails.
    """
    e = _engine(expecting=True, idle_s=400)
    assert e._camera_is_silent() is True
    assert e._camera_is_silent() is False, (
        "a second check straight after the first reported silent again; the "
        "reconnect never gets a window to work in")


async def test_the_gate_reconnects_a_silent_camera_that_claims_connected():
    """Through `_reconnect_gate`, which is where the defect lived: it skipped
    any device whose `connected` was true, and that is exactly what the
    stranded camera reported.

    MUTATION: restore `if dev is None or getattr(dev, "connected", False):
    continue`. Observed: `reconnect_role` is never called and this fails.
    """
    class _Cam:
        connected = True

    tried: list[str] = []

    class _Hub2:
        def __init__(self):
            self.devices = {"camera": _Cam()}

        async def reconnect_role(self, role):
            tried.append(role)
            return True

    from astrodeck.config import EscalationConfig, config_store

    e = SequenceEngine(_Hub2())
    cfg = config_store.cfg()
    e._cfg = cfg.model_copy(update={
        "escalation": EscalationConfig(reconnect_resume=True, reconnect_retries=1)})
    e.plan = None
    e._progress_expected = True
    e._cur_exposure_s = 10.0
    e._overhead_ema = 20.0
    e._last_frame_at = time.time() - 400

    await e._reconnect_gate()
    assert tried == ["camera"], (
        f"the gate did not try to heal a camera that was producing nothing: {tried}")


async def test_a_healthy_camera_is_left_alone():
    """The guard against the fix itself: a camera delivering frames must never
    be reconnected mid-run.

    MUTATION: make `_camera_is_silent` return True unconditionally. Observed:
    a working camera is torn down and reconnected at every frame boundary, and
    this fails.
    """
    class _Cam:
        connected = True

    tried: list[str] = []

    class _Hub2:
        def __init__(self):
            self.devices = {"camera": _Cam()}

        async def reconnect_role(self, role):
            tried.append(role)
            return True

    from astrodeck.config import EscalationConfig, config_store

    e = SequenceEngine(_Hub2())
    cfg = config_store.cfg()
    e._cfg = cfg.model_copy(update={
        "escalation": EscalationConfig(reconnect_resume=True, reconnect_retries=1)})
    e.plan = None
    e._progress_expected = True
    e._cur_exposure_s = 10.0
    e._overhead_ema = 20.0
    e._last_frame_at = time.time() - 5      # a frame five seconds ago

    await e._reconnect_gate()
    assert tried == [], f"a healthy camera was reconnected: {tried}"
