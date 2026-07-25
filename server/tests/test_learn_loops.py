"""Auto-learn loops: EGAIN (mean-variance) and per-filter AF offsets.

Both close a loop whose editor/consumer already ships, so the tests focus on the
two guards that make them safe rather than on the plumbing:
  * a MEASURED egain never overrides a DRIVER-reported one, and a degenerate
    variance raises instead of returning a wild number;
  * a filter slot whose autofocus FAILS keeps its PRIOR offset (writing 0 there
    would defocus that filter on every future exposure).
"""
from __future__ import annotations

import numpy as np
import pytest

import astrodeck.config as configmod
import astrodeck.focus.autofocus as af_module
import astrodeck.imaging.egain as egain_module
from astrodeck.focus.filter_offsets import default_ref_slot, offsets_from_positions
from astrodeck.hub import Hub
from astrodeck.imaging.egain import measure_egain


# ------------------------------------------------------------------ c1: math

def test_measure_egain_recovers_known_gain_and_guards_degenerate():
    rng = np.random.default_rng(7)
    g = 2.0                      # e-/ADU we want back out
    signal_e = 20000.0
    bias_adu = 500.0

    def bias():
        return bias_adu + rng.normal(0.0, 3.0, size=(256, 256))

    def flat():
        return bias_adu + rng.poisson(signal_e, size=(256, 256)) / g

    assert abs(measure_egain([flat(), flat()], [bias(), bias()]) - g) < 0.05 * g

    b = [bias(), bias()]
    flat_pair = [flat(), flat()]
    identical = np.full((32, 32), 1000.0)
    for flats, biases, msg in [
        ([flat_pair[0]], b, "at least 2 flat"),
        (flat_pair, [b[0]], "at least 2 bias"),
        ([identical, identical], b, "degenerate variance"),
    ]:
        with pytest.raises(ValueError, match=msg):
            measure_egain(flats, biases)


# ------------------------------------------------------- c1: loop + precedence

async def test_learn_egain_persists_and_never_overrides_the_driver(tmp_path,
                                                                   monkeypatch):
    monkeypatch.setattr(configmod, "EGAIN_CONFIG_FILE", tmp_path / "egain.json")
    monkeypatch.setattr(egain_module, "measure_egain",
                        lambda flats, biases: 3.25)
    h = Hub()
    await h.connect_sim()
    try:
        cam = h.devices["camera"]
        driver_value = 0.81        # a driver that DOES report e-/ADU
        cam.egain = driver_value

        out = await h.learn_egain(gain=100, count=2, exposure_s=0.01)
        assert out["egain"] == 3.25
        # driver-reported value wins: the measurement is stored, not applied
        assert out["applied"] is False and cam.egain == driver_value
        assert h.learned_egain(100) == 3.25
        assert h.learned_egain(101) is None          # exact match only, no interp
        assert configmod.load_egain_config(
            configmod.config_store.cfg().active_profile_id)[100] == 3.25

        # a camera that reports NOTHING (Alpaca/NINA) does get the measurement
        cam.egain = 0.0
        out2 = await h.learn_egain(gain=100, count=2, exposure_s=0.01)
        assert out2["applied"] is True and cam.egain == 3.25
    finally:
        await h.disconnect_all()


# ------------------------------------------------------------------ c2: math

@pytest.mark.parametrize("best, ref, prior, expect_off, expect_kept", [
    # plain deltas; the reference is pinned to 0
    ({0: 1000, 1: 1012, 2: 1120}, 0, [0, 0, 0], [0, 12, 120], []),
    # slot 1 produced no focus (starless narrowband) -> KEEP its prior offset
    ({0: 1000, 2: 1120}, 0, [0, 33, 0], [0, 33, 120], [1]),
    # a non-zero reference still lands on 0 for itself
    ({0: 1000, 1: 1012}, 1, [0, 0], [-12, 0], []),
])
def test_offsets_from_positions(best, ref, prior, expect_off, expect_kept):
    offsets, kept = offsets_from_positions(best, ref, len(prior), prior)
    assert offsets == expect_off and kept == expect_kept


def test_reference_slot_rules():
    # nothing to measure against -> honest raise, never a silent 0-offset wheel
    with pytest.raises(ValueError, match="reference slot"):
        offsets_from_positions({1: 1000}, 0, 2, [0, 0])
    # default picks a luminance-class slot (case-insensitive), else the current
    assert default_ref_slot(["Ha", "lum", "OIII"], 2) == 1
    assert default_ref_slot(["Ha", "OIII", "SII"], 2) == 2


# -------------------------------------------------------------- c2: learn loop

async def test_learn_filter_offsets_keeps_prior_on_a_failed_slot(tmp_path,
                                                                 monkeypatch):
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        fw.filter_names = ["L", "Ha", "OIII"]
        fw.filter_offsets = [0, 77, 0]           # 77 is the prior we must keep
        moved: list[int] = []

        async def fast_set_position(slot):       # skip the sim's wheel dwell
            moved.append(int(slot))
            fw.rig.filter_slot = int(slot)
        monkeypatch.setattr(fw, "set_position", fast_set_position)

        best = {0: 5000, 2: 5150}                # slot 1 (Ha) finds no stars

        async def fake_af(cam, foc, **kw):
            slot = fw.rig.filter_slot
            if slot not in best:
                raise RuntimeError("no stars found")
            return af_module.AutofocusResult(True, best[slot], 2.1, [], "ok")
        monkeypatch.setattr(af_module, "run_autofocus", fake_af)

        out = await h.learn_filter_offsets(ref_slot=None, exposure_s=0.01)

        # reference defaults to the L slot and is measured FIRST
        assert out["ref_slot"] == 0 and moved[0] == 0
        assert sorted(moved) == [0, 1, 2]
        # ref pinned to 0; measured delta for OIII; Ha KEEPS its prior 77
        assert out["offsets"] == [0, 77, 150]
        assert out["kept"] == [1]
        # persisted through the same store the manual editor uses
        assert configmod.load_filter_config(
            configmod.config_store.cfg().active_profile_id)["offsets"] == [0, 77, 150]
    finally:
        await h.disconnect_all()
