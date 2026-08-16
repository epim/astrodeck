"""The assumed-ambient extend path raised NameError on the rig, every time.

Caught on astrotown 2026-08-16, in the log rather than in a test:

    09:32:20 info   warming camera - ramping the setpoint -10.0 -> 20.0 C
                    (assumed) at 2 C/min, about 15 min, in the background
    09:48:20 error  warm ramp failed: name 'ambient_from' is not defined
                    - switching the cooler off

`ambient_from` is computed in the routine that STARTS the warm and stored in
``self._warm_state``. `_warm_ramp` unpacks `start_c`, `ambient_c` and `rate`
from that state and then uses `ambient_from` as if it were a local. It is not,
so the moment the ramp reaches its assumed ambient and tries to extend, the
whole ramp dies and falls back to switching the TEC off.

WHICH IS THE EXACT FAILURE #154 AND #134 EXIST TO PREVENT. #134: ramp the TEC
down, never cut it dead. #154: an ASSUMED 20 C ambient with 32 C air cuts the
TEC 12 C early and hands the sensor the rest of the climb in one jump. The
extend path is #154's whole fix -- and it could never execute. Measured again
tonight: assumed 20 C, real air 28.3 C, cooler switched off at the end.

WHY 6091 TESTS MISSED IT. `test_warm_ambient_extend.py` covers
`cooling.warm_extend_ambient_c` thoroughly -- eight cases, provenance, ceilings,
lagging sensors -- and calls the PURE FUNCTION directly, never through
`_warm_ramp`. A correct function wired to a caller that cannot reach it, tested
in a way that cannot notice. Same shape as the altitude-floor call site fixed
earlier today, and the same shape as most of what this file's neighbours cover.

So this test drives the REAL ramp to the real branch.
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck import cooling
from astrodeck.devices.base import Camera, CameraFrame, DeviceError
from astrodeck.hub import Hub


class _FollowingCam(Camera):
    """A camera with NO ambient readout (so the ambient is ASSUMED) whose sensor
    follows the setpoint all the way up (so the lead check never ends the ramp
    early, and the setpoint reaches the assumed ambient -- which is what makes
    the extend branch reachable)."""

    def __init__(self) -> None:
        super().__init__("Following Cam")
        self.connected = True
        self.can_cool = True
        self.calls: list[tuple[bool, float | None]] = []
        self._temp = -10.0

    async def expose(self, seconds, gain, offset, binning=1, light=True,
                     save=False, target="") -> CameraFrame:   # pragma: no cover
        raise DeviceError("not used")

    async def abort_exposure(self) -> None:                   # pragma: no cover
        return None

    async def connect(self) -> None:                          # pragma: no cover
        self.connected = True

    async def disconnect(self) -> None:                       # pragma: no cover
        self.connected = False

    async def set_cooler(self, on: bool, target_c: float | None = None) -> None:
        self.calls.append((on, target_c))
        if on and target_c is not None:
            self._temp = target_c

    async def get_temperature(self) -> float | None:
        return self._temp

    async def get_ambient_temperature(self) -> float | None:
        return None                  # no readout -> the ambient is ASSUMED


@pytest.fixture
def hub_and_cam(monkeypatch):
    monkeypatch.setattr(cooling, "WARM_STEP_S", 0.0)
    h = Hub()
    cam = _FollowingCam()
    h.devices["camera"] = cam
    return h, cam


async def _drain(hub: Hub, timeout: float = 10.0) -> None:
    task = hub._warm_task
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), timeout)


class TestTheRampSurvivesReachingItsAssumedAmbient:
    async def test_it_does_not_raise(self, hub_and_cam):
        """The bug, stated at the only level that would have caught it: run the
        real ramp and read the state the ramp itself leaves behind.

        NOT via `bus_lines`: that fixture yields (level, message, source)
        TUPLES, so `"warm ramp failed" in line` is exact-element matching and
        matches nothing ever. The first version of this test did exactly that
        and passed against the live bug."""
        hub, cam = hub_and_cam
        await hub.warm_camera(source="test")
        await _drain(hub)
        note = (hub._warm_state or {}).get("note") or ""
        assert "failed" not in note, f"the ramp died on its way to ambient: {note!r}"

    async def test_it_does_not_die_with_a_NameError_specifically(
            self, hub_and_cam):
        """Named separately so a future unrelated ramp failure cannot be
        mistaken for this one having come back."""
        hub, cam = hub_and_cam
        await hub.warm_camera(source="test")
        await _drain(hub)
        assert "is not defined" not in ((hub._warm_state or {}).get("note") or "")

    async def test_the_tec_is_switched_off_LAST_not_as_a_fallback(
            self, hub_and_cam):
        """#134's invariant, which the NameError silently undid: the fallback
        path switches the cooler off from a half-finished ramp, which is the
        plunge the ramp exists to prevent."""
        hub, cam = hub_and_cam
        await hub.warm_camera(source="test")
        await _drain(hub)
        assert cam.calls, "the ramp never commanded the cooler at all"
        assert cam.calls[-1][0] is False, (
            f"the last cooler command was not 'off': {cam.calls[-3:]}")
        stepped = [t for on, t in cam.calls if on and t is not None]
        assert len(stepped) >= 3, (
            f"the TEC was cut rather than ramped - only {len(stepped)} "
            f"setpoint steps: {cam.calls}")
        assert stepped == sorted(stepped), (
            f"the setpoints did not climb monotonically: {stepped}")


class TestTheExtendActuallyHappens:
    async def test_the_assumed_ambient_is_walked_past(self, hub_and_cam):
        """#154's fix, exercised through the ramp for the first time. A sensor
        still following at the assumed 20 C means 20 C was not the real air, so
        the ramp must keep climbing rather than switch off there."""
        hub, cam = hub_and_cam
        await hub.warm_camera(source="test")
        await _drain(hub)
        stepped = [t for on, t in cam.calls if on and t is not None]
        assert max(stepped) > 20.0, (
            f"the ramp stopped at the assumed 20 C ambient and never extended, "
            f"so #154's fix is still unreachable: peak setpoint {max(stepped)}")
