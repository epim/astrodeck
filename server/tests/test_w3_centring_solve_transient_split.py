# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""`_group_hop_checks` reads the split centring-transient key, falling back
to the union (#576's second part, WP-21 of the 2026-09-30 open-issue
backlog; D-03's own text: "WP-22 in this same wave splits goto_and_center's
solve_transient into centring_solve_transient and rotation_solve_transient
(keeping solve_transient as the union); read centring_solve_transient,
falling back to solve_transient when it is absent").

THE DEFECT (#576, "a second part"). The hub used to set one
``solve_transient`` key for the whole hop, beside whichever failure the held
solve-frame file cost: a centring solve's (``centered`` false) or the
rotate loop's (``rotation_skipped``). Either way the key read transient, so
a rotate solve that could not run, beside a centring miss that failed for
the SKY's own reasons, made the centring miss read as transient too --
hiding a real centring miss from the strike count and from the centring
pass rule (`GroupRun.close_pass`), which #563/D-03's held-pass escalation
now depends on counting correctly.

THE FIX (WP-22, out of scope here: server/astrodeck/hub.py). The hub splits
the one key into ``centring_solve_transient`` (set only when the CENTRING
solve itself could not run) and ``rotation_solve_transient`` (the rotate
loop's own), keeping ``solve_transient`` as their union for compatibility.
WP-21's own piece, pinned here, is the read side: `_group_hop_checks` reads
``centring_solve_transient``, falling back to the union ``solve_transient``
only when the split key is absent (so this engine builds and passes
whether or not the worktree that adds the split key has merged yet).

THESE ARE PURE CALLS to `SequenceEngine._group_hop_checks` (a staticmethod,
tests/test_h4_transient_solve_is_no_strike.py's own style for the hop):
no engine, no hub, no clock.

Every mutant was applied to a byte-for-byte backup of
server/astrodeck/sequence/engine.py inside this worktree, restored and
sha256-checked after each (never left in the tree), and the exact failing
assertion is quoted in the test it turns red, and in this work package's
own report.
"""
from __future__ import annotations

import pytest

from _group_harness import GROUP_ID, GROUP_NAME, panel  # noqa: F401
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.group_rules import CENTRING, SOLVE_TRANSIENT, PanelDeferred
from astrodeck.sequence.models import TargetGroup

#: A centring result naming no reason beyond "it failed": `_group_hop_checks`
#: takes ``miss`` as a separate argument, as the engine's own setup does.
MISS = "plate solve failed — used raw GoTo"


def _group(*, rotate: bool = False) -> TargetGroup:
    kw = {"rotate": True, "pa_deg": 30.0} if rotate else {}
    return TargetGroup(id=GROUP_ID, name=GROUP_NAME, **kw)


# -------------------------------------------------- the split key is read

def test_the_split_key_alone_is_enough():
    """``centring_solve_transient: True`` with no ``solve_transient`` key at
    all (a hub that has taken WP-22's split and sets only the new key) still
    defers the miss as SOLVE_TRANSIENT, not CENTRING.

    RED under mutant "split key not read" (``centring_transient`` computed
    as ``transient`` alone, dropping the ``centring_solve_transient`` read),
    observed:

        AssertionError: assert 'centring' == 'solve_transient'
    """
    result = {"centered": False, "centring_solve_transient": True}
    with pytest.raises(PanelDeferred) as caught:
        SequenceEngine._group_hop_checks(panel(0, 0), _group(), result, MISS)
    assert caught.value.kind == SOLVE_TRANSIENT


def test_falls_back_to_the_union_key_when_the_split_key_is_absent():
    """CONTROL (the pre-WP-22 shape, and what this engine must still accept
    from a hub that has not merged the split yet): no
    ``centring_solve_transient`` key at all, only the old union
    ``solve_transient``. The fallback reads it, exactly as before WP-22.

    RED under mutant "no fallback" (the ``.get``'s default changed from
    ``transient`` to ``False``, so an absent split key is read as "not
    transient" instead of falling back to the union), observed:

        AssertionError: assert 'centring' == 'solve_transient'
    """
    result = {"centered": False, "solve_transient": True}
    with pytest.raises(PanelDeferred) as caught:
        SequenceEngine._group_hop_checks(panel(0, 0), _group(), result, MISS)
    assert caught.value.kind == SOLVE_TRANSIENT


def test_without_either_key_a_miss_is_the_panels():
    """CONTROL: neither key present (no transient anywhere) is a plain
    CENTRING miss, as it always was."""
    result = {"centered": False}
    with pytest.raises(PanelDeferred) as caught:
        SequenceEngine._group_hop_checks(panel(0, 0), _group(), result, MISS)
    assert caught.value.kind == CENTRING


# ------------------------------- the point of the split: a rotate-only
# ------------------------------- transient must not hide a real centring miss

def test_a_rotation_only_transient_does_not_hide_a_real_centring_miss():
    """THE DEFECT ITSELF (#576's second part). The rotate loop's own solve
    could not run (so the union ``solve_transient`` is True, the pre-split
    shape), but the CENTRING solve ran and genuinely missed
    (``centring_solve_transient: False``, WP-22's split says so). The miss
    is the panel's or the sky's, same as any other centring miss: it must
    count toward the centring pass rule and `failed`, not vanish into a
    deferral nothing charges.

    RED under mutant "split key not read" (the same one as above:
    ``centring_transient`` falls back to the union ``transient`` alone, so
    this rotate-caused transient still masks the centring miss), observed:

        AssertionError: assert 'solve_transient' == 'centring'
    """
    result = {"centered": False, "centring_solve_transient": False,
              "rotation_solve_transient": True, "solve_transient": True}
    with pytest.raises(PanelDeferred) as caught:
        SequenceEngine._group_hop_checks(panel(0, 0), _group(), result, MISS)
    assert caught.value.kind == CENTRING


def test_the_rotation_check_is_unchanged_by_the_split():
    """CONTROL, out of WP-21's scope by the plan's own text (D-03: WP-21
    reads ``centring_solve_transient`` for the centring check; the rotate
    check is untouched). A rotator skip beside the UNION key still defers as
    SOLVE_TRANSIENT, exactly as before the split existed: WP-21 does not
    read ``rotation_solve_transient`` anywhere, so a hub that sets only the
    split keys (no union) is WP-22's own concern, not this test's."""
    result = {"centered": True, "rotation_skipped": True,
              "solve_transient": True}
    with pytest.raises(PanelDeferred) as caught:
        SequenceEngine._group_hop_checks(panel(0, 0, rotation_deg=30.0),
                                         _group(rotate=True), result, None)
    assert caught.value.kind == SOLVE_TRANSIENT
