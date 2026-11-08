# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The mosaic spec says the stage-status writer that exists (WP 4.2, #1125,
member #742).

Revision 12 of docs/superpowers/specs/2026-09-23-flows-mosaic-target-block-
design.md says the engine is the one writer of what each flow stage is doing,
how it publishes it, what keeps it in the store and the one selector every
reader asks. test_mosaic_spec_claims.py holds the sentences above it that used
to say `flows.statuses` is written by nothing; this file holds Revision 12 to
the code it describes, read as text, never executed.

RED under the spec mutant "a word dropped" (Revision 12 without the
backticked ``skipped``), observed:

    AssertionError: the engine's words are ['done', 'failed', 'running',
    'skipped'] and Revision 12 names none of ['skipped']

RED under the spec mutant "the old sentence put back" (section 8's S7
"Not built in S7" sentence restored to "`flows.statuses` is still written by
nothing: S7 deferred"), observed:

    AssertionError: the spec still says `flows.statuses` is written by
    nothing outside the history of ruling 10 and S5's own record:
    `flows.statuses` is still written by nothing
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs/superpowers/specs/2026-09-23-flows-mosaic-target-block-design.md"
ENGINE = ROOT / "server/astrodeck/sequence/engine.py"
UI = ROOT / "ui/src"


def _revision_12() -> str:
    text = SPEC.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("## Revision 12 (")
    return text[start:]


def test_revision_12_names_the_engines_words_and_the_selector():
    """Revision 12 names every word the engine says, the one writer, the plan
    fields that carry the names, the store's field and the one selector."""
    said = _revision_12()
    engine = ENGINE.read_text(encoding="utf-8")
    words = sorted(set(re.findall(r'return "(running|done|skipped)"', engine)
                       + ["failed"]))
    missing = [w for w in words if f"`{w}`" not in said]
    assert not missing, (
        f"the engine's words are {words} and Revision 12 names none of "
        f"{missing}")
    for name in ("SequenceEngine._publish_stages", "ExposureStep.stage",
                 "Target.block", "`stages`", "`flows.statuses`",
                 "`stageStatusOf`", "`useStageStatus`", "`flowRunLive`",
                 "`to_plan._identify`"):
        assert name in said, f"Revision 12 does not name {name}"


def test_what_revision_12_names_is_in_the_tree():
    engine = ENGINE.read_text(encoding="utf-8")
    assert re.search(r"def _publish_stages\(self", engine)
    assert "self._set_state(stages=" in engine
    models = (ROOT / "server/astrodeck/sequence/models.py").read_text(
        encoding="utf-8")
    assert "stage: str | None = Field(None, exclude=True)" in models
    assert "block: str | None = Field(None, exclude=True)" in models
    store = (UI / "store.ts").read_text(encoding="utf-8")
    assert "export function stageStatusOf(" in store
    assert "export const useStageStatus" in store
    for rel in ("components/flows/FlowNodeCard.tsx",
                "next/hubs/session/flows/canvas/FlowNode.tsx",
                "next/hubs/session/flows/canvas/FlowStagesPhoneSheet.tsx",
                "next/hubs/session/flows/canvas/FlowWires.tsx",
                "components/flows/FlowWireLayer.tsx"):
        assert "useStageStatus(" in (UI / rel).read_text(encoding="utf-8"), rel


def test_no_sentence_outside_history_says_nothing_writes_the_statuses():
    """The sentences that said `flows.statuses` is written by nothing are
    history now: each says "was written by nothing" and points at Revision
    12. A present-tense one left behind is the claim nothing keeps."""
    text = SPEC.read_text(encoding="utf-8").replace("\r\n", "\n")
    stale = [m.group(0) for m in re.finditer(
        r"`flows\.statuses` is still written by nothing", text)]
    assert not stale, (
        "the spec still says `flows.statuses` is written by nothing outside "
        "the history of ruling 10 and S5's own record: " + "; ".join(stale))
