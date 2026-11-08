# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""One flat set per distinct panel angle (#783, #1110 C-04b; wave 4, WP 4.1b).

THE CLASS: a claim nothing keeps. Flats are keyed by the rotator's mechanical
angle in the frame record, the CalKey and the matcher (WP-122, WP 2.4), and a
dust shadow follows the metal, not the sky. A mosaic whose panels sit at two
position angles is calibrated by two flats, one at each. Nothing shot them: the
DUSK FLATS stage took its one set at wherever the rotator happened to be, and
the stage's own log line said so.

THE STAGE NOW takes the plan's distinct panel angles (the ``rotation_deg`` of
the plan's light targets, which a rotating block's members carry), converts each
SKY angle to a MECHANICAL one the way the calibration-health route does for the
same rows (``rotation.sky_to_mechanical`` on the rotator's calibrated anchor and
the learned sign), folds angles that fall in one calibration bin to one, and
for each: turns the rotator there one-sided, shoots the set for every filter,
and so records ROTMECH on every flat. One set per angle per night. With no
rotator, or a plan with no angle, one set at the current angle, as before.

It REFUSES, and says why, rather than shoot at an angle it cannot name: a
rotator whose self-test failed (D-05), a sign never learned, a rotator never
calibrated against the sky. A flat at an invented angle is a flat nothing
matches.

Named mutant: the per-angle loop skipped (the stage shoots one set at the
current angle): ``test_a_two_angle_mosaic_shoots_two_sets_with_distinct_rotmech``
finds one set.
"""
from __future__ import annotations

import os
import time

import pytest
from astropy.io import fits

import astrodeck.hub as hub_module
from astrodeck.calibration.library import CalibrationLibrary
from astrodeck.hub import Hub
from astrodeck.rotation import sky_to_mechanical
from astrodeck.sequence import engine as engine_module
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import (DuskFlatsPlan, ExposureStep,
                                       SequencePlan, Target)

#: The anchor the rotator's last sky calibration measured: the rotator read
#: mechanical 100 when it was synced at an offset of 10, so sky PA s is at
#: mechanical 100 + (s - 90).
ANCHOR_MECH, ANCHOR_OFFSET = 100.0, 10.0


def _mech(sky: float) -> float:
    return sky_to_mechanical(sky, ANCHOR_MECH, ANCHOR_OFFSET, 1) % 360.0


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _calibrate(hub, monkeypatch, *, sign=1, offset=ANCHOR_OFFSET) -> None:
    """The rotator is synced and its last sky-angle record is calibrated, the
    state a night that has solved and synced once is in."""
    rot = hub.devices["rotator"]
    rot.synced = True
    rot.sync_offset_deg = offset
    monkeypatch.setattr(hub, "last_sky_angle", {
        "pa_deg": 90.0, "exposed_at": 1.0, "solved_at": 1.0,
        "source": "plate solve + sync", "pier_side": None,
        "camera": "Sim Camera", "calibrated": True, "reason": None,
        "mechanical_deg": ANCHOR_MECH, "rotator_before_deg": None,
        "offset_deg": offset}, raising=False)
    monkeypatch.setattr(hub, "_rotator_sky_sign", sign, raising=False)


def _panel(i: int, sky: float | None, filters=("L", "R")) -> Target:
    return Target(name=f"Panel {i}", ra_hours=0.7 + 0.05 * i, dec_deg=41.0,
                  center=False, autofocus_first=False, rotation_deg=sky,
                  steps=[ExposureStep(filter=f, exposure_s=1.0, gain=100,
                                      offset=30, count=2) for f in filters])


def _plan(skies, *, count=2, filters=("L", "R")) -> SequencePlan:
    return SequencePlan(
        name="Mosaic night", guide=False, dither_every=0,
        targets=[_panel(i, s, filters) for i, s in enumerate(skies)],
        dusk_flats=DuskFlatsPlan(method="panel", filters=list(filters),
                                 adu_target=8000, count=count))


class Watch:
    """The saved flats in order, each with the rotator's mechanical angle at
    its capture and the ROTMECH card of the file it wrote."""

    def __init__(self, hub, eng, monkeypatch):
        self.flats: list[tuple[str, float, float | None]] = []
        self.scheduled = 0
        real = hub.capture
        rot = hub.devices.get("rotator")

        async def capture(exposure_s, gain, offset, binning=1, **kw):
            info = await real(exposure_s, gain, offset, binning, **kw)
            if kw.get("frame_type") == "Flat" and kw.get("save", True):
                fw = hub.devices["filterwheel"]
                name = fw.filter_names[await fw.get_position()]
                card = None
                if info.get("saved_path"):
                    card = fits.getheader(info["saved_path"]).get("ROTMECH")
                at = (await rot.get_mechanical_position()
                      if rot is not None else None)
                self.flats.append((name, at, card))
            return info

        async def scheduled(plan):
            self.scheduled += 1

        monkeypatch.setattr(hub, "capture", capture)
        monkeypatch.setattr(eng, "_run_scheduled", scheduled)

    def sets(self) -> list[tuple[float | None, list[str]]]:
        """``(ROTMECH, filters in order)`` per run of one angle."""
        out: list[tuple[float | None, list[str]]] = []
        for name, _at, card in self.flats:
            key = None if card is None else round(card, 1)
            if out and out[-1][0] == key:
                out[-1][1].append(name)
            else:
                out.append((key, [name]))
        return out


def _log_spy(monkeypatch) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    real = engine_module.bus.log

    def log(level, message, source="hub", **kw):
        lines.append((level, message))
        return real(level, message, source, **kw)

    monkeypatch.setattr(engine_module.bus, "log", log)
    return lines


async def _run(hub, plan, monkeypatch, *, engine=None):
    eng = engine or SequenceEngine(hub)
    watch = Watch(hub, eng, monkeypatch)
    eng.start(plan)
    await eng._task
    return eng, watch


# ------------------------------------------------------------- the headline

@pytest.mark.asyncio
async def test_a_two_angle_mosaic_shoots_two_sets_with_distinct_rotmech(
        sim_hub, monkeypatch):
    """Two panels at sky PA 10 and 100 are two mechanical angles, and each
    gets a full set (2 per filter, L then R) with its own ROTMECH card: the
    file's, not just the rotator's reading.

    RED under "per-angle loop skipped" (the stage shoots one set at the
    current angle): one set, ROTMECH 0.0."""
    _calibrate(sim_hub, monkeypatch)
    _, w = await _run(sim_hub, _plan([10.0, 100.0]), monkeypatch)
    assert w.sets() == [(round(_mech(10.0), 1), ["L", "L", "R", "R"]),
                        (round(_mech(100.0), 1), ["L", "L", "R", "R"])], (
        w.flats)
    assert w.scheduled == 1, "the night did not go on to its lights"


@pytest.mark.asyncio
async def test_the_rotator_is_turned_to_each_angle_before_its_set(
        sim_hub, monkeypatch):
    """Every flat is shot with the rotator AT its angle (to the tolerance of
    the bin), not merely labelled with it. One-sided: a leg that runs against
    the approach direction overshoots and comes back, so the sim rotator is
    asked for more than one move per angle at most."""
    _calibrate(sim_hub, monkeypatch)
    await _run(sim_hub, _plan([100.0, 10.0]), monkeypatch)
    rot = sim_hub.devices["rotator"]
    assert _mech(100.0) in [round(m, 3) for m in rot.moves]
    assert _mech(10.0) in [round(m, 3) for m in rot.moves]


@pytest.mark.asyncio
async def test_panels_in_one_bin_share_one_set(sim_hub, monkeypatch):
    """Two panels one degree apart are one calibration bin (2 degrees wide):
    a second set at one master's angle would be the same master."""
    _calibrate(sim_hub, monkeypatch)
    _, w = await _run(sim_hub, _plan([10.0, 10.8]), monkeypatch)
    assert len(w.sets()) == 1, w.sets()


@pytest.mark.asyncio
async def test_panels_at_one_angle_share_one_set_whatever_their_order(
        sim_hub, monkeypatch):
    """A plan that visits angle A, B, A (panels interleaved) still shoots two
    sets, in the order the plan first needs them."""
    _calibrate(sim_hub, monkeypatch)
    _, w = await _run(sim_hub, _plan([10.0, 100.0, 10.0]), monkeypatch)
    assert [k for k, _ in w.sets()] == [round(_mech(10.0), 1),
                                        round(_mech(100.0), 1)]


# ------------------------------------------------------- when there is none

@pytest.mark.asyncio
async def test_no_rotator_means_one_set_as_before(sim_hub, monkeypatch):
    del sim_hub.devices["rotator"]
    _, w = await _run(sim_hub, _plan([10.0, 100.0]), monkeypatch)
    assert w.sets() == [(None, ["L", "L", "R", "R"])], w.flats


@pytest.mark.asyncio
async def test_a_plan_with_no_angle_shoots_one_set_and_leaves_the_rotator(
        sim_hub, monkeypatch):
    rot = sim_hub.devices["rotator"]
    _, w = await _run(sim_hub, _plan([None, None]), monkeypatch)
    assert len(w.sets()) == 1 and len(w.flats) == 4, w.flats
    assert rot.moves == [], "the rotator was moved for a plan with no angle"


# ------------------------------------------------------------- the refusals

@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["self-test failed", "sign unknown",
                                 "never calibrated"])
async def test_it_refuses_rather_than_shoot_at_an_angle_it_cannot_name(
        sim_hub, monkeypatch, why):
    """No flats at all, said once, the rotator untouched, and the night goes
    on to its lights. (A set at the current angle would be keyed at an angle no
    light of this plan has.)"""
    _calibrate(sim_hub, monkeypatch)
    if why == "self-test failed":
        monkeypatch.setattr(sim_hub, "_rotation_trusted", False)
    elif why == "sign unknown":
        monkeypatch.setattr(sim_hub, "_rotator_sky_sign", None)
    else:
        monkeypatch.setattr(sim_hub, "last_sky_angle", None, raising=False)
    lines = _log_spy(monkeypatch)
    _, w = await _run(sim_hub, _plan([10.0, 100.0]), monkeypatch)
    assert w.flats == [], w.flats
    assert w.scheduled == 1
    assert sim_hub.devices["rotator"].moves == []
    said = [m for lv, m in lines
            if lv == "warning" and "DUSK FLATS" in m and "rotator" in m]
    assert len(said) == 1, lines


# ------------------------------------------------- one set per angle per night

def _stamp_with_the_clock(root) -> None:
    now = time.time()
    for path in root.rglob("*.fits"):
        os.utime(path, (now, now))


@pytest.mark.asyncio
async def test_a_restart_shoots_only_the_angle_the_library_lacks(
        sim_hub, tmp_path, monkeypatch):
    """The per-night latch is the first engine's memory; the library is what a
    restart finds. Night one shot angle A. A second engine, whose plan now
    needs A and B, finds A fresh and shoots B alone."""
    _calibrate(sim_hub, monkeypatch)
    monkeypatch.setattr(sim_hub, "master_library",
                        CalibrationLibrary(lambda: tmp_path), raising=False)
    _, first = await _run(sim_hub, _plan([10.0]), monkeypatch)
    assert [k for k, _ in first.sets()] == [round(_mech(10.0), 1)]
    _stamp_with_the_clock(tmp_path)
    _, second = await _run(sim_hub, _plan([10.0, 100.0]), monkeypatch,
                           engine=SequenceEngine(sim_hub))
    assert [k for k, _ in second.sets()] == [round(_mech(100.0), 1)], (
        second.flats)


@pytest.mark.asyncio
async def test_the_same_engine_does_not_reshoot_an_angle_the_same_night(
        sim_hub, monkeypatch):
    _calibrate(sim_hub, monkeypatch)
    eng = SequenceEngine(sim_hub)
    _, first = await _run(sim_hub, _plan([10.0, 100.0]), monkeypatch,
                          engine=eng)
    assert len(first.sets()) == 2
    _, again = await _run(sim_hub, _plan([10.0, 100.0]), monkeypatch,
                          engine=eng)
    assert again.flats == []
