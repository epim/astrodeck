# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The save answer's ``reanchored`` rows, recorded where the UI can read them
(#352, S4 orchestrator ruling 6; #353 item 7's shape).

THE DEFECT THIS CLOSES. ``flowsSaveAnswer.test.ts`` graded the editor's
words for a re-anchor ("its rows or columns changed", "its angle changed
between any angle and a set one", "it now names a different object") against
rows it WROTE ITSELF, with a ``reason`` the server never sent: the save's
rows were ``{node_id, max_move_deg, threshold_deg}``, so in production every
grid, angle and object change read "its framing changed". A test double that
answers what the real collaborator never does.

THE FIXTURE IS THE CONTRACT. ``fixtures/save_answer_rows.json`` holds
``prepare_save``'s rows for one save of each reason, recorded by running this
file as a script and never edited by hand. This test grades the file against
``prepare_save`` EXACTLY, and the UI test reads the same file (as
``panelLane.test.ts`` reads ``panel_lane_cases.json``) and stubs the save
with its rows, so a row shape that drifts on either side goes red here or
there, never in neither.

EVERY NUMBER IN THE ROWS IS EXACT ON ANY PLATFORM, because the grade is
exact and CI runs this on two C libraries. A threshold is products of the
anchor's decimal field and overlap by powers of two. A move is measured with
trigonometry, and the law-of-cosines separation magnifies a last-place
difference in ``sin`` or ``cos`` a million times for a move of arcminutes, so
the ``move`` case is one whose every step is exact: a single panel with no
camera field (every corner is its centre, ``deproject``'s ``rho == 0`` path,
no trigonometry) moved a quarter turn along the equator, so the separation is
``acos(cos(pi/2))``, which rounds to ``pi/2`` on any library within an ulp,
and the row says 90.0 deg against a threshold of 0.0.

To record the fixture again after a DELIBERATE change to the row shape:

    cd server && .venv/Scripts/python.exe tests/test_flows_save_answer_fixture.py

Every named mutation was run in a private copy of ``server/`` and ``ui/``
under the session scratchpad (``s4-save-mut``), never in the shared tree
(#254); the failure each produced is quoted.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __name__ == "__main__":      # run as a script: import the package beside it
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrodeck.flows.models import FlowGraph, FlowNode, FlowRecord
from astrodeck.flows.save_rules import prepare_save

FIXTURE = Path(__file__).parent / "fixtures" / "save_answer_rows.json"

#: The spec's 3x2 (A.5): threshold 0.5 x 0.25 x 1.33 deg.
_BLOCK = {"name": "M31 3x2", "ra": "00h 42m 44s", "dec": "+41 00 00",
          "rotation": 30, "angle": "Rotate to PA", "rows": 2, "cols": 3,
          "overlap": 25, "fovX": 2.0, "fovY": 1.33}
#: A catalogue for the named case: name -> (ra_hours, dec_deg, identity).
_CATALOGUE = {"M 31": (0.7123, 41.269, "M31"), "M33": (1.5641, 30.66, "M33")}


def _named(name: str) -> dict:
    return {"name": name, "ra": "", "dec": "", "rotation": -1,
            "fovX": 2.0, "fovY": 1.33}


def _single(ra: str) -> dict:
    """A single panel with no camera field on the equator, any angle."""
    return {"name": "equator", "ra": ra, "dec": "+00 00 00", "rotation": -1}


#: ``reason -> (the stored block, the block as saved)``.
CASES = {
    "grid": (_BLOCK, {**_BLOCK, "rows": 3}),
    "angle": (_BLOCK, {**_BLOCK, "angle": "Any angle"}),
    "identity": (_named("M 31"), _named("M33")),
    "move": (_single("00h 00m 00s"), _single("06h 00m 00s")),
}


def _record(params: dict) -> FlowRecord:
    return FlowRecord(id="f-352", name="352", graph=FlowGraph(nodes=[
        FlowNode(id="t", type="target", params=dict(params))]))


def rows_now() -> dict[str, list[dict]]:
    """``prepare_save``'s rows for each case: the stored block saved first,
    then the changed one saved over it."""
    out = {}
    for reason, (before, after) in CASES.items():
        prior, _, _ = prepare_save(_record(before), None,
                                   resolve=_CATALOGUE.get)
        _saved, _, rows = prepare_save(_record(after), prior,
                                       resolve=_CATALOGUE.get)
        out[reason] = rows
    return out


def _document(rows: dict) -> dict:
    return {"about": ("save_rules.prepare_save's reanchored rows, one save "
                      "per reason. Recorded by running "
                      "server/tests/test_flows_save_answer_fixture.py as a "
                      "script, graded against prepare_save exactly by that "
                      "file, and read by ui/src/components/flows/__tests__/"
                      "flowsSaveAnswer.test.ts. Never edit it by hand."),
            "cases": rows}


def test_the_fixture_is_what_prepare_save_answers():
    """The recorded rows equal ``prepare_save``'s, value for value, float
    for float, and each case's row says the reason it is filed under.

    RED under mutant "row drops reason" (``save_rules._anchor_on_save``'s row
    built without ``reason``), observed:

        E   AssertionError: the recorded rows are not prepare_save's; record the fixture again only after a deliberate change
        E   assert {'angle': [{'...d_deg': 0.0}]} == {'angle': [{'...d_deg': 0.0}]}
        E     Differing items:
        E     {'move': [{'max_move_deg': 90.0, 'node_id': 't', 'threshold_deg': 0.0}]} != {'move': [{'max_move_deg': 90.0, 'node_id': 't', 'reason': 'move', 'threshold_deg': 0.0}]}
        E     {'angle': [{'max_move_deg': None, 'node_id': 't', 'threshold_deg': 0.16625}]} != {'angle': [{'max_move_deg': None, 'node_id': 't', 'reason': 'angle', 'threshold_deg': 0.16625}]}
        E     {'grid': [{'max_move_deg': None, 'node_id': 't', 'threshold_deg': 0.16625}]} != {'grid': [{'max_move_deg': None, 'node_id': 't', 'reason': 'grid', 'threshold_deg': 0.16625}]}

    Recording the fixture again from that mutated server does not make this
    green either, because each case's row must say the reason it is filed
    under; observed after re-recording:

        E   KeyError: 'reason'

    (and the UI test, reading the re-recorded file, then read "its framing
    changed" for grid, angle and identity: see flowsSaveAnswer.test.ts).

    RED under mutant "fixture hand-edited" (the recorded ``grid`` row's
    reason changed to ``"angle"`` in a copy of the file), observed:

        E   AssertionError: the recorded rows are not prepare_save's; record the fixture again only after a deliberate change
        E   assert {'angle': [{'...d_deg': 0.0}]} == {'angle': [{'...d_deg': 0.0}]}
        E     Differing items:
        E     {'grid': [{'max_move_deg': None, 'node_id': 't', 'reason': 'grid', 'threshold_deg': 0.16625}]} != {'grid': [{'max_move_deg': None, 'node_id': 't', 'reason': 'angle', 'threshold_deg': 0.16625}]}
    """
    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    assert rows_now() == recorded, (
        "the recorded rows are not prepare_save's; record the fixture "
        "again only after a deliberate change")
    assert sorted(recorded) == ["angle", "grid", "identity", "move"]
    for reason, rows in recorded.items():
        assert [r["reason"] for r in rows] == [reason], (
            f"the {reason} case records {rows}")


def test_the_move_row_is_exact_by_construction():
    """The premise the exact grade stands on (see the module docstring): the
    move is a quarter turn, 90.0 deg to the last bit, against 0.0."""
    (row,) = rows_now()["move"]
    assert (row["max_move_deg"], row["threshold_deg"]) == (90.0, 0.0)


if __name__ == "__main__":
    # LF on every platform, as the repo's other text fixtures are.
    FIXTURE.write_text(json.dumps(_document(rows_now()), indent=2) + "\n",
                       encoding="utf-8", newline="\n")
    print(f"recorded {FIXTURE}")
