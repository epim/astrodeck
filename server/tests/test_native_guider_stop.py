"""Stop, pressed while a native-guider START is still working.

``POST /api/guide/stop`` calls ``NativeGuider.stop_guiding``, which sets
``self._stop``. The calibration walk never read that flag and ``start_guiding``
cleared it again on the way into the guide loop — so a Stop pressed during the
one-to-three-minute walk was not merely ignored, it was ERASED, and the rig then
began guiding. The mount is pulsing the whole time. ``GuideView`` documents this
by DIMMING Stop during the walk ("This step can't be interrupted"), which is the
honest thing to do while the press does nothing.

These tests pin: the abort itself, the flag surviving the start that used to
clear it, the terminal state the narration reads afterwards, and the two latched
pieces of state that used to outlive a Stop ("lost" and a dither's open settle
window).

Fixture shape mirrors ``test_native_guide_phase.py`` / ``test_native_guider_
recovery.py`` — the sim rig plus a pulse-guide-capable sim mount, with
``CONFIG_DIR`` pointed at ``tmp_path`` so no run touches ``server/config/guider``.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sim import build_sim_rig
from astrodeck.events import bus
from astrodeck.providers import NATIVE_AVAILABLE

pytest.importorskip("astrodeck_native")  # skip cleanly when the wheel is absent
pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path, monkeypatch):
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return tmp_path


def _profile_id(tag: str) -> str:
    return f"test-stop-{tag}-{uuid.uuid4().hex[:8]}"


async def _connected_guider(tag: str, **cfg):
    """A connected NativeGuider over a fresh sim rig, plus the rig's mount (the
    tests spy on its ``pulse_guide`` to see the calibration walk happening)."""
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    config = {"image_scale_arcsec": 2.0, "exposure_s": 0.2}
    config.update(cfg)
    g = NativeGuider(cam, tel, config=config, profile_id=_profile_id(tag))
    await g.connect()
    return g, tel


class _GuideTicks:
    """Drain of the ``"guide"`` events the guider published — the exact stream
    the UI narrates from (``bus.publish("guide", **stats().__dict__)`` reaches
    the browser through the hub's WebSocket fan-out)."""

    def __init__(self) -> None:
        self.q = bus.subscribe()

    def close(self) -> None:
        bus.unsubscribe(self.q)

    def drain(self) -> list[dict]:
        out: list[dict] = []
        while True:
            try:
                ev = self.q.get_nowait()
            except asyncio.QueueEmpty:
                return out
            if ev.type == "guide":
                out.append(ev.data)


@pytest.mark.asyncio
async def test_stop_during_the_calibration_walk_aborts_and_never_guides():
    """THE defect: Stop pressed mid-walk must end the start, not be overwritten
    by it.

    The press is injected from inside the mount's own ``pulse_guide`` — i.e. at
    a moment the walk is provably in flight and the mount is provably moving —
    so the scenario cannot pass vacuously by never reaching the walk at all."""
    g, tel = await _connected_guider("walk")
    ticks = _GuideTicks()
    pulses: list[tuple[str, int]] = []
    phase_at_stop: list[str] = []
    real_pulse_guide = tel.pulse_guide
    STOP_AFTER = 2

    async def _spy_pulse_guide(direction, ms):
        pulses.append((direction, ms))
        result = await real_pulse_guide(direction, ms)
        if len(pulses) == STOP_AFTER:
            # PRECONDITION: this really is the calibration walk — the guider
            # says so, and the mount has just been pulsed twice.
            phase_at_stop.append(g.stats().phase)
            await g.stop_guiding()          # the user presses Stop
        return result

    tel.pulse_guide = _spy_pulse_guide
    try:
        with pytest.raises(DeviceError) as excinfo:
            await asyncio.wait_for(g.start_guiding(), timeout=120.0)

        from astrodeck.guide.native import GuidingStopped

        assert phase_at_stop == ["calibrating"], (
            f"expected the Stop to land during the calibration walk, "
            f"phase was {phase_at_stop}")
        assert isinstance(excinfo.value, GuidingStopped), (
            f"the start must end because it was STOPPED, not for some other "
            f"reason: {excinfo.value}")
        # The mount stops moving: no further calibration leg is dispatched.
        assert len(pulses) == STOP_AFTER, (
            f"expected the walk to issue no pulse after the Stop, got "
            f"{len(pulses)}: {pulses}")
        # ...and nothing starts guiding behind the user's back.
        assert g._loop_task is None, "a stopped start must not arm the guide loop"
        assert not await g.is_active()
        st = g.stats()
        assert st.guiding is False
        assert st.phase == "idle", (
            f"expected a terminal phase the narration can read, got {st.phase!r}")
        last = ticks.drain()[-1]
        assert last["guiding"] is False and last["phase"] == "idle", (
            f"the last published guide tick must be the terminal state: {last}")
    finally:
        # A REGRESSION here leaves a live guide loop exposing the sim camera for
        # the rest of the session; tear it down whatever the verdict.
        tel.pulse_guide = real_pulse_guide
        ticks.close()
        await g.disconnect()


@pytest.mark.asyncio
async def test_a_stop_that_lands_after_the_walk_is_not_cleared_by_the_start():
    """The other half of the erasure: ``start_guiding`` must not clear a stop
    flag that was already set on its way in. Stop lands in the window between
    the walk finishing and the loop being armed (pier-flip check + calibration
    persist), which the walk's own polling cannot see."""
    g, _tel = await _connected_guider("after-walk")
    stopped_inside: list[bool] = []
    real_flip = g._maybe_flip_for_pier

    async def _stop_after_the_pier_check():
        await real_flip()
        await g.stop_guiding()          # the user presses Stop
        stopped_inside.append(g._stop.is_set())

    # A REAL calibration walk runs first — only the moment of the press is
    # staged, so the guider reaches this window in the state production does.
    g._maybe_flip_for_pier = _stop_after_the_pier_check
    try:
        with pytest.raises(DeviceError) as excinfo:
            await asyncio.wait_for(g.start_guiding(), timeout=120.0)

        from astrodeck.guide.native import GuidingStopped

        assert stopped_inside == [True], (
            "PRECONDITION: the press must land after the walk, with the stop "
            "flag left set for start_guiding to find")
        assert isinstance(excinfo.value, GuidingStopped), (
            f"the start must end because it was STOPPED: {excinfo.value}")
        assert g._stop.is_set(), (
            "start_guiding cleared a stop that arrived after it began — that "
            "is the erasure this test exists for")
        assert g._loop_task is None
        assert not await g.is_active()
        assert g.stats().phase == "idle"
    finally:
        await g.disconnect()


@pytest.mark.asyncio
async def test_a_start_after_a_stop_is_not_refused_by_the_previous_stop():
    """The other side of "where is `already set` decided": the flag a Stop
    leaves behind belongs to the session it ended. If it were cleared any later
    than the top of ``start_guiding`` — where the walk's polling can no longer
    confuse the two — the FIRST thing every restart would find is the previous
    Stop, and the guider would refuse to start again for the rest of the
    session. That restart is the sequence engine's whole star-loss recovery."""
    g, _tel = await _connected_guider("restart")
    try:
        await asyncio.wait_for(g.start_guiding(), timeout=120.0)
        assert await g.is_active()
        await g.stop_guiding()
        assert g._stop.is_set(), (
            "PRECONDITION: a Stop must leave the flag set for the next start "
            "to find")

        await asyncio.wait_for(g.start_guiding(), timeout=120.0)
        assert await g.is_active()
        assert g.stats().phase in ("finding", "guiding"), (
            f"expected a live guide loop after the restart, phase is "
            f"{g.stats().phase!r}")
    finally:
        await g.disconnect()


@pytest.mark.asyncio
async def test_stop_clears_the_lost_latch_so_the_narration_moves_on():
    """``_lost`` latches a real star loss; nothing cleared it until the NEXT
    start, so after a Stop the panel kept reading "Guiding stopped — lost the
    guide star … Fix it, then Start Guiding again" over a guider the user had
    already stopped."""
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    g = NativeGuider(rig["guide_camera"], rig["telescope"],
                     config={"image_scale_arcsec": 2.0}, profile_id=None)
    # The real fatal path the guide loop takes on an unrecoverable lock loss.
    await g._handle_lock_lost("calibration_failed")
    assert g._lost is True
    assert g.stats().phase == "lost"          # PRECONDITION

    await g.stop_guiding()

    st = g.stats()
    assert st.phase == "idle", (
        f"a stopped guider must not still narrate a star loss: {st.phase!r}")
    assert st.guiding is False


@pytest.mark.asyncio
async def test_stop_during_a_dither_does_not_leave_the_narration_settling():
    """Same shape as the ``_lost`` latch, one layer down: the engine's settle
    window is state the STOPPED loop was going to close, so a Stop pressed
    mid-dither left the panel reading "Settling after the move…" forever."""
    g, _tel = await _connected_guider("dither")
    await asyncio.wait_for(g.start_guiding(), timeout=120.0)
    assert await g.is_active()
    await asyncio.sleep(1.0)

    dither_task = asyncio.create_task(g.dither(3.0))
    saw_settling = False
    for _ in range(50):                       # up to ~5 s
        await asyncio.sleep(0.1)
        if g.stats().phase == "settling":
            saw_settling = True
            break
        if dither_task.done():
            break
    assert saw_settling, "PRECONDITION: expected to catch the settle window open"

    await g.stop_guiding()
    with pytest.raises(DeviceError):
        await asyncio.wait_for(dither_task, timeout=30.0)

    # PRECONDITION for the fix: the window really is still open engine-side —
    # nothing closed it, because the loop that would have was cancelled.
    assert g._engine_settling() is True
    assert g.stats().phase == "idle", (
        f"a stopped guider must not still narrate a dither settle: "
        f"{g.stats().phase!r}")

    await g.disconnect()


@pytest.mark.asyncio
async def test_a_failed_calibration_does_not_leave_the_narration_calibrating(
        monkeypatch):
    """``_phase_hint`` was set before the walk and cleared only on the SUCCESS
    path, so a walk that timed out (or lost its star) left every 2 s status
    frame republishing "Calibrating the guider…" for the rest of the session —
    and dimmed Start / Force Recalibrate / Stop with it (GuideView.tsx)."""
    import astrodeck.guide.native as native

    g, _tel = await _connected_guider("timeout")
    ticks = _GuideTicks()
    # Zero wall-clock budget for the walk: the first loop pass gives up, AFTER
    # the star find and `begin_calibration` have set the hint.
    monkeypatch.setattr(native, "_CAL_TIMEOUT_S", 0.0)
    try:
        with pytest.raises(DeviceError) as excinfo:
            await asyncio.wait_for(g.start_guiding(), timeout=60.0)
        phases = [t.get("phase") for t in ticks.drain()]
    finally:
        ticks.close()

    assert "timed out" in str(excinfo.value)
    assert "calibrating" in phases, (
        f"PRECONDITION: the walk must have announced itself before failing, "
        f"published phases were {phases}")
    assert g._phase_hint is None
    assert g.stats().phase == "idle", (
        f"a failed calibration must not narrate itself as still running: "
        f"{g.stats().phase!r}")

    await g.disconnect()
