"""A wire the engine answers by another route is not a lost wire.

Three rules in the shipped campaign example ask for something the engine
already does, by a different mechanism than the one the wire names:

    SESSION REPORT done -> POOL advance   the scheduler advances the pool
    CLOUD WATCH      -> CALIB do          the hold shoots the darks itself
    SKY CLEARS       -> HOLD resume       the hold releases itself

All three were reported at ``warn``, under a heading that reads NOT HONOURED BY
A RUN, and ``/api/flows/{id}/start`` refused with "parts of this flow do not
survive the compile" until the operator accepted them. Every part of that is a
statement the same list disproves one line later.

The cause was small and exact: ``to_plan.Level`` was ``warn | danger`` while
two tables in that module documented themselves as emitting ``note``. The
doctor has emitted ``note`` since it shipped and the editor already inks it
dim, so the level existed everywhere except the module that needed it.

WHY THIS MATTERS MORE THAN A COLOUR. An operator who is told their month-long
campaign's loop-back wire will not run does not run the campaign - so the
feature that does work goes unused and nobody finds out. Over-reporting a loss
costs exactly what hiding one costs, and this file holds both ends: a note is
not a loss (below), and a real loss is still a loss (the guard class at the
bottom).
"""
from __future__ import annotations

import pytest

from astrodeck.flows import examples as ex
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.to_plan import (REDUNDANT_PORTS, blocking_reasons, losses,
                                     to_sequence_plan)


def _campaign() -> dict:
    rec = [f for f in ex.examples() if f.id == "example-campaign"][0]
    return compile_plan(rec.graph)


def _notes_for(unmapped: list[dict], needle: str) -> list[dict]:
    return [u for u in unmapped if needle in u["key"]]


class TestThePoolAdvancesWithoutARule:
    def test_the_campaign_wires_its_loop_back(self):
        """The premise. If the example stops drawing this wire the rest of the
        class is testing nothing, so assert the wire exists before asserting
        anything about how it is reported."""
        rules = _campaign().get("instructions") or []
        assert any(r.get("when") == "on_target_complete"
                   and r.get("action") == "pool"
                   and r.get("to_port") == "advance" for r in rules), (
            "the campaign example no longer wires SESSION REPORT done -> POOL "
            "advance, so this file's subject is gone")

    def test_the_loop_back_is_not_reported_as_a_lost_rule(self):
        _plan, unmapped = to_sequence_plan(_campaign())
        rows = _notes_for(unmapped, "on_target_complete")
        assert len(rows) == 1, f"expected one row for the loop-back, got {rows}"
        assert rows[0]["level"] == "note", (
            f"the campaign's loop-back wire is reported at "
            f"{rows[0]['level']!r}: {rows[0]['detail']}")
        assert "will not run" not in rows[0]["detail"], (
            "the loop-back is still described as a rule that will not run")

    def test_it_says_what_actually_advances_the_pool(self):
        """A note that only says "this is fine" is not worth the line. It has
        to name the mechanism, because that is what an operator checks."""
        _plan, unmapped = to_sequence_plan(_campaign())
        detail = _notes_for(unmapped, "on_target_complete")[0]["detail"]
        assert "ledger" in detail and "next member" in detail, detail


class TestNotesDoNotHoldTheStart:
    def test_losses_drops_notes_and_keeps_everything_else(self):
        _plan, unmapped = to_sequence_plan(_campaign())
        kept = losses(unmapped)
        assert kept, "the campaign still has real losses - they must survive"
        assert all(u["level"] != "note" for u in kept)
        dropped = [u for u in unmapped if u not in kept]
        assert dropped, "no notes at all in the campaign - the split is untested"
        assert all(u["level"] == "note" for u in dropped)

    def test_a_flow_whose_only_unmapped_rows_are_notes_starts_clean(self):
        """The behaviour the split exists for. `/start` gates on `losses`, so a
        flow whose whole unmapped list is notes must not need
        `accept_unmapped`."""
        notes_only = [{"key": "instructions[a -> b]", "detail": "x",
                       "level": "note"}]
        assert losses(notes_only) == []

    def test_a_note_never_becomes_a_dome_refusal(self):
        """The dome fails CLOSED and is not clearable by accept_unmapped, so
        the new level must not be able to reach that path - in either
        direction. A note is not blocking, and the dome entry is still blocking
        with a dome attached."""
        rows = [{"key": "instructions[x -> y]", "detail": "d", "level": "note"},
                {"key": "automation.dome", "detail": "d", "level": "danger"}]
        assert blocking_reasons(rows, dome_connected=True) == [rows[1]]
        assert blocking_reasons([rows[0]], dome_connected=True) == []


class TestRealLossesAreStillLosses:
    """The guard against the fix becoming the next defect. Quieting a class of
    report is one edit away from quieting the ones that matter."""

    # THIS LIST IS WHAT IS STILL OWED, and it shrinks by exactly one line each
    # time something gets built - removing the entry rather than relaxing the
    # assertion is the whole discipline. Two have gone:
    #
    #   `on_altitude_floor` - the engine gained `_enforce_altitude_floor`, so
    #   the trigger became legal and the rule RUNS.
    #   `on_shutdown_complete` - the wind-down gained a day-darks phase between
    #   the park and the warm, so the wire is HONOURED (a note, not a loss)
    #   without the engine needing the trigger at all.
    #
    # What remains is genuinely not built: PARK + CLOSE's roof half needs the
    # plan to be able to carry a DomePolicy, and the dome danger is the roof
    # that will not close.
    #   `on_night_end -> parkclose` - the night already ends parked with the
    #   cover shut whether or not the wire is there, so it too became a note.
    #   The ROOF half is the only part that depends on anything outside the
    #   graph, and it keeps its own two reports (the dome danger below, and
    #   `blocking_reasons` refusing the run when the roof would stay open).
    #
    # THE CAMPAIGN NOW HAS NO DEAD RULES AT ALL. What is left on this list is
    # the roof, which is a real danger and not a rule.
    @pytest.mark.parametrize("needle,level", [
        ("automation.dome", "danger"),   # a roof that will not close
    ])
    def test_it_is_still_reported(self, needle, level):
        _plan, unmapped = to_sequence_plan(_campaign())
        rows = _notes_for(unmapped, needle)
        assert rows, f"{needle} vanished from the unmapped list"
        assert rows[0]["level"] == level, (
            f"{needle} is reported at {rows[0]['level']!r}, expected {level!r}")

    def test_the_dome_still_refuses_the_start(self):
        _plan, unmapped = to_sequence_plan(_campaign())
        assert blocking_reasons(unmapped, dome_connected=True), (
            "a connected dome no longer blocks a flow whose dome policy is "
            "dropped")

    def test_every_redundant_port_names_a_real_node_and_port(self):
        """The table is keyed by (node type, input port) and a typo in either
        half is silent: the entry simply never matches and the wire goes back
        to reading as a loss."""
        from astrodeck.flows.nodes import NODE_DEFS
        for (node_type, port) in REDUNDANT_PORTS:
            nd = NODE_DEFS.get(node_type)
            assert nd is not None, f"no such node type: {node_type}"
            assert nd.port(port, "in") is not None, (
                f"{node_type} has no input port {port!r}")
