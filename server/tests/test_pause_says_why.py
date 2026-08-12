"""A paused run must say WHY it is paused — and not wear the last reason.

Reported from the rig 2026-08-11: "the plan status says paused because I stopped
it as opposed to paused because of shooting conditions". Those two are genuinely
different situations — one is waiting for a person, the other is waiting for the
sky — and the operator reads that sentence to decide whether to go to bed.

The engine tells them apart on the SAFETY side and not on the operator side:

    def pause(self):            self._set_state(state="paused")
    async def _pause_unsafe():  self._set_state(state="paused",
                                                detail=f"paused (unsafe): {reason}")

and ``_set_state`` MERGES (``self.state = {**self.state, **kw}``), so a key that
is not passed keeps its old value. The operator's pause therefore inherits
whatever sentence was on screen a moment earlier — mid-capture progress, or, at
its worst, a *previous weather pause's* reason. The state is derived and fresh;
the sentence explaining it is remembered and stale. Same shape as #224.

The UI renders ``seq.detail`` verbatim under a paused run (MonitorView:724,838),
so whatever is stale here is what the operator reads.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target

pytestmark = pytest.mark.asyncio


def _plan(count: int = 4) -> SequencePlan:
    return SequencePlan(
        name="pause-copy", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(name="T", ra_hours=5.5, dec_deg=-5.0, center=False,
                        autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=count)])])


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def test_an_operator_pause_says_it_was_the_operator(rig):
    """The plain reading of the reported behaviour: a person pressed Pause, so
    the sentence should say a person did."""
    eng = SequenceEngine(rig)
    eng.start(_plan())
    try:
        eng.pause()
        detail = (eng.state.get("detail") or "").lower()
        assert eng.state.get("state") == "paused"
        assert detail, "a paused run gave no reason at all"
        assert "you" in detail or "operator" in detail or "manual" in detail, (
            f"a manual pause reads {detail!r} — nothing in it says a person did "
            f"it, so it cannot be told apart from a weather hold")
    finally:
        await eng.abort()


async def test_a_manual_pause_does_not_inherit_a_WEATHER_reason(rig):
    """THE DEFECT, in the form that actually misleads.

    A weather pause writes 'paused (unsafe): <reason>'. If the sky clears, the
    run resumes, and the operator then presses Pause, the merge leaves that
    sentence in place — so a rig waiting for a HUMAN reads as a rig waiting for
    the WEATHER, and the natural response is to go to bed and let it recover on
    its own. It never will.
    """
    eng = SequenceEngine(rig)
    eng.start(_plan())
    try:
        # Stand in for a completed weather hold: the sentence it leaves behind.
        eng._set_state(state="running", detail="paused (unsafe): cloud cover 100%")
        eng.pause()
        detail = (eng.state.get("detail") or "")
        assert "unsafe" not in detail.lower(), (
            f"a manual pause is wearing the last weather reason: {detail!r}")
        assert "cloud" not in detail.lower(), detail
    finally:
        await eng.abort()


async def test_a_manual_pause_does_not_inherit_mid_capture_progress(rig):
    """The everyday version: the detail is the capture line ('T: LIGHT 60s
    [3/20]'), which under a PAUSED state reads as though it is still exposing."""
    eng = SequenceEngine(rig)
    eng.start(_plan())
    try:
        eng._set_state(state="running", detail="T: LIGHT 60s [3/20]")
        eng.pause()
        assert "[3/20]" not in (eng.state.get("detail") or ""), (
            "a paused run is still showing the frame it was taking")
    finally:
        await eng.abort()


async def test_resuming_clears_the_pause_sentence(rig):
    """The reason must not outlive the pause it explains — the same
    explicit-clear discipline `schedule=None` already has in _set_state."""
    eng = SequenceEngine(rig)
    eng.start(_plan())
    try:
        eng.pause()
        assert eng.state.get("detail")
        eng.resume()
        detail = (eng.state.get("detail") or "").lower()
        assert "pause" not in detail, (
            f"a resumed run still says {detail!r}")
    finally:
        await eng.abort()
