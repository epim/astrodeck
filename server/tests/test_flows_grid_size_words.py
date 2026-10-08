# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A grid's size in words (S4 orchestrator ruling 1, #339; the compile and
doctor half).

The code wrote a mosaic's size two ways. The eighth Example is named "M31
3x2" and the Sky hub's framing card writes ``cols x rows``, width by height,
the way a camera field is written ("2.0 x 1.33 deg"); the compile's skip
note, the doctor's M6 worked case and the brief wrote ``rows x cols``,
pulled that way by the panel labels, which are row-column ("M31 2-1" is row
2, column 1). So one block read "3x2" in the library and "2x3" in the text.

S4 orchestrator ruling 1: text an operator reads spells the size so it
cannot be misread, "3 columns by 2 rows", once, and keeps the panel labels
row-column, from "1-1" to "<rows>-<cols>". This file holds the compile's
half, the skip note, and the doctor's, where M6's worked case is the 4x1 (4
columns by 1 row) at 10% overlap at Dec 75 and its sentence is otherwise
unchanged. The brief is Tonight's, and not in this change.

MUTANTS were run from byte backups in a private copy of ``server/`` under the
session scratchpad (``s4-compile-mut``), never in the shared tree; each
observed failure is quoted in the test it turned red.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from astrodeck.flows import doctor
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode

M31 = {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09"}


def _block(rows: int, cols: int, skip: str) -> FlowGraph:
    """TARGET M31 at ``rows`` by ``cols``, framed and at an angle, owning a
    FILTER CYCLE with its loop wire."""
    params = {**M31, "rows": rows, "cols": cols, "skip": skip, "fovX": 2.0,
              "fovY": 1.33, "rotation": 30, "angle": "Rotate to PA"}
    return FlowGraph(
        nodes=[FlowNode(id="t", type="target", params=params),
               FlowNode(id="cy", type="cycle", x=200)],
        edges=[FlowEdge(**{"from": "t", "fromPort": "target", "to": "cy",
                           "toPort": "run"}),
               FlowEdge(**{"from": "cy", "fromPort": "pass", "to": "t",
                           "toPort": "next"})])


def _skip_notes(rows: int, cols: int, skip: str) -> list[str]:
    return [n["text"] for n in compile_plan(_block(rows, cols, skip),
                                            "n").get("notes", [])
            if n["node_id"] == "t"]


class TestTheSkipNote:
    def test_a_2_row_by_3_column_block_reads_3_columns_by_2_rows(self):
        """The ruling's own case. The size is said once, columns first, in
        words; the labels run row-column from 1-1 to 2-3; neither "2x3" nor
        "3x2" appears.

        RED under mutant "size spelled rows x cols" (the note's size written
        ``this {rows}x{cols} grid`` again, as S3 built it), observed:

            AssertionError: TARGET M31: skip '9-9' names no panel of this 2x3
            grid, so nothing is skipped for it. A panel is written
            row-column, from 1-1 to 2-3.
            assert 0 == 1

        RED under mutant "columns and rows swapped", observed:

            AssertionError: TARGET M31: skip '9-9' names no panel of this
            grid of 2 columns by 3 rows, so nothing is skipped for it. A
            panel is written row-column, from 1-1 to 2-3.
            assert 0 == 1
        """
        (text,) = _skip_notes(2, 3, "9-9")
        assert text.count("3 columns by 2 rows") == 1, text
        assert "from 1-1 to 2-3" in text, text
        assert "2x3" not in text and "3x2" not in text, text

    @pytest.mark.parametrize("rows,cols,size,last", [
        (1, 4, "4 columns by 1 row", "1-4"),
        (3, 1, "1 column by 3 rows", "3-1"),
        (2, 2, "2 columns by 2 rows", "2-2")],
        ids=["4x1", "1x3", "2x2"])
    def test_a_side_of_one_is_said_in_the_singular(self, rows, cols, size,
                                                    last):
        """"4 columns by 1 row", never "1 rows". The square grid is the
        control for the order: it reads the same either way, so only the
        two oblong grids can catch a swap.

        RED under mutant "columns and rows swapped" (``grid_size``'s words
        kept, the two numbers exchanged), observed on 4x1 and 1x3 (2x2
        stays green, as a square must), e.g.:

            AssertionError: TARGET M31: skip '0-0' names no panel of this
            grid of 1 column by 4 rows, so nothing is skipped for it. A
            panel is written row-column, from 1-1 to 1-4.
            assert 'grid of 4 columns by 1 row,' in "TARGET M31: ..."

        RED under mutant "size spelled rows x cols" on all three, e.g.:

            assert 'grid of 4 columns by 1 row,' in "TARGET M31: skip '0-0'
            names no panel of this 1x4 grid, so nothing is skipped for it.
            A panel is written row-column, from 1-1 to 1-4."
        """
        (text,) = _skip_notes(rows, cols, "0-0")
        assert f"grid of {size}," in text, text
        assert f"from 1-1 to {last}." in text, text

    def test_the_labels_are_row_column_as_the_skip_reads_them(self):
        """The note's label range is the one ``parse_skip`` reads: on 2 rows
        by 3 columns "2-3" is a panel and is skipped, "3-2" is not and is
        named in the note."""
        compiled = compile_plan(_block(2, 3, "2-3, 3-2"), "n")
        (entry,) = compiled["targets"]
        assert entry["mosaic"]["skip"] == [[2, 3]]
        (text,) = _skip_notes(2, 3, "2-3, 3-2")
        assert "'3-2'" in text and "'2-3'" not in text, text

    def test_control_a_skip_that_names_panels_says_nothing(self):
        """Control: every entry names a panel, so there is no note to word."""
        assert _skip_notes(2, 3, "1-1, 2-3") == []


class TestTheDoctorsWorkedCase:
    def test_m6_is_said_about_the_4x1(self):
        """M6's comment names its worked case as a 4x1 (4 columns by 1 row)
        and never as the 1x4 it was written as: the block its test builds
        is ``rows=1, cols=4``, and "use fewer columns", the sentence's own
        remedy, is only true of that one.

        RED under mutant "the M6 comment says 1x4" (``doctor.py``'s
        ``CONVERGENCE_WARN_SHARE`` comment back to S3's words for the case),
        observed:

            assert '1x4' not in '(the source of doctor.py)'
              '1x4' is contained here:
                #: 1x4 at 10% at Dec 75 uses 38.9% and warns; the 3x3
        """
        source = Path(doctor.__file__).read_text(encoding="utf-8")
        assert "1x4" not in source
        assert "4x1 (4 columns by 1 row) at 10% at Dec 75" in source

    def test_m6s_sentence_is_otherwise_unchanged(self):
        """Control: the ruling words the case, not the rule. The 4x1 at 10%
        at Dec 75, laid out at angle 0, still draws M6's one sentence, with
        its remedy of fewer columns, and no grid size in it.

        RE-PINNED FOR BACKLOG WP-124 (#175, wave 16 integration): M6 prices
        the camera that shoots every panel at ONE angle. A rotating block is
        commanded each panel's own angle now and M6 is silent for it, so the
        block here is the fixed camera it was always meant to be
        (``angle="Camera fixed at PA"``, the same pin test_flows_doctor_s3.py
        carries as ``FIXED``); a rotating block left in would draw no
        sentence to compare."""
        g = _block(1, 4, "")
        node = g.node("t")
        node.params.update(overlap=10, dec="+75 00 00", rotation=0,
                           angle="Camera fixed at PA")
        (issue,) = [i for i in doctor.check(g) if "convergence" in i.text]
        assert issue.level == "warn"
        assert issue.text == (
            "▸ TARGET M31 - at Dec 75 meridian convergence turns neighbouring "
            "panels against each other, which uses 38.9% of their 10% "
            "overlap. Widen the overlap or use fewer columns.")
