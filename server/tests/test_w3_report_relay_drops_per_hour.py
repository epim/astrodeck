# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The session report's ``relay_drops_per_hour`` (#521 fix 3): the owner's
suggested fix built in ``report.py``'s own build() -- "count drops per hour
in the night report, so a bad-network night is visible without log
forensics."

This file grades ``SessionReporter._relay_drops_per_hour`` / ``build()`` in
isolation, with ``relay_client.recent_drop_count`` and
``relay_client.current_client`` monkeypatched directly: the counting itself
(what a drop is, when it is logged) is test_w3_relay_drop_rate.py's job, and
re-driving a real ``RelayClient`` here as well would grade the same seam
twice while leaving the report's own arithmetic -- the rate, the minimum
span, the "no client ever ran" case -- untested.

Both mutants below were applied in a private copy of ``server/`` under the
session scratchpad (``w3-WP-28-mut``), restored byte-for-byte and verified
with a SHA-256 compare afterwards, never in the shared tree.
"""
from __future__ import annotations

import astrodeck.sequence.report as report_mod
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.report import SessionReporter


class _FakeClient:
    """Stands in for a live ``RelayClient`` just well enough that
    ``relay_client.current_client() is not None`` reads True."""


class _FrozenTime:
    """``report_mod.time`` with ``.time()`` pinned; every other attribute is
    the real module's. A plain ``monkeypatch.setattr(report_mod.time,
    "time", ...)`` would reach INTO the shared stdlib ``time`` module and
    freeze it process-wide (there is only one ``time`` module object, so
    every other import of it sees the same change) -- this instead replaces
    only the NAME ``report_mod.time`` resolves to, the way
    test_s7_report_final_retry_off_loop.py's ``_Clock`` does for sleeps."""

    def __init__(self, frozen: float) -> None:
        self._frozen = frozen

    def __getattr__(self, name):
        import time as _real_time
        return getattr(_real_time, name)

    def time(self) -> float:
        return self._frozen


def _no_client(monkeypatch) -> None:
    monkeypatch.setattr(report_mod.relay_client, "current_client",
                        lambda: None)


def _a_live_client(monkeypatch) -> None:
    monkeypatch.setattr(report_mod.relay_client, "current_client",
                        lambda: _FakeClient())


async def test_relay_drops_per_hour_divides_the_count_by_elapsed_hours(
        monkeypatch):
    """6 drops over the report's first 30 minutes answers 12/h: the count
    comes from ``relay_client.recent_drop_count``, called with the report's
    own ``started_at`` as its ``since``, and the rate is that count over the
    report's own elapsed hours -- never the wall clock's.

    RED under the mutant "relay_drops_per_hour removed from build()" (the
    ``build()`` call built without the ``relay_drops_per_hour=`` keyword,
    the shape before #521's fix 3 -- ``_relay_drops_per_hour`` itself is
    then never called), observed:
        E   AssertionError: recent_drop_count must be asked from the
            report's own start: {}
        E   assert None == 1000.0
    """
    _a_live_client(monkeypatch)
    seen: dict = {}

    def fake_count(since: float) -> int:
        seen["since"] = since
        return 6
    monkeypatch.setattr(report_mod.relay_client, "recent_drop_count",
                        fake_count)

    r = SessionReporter(SequencePlan(name="t"), report_id="drop-rate-basic",
                        started_at=1_000.0)
    r._ended_at = 1_000.0 + 1_800.0       # 30 minutes, well past the minimum
    rep = r.build()

    assert seen.get("since") == 1_000.0, (
        f"recent_drop_count must be asked from the report's own start: "
        f"{seen}")
    assert rep.relay_drops_per_hour == 12.0, rep.relay_drops_per_hour


async def test_relay_drops_per_hour_uses_now_while_the_report_is_still_open(
        monkeypatch):
    """A report with no ``ended_at`` yet (still running) measures its span
    to now, not to a frozen moment: the figure updates snapshot to
    snapshot, the way every other headline number does."""
    _a_live_client(monkeypatch)
    monkeypatch.setattr(report_mod.relay_client, "recent_drop_count",
                        lambda since: 3)
    monkeypatch.setattr(report_mod, "time", _FrozenTime(1_000.0 + 3_600.0))

    r = SessionReporter(SequencePlan(name="t"), report_id="drop-rate-open",
                        started_at=1_000.0)
    assert r._ended_at is None, "premise: the report has not finalized"
    rep = r.build()

    assert rep.relay_drops_per_hour == 3.0, rep.relay_drops_per_hour


async def test_relay_drops_per_hour_is_none_before_the_minimum_span(
        monkeypatch):
    """A report 10 s into its run answers None, not the 360/h one early
    drop would otherwise read as -- a number nobody would believe.

    RED under the mutant "minimum span removed" (the
    ``_DROPS_RATE_MIN_SPAN_S`` check dropped from ``_relay_drops_per_hour``,
    always computing a rate once a client is live), observed:
        E   assert 360.0 is None
    """
    _a_live_client(monkeypatch)
    monkeypatch.setattr(report_mod.relay_client, "recent_drop_count",
                        lambda since: 1)

    r = SessionReporter(SequencePlan(name="t"), report_id="drop-rate-early",
                        started_at=1_000.0)
    r._ended_at = 1_000.0 + 10.0
    rep = r.build()

    assert rep.relay_drops_per_hour is None, rep.relay_drops_per_hour


async def test_relay_drops_per_hour_is_none_when_remote_never_ran(
        monkeypatch):
    """No ``RelayClient`` has ever run in this process (the common LAN-only
    install): None, never 0 -- a 0 would read as a tunnel that stayed up
    rather than a question that does not apply. ``recent_drop_count`` is not
    even consulted.

    RED under the mutant "0 drops when no client" (``current_client() is
    None`` answers 0.0 instead of None, the shape that conflates "never
    tried" with "tried and clean"), observed:
        E   assert 0.0 is None
    """
    _no_client(monkeypatch)
    called = {"n": 0}

    def fake_count(since: float) -> int:
        called["n"] += 1
        return 0
    monkeypatch.setattr(report_mod.relay_client, "recent_drop_count",
                        fake_count)

    r = SessionReporter(SequencePlan(name="t"), report_id="drop-rate-none",
                        started_at=1_000.0)
    r._ended_at = 1_000.0 + 1_800.0
    rep = r.build()

    assert rep.relay_drops_per_hour is None, rep.relay_drops_per_hour
    assert called["n"] == 0, (
        f"recent_drop_count should not be asked with no live client: "
        f"{called}")
