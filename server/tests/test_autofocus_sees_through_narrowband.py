"""A two-second sweep through a 3 nm passband cannot see enough stars to fit.

Watched on the rig 2026-08-16 01:12. The meridian flip landed between the S and
Ha steps, so a narrowband filter was in the beam when the post-flip autofocus
ran, and the two sweeps either side of it read:

    L  in beam: 1277 stars at the starting position, clean 9-point V-curve,
                HFR 1.53
    NB in beam:   34 stars at the starting position, 4 of 6 points dropped for
                "no usable size" or "only 2 stars", failed not_enough_spread

`SequenceEngine._autofocus` called ``run_autofocus(cam, foc, hub=...)`` and
passed no exposure or gain, so every sweep ran at the signature defaults of 2 s
and gain 120 whatever was in the light path.

EVERYTHING NEEDED WAS ALREADY BUILT AND UNREACHED. `FilterWheel.is_narrowband`
exists; `filter_narrowband` is populated and the rig already has S/Ha/Oiii
ticked; `focus.filter_offsets.narrowband_sweep_settings` computes the sweep pair
and is used by the offset-learning lane. `devices/base.py` even documents the
flag by pointing AT that function. The sequence engine simply never asked.

WHY SCALE THE EXPOSURE RATHER THAN CHANGE FILTER, which is what the plate solver
does for the same underlying problem (#222). Focus position is filter-dependent
- that is the entire reason per-filter offsets exist - so focusing through a
different filter measures the wrong thing and then needs an offset applied to
guess back. Exposing longer through the filter actually in use measures the
thing we care about directly.

THE NUMBERS ARE NOT NEW: 4x exposure, and gain never left below the sensor's
high-conversion-gain knee (measured 3.96 e- to 1.36 e- read noise on the IMX571
at gain 125). A narrowband focus frame is read-noise limited, so the knee is the
whole noise budget. Both come from `narrowband_sweep_settings`, unchanged, so
the dialog and the sequencer cannot drift apart.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.focus.filter_offsets import (NARROWBAND_EXPOSURE_MULTIPLE,
                                            narrowband_sweep_settings)
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine


class _Result:
    success = True
    message = ""
    position = 1000


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


@pytest.fixture
def af_calls(monkeypatch):
    """Capture what the engine actually asks the sweep for."""
    calls: list[dict] = []

    async def _fake(cam, foc, **kw):
        calls.append(kw)
        return _Result()

    monkeypatch.setattr(engine_mod, "run_autofocus", _fake)
    return calls


#: The BROADBAND base this file measures against. Pinned explicitly rather
#: than assumed: since the focus frame-settings scope was wired through
#: (#233) the base is the OPERATOR SETTING, not a constant, so a test that
#: hardcoded 2 s / gain 120 would now be asserting the old bug.
BASE_EXP, BASE_GAIN, BASE_BIN = 2.0, 120, 2


async def _focus_through(sim_hub, slot: int, *, narrowband: list[bool],
                         hcg: int | None = 125, monkeypatch=None):
    """Park the wheel on ``slot``, mark the wheel up, and run one autofocus."""
    if monkeypatch is not None:
        focus = hub_module.config_store.cfg().frames.focus
        monkeypatch.setattr(focus, "exposure_s", BASE_EXP)
        monkeypatch.setattr(focus, "gain", BASE_GAIN)
        monkeypatch.setattr(focus, "binning", BASE_BIN)
    fw = sim_hub.devices["filterwheel"]
    fw.filter_names = ["L", "R", "G", "B", "S", "Ha", "Oiii", "Dark"]
    fw.filter_narrowband = narrowband
    await fw.set_position(slot)
    cam = sim_hub.devices["camera"]
    cam.hcg_threshold_gain = hcg
    e = SequenceEngine(sim_hub)
    await e._autofocus("test focus")
    return e


class TestTheSweepIsToldWhatItIsLookingThrough:
    async def test_a_narrowband_slot_gets_a_longer_exposure(
            self, sim_hub, af_calls, monkeypatch):
        await _focus_through(sim_hub, 5,          # Ha
                             narrowband=[False] * 4 + [True] * 3 + [False],
                             monkeypatch=monkeypatch)
        assert af_calls, "run_autofocus was never called"
        got = af_calls[0]
        assert got.get("exposure_s") == pytest.approx(BASE_EXP * NARROWBAND_EXPOSURE_MULTIPLE), (
            f"the sweep still runs at the broadband exposure through a "
            f"narrowband filter: {got}")

    async def test_the_gain_clears_the_read_noise_knee(self, sim_hub, af_calls,
                                                       monkeypatch):
        await _focus_through(sim_hub, 5,
                             narrowband=[False] * 4 + [True] * 3 + [False],
                             hcg=125, monkeypatch=monkeypatch)
        assert af_calls[0].get("gain") == 125

    async def test_the_numbers_are_the_shared_ones_not_re_derived(
            self, sim_hub, af_calls, monkeypatch):
        """Guard against the sequencer growing its own copy of the arithmetic
        and drifting from what the offsets dialog shows."""
        await _focus_through(sim_hub, 6,          # Oiii
                             narrowband=[False] * 4 + [True] * 3 + [False],
                             hcg=125, monkeypatch=monkeypatch)
        want_exp, want_gain = narrowband_sweep_settings(
            BASE_EXP, BASE_GAIN, hcg_threshold_gain=125)
        assert af_calls[0].get("exposure_s") == pytest.approx(want_exp)
        assert af_calls[0].get("gain") == want_gain

    async def test_a_camera_that_cannot_report_a_knee_keeps_its_gain(
            self, sim_hub, af_calls, monkeypatch):
        """Honest absence: no reported threshold means no basis to raise the
        gain, but the exposure scaling still applies."""
        await _focus_through(sim_hub, 5,
                             narrowband=[False] * 4 + [True] * 3 + [False],
                             hcg=None, monkeypatch=monkeypatch)
        got = af_calls[0]
        assert got.get("gain") == BASE_GAIN
        assert got.get("exposure_s") == pytest.approx(BASE_EXP * NARROWBAND_EXPOSURE_MULTIPLE)


class TestBroadbandIsUntouched:
    async def test_a_broadband_slot_uses_the_defaults(self, sim_hub, af_calls,
                                                      monkeypatch):
        """THE POSITIVE CONTROL. Without it the change could scale every sweep
        and every test above would still pass - and a 4x luminance sweep would
        quadruple the cost of the common case."""
        await _focus_through(sim_hub, 0,          # L
                             narrowband=[False] * 4 + [True] * 3 + [False],
                             monkeypatch=monkeypatch)
        got = af_calls[0]
        assert got.get("exposure_s") == pytest.approx(BASE_EXP), got
        assert got.get("gain") == BASE_GAIN, got

    async def test_an_unmarked_wheel_is_all_broadband(self, sim_hub, af_calls,
                                                       monkeypatch):
        """A wheel nobody has ticked must behave exactly as it did before."""
        await _focus_through(sim_hub, 5, narrowband=[],
                             monkeypatch=monkeypatch)
        got = af_calls[0]
        assert got.get("exposure_s") == pytest.approx(BASE_EXP), got


class TestItNeverCostsTheFocus:
    async def test_no_filterwheel_at_all_still_focuses(self, sim_hub, af_calls,
                                                       monkeypatch):
        """A rig with no wheel must autofocus exactly as before, not raise."""
        monkeypatch.delitem(sim_hub.devices, "filterwheel", raising=False)
        e = SequenceEngine(sim_hub)
        await e._autofocus("no wheel")
        assert af_calls, "removing the wheel stopped autofocus running at all"

    async def test_a_wheel_that_raises_does_not_break_the_sweep(
            self, sim_hub, af_calls, monkeypatch):
        """A wheel whose link dropped mid-night must cost a filter-aware
        exposure, never the focus run itself."""
        fw = sim_hub.devices["filterwheel"]

        async def _boom():
            raise RuntimeError("wheel link down")

        monkeypatch.setattr(fw, "get_position", _boom)
        e = SequenceEngine(sim_hub)
        await e._autofocus("broken wheel")
        assert af_calls, "a wheel error took the autofocus down with it"


class TestItSaysWhatItChose:
    async def test_the_longer_sweep_is_announced(self, sim_hub, af_calls,
                                                 monkeypatch):
        """The 34-vs-1277 star collapse was only diagnosable because the sweep
        logs its settings. A silent change of exposure would make the next
        forensic pass harder, not easier."""
        from astrodeck.events import bus
        q = bus.subscribe()
        try:
            await _focus_through(sim_hub, 5,
                                 narrowband=[False] * 4 + [True] * 3 + [False],
                                 monkeypatch=monkeypatch)
            msgs = []
            while not q.empty():
                ev = q.get_nowait()
                if ev.type == "log":
                    msgs.append(str((ev.data or {}).get("message", "")))
        finally:
            bus.unsubscribe(q)
        assert any("narrowband" in m.lower() for m in msgs), msgs
