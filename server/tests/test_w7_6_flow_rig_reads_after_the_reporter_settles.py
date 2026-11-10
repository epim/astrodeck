# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""`FlowRig.read` waits for the reporter's pending writes before it grades a
report (#1149, WP 7.6).

`FlowRig._report` used to wait for the snapshot with a fixed two seconds of
real time and, when the deadline passed short of the ledger's frame count,
returned the stale body: on a loaded box a test then graded a report the
engine had not finished writing. `SessionReporter.flush` (#668) is the awaitable
settle; `read` now calls it before the first `GET /api/reports/{id}`.

NAMED MUTANT. The `await night.engine.reporter.flush()` line deleted from
`FlowRig.read` (`_flow_night.py`). Observed:

    AssertionError: GET /api/reports/... was read before the reporter's
    pending writes were awaited: [...]
"""
from __future__ import annotations

from _flow_night import FlowRig, flow_rig, mosaic_flow  # noqa: F401 (fixture)
from _group_harness import group_store, ra_at  # noqa: F401 (fixture)
from astrodeck.sequence.report import SessionReporter

FLOW = mosaic_flow(ra_hours=ra_at(-2.0), plan="L 10, R 10", cycles=1)


async def test_read_awaits_the_reporter_flush_before_the_first_report_get(
        flow_rig, monkeypatch):
    rig: FlowRig = flow_rig
    night = await rig.night()
    fid = await rig.save_flow(FLOW)
    assert (await rig.run(fid)).status_code == 200
    await night.finish()

    events: list[str] = []
    real_flush = SessionReporter.flush

    async def _flush(self):
        await real_flush(self)
        events.append("flush")

    monkeypatch.setattr(SessionReporter, "flush", _flush)
    real_get = rig.client.get

    async def _get(url, *args, **kwargs):
        if str(url).startswith("/api/reports/"):
            events.append("report-get")
        return await real_get(url, *args, **kwargs)

    monkeypatch.setattr(rig.client, "get", _get)

    end = await rig.read(night, fid)

    assert end.reports, "premise: the night has a report to read"
    assert "report-get" in events, f"premise: read fetched the report: {events}"
    first_get = events.index("report-get")
    assert "flush" in events[:first_get], (
        f"GET /api/reports/... was read before the reporter's pending writes "
        f"were awaited: {events}")
