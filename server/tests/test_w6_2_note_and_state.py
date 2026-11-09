# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 6.2 (#180 part B, owner ruling D-E): what a skipped panel says.

* ``state.group.skipped`` (the engine's `_group_state`): the panels a
  rotating run is skipping, with the streak's kind word and the consecutive
  failed visits. Always sent, empty when none is.
* the progress answer's ``rotating`` key on a mosaic panel
  (``progress._rotating``, ``Session.standing_skip``): present only while the
  panel's newest skip has no frame banked after it.
* the Tonight note's sentence (``tonight._panels_clause``): which panel is
  skipped, why in the kind's fixed words, and when it is next due ("in the
  next round, after the panels that still need frames", no clock time).

Each test names its mutant. THE SITE IS A FIXTURE and no clock is read: the
skip records carry fixed times and nights.
"""
from __future__ import annotations

import json

import pytest

from _group_harness import (T0, Night, grid_plan, group_hub,  # noqa: F401
                            group_store)
from astrodeck.flows import progress as progress_mod
from astrodeck.flows.tonight import _campaign
from astrodeck.sequence.session import SessionFrame, session_store
from test_w17_starved_panel import (_answer, _block, _cell, _key,
                                    _mosaic_only, _rid, _world)


# ------------------------------------------------------------ the progress key

def _skipped_world(kind="centring", visits=4):
    graph, compiled, plan, s = _world(days=(16,))
    p = _cell(plan, 0, 1)
    s.note_panel_skipped(p.id, night=_key(16), kind=kind, visits=visits,
                         ts=1_790_000_016.0)
    return graph, compiled, plan, s, p


def test_a_panel_with_a_standing_skip_carries_rotating():
    """MUTANT "progress never copies the skip" (``rotating = None`` in
    ``_mosaic``). Observed:
        AssertionError: Differing items:
        {'M16 1-2': None} != {'M16 1-2': {'kind': 'centring', 'visits': 4}}
    """
    graph, compiled, plan, s, p = _skipped_world()
    block = _block(_answer(compiled, plan, s))
    got = {e["name"]: e.get("rotating") for e in block["panels"]}
    assert got == {"M16 1-1": None,
                   "M16 1-2": {"visits": 4, "kind": "centring"},
                   "M16 2-1": None, "M16 2-2": None}


def test_a_frame_banked_after_the_skip_ends_it():
    """A panel that banks a frame after its skip is no longer rotating. The
    mutant "standing_skip ignores frames" is shown red on the session's own
    test (test_w6_2_rotation_rules.py) and on the clocked night
    (test_w6_2_failed_panel_rotates.py).
    """
    graph, compiled, plan, s, p = _skipped_world()
    s.frames.append(SessionFrame(night=_rid(16), target_id=p.id,
                                 step_id=p.steps[0].id, ts=1_790_000_099.0))
    block = _block(_answer(compiled, plan, s))
    assert all("rotating" not in e for e in block["panels"])


def test_the_answer_without_a_skip_is_what_it_was():
    """No skip record, no key: every answer without a skip is the answer
    before.
    """
    graph, compiled, plan, s = _world(days=(16,))
    assert all("rotating" not in e for b in _answer(compiled, plan, s)["blocks"]
               for e in b["panels"])


def test_the_answer_carries_a_word_and_a_count_and_no_time():
    """Served to a viewer: the kind is one of a closed set, the count a whole
    number, and nothing of the record's time is in the answer.
    """
    graph, compiled, plan, s, p = _skipped_world()
    dumped = json.dumps(_answer(compiled, plan, s))
    assert "1790000016" not in dumped
    assert progress_mod._rotating(None, p) is None
    assert progress_mod._rotating(s, None) is None


def test_a_skip_kind_outside_the_closed_set_is_not_served():
    """A hand-edited record cannot put free text in a viewer's answer.

    MUTANT "kind not checked" (``False or isinstance(visits, bool)``).
    Observed:
        AssertionError: assert {'kind': 'RSN-7731 solve failed', 'visits': 4}
        is None
    """
    graph, compiled, plan, s, p = _skipped_world(kind="RSN-7731 solve failed")
    assert progress_mod._rotating(s, p) is None


# ----------------------------------------------------------------- the note

def _rows(rotating):
    return {"blocks": [{"node_id": "m", "panels": [
        {"name": "M16 1-1", "row": 0, "col": 0, "banked": 0, "owed": 4,
         "total": 4},
        {"name": "M16 1-2", "row": 0, "col": 1, "banked": 0, "owed": 4,
         "total": 4, **({"rotating": rotating} if rotating else {})}],
        "skipped": []}]}


@pytest.mark.parametrize("kind, why", [
    ("centring", " (no star to solve on)"),
    ("guide_start", " (no guide star)"),
    ("rejects", " (its frames were rejected)"),
    ("deferred", ""),
])
def test_the_note_names_the_skipped_panel_and_when_it_is_next_due(kind, why):
    """One sentence per skipped panel: which panel, how many visits failed,
    the kind's fixed words (none for a mixed streak: it may be pier side or
    autofocus), and that it is next due in the next round, after the panels
    that still need frames. No clock time, no 'set aside', no promise word.

    MUTANT "no sentence" (``_panels_clause`` appends nothing for a rotating
    row), and again "row without rotating" (``_panel_rows`` does not copy it).
    Observed, both:
        AssertionError: 0 of 2 mosaic panels done, 0 of 8 subs captured. ...
        assert 'Panel M16 1-2 is being skipped: its last 4 visits failed (no
        star to solve on), so each round it goes after ...' in "0 ...
    """
    prog = _rows({"visits": 4, "kind": kind})
    note = _campaign(_mosaic_only(), None, lambda: prog)["note"]
    assert (f"Panel M16 1-2 is being skipped: its last 4 visits failed{why}, "
            f"so each round it goes after the panels that still need frames, "
            f"and it is tried again in the next round until it has its "
            f"frames.") in note, note
    for word in ("set aside", "tomorrow", "later night", "next night"):
        assert word not in note, (word, note)


def test_one_failed_visit_is_said_in_the_singular():
    """One visit is "its last visit failed", not "its last 1 visits"."""
    prog = _rows({"visits": 1, "kind": "centring"})
    note = _campaign(_mosaic_only(), None, lambda: prog)["note"]
    assert "its last visit failed (no star to solve on)" in note, note


def test_a_row_the_server_never_sends_says_nothing():
    """The progress answer is injected: a kind outside the closed set, or a
    count that is no whole number, reaches no sentence.

    MUTANT "_rotating_of takes any entry" (the closed-set test made
    ``False``). Observed:
        KeyError: 'RSN-7731'
    """
    for bad in ({"visits": 4, "kind": "RSN-7731"},
                {"visits": 0, "kind": "centring"},
                {"visits": True, "kind": "centring"},
                {"visits": "4", "kind": "centring"}):
        prog = _rows(bad)
        note = _campaign(_mosaic_only(), None, lambda: prog)["note"]
        assert "is being skipped" not in note, (bad, note)


def test_no_skip_means_the_note_is_the_note_it_was():
    """With no rotating row the note carries no such sentence."""
    note = _campaign(_mosaic_only(), None, lambda: _rows(None))["note"]
    assert "is being skipped" not in note


# ------------------------------------------------------- the published state

def _plan():
    return grid_plan(panel_kw={"count": 8})


def _never_centres(who_label):
    def goto(who, n, result):
        if who.endswith(who_label):
            return {**result, "centered": False, "error_arcmin": None}
        return result
    return goto


async def _night(hub, monkeypatch, plan, **kw) -> Night:
    night = Night(hub, monkeypatch, **kw)
    try:
        night.done = await night.run(plan)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


async def test_state_group_publishes_the_skipped_panels(group_hub,
                                                        monkeypatch):
    """The group's published state names the skipped panel with its kind and
    visit count from its third failed visit on, and nothing before it; every
    published group carries the key.

    MUTANT "skipped never published" (the list comprehension filtered to
    ``if False``). Observed:
        AssertionError: the skipped panel was never published
        assert []
    """
    night = await _night(group_hub, monkeypatch, _plan(),
                         goto=_never_centres("2-2"))
    groups = [e[2]["group"] for e in night.trace
              if e[1] == "state" and e[2].get("group")]
    assert groups and all("skipped" in g for g in groups)
    seen = [g["skipped"] for g in groups if g["skipped"]]
    assert seen, "the skipped panel was never published"
    assert all(len(sk) == 1 and sk[0]["panel"] == "2-2"
               and sk[0]["kind"] == "centring" for sk in seen), seen
    assert [sk[0]["visits"] for sk in seen][0] == 3
    first = next(i for i, g in enumerate(groups) if g["skipped"])
    assert all(not g["skipped"] for g in groups[:first])
    assert all(g["set_aside"] == [] or g["set_aside"][0]["kind"] == "deferred"
               for g in groups)


async def test_a_clean_night_publishes_an_empty_list(group_hub, monkeypatch):
    """MUTANT "skipped renamed in `_group_state`". Observed:
        KeyError: 'skipped'
    """
    night = await _night(group_hub, monkeypatch, grid_plan())
    groups = [e[2]["group"] for e in night.trace
              if e[1] == "state" and e[2].get("group")]
    assert groups and all(g["skipped"] == [] for g in groups)
