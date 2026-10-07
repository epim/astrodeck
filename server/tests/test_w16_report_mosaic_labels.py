# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A mosaic's panels are rows of ONE mosaic in the session report (#188 WP-2).

The report listed a 2x2 mosaic as four unrelated targets, so neither report
view could group them and the stacking bundle could not tell a mosaic's panel
from a target that happens to share its name. ``FrameRecord`` and
``TargetBreakdown`` now carry ``mosaic`` (the group's label, what the FITS
``MOSAIC`` card says) and ``panel`` (``"r-c"``, what the ``PANEL`` card says),
both additive and None for every frame that is not a group member's light.

WHAT IS PINNED HERE is the report's own half: a labelled frame labels its
target row, a row keeps its label through a dark that carries none and through
a crash-resume (``_Totals.from_report``), and a report written before the
fields loads unchanged. WHO LABELS A FRAME (a group member's light frames and
nothing else, the FITS cards' rule) is the engine's ``_reporter_record``, which
this work package does not own: see the hand-off in the work package's return.

Mutants (each run from a byte backup of report.py, restored byte-identical
and grepped gone; the failure is as pytest printed it):

* ``from_report drops labels``: ``_Totals.from_report`` rebuilds each ``pt``
  entry with ``"mosaic": None, "panel": None``. Reds
  ``test_a_crash_resume_keeps_every_panels_label``::

      AssertionError: ['Veil 1-1', 'Veil 1-2'] lost their labels over a
      resume: [(None, None), (None, None)]

* ``a later unlabelled frame overwrites``: ``_Totals.add`` writes the frame's
  label whatever it is (``if fr.mosaic:`` becomes ``if True:``). Reds
  ``test_a_dark_after_the_light_does_not_unlabel_its_panel``::

      AssertionError: assert {'Veil 1-1': (None, None)} == {'Veil 1-1': ('Veil', '1-1')}

* ``first frame decides``: ``_Totals.add`` sets the label only while the row
  has no frame yet (``if fr.mosaic and te["frames"] + te["rejected"] == 0:``).
  Reds ``test_a_dark_before_the_first_light_does_not_leave_its_panel_unlabelled``::

      AssertionError: assert {'Veil 1-1': (None, None)} == {'Veil 1-1': ('Veil', '1-1')}
"""
from __future__ import annotations

import astrodeck.hub as hub_module
import pytest

from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.report import (FrameRecord, SessionReport,
                                       SessionReporter, TargetBreakdown)


@pytest.fixture(autouse=True)
def isolate_reports(tmp_path, monkeypatch):
    """Reports land in tmp, never the real captures/."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    return tmp_path


def _light(ts, target, *, mosaic=None, panel=None, filt="Ha", accepted=True):
    return FrameRecord(ts=ts, target=target, filter=filt, frame_type="Light",
                       exposure_s=300.0, accepted=accepted, hfr=1.9,
                       mosaic=mosaic, panel=panel)


def _dark(ts, target):
    """A dark a hold shoots on a panel: the engine labels no dark."""
    return FrameRecord(ts=ts, target=target, filter="Ha", frame_type="Dark",
                       exposure_s=300.0)


def _rows(report: SessionReport) -> dict[str, tuple[str | None, str | None]]:
    return {t.name: (t.mosaic, t.panel) for t in report.targets}


# ------------------------------------------------------------ the new fields

def test_the_labels_default_to_none_on_a_report_written_before_them():
    """An old persisted report (no mosaic/panel keys anywhere) loads with
    None, so every existing report on disk keeps loading."""
    old = SessionReport(**{
        "id": "legacy", "plan_name": "old",
        "targets": [{"name": "M31", "frames": 3, "rejected": 0,
                     "integration_s": 900.0, "by_filter": []}],
        "frames": [{"ts": 1.0, "target": "M31", "filter": "Ha",
                    "frame_type": "Light", "exposure_s": 300.0}]})
    assert old.frames[0].mosaic is None and old.frames[0].panel is None
    assert old.targets[0].mosaic is None and old.targets[0].panel is None
    assert TargetBreakdown(name="x").model_dump()["mosaic"] is None
    assert TargetBreakdown(name="x").model_dump()["panel"] is None


def test_a_labelled_frame_labels_its_target_row_and_persists():
    r = SessionReporter(SequencePlan(name="Veil"))
    r.record_frame(_light(1.0, "Veil 1-1", mosaic="Veil", panel="1-1"))
    r.record_frame(_light(2.0, "Veil 1-2", mosaic="Veil", panel="1-2"))
    r.record_frame(_light(3.0, "M31"))
    r.finalize("complete")

    loaded = SessionReporter.load(r.id)
    assert _rows(loaded) == {"Veil 1-1": ("Veil", "1-1"),
                             "Veil 1-2": ("Veil", "1-2"),
                             "M31": (None, None)}, (
        "a panel's row carries its mosaic and panel, a non-member's row "
        "carries None, and both survive the write and the read")
    # The frame detail carries them too: the bundle groups from frames.
    by_target = {f.target: (f.mosaic, f.panel) for f in loaded.frames}
    assert by_target["Veil 1-2"] == ("Veil", "1-2")
    assert by_target["M31"] == (None, None)


def test_a_rejected_labelled_frame_still_labels_the_row():
    """A panel whose only frame so far was rejected is still a panel of its
    mosaic: the row is labelled from the frame, accepted or not."""
    r = SessionReporter(SequencePlan(name="Veil"))
    r.record_frame(_light(1.0, "Veil 2-1", mosaic="Veil", panel="2-1",
                          accepted=False))
    report = r.finalize("complete")
    assert _rows(report) == {"Veil 2-1": ("Veil", "2-1")}
    assert report.targets[0].frames == 0 and report.targets[0].rejected == 1


def test_a_dark_before_the_first_light_does_not_leave_its_panel_unlabelled():
    """A hold can shoot a dark on a panel before that panel's first light.
    The dark carries no label, and the light that follows must still label
    the row it joins."""
    r = SessionReporter(SequencePlan(name="Veil"))
    r.record_frame(_dark(1.0, "Veil 1-1"))
    r.record_frame(_light(2.0, "Veil 1-1", mosaic="Veil", panel="1-1"))
    assert _rows(r.finalize("complete")) == {"Veil 1-1": ("Veil", "1-1")}


def test_a_dark_after_the_light_does_not_unlabel_its_panel():
    r = SessionReporter(SequencePlan(name="Veil"))
    r.record_frame(_light(1.0, "Veil 1-1", mosaic="Veil", panel="1-1"))
    r.record_frame(_dark(2.0, "Veil 1-1"))
    assert _rows(r.finalize("complete")) == {"Veil 1-1": ("Veil", "1-1")}


# ---------------------------------------------------------------- the resume

def test_a_crash_resume_keeps_every_panels_label():
    """``_Totals.from_report`` rebuilds the running totals from the persisted
    header, and a resumed night then writes the report again from them. A
    panel that gets no frame on the second pass must keep the label it was
    written with, or the second write silently ungroups the mosaic."""
    first = SessionReporter(SequencePlan(name="Veil"))
    first.record_frame(_light(1.0, "Veil 1-1", mosaic="Veil", panel="1-1"))
    first.record_frame(_light(2.0, "Veil 1-2", mosaic="Veil", panel="1-2"))
    first.finalize("incomplete")

    resumed = SessionReporter.attach_existing(first.id)
    assert resumed is not None
    # The second pass shoots an unrelated target only.
    resumed.record_frame(_light(3.0, "M31"))
    report = resumed.finalize("complete")

    got = _rows(SessionReporter.load(first.id))
    assert got["M31"] == (None, None)
    lost = [n for n in ("Veil 1-1", "Veil 1-2") if got[n] == (None, None)]
    assert not lost, (
        f"{lost} lost their labels over a resume: "
        f"{[got[n] for n in lost]}")
    assert got["Veil 1-2"] == ("Veil", "1-2")
    assert _rows(report) == got, "the returned report and the file agree"


def test_a_resumed_panel_that_shoots_again_keeps_its_label_and_counts():
    first = SessionReporter(SequencePlan(name="Veil"))
    first.record_frame(_light(1.0, "Veil 1-1", mosaic="Veil", panel="1-1"))
    first.finalize("incomplete")
    resumed = SessionReporter.attach_existing(first.id)
    resumed.record_frame(_light(2.0, "Veil 1-1", mosaic="Veil", panel="1-1"))
    report = resumed.finalize("complete")
    (row,) = report.targets
    assert (row.mosaic, row.panel) == ("Veil", "1-1")
    assert row.frames == 2 and row.integration_s == pytest.approx(600.0)
