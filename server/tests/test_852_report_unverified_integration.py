# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Lights shot at a pointing nobody verified are kept, and not booked as
integration (#852, ruling R4).

On 2026-10-07 the run report claimed "3960 s integration" when six frames
(600 s) were real on-target subs and 28 accepted frames were of a field 2.8
degrees away. A light shot while the target's pointing was not confirmed by a
solve (the no-light hold ran out, a re-centre raised, an in-place re-check
did not solve) now carries ``pointing_unverified``: it is accepted, on disk
and counted toward quotas, and the report keeps its seconds in
``unverified_integration_s`` instead of ``integration_s``.

Driven through the real ``SessionReporter`` and the real engine methods.
Every mutant named below was applied to a byte copy of the production file,
run under the suite's normal command, and the file restored from the copy
with its sha256 checked.
"""
from __future__ import annotations

import time

import pytest

import astrodeck.flows.tonight as tonight_mod
from astrodeck.sequence.report import (FrameRecord, SessionReport,
                                       SessionReporter, _Totals)

from test_centring_settings_reach_goto import _setup, _target


@pytest.fixture(autouse=True)
def _the_window_is_open(monkeypatch):
    monkeypatch.setattr(tonight_mod, "target_own_window",
                        lambda *a, **kw: None)


def _reporter(monkeypatch) -> SessionReporter:
    """A real reporter whose snapshot writes go nowhere."""
    rep = SessionReporter(None, report_id="r-852")
    monkeypatch.setattr(rep, "_schedule_write", lambda: None)
    return rep


def _light(*, unverified: bool, exposure_s: float = 300.0) -> FrameRecord:
    return FrameRecord(ts=time.time(), target="Fictional H", filter="L",
                       frame_type="Light", exposure_s=exposure_s,
                       accepted=True, pointing_unverified=unverified)


def test_an_unverified_light_is_kept_but_not_integrated(monkeypatch):
    """One verified and one unverified 300 s light: two frames captured,
    300 s integrated (headline, filter and target), 300 s unverified.

    MUTANT "integrate everything" (``and not fr.pointing_unverified`` removed
    from `_Totals.add`): RED -
        AssertionError: assert 600.0 == 300.0
    """
    rep = _reporter(monkeypatch)
    rep.record_frame(_light(unverified=False))
    rep.record_frame(_light(unverified=True))
    out = rep.build()
    assert out.frames_captured == 2
    assert out.integration_s == 300.0
    assert out.unverified_integration_s == 300.0
    assert out.targets[0].integration_s == 300.0
    assert out.targets[0].frames == 2
    assert out.by_filter[0].integration_s == 300.0


def test_the_unverified_total_survives_a_resume(monkeypatch):
    """A crash-resume rebuilds the totals from the persisted header; the
    unverified seconds come back with the rest.

    MUTANT "forgotten on resume" (``t.unverified_s =
    rep.unverified_integration_s`` removed from `_Totals.from_report`): RED -
        AssertionError: assert 300.0 == 600.0
    """
    rep = _reporter(monkeypatch)
    rep.record_frame(_light(unverified=True))
    again = _Totals.from_report(rep.build())
    again.add(_light(unverified=True))
    assert again.unverified_s == 600.0
    assert again.integ == 0.0


def test_old_reports_load():
    """CONTROL. A report written before the fields loads with their
    defaults."""
    old = SessionReport.model_validate({
        "id": "old", "frames": [{"ts": 1.0, "target": "x"}]})
    assert old.unverified_integration_s == 0.0
    assert old.frames[0].pointing_unverified is False


async def test_frames_after_a_failed_re_centre_are_marked(monkeypatch):
    """The engine's own record: after a re-centre that left the pointing
    unverified, the next light says so; after good centring evidence, the
    one after it does not.

    MUTANT "never marked" (`_reporter_record` passing
    ``pointing_unverified=False``): RED -
        AssertionError: assert False is True
    """
    t = _target(name="Fictional H")
    e, hub = _setup(t)
    e.reporter = _reporter(monkeypatch)
    step = t.steps[0]
    e._mark_pointing_unverified(t)
    e._reporter_record(t, step, {}, accepted=True)
    e._note_centring_evidence({"centered": True, "error_arcmin": 0.3}, t)
    e._reporter_record(t, step, {}, accepted=True)
    frames = e.reporter._frames
    assert [f.pointing_unverified for f in frames] == [True, False]
    assert frames[0].pointing_unverified is True


async def test_the_no_light_continuation_marks_its_frames(bus_lines):
    """Setup's no-light hold ran out (every centring found nothing to
    solve) and setup carried on: this target's lights are unverified.

    MUTANT "setup reads no evidence" (the ``self._note_centring_evidence(result,
    target)`` call in `_setup_target` removed): RED -
        AssertionError: assert None == '...'
    """
    t = _target(name="Fictional H")
    e, hub = _setup(t)

    async def goto(*args, **kwargs):
        hub.gotos.append((args, dict(kwargs)))
        return {"centered": False, "error_arcmin": None}
    hub.goto_and_center = goto
    await e._setup_target(0, t)
    assert any("still no light after holding" in m
               for _l, m, _s in bus_lines), "premise: the hold ran out"
    assert e._pointing_unverified_for == t.id
