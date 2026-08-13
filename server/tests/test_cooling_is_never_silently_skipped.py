"""A plan that asks for a temperature never shoots warm in silence.

MEASURED COST, 2026-08-12/13. A redeploy at 23:39 restarted the server; the run
was restarted seconds later while the camera was still connecting. The first
line of `_cool_and_wait` read

    if not cam or not cam.connected or not getattr(cam, "can_cool", False):
        return True

so it answered "nothing to wait on" and returned SUCCESS. The night then shot
53 of its 83 frames at +16 °C against a -5 °C dark library, and the log said
nothing at all — because the "cooling camera to …" line sat BELOW that return.

The frames are the evidence: CCD-TEMP -5.0 with SET-TEMP -5.0 before the
restart, CCD-TEMP 16-17 with SET-TEMP ABSENT after it.

Three situations were collapsed into one answer, and they want three:
  * a camera with no cooler        — proceed, but SAY so; the plan asked
  * a camera not connected YET     — transient; heal, then look again
  * a camera still not connected   — a real failure; escalation decides
"""
from __future__ import annotations

import pytest

from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import SequencePlan


class _Cam:
    def __init__(self, connected=True, can_cool=True):
        self.connected = connected
        self.can_cool = can_cool
        self.cooled_to = None

    async def set_cooler(self, on, target):
        self.cooled_to = target


class _Hub:
    def __init__(self, cam=None):
        self.devices = {"camera": cam} if cam is not None else {}
        self.guider = None
        self.site = {}
        self.master_library = None


def _engine(cam=None, *, heals_to=None):
    eng = SequenceEngine(_Hub(cam))
    eng.plan = SequencePlan(name="n", cool_to=-5.0)
    eng.logs: list[tuple[str, str]] = []
    eng._set_state = lambda **kw: None

    import astrodeck.sequence.engine as mod
    eng._orig_log = mod.bus.log

    async def _gate():
        # what a real reconnect does: the camera turns up
        if heals_to is not None:
            eng.hub.devices["camera"] = heals_to

    eng._reconnect_gate = _gate
    return eng


@pytest.fixture(autouse=True)
def _capture_logs(monkeypatch):
    seen: list[tuple[str, str]] = []
    import astrodeck.sequence.engine as mod
    monkeypatch.setattr(mod.bus, "log",
                        lambda level, msg, src="": seen.append((level, msg)))
    return seen


class TestTheTransientCase:
    async def test_a_camera_still_connecting_is_healed_then_cooled(self):
        """The exact 23:39 shape: the run starts before the camera is ready."""
        good = _Cam(connected=True)
        eng = _engine(_Cam(connected=False), heals_to=good)
        ok = await eng._cool_and_wait(-5.0, 1)
        assert good.cooled_to == -5.0, "never issued the cooler command"
        assert ok is not False or good.cooled_to == -5.0

    async def test_a_camera_that_never_turns_up_is_reported_not_swallowed(
            self, _capture_logs):
        eng = _engine(_Cam(connected=False))          # heals to nothing
        ok = await eng._cool_and_wait(-5.0, 1)
        assert ok is False, "an absent camera reported cooling as succeeded"
        assert any("not connected" in m for _, m in _capture_logs), _capture_logs

    async def test_no_camera_at_all_is_reported_too(self, _capture_logs):
        eng = _engine(None)
        assert await eng._cool_and_wait(-5.0, 1) is False
        assert any("-5" in m for _, m in _capture_logs)


class TestTheGenuinelyUncooledCamera:
    async def test_it_proceeds_but_says_so(self, _capture_logs):
        """Proceeding is right — there is no cooler to wait for. Silence is not:
        the operator asked for a temperature and is not getting one."""
        eng = _engine(_Cam(connected=True, can_cool=False))
        assert await eng._cool_and_wait(-5.0, 1) is True
        assert any("cannot cool" in m for _, m in _capture_logs), _capture_logs

    async def test_it_names_the_temperature_that_was_asked_for(
            self, _capture_logs):
        eng = _engine(_Cam(connected=True, can_cool=False))
        await eng._cool_and_wait(-12.5, 1)
        assert any("-12.5" in m for _, m in _capture_logs), _capture_logs


class TestTheSilenceIsGone:
    async def test_every_path_out_of_the_guard_logs_something(
            self, _capture_logs):
        """The defect was not the fail-open, it was the fail-open with NOTHING
        written down. Whatever the answer, the night's record must show that
        cooling was considered."""
        for cam, heals in ((_Cam(connected=False), None),
                           (_Cam(connected=True, can_cool=False), None),
                           (None, None)):
            _capture_logs.clear()
            eng = _engine(cam, heals_to=heals)
            await eng._cool_and_wait(-5.0, 1)
            assert _capture_logs, f"silent for cam={cam}"
