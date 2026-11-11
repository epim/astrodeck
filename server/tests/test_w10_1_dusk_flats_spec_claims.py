# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The mosaic spec says the DUSK FLATS node naming that exists (WP 10.1 part c,
#1231).

Revision 15 of docs/superpowers/specs/2026-09-23-flows-mosaic-target-block-
design.md says how the plan and the session record carry the DUSK FLATS node's
id, what `SequenceEngine._dusk_flats` returns from every exit and the word each
outcome reads. This file holds Revision 15 to the code it describes, read as
text, never executed.

RED under the spec mutant "an outcome left out" (Revision 15 without
`DUSK_FLATS_NOT_TONIGHT`), observed:

    AssertionError: Revision 15 does not name `DUSK_FLATS_NOT_TONIGHT`
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs/superpowers/specs/2026-09-23-flows-mosaic-target-block-design.md"
ASTRODECK = ROOT / "server/astrodeck"
OUTCOMES = ("DUSK_FLATS_SHOT", "DUSK_FLATS_NOTHING_OWED",
            "DUSK_FLATS_NOT_TONIGHT", "DUSK_FLATS_FAILED")


def _revision_15() -> str:
    text = SPEC.read_text(encoding="utf-8").replace("\r\n", "\n")
    return text[text.index("## Revision 15 ("):]


def test_revision_15_names_what_it_describes():
    said = _revision_15()
    for name in ("`DuskFlatsPlan.node`", "`to_plan._dusk_flats_node`",
                 "`Session.flow_dusk_flats`", "`Session.note_stages`",
                 "`_name_the_stages`", "`SESSION_SCHEMA`", "`_dusk_flats`",
                 "`_dusk_flats_outcome`", "`_say_dusk_flats`",
                 "`_lane_node_ids`", "`_DUSK_FLATS_WORD`",
                 *(f"`{o}`" for o in OUTCOMES)):
        assert name in said, f"Revision 15 does not name {name}"
    for word in ("`running`", "`done`", "`skipped`", "`failed`",
                 "`deferred`"):
        assert word in said, f"Revision 15 does not name {word}"


def test_what_revision_15_names_is_in_the_tree():
    models = (ASTRODECK / "sequence/models.py").read_text(encoding="utf-8")
    assert "node: str | None = Field(None, exclude=True)" in models
    engine = (ASTRODECK / "sequence/engine.py").read_text(encoding="utf-8")
    for fn in ("_dusk_flats_outcome", "_say_dusk_flats", "_lane_node_ids"):
        assert re.search(rf"def {fn}\(", engine), fn
    assert re.search(r"async def _dusk_flats\(self\) -> str:", engine)
    for outcome in OUTCOMES:
        assert re.search(rf"^{outcome} = ", engine, re.M), outcome
    table = engine[engine.index("_DUSK_FLATS_WORD = {"):]
    table = table[:table.index("}")]
    for outcome, word in (("DUSK_FLATS_SHOT", "done"),
                          ("DUSK_FLATS_NOTHING_OWED", "done"),
                          ("DUSK_FLATS_NOT_TONIGHT", "skipped"),
                          ("DUSK_FLATS_FAILED", "failed")):
        assert re.search(rf'{outcome}: "{word}"', table), (outcome, word)
    session = (ASTRODECK / "sequence/session.py").read_text(encoding="utf-8")
    assert "flow_dusk_flats: Any = None" in session
    assert re.search(r"def _dusk_flats_node\(graph", (
        ASTRODECK / "flows/to_plan.py").read_text(encoding="utf-8"))
