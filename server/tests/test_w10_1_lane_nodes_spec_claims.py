# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The mosaic spec says the lane-node naming that exists (WP 10.1 part b,
#1231).

Revision 14 of docs/superpowers/specs/2026-09-23-flows-mosaic-target-block-
design.md says which nodes the engine now names, how the plan and the session
record carry them, and supersedes Revision 12's sentence that said an AUTOFOCUS
or GUIDE node is never named. This file holds Revision 14 to the code it
describes, read as text, never executed.

RED under the spec mutant "the old sentence not superseded" (Revision 14
without the quoted Revision 12 sentence), observed:

    AssertionError: Revision 14 does not supersede Revision 12's sentence by
    name
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs/superpowers/specs/2026-09-23-flows-mosaic-target-block-design.md"
ASTRODECK = ROOT / "server/astrodeck"
OLD = ("A stage the engine does not run as a stage (an AUTOFOCUS node, a GUIDE "
       "node, the REPORT) is never named")


def _spec() -> str:
    return SPEC.read_text(encoding="utf-8").replace("\r\n", "\n")


def _revision_14() -> str:
    text = _spec()
    return text[text.index("## Revision 14 ("):]


def test_revision_14_supersedes_revision_12s_sentence_by_name():
    text = _spec()
    before = text[:text.index("## Revision 14 (")]
    assert OLD in before, "premise: Revision 12 still carries the sentence"
    said = _revision_14()
    assert "supersedes, by name, Revision 12's sentence" in said, (
        "Revision 14 does not supersede Revision 12's sentence by name")
    assert f'"{OLD}"' in said


def test_revision_14_names_what_it_describes():
    said = _revision_14()
    for name in ("`Target.lane`", "`LaneNode`", "`flow_lanes`", "`_say_lane`",
                 "`_finish_lane`", "`_autofocus_sweep`", "`_leave_target`",
                 "`to_plan._lane_nodes`", "`Session.note_stages`",
                 "`_name_the_stages`", "`SESSION_SCHEMA`"):
        assert name in said, f"Revision 14 does not name {name}"
    for word in ("`running`", "`done`", "`failed`", "`deferred`"):
        assert word in said, f"Revision 14 does not name {word}"


def test_what_revision_14_names_is_in_the_tree():
    models = (ASTRODECK / "sequence/models.py").read_text(encoding="utf-8")
    assert "lane: list[LaneNode] = Field(default_factory=list, exclude=True)" \
        in models
    assert re.search(r"class LaneNode\(BaseModel\)", models)
    engine = (ASTRODECK / "sequence/engine.py").read_text(encoding="utf-8")
    for fn in ("_say_lane", "_finish_lane", "_autofocus_sweep",
               "_leave_target", "_lane_node_ids"):
        assert re.search(rf"def {fn}\(self", engine), fn
    session = (ASTRODECK / "sequence/session.py").read_text(encoding="utf-8")
    assert "flow_lanes: dict[str, Any]" in session
    assert re.search(r"def _lane_nodes\(graph", (
        ASTRODECK / "flows/to_plan.py").read_text(encoding="utf-8"))
