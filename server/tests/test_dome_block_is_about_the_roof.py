"""Refusing a run protects a roof only when the roof would otherwise stay open.

A flow with a DOME CONTROL node could not start at all on a rig with a dome
connected. The 409 said:

    "the plan carries no dome policy, so nothing would close the shutter on an
     unsafe reading. Remove the dome node to run the rest of the flow."

The first clause is true - `SequencePlan` has nowhere to put the compiled
`DomePolicy`. The second is FALSE on any rig with `close_dome_on_unsafe` set
(the Remote preset sets it): `engine._run` passes that flag into the unsafe
wind-down, which routes a connected dome through `roof.close_observatory` -
never over an unparked mount. The node's one non-negotiable promise is kept, by
config rather than by the node.

So the refusal fired on runs whose roof WOULD close, and the advice was actively
wrong: removing the dome node changes nothing about whether the shutter shuts,
and throws away the operator's stated intent. Someone with a dome, a rain
sensor, and the right setting could not run their flow, and the reason given was
about a danger that did not exist.

FAIL-CLOSED IS PRESERVED. With the setting off the block still fires, because
then the sentence is true. What changes is the advice: turn the setting on, not
delete the node.

WHAT IS GENUINELY LOST EITHER WAY is the node's own azimuth binding and shutter
timeout. `DomePolicy.apply_binding` and `DomePolicy.from_plan` have no callers
anywhere in the server, so a dome that should track the mount will not be told
to. That is a note, not a wall.
"""
from __future__ import annotations

import pytest

from astrodeck.flows import examples as ex
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.to_plan import blocking_reasons, to_sequence_plan

DOME_ROW = {"key": "automation.dome", "detail": "d", "level": "danger"}
OTHER = {"key": "automation.dusk_flats", "detail": "d", "level": "warn"}


class TestTheBlockFollowsTheRoof:
    def test_no_dome_attached_never_blocks(self):
        """Unchanged: with no roof there is nothing to leave open, and refusing
        would stop the shipped examples running on the simulator."""
        assert blocking_reasons([DOME_ROW], dome_connected=False) == []
        assert blocking_reasons([DOME_ROW], dome_connected=False,
                                closes_on_unsafe=True) == []

    def test_a_dome_that_will_NOT_close_still_blocks(self):
        """Fail-closed, preserved. This is the case the refusal was written for
        and it is still refused."""
        assert blocking_reasons([DOME_ROW, OTHER], dome_connected=True,
                                closes_on_unsafe=False) == [DOME_ROW]

    def test_a_dome_that_WILL_close_does_not_block(self):
        """The regression. A rig on the Remote preset closes its roof on an
        unsafe reading for every run; refusing the flow protected nothing and
        told the operator to delete the node that says so."""
        assert blocking_reasons([DOME_ROW, OTHER], dome_connected=True,
                                closes_on_unsafe=True) == []

    def test_the_default_is_the_strict_one(self):
        """A caller that has not been taught about the flag must keep getting
        the old, safe answer - a new keyword must never quietly unblock a roof."""
        assert blocking_reasons([DOME_ROW], dome_connected=True) == [DOME_ROW]

    def test_only_the_dome_row_is_ever_blocking(self):
        assert blocking_reasons([OTHER], dome_connected=True,
                                closes_on_unsafe=False) == []


class TestTheNoteSaysWhichHalfIsLost:
    def _campaign(self, closes: bool):
        rec = [f for f in ex.examples() if f.id == "example-campaign"][0]
        return to_sequence_plan(compile_plan(rec.graph, rec.name), rec.graph,
                                closes_on_unsafe=closes)

    def test_with_the_setting_off_it_is_a_danger(self):
        _p, un = self._campaign(False)
        row = [u for u in un if u["key"] == "automation.dome"][0]
        assert row["level"] == "danger"
        assert "close it on an unsafe reading" in row["detail"]

    def test_with_the_setting_on_it_stops_claiming_the_roof_stays_open(self):
        _p, un = self._campaign(True)
        row = [u for u in un if u["key"] == "automation.dome"][0]
        assert row["level"] != "danger", (
            "a roof that will close is still reported as a danger, which sends "
            "someone out to a shutter that shut hours ago")
        assert "WILL close" in row["detail"]

    def test_it_still_names_what_IS_lost(self):
        """The guard against the fix becoming an overclaim in the other
        direction. Binding and timeout genuinely do not reach the engine."""
        _p, un = self._campaign(True)
        detail = [u for u in un if u["key"] == "automation.dome"][0]["detail"]
        assert "binding" in detail and "timeout" in detail, detail

    def test_the_dome_row_never_disappears(self):
        """Whichever way the setting is set, the operator is told something
        about the dome node. Silence would be the worst of the three."""
        for closes in (True, False):
            _p, un = self._campaign(closes)
            assert [u for u in un if u["key"] == "automation.dome"], (
                f"the dome node vanished from the report at closes={closes}")


class TestTheThingsNothingHonoursStayHonest:
    def test_apply_binding_and_from_plan_have_no_callers(self):
        """The claim the note rests on, checked rather than remembered. If
        either grows a caller, the note's "binding is dropped" clause becomes
        the next stale sentence and this test is what says so.

        THE AST, NOT A SUBSTRING SEARCH. The first version grepped the text and
        failed instantly - on the comment in `to_plan.py` that names these two
        functions while explaining that nothing calls them. That is defect #197
        in this repo's own backlog ("test_routes_have_callers is fooled by a
        path inside a comment"), reproduced from scratch, which is a fair
        indication of how easy the mistake is. Attribute nodes cannot appear in
        a comment.
        """
        import ast
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1] / "astrodeck"
        watched = {"apply_binding", "from_plan"}
        hits: list[str] = []
        for p in root.rglob("*.py"):
            if p.name == "base.py" and p.parent.name == "devices":
                continue          # the definitions themselves
            try:
                tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in watched:
                    hits.append(f"{p.name}:{node.lineno} .{node.attr}")
        # `from_plan` is a common enough name that a same-named method elsewhere
        # would be a false positive; report what was found rather than a bare
        # count so the next reader can tell the two apart.
        assert not hits, (
            f"something now reaches the dome binding path, so the unmapped "
            f"note claiming it is dropped has gone stale: {hits}")
