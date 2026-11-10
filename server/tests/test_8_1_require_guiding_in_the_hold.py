# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1146 part 3: the hold for unfit guiding answers to ``require_guiding``.

At the end of its hold the over-ceiling guard (#1096) took the retry spells or,
for a mosaic panel, the group's deferral, and never read
``escalation.require_guiding``, so a plan that asks to abort or skip when guiding
is not good got a different answer than at the stopped-guider guard
(`_maybe_recover_guiding`). It now takes the operator's chosen answer after the
panel's deferral and before a spell, as that guard does, for a guider over its
ceiling and for one that has sent no sample (#1146 part 2), and names the
condition that ended the hold: not the words of a lost guider.

Every night runs the real frame loop on the clocked simulator
(tests/_group_harness.py).
"""
from __future__ import annotations

import pytest

from astrodeck.config import EscalationConfig
from astrodeck.sequence import SequencePlan
from astrodeck.sequence.engine import (
    GUIDE_OVER_REQUIRED, GUIDE_OVER_STOP, GUIDE_SILENT_REQUIRED,
    GUIDE_SILENT_STOP, MAX_GUIDE_RETRIES_PER_TARGET_NIGHT)

from _group_harness import (  # noqa: F401
    Night, grid_plan, group_hub, group_store, single)
from _w1_2_guider import SampledGuider
from _w8_1_guider import StampedGuider

CEILING = 5.0
SPELL_WORDS = "is checked again in 10 min"


def _plan(**over) -> SequencePlan:
    kw = dict(name="w81req", guide=True, recover_guiding=True,
              max_guide_rms=CEILING, dither_every=0, autofocus_every=0,
              meridian_flip=False, park_when_done=False,
              warm_cooler_when_done=False, count_mode="accepted")
    kw.update(over)
    return SequencePlan(targets=[single("Alpha", count=2)], **kw)


def _over():
    return SampledGuider(lambda t: 7.0), GUIDE_OVER_REQUIRED


def _silent():
    return (StampedGuider(lambda t: 0.5, lambda now: []),
            GUIDE_SILENT_REQUIRED)


async def _run(hub, monkeypatch, plan, guider) -> Night:
    hub.guider = guider
    night = Night(hub, monkeypatch)
    guider.bind(night)
    try:
        night.done = await night.run(plan, wall_s=120.0)
    finally:
        await night.close()
    assert night.done, night.trace[-3:]
    return night


def _spells(night: Night) -> list[str]:
    return [m for _t, _l, m in night.lines if SPELL_WORDS in m]


@pytest.mark.parametrize("make", [_over, _silent], ids=["over", "silent"])
async def test_a_required_guider_aborts_at_the_end_of_the_hold(
        group_hub, group_store, monkeypatch, make):
    """MUTANT (the hold ignores ``require_guiding``): RED - the target is held
    through six spells instead and the end reason is not "unsafe"."""
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action="abort"))
    guider, why = make()
    night = await _run(group_hub, monkeypatch, _plan(), guider)
    assert night.engine.state.get("end_reason") == "unsafe", night.engine.state
    assert night.engine.state.get("detail") == why
    assert not _spells(night), "held for a spell before the answer"
    assert night.captures == [], night.shots()


@pytest.mark.parametrize("make", [_over, _silent], ids=["over", "silent"])
async def test_a_required_guider_skips_the_target_at_the_end_of_the_hold(
        group_hub, group_store, monkeypatch, make):
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action="skip"))
    guider, why = make()
    night = await _run(group_hub, monkeypatch, _plan(), guider)
    assert night.said(f"{why}; skipping Alpha"), [
        m for _t, _l, m in night.lines][-4:]
    assert not _spells(night)
    assert night.engine.state.get("end_reason") != "unsafe"


@pytest.mark.parametrize("make", [_over, _silent], ids=["over", "silent"])
@pytest.mark.parametrize("escalation", [
    EscalationConfig(require_guiding=True, guiding_action="warn"),
    EscalationConfig(require_guiding=False, guiding_action="abort"),
], ids=["require-warn", "not-required"])
async def test_anything_else_takes_the_retry_spells_as_before(
        group_hub, group_store, monkeypatch, make, escalation):
    group_store.set_escalation(escalation)
    guider, _why = make()
    night = await _run(group_hub, monkeypatch, _plan(), guider)
    assert len(_spells(night)) == MAX_GUIDE_RETRIES_PER_TARGET_NIGHT
    assert night.engine.state.get("end_reason") != "unsafe"


@pytest.mark.parametrize("make, stop", [(_over, GUIDE_OVER_STOP),
                                        (_silent, GUIDE_SILENT_STOP)],
                         ids=["over", "silent"])
async def test_a_mosaic_panel_defers_whatever_the_action_says(
        group_hub, group_store, monkeypatch, make, stop):
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action="abort"))
    plan = grid_plan(rows=1, cols=2,
                     panel_kw={"filters": ("L",), "count": 2, "per_visit": 2},
                     guide=True, recover_guiding=True, count_mode="accepted",
                     max_guide_rms=CEILING)
    guider, _why = make()
    night = await _run(group_hub, monkeypatch, plan, guider)
    assert night.engine.state.get("end_reason") != "unsafe", night.engine.state
    assert [m for _t, _l, m in night.lines
            if "the visit stopped on 1-1" in m
            and "retried on the next pass" in m]
    assert not _spells(night)
