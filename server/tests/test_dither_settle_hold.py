"""Consecutive dither settle failures mean the field is walking.

2026-09-10: the mount walked 3.19 degrees off NGC 7331 over 55 minutes while
the guider reported it was guiding at snr 391 with a healthy declination. Every
existing guard missed it -- `_maybe_recover_guiding` because the guider never
went inactive, and the GN-03 re-lock gate because it needs 3 re-locks in 10
minutes and the night produced 2 spread over 22.5.

The signal that DID separate, and separated perfectly, was the dither:

    22:30 - 00:08  pre-flip        11 dithers   11 settled    0 failed
    00:40:50       after the flip   1 dither     1 settled    0 failed
    00:36 - 01:39  THE WALK        14 dithers    0 settled   14 failed
    01:54 - 04:46  after recovery  20 dithers   20 settled    0 failed

It works because a settle failure is nearly a direct measurement. The settle
window is 90 s; on a stationary field the move completes in about 5 (1.3 guide
cycles in RA, 2.5 in Dec) even with every dither pulse clamped by the mount's
1000 ms per-move cap. Failing to converge in 90 s means the field is moving
and the loop is not winning.

Note what these tests must NOT assert. A double that reports a lost lock, an
inactive guider or a bad RMS is testing a different failure -- and one the
existing guards already catch. The signature here is a guider that believes it
is guiding, healthily, while the settles quietly stop landing.
"""
import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    """A sim rig with the sun cone disarmed and the fast legacy sim guider --
    test_native_guider_relocks.py's fixture, because what is under test is the
    ENGINE's gate, not any guider's calibration walk."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


class _Logs:
    def __init__(self, monkeypatch):
        self.rows: list[tuple[str, str]] = []
        import astrodeck.sequence.engine as engine_mod

        real = engine_mod.bus.log
        monkeypatch.setattr(
            engine_mod.bus, "log",
            lambda level, msg, src="", *a, **k: (
                self.rows.append((level, str(msg))), real(level, msg, src))[0])

    def at(self, level, needle):
        return [m for lv, m in self.rows if lv == level and needle in m]


class _HoldSpy:
    """Records the four things a hold must do, in order."""

    def __init__(self, hub, monkeypatch) -> None:
        self.calls: list[str] = []
        g = hub.guider

        async def _stop():
            self.calls.append("stop")

        def _clear():
            self.calls.append("clear")
            return True

        async def _center(ra, dec, rotation_deg=None):
            self.calls.append("center")

        async def _start():
            self.calls.append("start")

        monkeypatch.setattr(g, "stop_guiding", _stop)
        monkeypatch.setattr(g, "clear_calibration", _clear, raising=False)
        monkeypatch.setattr(g, "start_guiding", _start)
        monkeypatch.setattr(hub, "goto_and_center", _center)


def _engine(sim_hub, monkeypatch, *, limit=2):
    from astrodeck.config import config_store
    from astrodeck.sequence.engine import SequenceEngine
    from astrodeck.sequence.models import SequencePlan
    import astrodeck.sequence.engine as engine_mod

    monkeypatch.setattr(engine_mod, "GUIDE_QUIET_POLL_S", 0.01)
    cfg = config_store.cfg()
    monkeypatch.setattr(cfg.guide, "dither_settle_fail_limit", limit)
    # The re-lock gate must not fire in these tests: this is the OTHER
    # detector, and a shared hold body makes it easy to grade the wrong one.
    monkeypatch.setattr(cfg.guide, "relock_limit", 0)
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(guide=True, recover_guiding=True)
    e._cfg = cfg
    return e


def _target(**kw):
    from astrodeck.sequence.models import ExposureStep, Target
    base = dict(name="NGC 7331", ra_hours=22.6182, dec_deg=34.4098,
                center=True, autofocus_first=False,
                steps=[ExposureStep(filter="L", exposure_s=0.01, count=1)])
    base.update(kw)
    return Target(**base)


async def test_two_consecutive_settle_failures_hold(sim_hub, monkeypatch):
    logs = _Logs(monkeypatch)
    e = _engine(sim_hub, monkeypatch)
    spy = _HoldSpy(sim_hub, monkeypatch)

    e._dither_settle_fails = 2
    await e._maybe_hold_for_dither_failures(_target())

    assert spy.calls == ["stop", "clear", "center", "start"], \
        f"wrong order: {spy.calls}"
    assert logs.at("warning", "consecutive dither settles failed"), \
        f"the hold did not say why; warnings were {logs.at('warning', '')}"
    assert logs.at("warning", "the field is walking")


async def test_one_failure_does_not_hold(sim_hub, monkeypatch):
    """A single settle can be lost to a cloud crossing, and a hold costs a
    slew plus a calibration walk. One is a warning, two is a hold."""
    e = _engine(sim_hub, monkeypatch)
    spy = _HoldSpy(sim_hub, monkeypatch)
    e._dither_settle_fails = 1
    await e._maybe_hold_for_dither_failures(_target())
    assert spy.calls == []


async def test_a_settled_dither_resets_the_streak(sim_hub, monkeypatch):
    """CONSECUTIVE is the whole point. On the failure night 31 dithers settled
    with the same 1000 ms pulse cap in force, so a gate on the total would
    have held on a perfectly healthy night."""
    e = _engine(sim_hub, monkeypatch)
    spy = _HoldSpy(sim_hub, monkeypatch)

    e._dither_settle_fails = 1
    e._dither_settle_fails = 0          # what a settled dither does
    e._dither_settle_fails = 1          # and one more failure after it
    await e._maybe_hold_for_dither_failures(_target())
    assert spy.calls == [], "held on two failures separated by a success"


async def test_the_hold_resets_the_counter(sim_hub, monkeypatch):
    """Those failures bought the hold; counting them again would hold on every
    frame afterwards."""
    e = _engine(sim_hub, monkeypatch)
    spy = _HoldSpy(sim_hub, monkeypatch)
    e._dither_settle_fails = 3
    await e._maybe_hold_for_dither_failures(_target())
    assert spy.calls == ["stop", "clear", "center", "start"]
    assert e._dither_settle_fails == 0

    spy.calls.clear()
    await e._maybe_hold_for_dither_failures(_target())
    assert spy.calls == [], "held twice on one streak"


async def test_limit_zero_disables(sim_hub, monkeypatch):
    """0 is off, the convention every other threshold in this config uses."""
    e = _engine(sim_hub, monkeypatch, limit=0)
    spy = _HoldSpy(sim_hub, monkeypatch)
    e._dither_settle_fails = 9
    await e._maybe_hold_for_dither_failures(_target())
    assert spy.calls == []


async def test_the_counter_starts_at_zero_on_a_fresh_engine(sim_hub, monkeypatch):
    e = _engine(sim_hub, monkeypatch)
    assert getattr(e, "_dither_settle_fails", None) == 0


async def test_only_settle_failures_feed_the_gate(sim_hub, monkeypatch):
    """``dither()`` also raises when the guider is not guiding and when the
    Guiding Assistant owns the mount. Neither says anything about where the
    field is, and counting them would slew and recalibrate the run for a
    reason that is not a walking field.

    This CALLS the classifier rather than reading its source. The first
    version of this test grepped the handler for its condition, and a sabotage
    pass showed that `if False and "settle" in ...` leaves the grepped
    substring in place and the test green -- a test that could not fail.
    """
    from astrodeck.devices.base import DeviceError

    e = _engine(sim_hub, monkeypatch)

    for msg in ("native guider: dither settle timed out",
                "native guider: dither settle failed (settle timed out)"):
        before = e._dither_settle_fails
        e._note_dither_failure(DeviceError(msg))
        assert e._dither_settle_fails == before + 1, f"not counted: {msg}"

    e._dither_settle_fails = 0
    for msg in ("native guider: cannot dither when not guiding",
                "native guider: the Guiding Assistant is using the mount — "
                "cannot dither",
                "dither timed out after 120.0s"):
        e._note_dither_failure(DeviceError(msg))
        assert e._dither_settle_fails == 0, \
            f"a non-settle failure fed the walking-field gate: {msg}"


async def test_the_dither_handler_actually_calls_the_classifier(sim_hub,
                                                                monkeypatch):
    """The gate is worthless if nothing feeds it in production.

    The classifier above is tested by calling it; this asserts the one place
    that is supposed to call it still does. Kept deliberately narrow -- the
    presence of the CALL, not the shape of the condition, because the
    condition's behaviour is graded above.
    """
    import inspect
    import astrodeck.sequence.engine as engine_mod

    src = inspect.getsource(engine_mod.SequenceEngine)
    idx = src.find('f"dither failed: {e}"')
    assert idx > 0, "the dither except-handler moved; re-point this test"
    window = src[max(0, idx - 400):idx]
    assert "_note_dither_failure" in window, \
        "the dither handler no longer feeds the settle-failure gate, so the " \
        "detector cannot fire on the rig however green its own tests are"
    assert "_maybe_hold_for_dither_failures" in src, "nothing calls the gate"


async def test_both_detectors_share_one_hold_body(sim_hub, monkeypatch):
    """The re-lock gate and this one must reach the SAME hold. A second copy
    of a path that stops guiding, clears a calibration and slews the mount is
    a second place for those to drift apart."""
    import inspect
    import astrodeck.sequence.engine as engine_mod

    src = inspect.getsource(engine_mod.SequenceEngine)
    assert src.count("async def _hold_recentre_recalibrate") == 1
    for caller in ("_maybe_hold_for_relocks", "_maybe_hold_for_dither_failures"):
        body = src[src.find(f"async def {caller}"):]
        body = body[:body.find("\n    async def ", 10)]
        assert "_hold_recentre_recalibrate" in body, \
            f"{caller} no longer uses the shared hold"
