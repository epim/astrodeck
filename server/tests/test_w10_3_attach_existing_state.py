# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1148: a re-attached report is built by ``__new__``, which skips ``__init__``,
so every private field ``__init__`` sets has to be set again. ``_pending`` was
not: the first record on a re-attached report, made inside a running loop,
raised AttributeError in ``_schedule_write``.

MUTANT "drop the _pending assignment in attach_existing" -- the first record
on a re-attached report raises AttributeError.
"""
from __future__ import annotations

import astrodeck.hub as hub_module
import pytest

from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.report import FrameRecord, SessionReporter


@pytest.fixture(autouse=True)
def isolate_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    return tmp_path


def _frame(ts):
    return FrameRecord(ts=ts, target="M31", filter="Ha", frame_type="Light",
                       exposure_s=300.0, accepted=True)


def _attached(report_id):
    r = SessionReporter(SequencePlan(name="Resume"), report_id=report_id,
                        started_at=1.0)
    r.record_frame(_frame(1.0))
    r.finalize("aborted")
    again = SessionReporter.attach_existing(report_id)
    assert again is not None
    return again


def test_attach_existing_sets_every_field_init_sets():
    fresh = SessionReporter(SequencePlan(name="Resume"), report_id="fields-a",
                            started_at=1.0)
    fresh.finalize("aborted")
    again = SessionReporter.attach_existing("fields-a")
    missing = sorted(set(vars(fresh)) - set(vars(again)))
    assert not missing, f"attach_existing leaves __init__ fields unset: {missing}"


async def test_first_record_on_a_reattached_report_in_a_loop_does_not_raise():
    r = _attached("loop-a")
    r.record_frame(_frame(2.0))
    await r.flush()
    rep = r.finalize("complete")
    assert rep.frames_captured == 2
    assert not r._pending
