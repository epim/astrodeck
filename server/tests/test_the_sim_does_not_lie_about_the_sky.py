"""Two things the simulated rig asserts that are not true.

Both were found by attacking a testing design rather than by running anything,
and both matter because the plan is to grade whole simulated nights against
this rig. A simulator that lies is worse than none: it manufactures confidence.

1. THE OPAQUE DARK SLOT RENDERS STARS AT FULL LUMINANCE FLUX.
   `filter_names` has EIGHT entries with "Dark" at index 7 and
   `filter_opaque = [False] * 7 + [True]` marks it (devices/sim.py:1098-1100),
   but `_render_stars` picks its flux from a SEVEN-element list indexed
   `self.rig.filter_slot % 7` (devices/sim.py:552). Slot 7 wraps to 0, so a
   frame through the blackout slot comes out as bright as an L frame.

   That is exactly the condition hub.py's blackout guard exists for. Its own
   comment records the 2026-08-13 incident: a cloud hold parked the wheel on
   slot 7, the probe frames were black (median 241 against a 240 dark floor),
   and `cloud_score` called them "clear (12 bright stars, 9x noise)" twice --
   which released the hold and sent the run back out under cloud. The sim
   cannot stage that incident, and cannot grade the guard written for it.

2. THE TWO PIER-SIDE ORACLES DISAGREE.
   `pier_side` computes honestly from the hour angle; `destination_pier_side`
   uses a toy rule on RA alone (devices/sim.py:907, :930). They are consulted
   by the same pre-slew guard, so in the sim a meridian-flip test is graded by
   a contradiction rather than by geometry.
"""
import numpy as np
import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.imaging.stars import detect_stars


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def _expose(hub, *, exposure_s=0.05):
    """A LIGHT exposure -- shutter open. Through a blackout slot that is exactly
    the 2026-08-13 probe frame: the shutter opened and no photon arrived."""
    await hub.capture(exposure_s, 100, 0, 1, save=False, frame_type="Light")
    frame = hub.last_frame            # capture() returns an info dict, not the frame
    data = getattr(frame, "data", None)
    assert data is not None, "the sim must hand back linear pixels"
    return np.asarray(data)


async def test_the_opaque_dark_slot_renders_a_dark_frame(sim_hub):
    """A slot the operator flagged as carrying no glass passes no starlight."""
    fw = sim_hub.devices.get("filterwheel")
    assert fw is not None, "the sim rig has a filter wheel"
    dark_slot = [i for i, op in enumerate(fw.filter_opaque or []) if op]
    assert dark_slot, "precondition: some slot is flagged opaque"
    slot = dark_slot[0]
    assert fw.filter_names[slot] == "Dark", "the opaque slot is the Dark one"

    await fw.set_position(0)                       # L, for the comparison
    lit = await _expose(sim_hub)
    lit_stars = len(detect_stars(lit))

    await fw.set_position(slot)
    dark = await _expose(sim_hub)
    dark_stars = len(detect_stars(dark))

    assert lit_stars > 0, (
        f"precondition: the L frame must have stars to compare against, got "
        f"{lit_stars}")
    assert dark_stars == 0, (
        f"the blackout slot rendered {dark_stars} stars against L's {lit_stars}. "
        f"flux_scale is a 7-element list indexed `% 7` (sim.py:552) so slot "
        f"{slot} wraps onto slot 0 and Dark is as bright as L. This is the "
        f"2026-08-13 incident the hub's blackout guard exists for, and the sim "
        f"cannot reproduce it.")


async def test_a_light_through_the_blackout_slot_matches_a_shutter_closed_dark(sim_hub):
    """The physical invariant, and the one that is robust to measure.

    Opening the shutter behind a slot carrying no glass admits exactly as much
    sky as not opening it at all. So a LIGHT frame through the blackout slot
    must be indistinguishable from a shutter-closed DARK frame.

    Measured against the bug: L peaks 8.3 sigma over its floor with 3 stars,
    a narrowband frame 4.7 sigma with 0, and the blackout slot 7.2 sigma with
    2 -- i.e. the Dark slot behaves like L. An earlier version of this test
    compared max and std between the lit and blackout frames and PASSED,
    because with flux_scale wrapped to 1.0 the two are statistically identical
    and which peaks higher is a coin flip; a sigma threshold could not
    separate the buggy 7.2 from an empty field's 4.7 either. Comparing against
    a shutter-closed frame is the comparison that cannot be fooled.
    """
    fw = sim_hub.devices["filterwheel"]
    slot = [i for i, op in enumerate(fw.filter_opaque or []) if op][0]
    await fw.set_position(slot)

    through_glassless_slot = await _expose(sim_hub)          # shutter OPEN
    await sim_hub.capture(0.05, 100, 0, 1, save=False, frame_type="Dark")
    shutter_closed = np.asarray(sim_hub.last_frame.data)     # shutter CLOSED

    open_stars = len(detect_stars(through_glassless_slot))
    closed_stars = len(detect_stars(shutter_closed))
    assert open_stars == closed_stars == 0, (
        f"shutter open through the blackout slot found {open_stars} stars, "
        f"shutter closed found {closed_stars}. Both must be 0: no photon from "
        f"the sky reached the sensor either way.")


async def test_the_two_pier_side_oracles_agree(sim_hub):
    """`destination_pier_side` must predict what `pier_side` will report.

    They are read by the same pre-slew guard, so a disagreement means every
    meridian-flip decision in the sim is graded against a contradiction. The
    toy rule (`ra < 12h -> EAST`) ignores the hour angle entirely.

    This was parametrized over three sidereal times until I noticed the
    parameter was never applied -- all three cases ran at the wall clock's LST
    and returned the identical 8/24, which is a test that cannot tell its cases
    apart. One LST is enough to prove the oracle ignores hour angle; sweeping
    RA across the full circle at that LST is what does the work.
    """
    tel = sim_hub.devices.get("telescope")
    assert tel is not None
    rig = getattr(tel, "rig", None) or getattr(sim_hub, "rig", None)
    assert rig is not None, "the sim telescope exposes its rig"

    disagreements = []
    for ra in range(24):
        rig.ra_hours = float(ra)
        rig.dec_deg = 45.0
        actual = await tel.pier_side()
        predicted = await tel.destination_pier_side(float(ra), 45.0)
        if predicted != actual:
            disagreements.append((ra, predicted, actual))

    assert not disagreements, (
        f"{len(disagreements)} of 24 RA hours disagree; "
        f"first three: {disagreements[:3]}. destination_pier_side uses a rule "
        f"on RA alone (sim.py:930) while pier_side computes from the hour "
        f"angle (sim.py:907).")
