# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The engine labels a mosaic's frames in the session report (#188, backlog
WP-127; wave 16 integration).

THE GAP. WP-127 taught the report, the bundle and both report views to group a
mosaic's panels by two labels on the frame record, ``mosaic`` and ``panel``,
and built every reader. Nothing WROTE them: ``SequenceEngine._reporter_record``
built the ``FrameRecord`` with neither, so on a real night every report said
``mosaic: None`` and both views drew exactly what they drew before, a feature
built, tested and unreachable. The engine now labels a group member's LIGHT
frame with the same rule that writes the FITS ``MOSAIC`` and ``PANEL`` cards
(``SequenceEngine._panel_labels``), inside the existing ``try`` so a panel with
no grid position logs "report record failed" and the run goes on.

THE NIGHT is the clocked group harness's (``tests/_group_harness.py``): the
S5 probe's flow (``_flow_night.mosaic_flow``), a rotating 2x2 of ``L 10, R 10``
for two cycles, saved through ``POST /api/flows`` and run through ``POST
/api/flows/{id}/run``; the report is read back through ``GET
/api/reports/{id}`` as the UI reads it.

NAMED MUTANTS. Each was run from a byte backup of ``sequence/engine.py``
inside the integration worktree, restored byte-identically (sha256 compared)
and the mutant text grepped out, under one worker. The assertion each broke is
recorded verbatim on the test that caught it.
"""
from __future__ import annotations

import pytest

from _flow_night import (FlowRig, flow_rig,  # noqa: F401 (fixture)
                         mosaic_flow, panel_label)
from _group_harness import group_store, ra_at  # noqa: F401 (fixture)

FLOW = mosaic_flow(ra_hours=ra_at(-2.0), plan="L 10, R 10", cycles=2)
PANELS = {"1-1", "1-2", "2-1", "2-2"}


async def _the_report(flow_rig) -> dict:
    """Run the night to its end and read the one run's report back."""
    rig: FlowRig = flow_rig
    night = await rig.night()
    fid = await rig.save_flow(FLOW)
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    await night.finish()
    end = await rig.read(night, fid)
    (rid,) = end.session.nights
    return end.reports[rid]


async def test_every_panel_row_of_a_mosaic_night_carries_its_mosaic_and_panel(
        flow_rig):
    """Four panels, each row labelled ('M31', 'r-c') as the FITS cards of its
    frames are; the panel is the row's own, not the last frame's of the night.

    RED under mutant "the record is not labelled" (the ``mosaic=`` and
    ``panel=`` arguments dropped from the ``FrameRecord`` built in
    ``_reporter_record``), observed:

        AssertionError: the panel rows carry [(None, None), (None, None),
        (None, None), (None, None)] where the mosaic and panel were due: [...]
    """
    rep = await _the_report(flow_rig)
    rows = {panel_label(t["name"]): (t.get("mosaic"), t.get("panel"))
            for t in rep["targets"]}
    assert set(rows) == PANELS, f"premise: four panels in the report: {rows}"
    assert rows == {p: ("M31", p) for p in PANELS}, (
        f"the panel rows carry {sorted(rows.values(), key=str)} where the "
        f"mosaic and panel were due: {rep['targets']}")


async def test_only_a_group_members_light_is_labelled(flow_rig):
    """CONTROL: a dark a hold shoots on a panel, and a non-member's frame,
    record neither label (the FITS cards are written by the same helper), so
    the report cannot label what the file does not.

    RED under mutant "labels set on dark frames too" (the ``frame_type`` test
    dropped from ``_panel_labels``), observed:

        AssertionError: a dark on a panel is labelled {'mosaic': 'M31', 'panel': '1-1'}
        assert {'mosaic': 'M...panel': '1-1'} == {}
    """
    from astrodeck.sequence.models import ExposureStep, Target

    rig: FlowRig = flow_rig
    night = await rig.night()
    fid = await rig.save_flow(FLOW)
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    engine = night.engine
    member = next(t for t in engine.plan.targets
                  if engine._group_of(t) is not None)
    light = ExposureStep(filter="L", exposure_s=10, count=1)
    dark = ExposureStep(filter=None, exposure_s=10, count=1, frame_type="Dark")
    got = engine._panel_labels(member, light)
    assert got == {"mosaic": "M31", "panel": panel_label(member.name)}, (
        f"premise: a member's light is labelled {got}")
    assert engine._panel_labels(member, dark) == {}, (
        f"a dark on a panel is labelled {engine._panel_labels(member, dark)}")
    stranger = Target(name="Vega", ra_hours=18.6, dec_deg=38.8, steps=[light])
    assert engine._group_of(stranger) is None, "premise: Vega is no member"
    assert engine._panel_labels(stranger, light) == {}, (
        "a non-member's light is labelled")
    assert engine._panel_labels(None, light) == {}, "no target is labelled"
    await night.finish()


async def test_a_label_that_fails_logs_and_the_run_goes_on(flow_rig):
    """The labelling sits INSIDE ``_reporter_record``'s try: ``naming.
    panel_label`` raises for a member with no row/column, and a report-write
    hiccup must never break the run (that function's docstring promise). Here
    the labelling fails ONLY when the reporter asks (not for the FITS cards):
    the night still ends complete with all 16 frames in its ledger, 4 panels
    x 2 filters x 2 cycles.

    RED under mutant "the label outside the try" (the ``labels = self.
    _panel_labels(target, step)`` line moved above the ``try:`` of
    ``_reporter_record``), observed: the error leaves the engine and ends the
    run instead of being logged, so the session does not complete:

        AssertionError: the run did not complete: {'id': '...', 'status':
        'dormant', 'nights': 1, 'count_mode': 'accepted', 'armed': True, ...}
        assert 'dormant' == 'complete'
    """
    rig: FlowRig = flow_rig
    night = await rig.night()
    fid = await rig.save_flow(FLOW)
    engine = night.engine
    real_record, real_labels = engine._reporter_record, engine._panel_labels
    inside = {"on": False}

    def record(*a, **kw):
        inside["on"] = True
        try:
            return real_record(*a, **kw)
        finally:
            inside["on"] = False

    def labels(target, step):
        if inside["on"]:
            raise ValueError("no grid position")
        return real_labels(target, step)

    engine._reporter_record = record
    engine._panel_labels = labels
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    await night.finish()
    end = await rig.read(night, fid)
    assert end.progress["session"]["status"] == "complete", (
        f"the run did not complete: {end.progress['session']}")
    assert len(end.session.frames) == 16, (
        f"premise: all 16 frames banked, got {len(end.session.frames)}")
