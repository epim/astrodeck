# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The engine stamps a mosaic's identity on the report's frame records (WP 3.2,
#1114, #796).

``test_w3_mosaic_identity.py`` pins what the report and the bundle do with the
ids; this file pins who writes them. ``SequenceEngine._reporter_record`` builds
the report's ``FrameRecord`` and now passes ``mosaic_id`` (the group's compiled
id) and ``panel_id`` (the panel target's id) for EVERY frame a group member
shoots, a hold's dark included, while the display labels stay on lights.

THE NIGHT is the clocked group harness's rotating 2x2 (``_flow_night.
mosaic_flow``), run to its end and read back through ``GET /api/reports/{id}``.
THE TWINS are two groups with one name and one set of panel names, put on the
engine of a started night and fed through ``_reporter_record`` as the capture
loop does.

NAMED MUTANT "panel_id dropped for darks": ``_reporter_record`` takes the
identity only for a light step. A dark then opens a row of its own, which reds
``test_a_dark_joins_its_own_panels_row``.
"""
from __future__ import annotations

from _flow_night import (FlowRig, flow_rig,  # noqa: F401 (fixture)
                         mosaic_flow, panel_label)
from _group_harness import group_store, ra_at  # noqa: F401 (fixture)
from astrodeck.sequence.models import ExposureStep

FLOW = mosaic_flow(ra_hours=ra_at(-2.0), plan="L 10, R 10", cycles=2)
LIGHT = ExposureStep(filter="L", exposure_s=10, count=1)
DARK = ExposureStep(filter=None, exposure_s=10, count=1, frame_type="Dark")


async def test_a_real_nights_panel_rows_carry_the_groups_and_the_panels_ids(
        flow_rig):
    """Four panels, each row carrying the compiled group's id and its own
    panel target's id, read through the route the UI reads.

    RED under mutant "the ids are not passed" (the ``mosaic_id=`` and
    ``panel_id=`` arguments dropped from the ``FrameRecord`` built in
    ``_reporter_record``): every row carries None.
    """
    rig: FlowRig = flow_rig
    night = await rig.night()
    fid = await rig.save_flow(FLOW)
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    engine = night.engine
    group_ids = set(engine._groups)
    panel_ids = {t.id for t in engine.plan.targets
                 if engine._group_of(t) is not None}
    await night.finish()
    end = await rig.read(night, fid)
    (rid,) = end.session.nights
    rows = end.reports[rid]["targets"]
    assert {panel_label(t["name"]) for t in rows} == {"1-1", "1-2", "2-1", "2-2"}
    assert {t["mosaic_id"] for t in rows} == group_ids and len(group_ids) == 1, (
        f"the rows carry mosaic ids {[t.get('mosaic_id') for t in rows]}")
    assert {t["panel_id"] for t in rows} == panel_ids and len(panel_ids) == 4, (
        f"the rows carry panel ids {[t.get('panel_id') for t in rows]}")
    frames = end.reports[rid]["frames"]
    assert all(f["mosaic_id"] in group_ids and f["panel_id"] in panel_ids
               for f in frames), "a light frame record lacks its ids"


def _twins(engine):
    """Two groups both named "Veil", each with panels "Veil 1-1" and
    "Veil 1-2": the only thing telling them apart is the ids."""
    base_group = next(iter(engine._groups.values()))
    base = [t for t in engine.plan.targets if engine._group_of(t) is not None]
    twins = {}
    for key in ("a", "b"):
        gid = f"twin-group-{key}"
        engine._groups[gid] = base_group.model_copy(
            update={"id": gid, "name": "Veil"})
        twins[key] = [
            t.model_copy(update={"id": f"twin-{key}-{i}", "mosaic_group": gid,
                                 "name": f"Veil {t.panel_row + 1}-{t.panel_col + 1}"})
            for i, t in enumerate(base[:2])]
    return twins


def _veil_rows(engine):
    rows = engine.reporter._totals.breakdowns()[1]
    return sorted(((t.mosaic_id, t.panel_id, t.name, t.frames, t.rejected)
                   for t in rows if t.name.startswith("Veil")), key=str)


async def test_two_same_named_groups_are_four_rows_with_distinct_ids(flow_rig):
    rig: FlowRig = flow_rig
    night = await rig.night()
    fid = await rig.save_flow(FLOW)
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    engine = night.engine
    twins = _twins(engine)
    for key in ("a", "b"):
        for t in twins[key]:
            engine._reporter_record(t, LIGHT, {}, accepted=True)
    for t in twins["a"]:
        engine._reporter_record(t, LIGHT, {}, accepted=True)
    assert _veil_rows(engine) == [
        ("twin-group-a", "twin-a-0", "Veil 1-1", 2, 0),
        ("twin-group-a", "twin-a-1", "Veil 1-2", 2, 0),
        ("twin-group-b", "twin-b-0", "Veil 1-1", 1, 0),
        ("twin-group-b", "twin-b-1", "Veil 1-2", 1, 0)], (
        "two groups named Veil, with the same panel names, are two mosaics")
    await night.finish()


async def test_a_dark_joins_its_own_panels_row(flow_rig):
    """A dark a hold shoots on a panel carries no label but does carry the
    ids, so it lands on its own panel's row and not on a namesake's.

    RED under mutant "panel_id dropped for darks" (``_reporter_record``
    taking the identity only for a light step): the dark opens a row of its
    own.
    """
    rig: FlowRig = flow_rig
    night = await rig.night()
    fid = await rig.save_flow(FLOW)
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    engine = night.engine
    twins = _twins(engine)
    engine._reporter_record(twins["a"][0], LIGHT, {}, accepted=True)
    engine._reporter_record(twins["b"][0], LIGHT, {}, accepted=True)
    engine._reporter_record(twins["b"][0], DARK, {}, accepted=True)
    assert _veil_rows(engine) == [
        ("twin-group-a", "twin-a-0", "Veil 1-1", 1, 0),
        ("twin-group-b", "twin-b-0", "Veil 1-1", 2, 0)], (
        "a dark shot on a panel did not land on its own panel's row")
    await night.finish()


async def test_a_non_member_carries_no_ids(flow_rig):
    from astrodeck.sequence.models import Target

    rig: FlowRig = flow_rig
    night = await rig.night()
    fid = await rig.save_flow(FLOW)
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    engine = night.engine
    stranger = Target(name="Vega", ra_hours=18.6, dec_deg=38.8, steps=[LIGHT])
    assert engine._panel_identity(stranger) == {}
    assert engine._panel_identity(None) == {}
    member = next(t for t in engine.plan.targets
                  if engine._group_of(t) is not None)
    assert engine._panel_identity(member) == {
        "mosaic_id": engine._group_of(member).id, "panel_id": member.id}
    await night.finish()
