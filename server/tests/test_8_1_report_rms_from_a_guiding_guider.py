# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1144: a report frame stores a guide RMS only from a guider that guided it.

`_reporter_record` took ``guide_rms_total`` from the guider's rolling
``stats().rms_total`` whenever a guider was connected: a stale figure from a
guider that had stopped, the rolling window and not the exposure, and a PIXEL
number (no guide focal length) that the report labels arcsec. It now stores
the frame's own judged exposure RMS in arcsec, the figure the ledger quotes
(`GuideVerdict.figure`), and None otherwise: no guider, not guiding at the
shutter, a calibration frame, too few samples, or a unit that is not arcsec.

Every night runs the real frame loop on the clocked simulator
(tests/_group_harness.py) with the guider's samples scripted
(tests/_w8_1_guider.py), and the reporter's records are read as they are
written.

Named mutant (``_reporter_record`` reads the idle guider's ``rms_total``
again): RED on ``test_an_idle_guider_with_a_stale_figure_leaves_the_report_frame_without_rms``.
"""
from __future__ import annotations

import json

import pytest

import astrodeck.hub as hub_module
from astrodeck.guide.base import GuideStats
from astrodeck.sequence import SequencePlan
from astrodeck.sequence.bundle import NullMasterLibrary, build_bundle
from astrodeck.sequence.report import (FrameRecord, SessionReport,
                                       SessionReporter)
from astrodeck.sequence.session import session_store

from _group_harness import (  # noqa: F401
    Night, group_hub, group_store, single)
from _w8_1_guider import StampedGuider

CEILING = 5.0


def _plan(*, guide: bool = True, **over) -> SequencePlan:
    kw = dict(name="w81rep", guide=guide, recover_guiding=True,
              max_guide_rms=CEILING, dither_every=0, autofocus_every=0,
              meridian_flip=False, park_when_done=False,
              warm_cooler_when_done=False, count_mode="accepted")
    kw.update(over)
    return SequencePlan(targets=[single("Alpha", count=1)], **kw)


def _steady(now: float) -> list[float]:
    return [float(t) for t in range(int(now) - 200, int(now) + 1)]


async def _night(hub, monkeypatch, guider, plan) -> tuple[Night, list[FrameRecord]]:
    """Run ``plan`` and return the night with every ``FrameRecord`` the engine
    handed the reporter, in order."""
    records: list[FrameRecord] = []
    real = SessionReporter.record_frame

    def record_frame(self, rec):
        records.append(rec)
        return real(self, rec)

    monkeypatch.setattr(SessionReporter, "record_frame", record_frame)
    hub.guider = guider
    night = Night(hub, monkeypatch)
    guider.bind(night)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    assert night.done, night.trace[-3:]
    return night, records


async def test_an_idle_guider_with_a_stale_figure_leaves_the_report_frame_without_rms(
        group_hub, group_store, monkeypatch):
    """The plan does not guide; the connected guider holds a high stale figure
    from an earlier stretch. The report frame stores None.

    MUTANT (``rms`` read from ``stats().rms_total`` whenever a guider is
    connected): RED - ``assert 9.0 is None``.
    """
    guider = StampedGuider(lambda t: 9.0, _steady)
    _night_, records = await _night(group_hub, monkeypatch, guider,
                                    _plan(guide=False))
    assert guider.started_at == [], "premise: the plan never started it"
    assert guider.stats().rms_total == pytest.approx(9.0), "premise"
    lights = [r for r in records if r.frame_type.lower() == "light"]
    assert len(lights) == 1
    assert lights[0].guide_rms_total is None


async def test_a_guiding_guider_stores_the_exposures_figure_not_the_rolling_one(
        group_hub, group_store, monkeypatch):
    """Guiding is good before the shutter and 2 arcsec out inside the
    exposure. The rolling window is dominated by the good stretch; the report
    stores the exposure's own figure, the ledger's number."""
    state: dict[str, float] = {}

    def err(t: float) -> float:
        opened = state.get("open")
        return 2.0 if opened is not None and opened <= t <= opened + 30.0 \
            else 0.5

    guider = StampedGuider(err, _steady)
    night = None

    def at_shutter(rec) -> None:
        state.setdefault("open", rec["t"])

    hub = group_hub
    hub.guider = guider
    records: list[FrameRecord] = []
    real = SessionReporter.record_frame
    monkeypatch.setattr(SessionReporter, "record_frame",
                        lambda self, rec: (records.append(rec), real(self, rec))[1])
    night = Night(hub, monkeypatch)
    guider.bind(night)
    night.on_capture = at_shutter
    try:
        night.done = await night.run(_plan(), wall_s=60.0)
    finally:
        await night.close()
    assert night.done, night.trace[-3:]
    (ledger_frame,) = list(session_store.load(night.session_id).frames)
    (light,) = [r for r in records if r.frame_type.lower() == "light"]
    assert light.guide_rms_total == pytest.approx(
        ledger_frame.metrics["guide_rms"])
    assert light.guide_rms_total == pytest.approx(2.0, abs=0.05)
    assert guider.stats().rms_total < 1.5, "premise: the rolling figure differs"


async def test_a_pixel_only_guider_stores_none(group_hub, group_store,
                                               monkeypatch):
    class PixelGuider(StampedGuider):
        def stats(self):
            real = super().stats()
            return GuideStats(guiding=real.guiding, rms_total=real.rms_total,
                              recent=real.recent, is_arcsec=False,
                              image_scale=0.0, phase=real.phase)

    guider = PixelGuider(lambda t: 0.5, _steady)
    _night_, records = await _night(group_hub, monkeypatch, guider, _plan())
    (light,) = [r for r in records if r.frame_type.lower() == "light"]
    assert light.guide_rms_total is None


async def test_an_unjudged_frame_stores_none(group_hub, group_store,
                                             monkeypatch):
    state: dict[str, float] = {}

    def stamps(now: float) -> list[float]:
        opened = state.get("open")
        steady = _steady(now)
        if opened is None:
            return steady
        return [t for t in steady if not opened <= t <= opened + 30.0] + [
            opened + 1.0]

    guider = StampedGuider(lambda t: 0.5, stamps)
    hub = group_hub
    hub.guider = guider
    records: list[FrameRecord] = []
    real = SessionReporter.record_frame
    monkeypatch.setattr(SessionReporter, "record_frame",
                        lambda self, rec: (records.append(rec), real(self, rec))[1])
    night = Night(hub, monkeypatch)
    guider.bind(night)
    night.on_capture = lambda rec: state.setdefault("open", rec["t"])
    try:
        night.done = await night.run(_plan(), wall_s=60.0)
    finally:
        await night.close()
    assert night.done, night.trace[-3:]
    (light,) = [r for r in records if r.frame_type.lower() == "light"]
    assert light.guide_rms_total is None


async def test_a_calibration_frame_stores_none(group_hub, group_store,
                                               monkeypatch):
    """A dark shot with a connected, guiding guider is not a guided frame."""
    from astrodeck.sequence.models import ExposureStep, Target
    guider = StampedGuider(lambda t: 3.0, _steady)
    target = Target(id="dk", name="Darks", ra_hours=0.0, dec_deg=0.0,
                    center=False, autofocus_first=False, calibration=True,
                    steps=[ExposureStep(id="dk-d", filter="L", exposure_s=10.0,
                                        count=1, frame_type="Dark")])
    plan = SequencePlan(name="w81dark", targets=[target], guide=True,
                        max_guide_rms=CEILING, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        park_when_done=False, warm_cooler_when_done=False)
    _night_, records = await _night(group_hub, monkeypatch, guider, plan)
    darks = [r for r in records if r.frame_type.lower() == "dark"]
    assert darks, [r.frame_type for r in records]
    assert all(r.guide_rms_total is None for r in darks)


# ----------------------------------------------------- old reports on disk

OLD_REPORT = {
    "id": "old-night", "plan_name": "old", "started_at": 1.0,
    "frames": [
        {"ts": 1.0, "target": "M42", "filter": "Ha", "frame_type": "Light",
         "exposure_s": 300.0, "accepted": True, "hfr": 2.0,
         "guide_rms_total": 0.5, "saved_path": "/cap/a.fits", "gain": 100,
         "binning": 1},
        {"ts": 2.0, "target": "M42", "filter": "Ha", "frame_type": "Light",
         "exposure_s": 300.0, "accepted": True, "hfr": 4.0,
         "guide_rms_total": 1.0, "saved_path": "/cap/b.fits", "gain": 100,
         "binning": 1},
        {"ts": 3.0, "target": "M42", "filter": "Ha", "frame_type": "Light",
         "exposure_s": 300.0, "accepted": True, "hfr": 3.0,
         "guide_rms_total": None, "saved_path": "/cap/c.fits", "gain": 100,
         "binning": 1},
    ],
}


def test_an_old_report_reads_as_it_did():
    """RULE 14: a report written before this change loads with its numbers and
    every reader answers as it did. The expected values were taken on the tree
    before the change: the trends plot the stored points (a None frame is
    skipped) and the bundle weights follow from them (best sub 1.0, the sub
    with twice the HFR and twice the RMS 0.5, the sub with no RMS scored on its
    HFR alone)."""
    report = SessionReport(**json.loads(json.dumps(OLD_REPORT)))
    assert [f.guide_rms_total for f in report.frames] == [0.5, 1.0, None]
    trends = SessionReporter.trends(report)
    assert trends["rms"] == [[1.0, 0.5], [2.0, 1.0]]
    bundle = build_bundle(report, NullMasterLibrary(), is_local=lambda p: True)
    weights = {light.src: light.weight for light in bundle.groups[0].lights}
    assert weights["/cap/a.fits"] == pytest.approx(1.0)
    assert weights["/cap/b.fits"] == pytest.approx(0.5)
    assert weights["/cap/c.fits"] == pytest.approx(2.0 / 3.0, abs=1e-4)
