# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A flow night keeps recording its log lines after the run's terminal
publish (#808, #1122).

THE GAP. `Night` stops recording at a run's first terminal publish, so the
line `SequenceEngine._run` logs next ("sequence '...' complete: N frames")
and anything `_finalize_report` or the wind-down says never reached
`Night.lines` or `Night.said`. A test of such a line had to read the bus
through the `bus_lines` fixture instead, which also kept it out of the night
log. `FlowRig` now records the `sequence` lines of its current night until
the rig is closed, and leaves the trace, the states and the events, which the
golden comparison reads, exactly where the terminal publish stopped them.
"""
from __future__ import annotations

from astrodeck import events

from _flow_night import FlowRig, flow_rig, mosaic_flow  # noqa: F401 (fixture)
from _group_harness import group_store, ra_at  # noqa: F401 (fixture)

FLOW = mosaic_flow(ra_hours=ra_at(-2.0), plan="L 10", cycles=1)


async def _finished_night(rig: FlowRig):
    night = await rig.night()
    fid = await rig.save_flow(FLOW)
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    await night.finish()
    return night


async def test_the_line_logged_after_the_terminal_publish_is_in_night_lines(flow_rig):
    """The run's own "complete: N frames" line comes after the `complete`
    state is published, and is in `lines` and found by `said`.

    RED under mutant "the recording stops at the terminal publish" (the
    `FlowRig` hook that keeps the lines removed), observed:

        AssertionError: the line the run logged after its terminal publish
        was not recorded: []
    """
    night = await _finished_night(flow_rig)
    said = night.said("' complete: ")
    assert said, ("the line the run logged after its terminal publish was "
                  f"not recorded: {said}")
    assert " frames" in said[0], said
    assert any(m == said[0] for _t, _lvl, m in night.lines)


async def test_the_golden_trace_still_stops_at_the_terminal_publish(flow_rig):
    """Keeping the lines must not move what the golden comparison reads: the
    trace's last entry is still the terminal state, and the events and the
    states stop there too.

    RED under mutant "the whole night keeps recording" (the hook sets
    `night._recording = True` after it appends a line), observed:

        AssertionError: the trace went on past the terminal publish:
        [40.0, 'log', 'info', 'warming camera in the background ...']
    """
    night = await _finished_night(flow_rig)
    last = night.trace[-1]
    assert last[1] == "state" and last[2]["state"] == "complete", (
        f"the trace went on past the terminal publish: {last}")
    assert night.states[-1]["state"] == "complete"
    assert night.events[-1]["data"].get("state") == "complete", night.events[-1]


async def test_lines_stop_being_recorded_when_the_rig_closes(flow_rig):
    """The record is closed with the harness: a line logged once the rig is
    closed is not added to a night's lines.

    RED under mutant "the record is never closed" (`FlowRig.close` no longer
    marks the rig closed), observed:

        AssertionError: a line logged after the rig closed was recorded
    """
    night = await _finished_night(flow_rig)
    before = len(night.lines)
    await flow_rig.close()
    events.bus.log("info", "after the close", "sequence")
    assert len(night.lines) == before, (
        "a line logged after the rig closed was recorded")
