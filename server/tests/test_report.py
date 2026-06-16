"""Session-report tests (Batch 4b §1.7 / report.py).

Covers: record -> finalize -> load round-trip; per-filter headline as the leading
breakdown; rejects counted but not integrated; trends derived from frames at read
time (no duplicate arrays); list_reports summaries; attach_existing resume."""
from __future__ import annotations

import astrodeck.hub as hub_module
import pytest

from astrodeck.sequence.report import (FrameRecord, SessionReport,
                                       SessionReporter)
from astrodeck.sequence.models import SequencePlan


@pytest.fixture(autouse=True)
def isolate_reports(tmp_path, monkeypatch):
    """Redirect CAPTURE_DIR so reports land in tmp, never the real captures/."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    return tmp_path


def _frame(ts, target="M31", filt="Ha", accepted=True, exp=300.0, hfr=1.8,
           temp=-10.0, rms=0.6):
    return FrameRecord(ts=ts, target=target, filter=filt, frame_type="Light",
                       exposure_s=exp, accepted=accepted, hfr=hfr,
                       sensor_temp_c=temp, guide_rms_total=rms)


# --------------------------------------------------------- round-trip + headline

def test_record_finalize_load_roundtrip():
    plan = SequencePlan(name="MyNight")
    r = SessionReporter(plan)
    r.record_frame(_frame(1.0, filt="Ha"))
    r.record_frame(_frame(2.0, filt="Ha"))
    r.record_frame(_frame(3.0, filt="OIII"))
    r.record_safety("cloud sensor", "pause")
    report = r.finalize("complete")

    assert report.end_reason == "complete"
    assert report.ended_at is not None
    assert report.frames_captured == 3
    assert report.integration_s == pytest.approx(900.0)   # 3 * 300

    # persisted + reloadable by id
    loaded = SessionReporter.load(r.id)
    assert isinstance(loaded, SessionReport)
    assert loaded.id == r.id
    assert loaded.plan_name == "MyNight"
    assert loaded.frames_captured == 3
    assert len(loaded.safety_events) == 1
    assert loaded.safety_events[0]["action"] == "pause"


def test_per_filter_headline_is_present_and_split():
    r = SessionReporter(SequencePlan(name="N"))
    for i in range(3):
        r.record_frame(_frame(float(i), filt="Ha", exp=300.0))
    for i in range(2):
        r.record_frame(_frame(10.0 + i, filt="OIII", exp=600.0))
    rep = r.finalize("complete")
    by_filter = {f.filter: f for f in rep.by_filter}
    assert set(by_filter) == {"Ha", "OIII"}
    assert by_filter["Ha"].frames == 3
    assert by_filter["Ha"].integration_s == pytest.approx(900.0)
    assert by_filter["OIII"].integration_s == pytest.approx(1200.0)
    assert by_filter["Ha"].hfr_median is not None


def test_rejects_counted_not_integrated():
    r = SessionReporter(SequencePlan(name="N"))
    r.record_frame(_frame(1.0, accepted=True, exp=300.0))
    r.record_frame(_frame(2.0, accepted=False, exp=300.0))   # rejected
    rep = r.finalize("complete")
    assert rep.frames_captured == 1
    assert rep.frames_rejected == 1
    assert rep.integration_s == pytest.approx(300.0)         # reject not added
    ha = rep.by_filter[0]
    assert ha.frames == 1 and ha.rejected == 1


def test_calibration_frames_dont_integrate():
    r = SessionReporter(SequencePlan(name="N"))
    r.record_frame(FrameRecord(ts=1.0, target="darks", filter=None,
                               frame_type="Dark", exposure_s=300.0, accepted=True))
    rep = r.finalize("complete")
    assert rep.frames_captured == 1
    assert rep.integration_s == 0.0          # darks don't count as integration


# ------------------------------------------------------------------ trends

def test_trends_derived_from_frames():
    r = SessionReporter(SequencePlan(name="N"))
    for i in range(5):
        r.record_frame(_frame(float(i), hfr=1.5 + i * 0.1, temp=-10.0 - i,
                              rms=0.5 + i * 0.05))
    rep = r.finalize("complete")
    trends = SessionReporter.trends(rep)
    assert set(trends) == {"hfr", "temp", "rms"}
    assert len(trends["hfr"]) == 5
    # series carry (ts, value) pairs in order
    assert trends["hfr"][0] == [0.0, pytest.approx(1.5)]
    assert trends["temp"][-1] == [4.0, pytest.approx(-14.0)]


def test_trends_downsample_keeps_last():
    r = SessionReporter(SequencePlan(name="N"))
    for i in range(50):
        r.record_frame(_frame(float(i), hfr=float(i)))
    rep = r.finalize("complete")
    tr = SessionReporter.trends(rep, max_points=10)
    assert len(tr["hfr"]) <= 11          # ~10 + the always-kept last point
    assert tr["hfr"][-1][0] == 49.0      # last frame preserved


def test_trends_skip_rejected_and_missing():
    r = SessionReporter(SequencePlan(name="N"))
    r.record_frame(_frame(1.0, accepted=True, hfr=2.0))
    r.record_frame(_frame(2.0, accepted=False, hfr=9.0))     # rejected -> excluded
    r.record_frame(FrameRecord(ts=3.0, target="M31", filter="Ha",
                               frame_type="Light", exposure_s=300.0,
                               accepted=True, hfr=None))       # no hfr
    rep = r.finalize("complete")
    tr = SessionReporter.trends(rep)
    assert tr["hfr"] == [[1.0, 2.0]]


# ------------------------------------------------------------------ list/attach

def test_list_reports_summaries_newest_first():
    SessionReporter(SequencePlan(name="A"),
                    report_id="A-20240101-000000", started_at=100.0).finalize("complete")
    SessionReporter(SequencePlan(name="B"),
                    report_id="B-20240102-000000", started_at=200.0).finalize("aborted")
    summaries = SessionReporter.list_reports()
    assert [s["id"] for s in summaries] == ["B-20240102-000000", "A-20240101-000000"]
    assert "frames" not in summaries[0]      # summaries carry no frame detail
    assert summaries[0]["end_reason"] == "aborted"


def test_load_missing_returns_none():
    assert SessionReporter.load("does-not-exist") is None


def test_attach_existing_resumes_appending():
    r = SessionReporter(SequencePlan(name="Resume"), report_id="Resume-x",
                        started_at=1.0)
    r.record_frame(_frame(1.0))
    r.finalize("aborted")          # crash mid-run, say

    r2 = SessionReporter.attach_existing("Resume-x")
    assert r2 is not None
    assert r2.id == "Resume-x"
    r2.record_frame(_frame(2.0))
    rep = r2.finalize("complete")
    assert rep.frames_captured == 2            # original + appended
    assert rep.end_reason == "complete"
