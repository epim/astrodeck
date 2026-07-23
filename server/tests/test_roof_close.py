"""PRO-4 Task 4 — the never-crush-the-mount close-ordering guard
(``sequence/roof.close_observatory``).

Driven with the real ``SimDome`` + ``SimTelescope`` on a shared ``SimRig`` and a
tiny log spy. The SimDome's self-checking collision model means a bug that
bypassed the guard would RAISE inside ``close_shutter`` — so the refuse test
additionally asserts the shutter was never even touched (stayed OPEN).
"""
import pytest

from astrodeck.devices.base import DomeShutterState
from astrodeck.devices.sim import build_sim_rig
from astrodeck.sequence.roof import close_observatory


def _spy():
    logs: list[tuple] = []
    return logs, (lambda level, msg, source: logs.append((level, msg, source)))


async def _connected(rig):
    dome = rig["dome"]
    tel = rig["telescope"]
    await dome.connect()
    await tel.connect()
    return dome, tel


@pytest.mark.asyncio
async def test_refuse_when_unparked_never_touches_the_shutter():
    rig = build_sim_rig()
    dome, tel = await _connected(rig)
    rig["_rig"].parked = False
    logs, log = _spy()

    ok = await close_observatory(dome, tel, log=log)

    assert ok is False
    # The invariant: close_shutter was NEVER called — the shutter stayed OPEN
    # (not ERROR, which is what the sim's collision guard would set).
    assert dome._state is DomeShutterState.OPEN
    assert any(lvl == "error" and "REFUSED" in msg for lvl, msg, _ in logs)


@pytest.mark.asyncio
async def test_park_then_close_reaches_closed():
    rig = build_sim_rig()
    dome, tel = await _connected(rig)
    await tel.park()                      # → rig.parked True
    logs, log = _spy()

    ok = await close_observatory(dome, tel, log=log)

    assert ok is True
    assert dome._state is DomeShutterState.CLOSED


@pytest.mark.asyncio
async def test_idempotent_when_already_closed():
    rig = build_sim_rig()
    dome, tel = await _connected(rig)
    # Park + close first so the shutter is genuinely CLOSED.
    await tel.park()
    await dome.close_shutter()
    assert dome._state is DomeShutterState.CLOSED
    # Now unpark: an idempotent early-return must NOT consult the park state.
    rig["_rig"].parked = False
    logs, log = _spy()

    ok = await close_observatory(dome, tel, log=log)

    assert ok is True
    assert dome._state is DomeShutterState.CLOSED


@pytest.mark.asyncio
async def test_requires_park_false_closes_even_when_unparked():
    rig = build_sim_rig()
    dome, tel = await _connected(rig)
    rig["_rig"].parked = False
    # A rotating dome whose shutter clears the OTA at any mount position opts out
    # of the park gate. Model it: capability flag off + a cooperative close that
    # has NO collision model (unlike the roll-off SimDome, which would raise).
    dome.requires_park_before_close = False

    async def rotating_close():
        dome._state = DomeShutterState.CLOSED

    dome.close_shutter = rotating_close
    # Spy on is_parked to PROVE the guard was skipped (never consulted).
    parked_queried = False
    orig_is_parked = tel.is_parked

    async def watched_is_parked():
        nonlocal parked_queried
        parked_queried = True
        return await orig_is_parked()

    tel.is_parked = watched_is_parked
    logs, log = _spy()

    ok = await close_observatory(dome, tel, log=log)

    assert ok is True
    assert dome._state is DomeShutterState.CLOSED
    assert parked_queried is False, "park gate must be skipped when flag is False"
    assert not any("REFUSED" in msg for _, msg, _ in logs)


@pytest.mark.asyncio
async def test_no_telescope_closes():
    rig = build_sim_rig()
    dome = rig["dome"]
    await dome.connect()
    rig["_rig"].parked = True     # no telescope to gate on, but keep the sim happy
    logs, log = _spy()

    ok = await close_observatory(dome, None, log=log)

    assert ok is True
    assert dome._state is DomeShutterState.CLOSED


@pytest.mark.asyncio
async def test_close_failure_returns_false_and_logs():
    rig = build_sim_rig()
    dome, tel = await _connected(rig)
    await tel.park()

    async def boom():
        raise RuntimeError("motor jam")

    dome.close_shutter = boom     # monkeypatch the close to fail
    logs, log = _spy()

    ok = await close_observatory(dome, tel, log=log)

    assert ok is False
    assert any(lvl == "error" and "roof close failed" in msg for lvl, msg, _ in logs)
