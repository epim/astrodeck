"""The focus frame-settings scope reached the in-run sweep through nothing.

`run_autofocus` declares its sweep frame as SIGNATURE DEFAULTS, and of its three
callers only two ever passed anything:

    POST /api/focuser/autofocus  -> body.exposure_s / gain / binning
    filter-offset learning       -> slot_exp / slot_gain / binning
    THE IN-RUN SWEEP             -> run_autofocus(cam, foc, hub=self.hub)

So every autofocus inside a sequence ran at 2 s, gain 120, bin 2 whatever
`frames.focus` said. `PUT /api/camera/frame-settings?scope=focus` accepted the
write, persisted it, echoed it back and broadcast it on the bus — and the run
never read it. A control that takes a value, shows it back and is consulted by
nothing is the house defect class.

MEASURED 2026-08-11/12. Focus was set to 6 s / gain 200 / bin 1, acting on the
rig's own diagnosis; the route returned exactly that, and the three sweeps that
followed all logged "2s at gain 120, bin 2" and all failed not_enough_spread.

WHY BIN MATTERS MOST, and why this is not a cosmetic setting. The dominant
failure was "N stars but no usable size", which is what bin 2 produces at
0.97"/px: a star one or two pixels across is DETECTED and then discarded because
it cannot be sized. The same sentence appeared again on 2026-08-16 01:12
("7 stars but no usable size"), which is why the narrowband exposure fix landed
beside this one and is not a substitute for it.

IT ALSO EXPLAINS WHY #219 RESISTED DIAGNOSIS. "Autofocus only works on a bright
calibration field" was really "autofocus always gets a 2 s bin-2 frame", and the
knob that would have tested it was connected to nothing.

THE GUARD, ON THE SAME VISIT. The in-run sweep also passed no ``expose_guard``,
alone among the three callers. That guard is what stops two capture paths
exposing one camera; without it a sweep frame and a sequence frame can interleave
their imageready polls and download each other's data.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.focus.filter_offsets import NARROWBAND_EXPOSURE_MULTIPLE
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan


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
    calls: list[dict] = []

    async def _fake(cam, foc, **kw):
        calls.append(kw)
        return _Result()

    monkeypatch.setattr(engine_mod, "run_autofocus", _fake)
    return calls


def _set_focus_scope(monkeypatch, *, exposure_s=6.0, gain=200, binning=1):
    focus = hub_module.config_store.cfg().frames.focus
    monkeypatch.setattr(focus, "exposure_s", exposure_s)
    monkeypatch.setattr(focus, "gain", gain)
    monkeypatch.setattr(focus, "binning", binning)


class TestTheKnobIsConnected:
    async def test_the_sweep_uses_the_focus_scopes_exposure_and_gain(
            self, sim_hub, af_calls, monkeypatch):
        _set_focus_scope(monkeypatch, exposure_s=6.0, gain=200, binning=1)
        await SequenceEngine(sim_hub)._autofocus("in-run")
        got = af_calls[0]
        assert got.get("exposure_s") == pytest.approx(6.0), got
        assert got.get("gain") == 200, got

    async def test_the_sweep_uses_the_focus_scopes_BINNING(
            self, sim_hub, af_calls, monkeypatch):
        """Called out on its own because bin is the one that actually broke the
        sweeps: at 0.97"/px a bin-2 star cannot be sized, so points are found
        and then dropped for "no usable size"."""
        _set_focus_scope(monkeypatch, binning=1)
        assert af_calls == []
        await SequenceEngine(sim_hub)._autofocus("in-run")
        assert af_calls[0].get("binning") == 1, af_calls[0]

    async def test_changing_the_scope_changes_the_sweep(
            self, sim_hub, af_calls, monkeypatch):
        """The promise the settings route makes. Two different scopes must give
        two different sweeps, or the knob is decoration."""
        _set_focus_scope(monkeypatch, exposure_s=3.0, gain=100, binning=2)
        await SequenceEngine(sim_hub)._autofocus("first")
        _set_focus_scope(monkeypatch, exposure_s=9.0, gain=300, binning=1)
        await SequenceEngine(sim_hub)._autofocus("second")
        assert af_calls[0]["exposure_s"] != af_calls[1]["exposure_s"]
        assert af_calls[0]["gain"] != af_calls[1]["gain"]
        assert af_calls[0]["binning"] != af_calls[1]["binning"]


class TestTheGuardIsPassed:
    async def test_the_sweep_is_serialized_against_the_other_capture_paths(
            self, sim_hub, af_calls, monkeypatch):
        """Alone among the three callers, the in-run sweep passed no guard. Two
        capture paths exposing one camera interleave their imageready polls and
        download each other's frames."""
        _set_focus_scope(monkeypatch)
        await SequenceEngine(sim_hub)._autofocus("in-run")
        assert af_calls[0].get("expose_guard") is not None, (
            "the in-run sweep still exposes the camera unguarded")


class TestNarrowbandScalesTHESCOPE:
    """The two fixes compose: the scope decides the base frame, narrowband
    multiplies it. Scaling a hardcoded constant instead would silently undo the
    operator's setting on exactly the filters that need it most.

    `apply_filter_offsets` is turned OFF for both, and that is not a workaround:
    since 2026-09-08 a sweep whose offsets can put the focus back MOVES to
    luminance rather than exposing four times as long through the narrowband
    slot, so the scaling this class is about is reached only when nothing can
    put it back. See `test_autofocus_sees_through_narrowband.py`.
    """

    @staticmethod
    def _engine(sim_hub):
        e = SequenceEngine(sim_hub)
        e.plan = SequencePlan(apply_filter_offsets=False)
        return e

    async def test_it_multiplies_the_scopes_exposure_not_a_constant(
            self, sim_hub, af_calls, monkeypatch):
        _set_focus_scope(monkeypatch, exposure_s=5.0, gain=100, binning=1)
        fw = sim_hub.devices["filterwheel"]
        fw.filter_names = ["L", "R", "G", "B", "S", "Ha", "Oiii", "Dark"]
        fw.filter_narrowband = [False] * 4 + [True] * 3 + [False]
        await fw.set_position(5)                       # Ha
        await self._engine(sim_hub)._autofocus("in-run")
        assert af_calls[0]["exposure_s"] == pytest.approx(
            5.0 * NARROWBAND_EXPOSURE_MULTIPLE), af_calls[0]

    async def test_binning_is_not_touched_by_the_narrowband_scaling(
            self, sim_hub, af_calls, monkeypatch):
        """Binning is a sampling decision, not a brightness one. Scaling it for
        narrowband would re-introduce the un-sizeable star the scope exists to
        let the operator fix."""
        _set_focus_scope(monkeypatch, binning=1)
        fw = sim_hub.devices["filterwheel"]
        fw.filter_names = ["L", "R", "G", "B", "S", "Ha", "Oiii", "Dark"]
        fw.filter_narrowband = [False] * 4 + [True] * 3 + [False]
        await fw.set_position(6)
        await self._engine(sim_hub)._autofocus("in-run")
        assert af_calls[0]["binning"] == 1


class TestItStillNeverCostsTheFocusRun:
    async def test_an_unreadable_scope_falls_back_and_still_focuses(
            self, sim_hub, af_calls, monkeypatch):
        """Honest absence: if the config cannot be read the sweep still runs, on
        the documented broadband pair, rather than the run losing its focus."""
        def _boom(*a, **k):
            raise RuntimeError("config unreadable")

        monkeypatch.setattr(engine_mod, "frames_payload", _boom)
        await SequenceEngine(sim_hub)._autofocus("in-run")
        assert af_calls, "an unreadable focus scope stopped autofocus entirely"
        assert af_calls[0].get("exposure_s") == pytest.approx(
            engine_mod.SWEEP_EXPOSURE_S)
        assert af_calls[0].get("gain") == engine_mod.SWEEP_GAIN
