"""The engine hand-off: the session stack is fed ACCEPTED frames, and only those.

This is the assertion the feature stands on. The composite is meant to show what
the run is KEEPING, so a sub the quality gate refused -- trailed, clouded, out
of focus -- must not be in the only picture the operator is looking at. The
gate's verdict and the pixels are both in hand at exactly one place in the app
(sequence/engine.py, right after ``accepted = self._check_quality(info)``), and
this test runs the real frame loop on the sim rig past a spy to prove the call
sits there and nowhere looser.
"""
import asyncio

import numpy as np
import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


class SpyStack:
    """Stands in for the hub's SessionStacker and records what reaches it."""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.calls: list[dict] = []
        self.target = ""

    def add(self, data, filter_name, exposure_s, *, target=None, session=None):
        self.calls.append({"data": data, "filter": filter_name,
                           "exposure_s": exposure_s, "target": target,
                           "session": session})
        return "L"


def three_frame_plan() -> SequencePlan:
    return SequencePlan(
        name="stack-test",
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="Ha", exposure_s=0.05, gain=100, count=3)],
        )],
        guide=False, dither_every=0, autofocus_every=0, meridian_flip=False,
    )


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


async def test_only_accepted_frames_reach_the_stack(sim_hub, monkeypatch):
    spy = SpyStack()
    sim_hub.session_stack = spy
    engine = SequenceEngine(sim_hub)

    # Reject the SECOND frame. In attempts mode a reject still consumes its
    # slot (the file is kept and ledger-recorded), so the loop grades three
    # frames and accepts two of them.
    verdicts = [True, False, True]
    seen = {"n": 0}
    real = engine._check_quality

    def gated(info, **kw):
        real(info, **kw)                      # keep the real recording path
        i = seen["n"]
        seen["n"] += 1
        return verdicts[i] if i < len(verdicts) else True

    monkeypatch.setattr(engine, "_check_quality", gated)

    engine.start(three_frame_plan())
    assert await wait_for(lambda: engine.state.get("state") == "complete"), \
        engine.state
    assert seen["n"] == 3, f"the loop graded {seen['n']} frames, not 3"
    assert len(spy.calls) == 2, \
        (f"{len(spy.calls)} frames reached the stack out of 3 graded, 2 "
         "accepted: the hook is not gated on the quality verdict")


async def test_the_stack_gets_linear_pixels_and_the_resolved_filter(sim_hub):
    spy = SpyStack()
    sim_hub.session_stack = spy
    engine = SequenceEngine(sim_hub)
    engine.start(three_frame_plan())
    assert await wait_for(lambda: engine.state.get("state") == "complete")

    assert spy.calls, "nothing reached the stack at all"
    for call in spy.calls:
        data = call["data"]
        assert isinstance(data, np.ndarray) and data.ndim == 2, type(data)
        assert data.dtype == np.uint16, data.dtype
        # The RESOLVED filter (the same string the FITS header carries), not
        # the plan's step text: the composite's channel mapping reads this.
        assert call["filter"] == "Ha", call["filter"]
        assert call["exposure_s"] == pytest.approx(0.05)
        assert call["target"] == "M42"
        assert call["session"], "no run identity, so a second run would resume the first"


async def test_a_disabled_stack_is_never_called(sim_hub):
    # The default. Off must mean the frame loop does not even look up the sub.
    spy = SpyStack(enabled=False)
    sim_hub.session_stack = spy
    engine = SequenceEngine(sim_hub)
    engine.start(three_frame_plan())
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    assert spy.calls == []


async def test_the_real_stacker_produces_a_composite_from_a_sim_run(sim_hub):
    # End to end with nothing faked but the rig: switch on, run a plan, get a
    # JPEG. The spy above proves WHICH frames arrive; this proves they arrive
    # somewhere that can draw them.
    sim_hub.start_session_stack()
    engine = SequenceEngine(sim_hub)
    # A LONGER SUB THAN THE TESTS ABOVE, deliberately. At 0.05 s the simulator's
    # M42 is a noise field with two stars in it, the stacker finds nothing to
    # register on and rejects every frame -- which would make this test pass
    # against a stacker that never worked at all. One second puts ~17 stars in
    # the binned frame, and the sim's pacing is collapsed by the suite fixture
    # so it costs nothing in wall clock.
    engine.start(SequencePlan(
        name="stack-e2e",
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="Ha", exposure_s=1.0, gain=100, count=3)],
        )],
        guide=False, dither_every=0, autofocus_every=0, meridian_flip=False,
    ))
    assert await wait_for(lambda: engine.state.get("state") == "complete")

    st = sim_hub.session_stack_status()
    assert st["enabled"] is True and st["target"] == "M42"
    assert st["frames"] >= 1, st
    assert [c["channel"] for c in st["channels"]] == ["Ha"]
    got = sim_hub.session_stack_preview(320)
    assert got is not None
    jpeg, meta = got
    assert jpeg[:2] == b"\xff\xd8" and meta["mode"] == "narrowband"


async def test_a_stack_that_throws_does_not_end_the_night(sim_hub):
    class Exploding(SpyStack):
        def add(self, *a, **kw):
            raise RuntimeError("boom")

    sim_hub.session_stack = Exploding()
    engine = SequenceEngine(sim_hub)
    engine.start(three_frame_plan())
    assert await wait_for(lambda: engine.state.get("state") == "complete"), \
        "a preview accumulator raising took the whole run down with it"
    assert engine.state["progress"]["frames_done"] == 3
