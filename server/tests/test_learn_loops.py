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


# ------------------------------------------- c3: narrowband slots (#148, 2026-08-08)
# The rig's offsets run measured L, R, G and B (0, -59, -30, -28) and could not
# focus S, Ha or Oiii at all, because one exposure ran the whole wheel. A 3-7 nm
# passband delivers a star 40-100x fainter than luminance does.

def test_narrowband_settings_are_derived_from_the_broadband_pair():
    from astrodeck.focus.filter_offsets import (NARROWBAND_EXPOSURE_MULTIPLE,
                                                narrowband_sweep_settings)
    # The rig's own working broadband sweep: 10 s at gain 300.
    exp, gain = narrowband_sweep_settings(10.0, 300)
    assert exp == 10.0 * NARROWBAND_EXPOSURE_MULTIPLE
    assert gain == 300, "gain moved on a rig already past its HCG knee"
    # A camera that reports a high-conversion-gain threshold: never sweep a
    # read-noise-limited frame BELOW it (measured 3.96 e- -> 1.36 e- at 125).
    assert narrowband_sweep_settings(10.0, 100, hcg_threshold_gain=125)[1] == 125
    assert narrowband_sweep_settings(10.0, 300, hcg_threshold_gain=125)[1] == 300
    # …and a camera that reports none changes nothing at all.
    assert narrowband_sweep_settings(4.0, 100)[1] == 100


async def test_a_narrowband_slot_is_swept_at_its_own_exposure_and_gain(
        tmp_path, monkeypatch):
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        fw.filter_names = ["L", "Ha", "OIII"]
        fw.filter_offsets = [0, 0, 0]

        async def fast_set_position(slot):
            fw.rig.filter_slot = int(slot)
        monkeypatch.setattr(fw, "set_position", fast_set_position)

        seen: list[tuple[int, float, int]] = []

        async def fake_af(cam, foc, **kw):
            seen.append((fw.rig.filter_slot, kw["exposure_s"], kw["gain"]))
            return af_module.AutofocusResult(True, 5000, 2.1, [], "ok")
        monkeypatch.setattr(af_module, "run_autofocus", fake_af)

        await h.learn_filter_offsets(
            ref_slot=0, exposure_s=10.0, gain=300,
            narrowband=[False, True, True])

        by_slot = {s: (e, g) for s, e, g in seen}
        assert by_slot[0] == (10.0, 300), "the broadband slot was not left alone"
        # x4 of the broadband exposure — see NARROWBAND_EXPOSURE_MULTIPLE.
        assert by_slot[1] == (40.0, 300), by_slot
        assert by_slot[2] == (40.0, 300), by_slot

        # An explicit pair overrides the derivation, both halves.
        seen.clear()
        await h.learn_filter_offsets(
            ref_slot=0, exposure_s=10.0, gain=300,
            nb_exposure_s=90.0, nb_gain=420)
        by_slot = {s: (e, g) for s, e, g in seen}
        assert by_slot[1] == (90.0, 420), by_slot
        assert by_slot[0] == (10.0, 300), "the override leaked onto a broadband slot"
    finally:
        await h.disconnect_all()


async def test_a_filters_own_saved_settings_beat_the_narrowband_derivation(
        tmp_path, monkeypatch):
    """#215. THE ONE PLACE a per-filter pin is authoritative rather than a
    default.

    Everywhere else in the product these are seeds — the camera dial takes them
    when the filter changes, a new plan step is filled in from them — and the
    sequence engine never reads them, because a plan that gets rewritten
    underneath the operator stops describing the night.

    A sweep has no plan to consult. It is a measurement that either works or
    wastes the night, and the derivation it would otherwise use is a HEURISTIC:
    4x the broadband exposure, applied identically to every narrowband slot. An
    operator who has actually measured that Ha needs 60 s has better information
    than the multiplier does, and the multiplier silently overriding them is how
    #148 stayed open.

    Note the mixed case below: pinning only ONE half must leave the other half
    on the derivation, not reset it to the broadband value.
    """
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        fw.filter_names = ["L", "Ha", "OIII"]
        fw.filter_offsets = [0, 0, 0]
        fw.filter_narrowband = [False, True, True]
        # Ha: both halves pinned. OIII: nothing pinned, so it keeps the x4
        # derivation. L: broadband, untouched.
        fw.filter_exposures = [None, 60.0, None]
        fw.filter_gains = [None, 180, None]

        async def fast_set_position(slot):
            fw.rig.filter_slot = int(slot)
        monkeypatch.setattr(fw, "set_position", fast_set_position)

        seen: list[tuple[int, float, int]] = []

        async def fake_af(cam, foc, **kw):
            seen.append((fw.rig.filter_slot, kw["exposure_s"], kw["gain"]))
            return af_module.AutofocusResult(True, 5000, 2.1, [], "ok")
        monkeypatch.setattr(af_module, "run_autofocus", fake_af)

        await h.learn_filter_offsets(ref_slot=0, exposure_s=10.0, gain=300)
        by_slot = {s: (e, g) for s, e, g in seen}
        assert by_slot[1] == (60.0, 180), (
            f"Ha's own saved settings lost to the x4 derivation: {by_slot}")
        assert by_slot[2] == (40.0, 300), (
            f"an UNPINNED narrowband slot must keep the derivation: {by_slot}")
        assert by_slot[0] == (10.0, 300), (
            f"a pin leaked onto a broadband slot: {by_slot}")

        # Half-pinned: the exposure is the operator's, the gain is still the
        # derivation's. Falling back to the BROADBAND gain here would sweep a
        # 60 s narrowband frame at a gain chosen for luminance.
        seen.clear()
        fw.filter_exposures = [None, 60.0, None]
        fw.filter_gains = [None, None, None]
        await h.learn_filter_offsets(ref_slot=0, exposure_s=10.0, gain=300)
        by_slot = {s: (e, g) for s, e, g in seen}
        assert by_slot[1] == (60.0, 300), by_slot
    finally:
        await h.disconnect_all()


async def test_the_narrowband_marking_persists_and_survives_a_cancelled_run(
        tmp_path, monkeypatch):
    """It is a property of the WHEEL, not of the run.

    The operator's wheel does not change between nights, so a marking that
    lived only as long as the run would have to be re-entered every time — and
    the run this exists for is the one that gets cancelled."""
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        fw.filter_names = ["L", "Ha", "OIII"]
        fw.filter_offsets = [0, 0, 0]

        async def fast_set_position(slot):
            fw.rig.filter_slot = int(slot)
        monkeypatch.setattr(fw, "set_position", fast_set_position)

        async def die_on_the_second_slot(cam, foc, **kw):
            if fw.rig.filter_slot != 0:
                raise RuntimeError("stopped")
            return af_module.AutofocusResult(True, 5000, 2.1, [], "ok")
        monkeypatch.setattr(af_module, "run_autofocus", die_on_the_second_slot)

        await h.learn_filter_offsets(ref_slot=0, exposure_s=0.01,
                                     narrowband=[False, True, True])
        saved = configmod.load_filter_config(
            configmod.config_store.cfg().active_profile_id)
        assert saved["narrowband"] == [False, True, True], saved
        assert fw.is_narrowband(1) and not fw.is_narrowband(0)

        # …and it comes back on the next connect, or the next night's run
        # sweeps three narrowband slots at the broadband exposure again.
        fw.filter_narrowband = []
        h._seed_filter_config()
        assert fw.filter_narrowband == [False, True, True]
    finally:
        await h.disconnect_all()


async def test_a_blackout_slot_is_never_narrowband(tmp_path, monkeypatch):
    """The two flags together would only buy a longer exposure of nothing."""
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        fw.filter_names = ["L", "Ha", "Dark"]
        out = await h.set_filter_names(["L", "Ha", "Dark"], [0, 0, 0],
                                       [False, False, True],
                                       [False, True, True])
        assert out["narrowband"] == [False, True, False], out
    finally:
        await h.disconnect_all()
