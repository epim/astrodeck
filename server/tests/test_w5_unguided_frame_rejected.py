# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-37 (b) / #142, D-06 (backlog ruling, owner-approved 2026-09-30, "Yes to
both"): a frame with no RMS because guiding had stopped is rejected when a
ceiling (``max_guide_rms``) is set.

Before the fix, ``_check_quality``'s guide-RMS gate read ``self._guide_rms()``
and compared it against the ceiling -- but a guider that has stopped reports
a flat 0.00 RMS (not an unreadable value), which sails under any positive
ceiling and reads as a perfectly guided frame. The mosaic design review
(docs/superpowers/specs/2026-09-23-flows-mosaic-target-block-design.md) found
this banking trailed frames on a star-poor panel whose guide start kept
failing. Single targets carry the exact same defect for any frame shot after
the guider stops mid-visit (owner's comment on #142).

D-06's second half -- "the all-panels-failed pass goes to guiding_action" --
was already shipped (``GroupRun.close_pass``'s ``"guiding_action"`` boundary,
``group_rules.py``, covered by test_group_no_guider_reachable_members.py and
siblings); nothing there needed to change for this ruling.

The fix: the engine's own real per-frame read of whether THIS frame was
actually guided (``_frame_was_guided``, already taken for ``_record_frame``'s
#72 recovery bound) is cached onto ``self._frame_guided`` and read back by
``_check_quality``. ``_frame_was_guided`` is patched directly here rather
than orchestrated through a real guide-start/stop sequence, to test the gate
in isolation from guide-start timing; its own contract (unguided only when
guiding was this plan's business and ``is_active()`` said no) is
``test_sky_stands_in_for_a_missing_monitor.py``'s neighbourhood, not this
file's.

Named mutant: ``if self._frame_guided is False:`` in ``_check_quality``
deleted (falling straight to the old numeric comparison) -- RED, observed
verbatim:

    AssertionError: an unguided frame passed the RMS ceiling: rejected=0
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target


def _plan(**plan_kw) -> SequencePlan:
    step = ExposureStep(filter="L", exposure_s=0.05, count=1)
    return SequencePlan(name="p", targets=[
        Target(name="T", ra_hours=5.5881, dec_deg=-5.3911, center=False,
              autofocus_first=False, steps=[step])],
        **plan_kw)


@pytest.mark.asyncio
async def test_unguided_frame_rejected_when_ceiling_set(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    hub = Hub()
    await hub.connect_sim()
    eng = SequenceEngine(hub)

    async def _never_guided() -> bool:
        return False
    monkeypatch.setattr(eng, "_frame_was_guided", _never_guided)

    eng.start(_plan(guide=True, max_guide_rms=1.0, dither_every=0,
                    autofocus_every=0, meridian_flip=False))
    await eng._task

    assert eng._rejected == 1, (
        f"an unguided frame passed the RMS ceiling: rejected={eng._rejected}")


@pytest.mark.asyncio
async def test_a_guided_frame_is_not_rejected_by_the_same_ceiling(
        tmp_path, monkeypatch):
    """CONTROL: the same ceiling, but the frame WAS guided -- accepted."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    hub = Hub()
    await hub.connect_sim()
    eng = SequenceEngine(hub)

    async def _was_guided() -> bool:
        return True
    monkeypatch.setattr(eng, "_frame_was_guided", _was_guided)
    monkeypatch.setattr(eng, "_guide_rms", lambda: 0.5)   # well under the ceiling

    eng.start(_plan(guide=True, max_guide_rms=1.0, dither_every=0,
                    autofocus_every=0, meridian_flip=False))
    await eng._task

    assert eng._rejected == 0, (
        f"a guided frame under the ceiling was rejected: {eng._rejected}")


@pytest.mark.asyncio
async def test_an_unguided_frame_passes_when_no_ceiling_is_set(
        tmp_path, monkeypatch):
    """CONTROL: ``max_guide_rms`` 0 (off, set explicitly) -- the new rule is
    inert, same as every other gate here."""
    # DELIBERATE PIN CHANGE (#854): docstring only; 0 is no longer the default.
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    hub = Hub()
    await hub.connect_sim()
    eng = SequenceEngine(hub)

    async def _never_guided() -> bool:
        return False
    monkeypatch.setattr(eng, "_frame_was_guided", _never_guided)

    eng.start(_plan(guide=True, max_guide_rms=0.0, dither_every=0,
                    autofocus_every=0, meridian_flip=False))
    await eng._task

    assert eng._rejected == 0, (
        f"an unguided frame was rejected with no ceiling set: {eng._rejected}")


def test_a_synthetic_info_dict_is_not_gated_by_a_stale_flag():
    """A direct ``_check_quality(info)`` call outside the real frame loop
    (every test in test_session_quota.py / test_sequence_engine_fixes.py)
    must see the gate exactly as before: ``_frame_guided`` starts at None on
    a fresh engine and the old numeric comparison alone applies."""
    from astrodeck.hub import Hub
    eng = SequenceEngine(Hub())
    eng.plan = SequencePlan(name="p", min_stars=0, max_guide_rms=1.5)
    assert eng._frame_guided is None, "premise: a fresh engine has not graded a frame"
    eng._guide_rms = lambda: None   # type: ignore[assignment]
    eng._guiding_now = lambda: False   # type: ignore[assignment]
    assert eng._check_quality({"hfr": 2.0}) is True, (
        "an unreadable RMS with no real-frame signal must stay ungated")
