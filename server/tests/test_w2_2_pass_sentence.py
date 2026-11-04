# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 2.2 fix round 1 (#1093): the centring-hold sentence names what the
solve frames showed.

A pass in which every panel tried failed to centre says "the sky or the
geometry is to blame, not a panel" (``GroupRun.close_pass``). When the engine
read every failed panel's frame through ``imaging.frame_class.classify`` and
they all agree, ``close_pass(frame_cause=...)`` puts that cause in the
sentence in place of the generic words; the generic words stay when nothing is
known, and the sentence stays in fixed words (no figures beyond the pass and
panel counts it always carried, #618).

The engine's side (``SequenceEngine._pass_blame``) gives a cause only when
EVERY panel the pass tried was named and they all read the same class: a
sentence about the pass must not claim more than the frames showed.

MUTANT RUN, 2026-11-04, from a byte backup of ``astrodeck/sequence/
group_rules.py``, restored and sha256-verified: ``frame_cause`` ignored
(the sentence always built from the generic words). The first case below
fails with

    AssertionError: assert 'an obstruction in the foreground' in 'centring
    failed on every one of the 6 panels tried in pass 1: the sky or the
    geometry is to blame, not a panel, ...'
"""
from __future__ import annotations

import re

from astrodeck.imaging.frame_class import FrameClass
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.group_rules import CENTRING, GroupRun, PanelDeferred

OBSTRUCTION = "an obstruction in the foreground"


def _run(n: int = 6) -> GroupRun:
    labels = ["1-1", "1-2", "1-3", "2-3", "2-2", "2-1"][:n]
    return GroupRun({f"p{i}": lab for i, lab in enumerate(labels)},
                    max_failed_visits=3)


def _every_panel_misses(run: GroupRun) -> None:
    for p in run.members:
        run.visit_outcome(
            p, complete=False, exposures=0, accepted=0,
            deferred=PanelDeferred("centring failed", kind=CENTRING,
                                   last_error="plate solve failed"))


def test_the_sentence_takes_the_cause_the_frames_showed():
    run = _run()
    _every_panel_misses(run)

    end = run.close_pass(frame_cause=OBSTRUCTION)

    assert end.boundary == "centring_hold"
    assert OBSTRUCTION in end.reason, end.reason
    assert "the sky or the geometry" not in end.reason
    assert "no panel's failure count moved" in end.reason


def test_the_generic_words_stay_when_nothing_is_known():
    run = _run()
    _every_panel_misses(run)

    end = run.close_pass()

    assert "the sky or the geometry is to blame" in end.reason, end.reason


def test_the_cause_adds_no_figure_to_the_sentence():
    """Fixed words: the digits in the sentence are the pass and the panel
    count it always carried, and the hold's minutes, nothing else."""
    run = _run()
    _every_panel_misses(run)
    end = run.close_pass(frame_cause=OBSTRUCTION)

    digits = re.findall(r"\d+", end.reason)
    assert digits == ["6", "1", "10"], end.reason


# ------------------------------------------------------- the engine's side

def test_a_cause_needs_every_tried_panel_named_the_same():
    blame = SequenceEngine._pass_blame
    obs = FrameClass.OBSTRUCTED

    assert blame([obs] * 4, 4) == OBSTRUCTION
    assert blame([FrameClass.CLOUD] * 3, 3) == "cloud"
    assert blame([FrameClass.NO_LIGHT] * 2, 2) == "no light reaching the sensor"
    # Some panels' frames could not be named: nothing is claimed.
    assert blame([obs] * 3, 4) is None
    # The frames disagree: nothing is claimed.
    assert blame([obs, obs, FrameClass.CLOUD, obs], 4) is None
    assert blame([], 4) is None
