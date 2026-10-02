# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The session report names a set-aside panel AND its reason (#524, spec 6.7:
"The report names every set-aside panel and its reason").

``SequenceEngine._set_panel_aside`` already held the reason -- it records it
in ``Session.set_aside``, logs it in a warning, and publishes it in
``group.set_aside`` -- and ``SessionReporter.mark_skipped`` threw it away,
writing only ``"skipped <name>"`` into the report's ``safety_events``. The
report is the morning-after record of the night: it said a panel was skipped
but not why a centring failure, a guider fault, a floor stop or a closed
window told it apart from any other skip.

``mark_skipped`` now takes an optional ``reason`` and appends it to the text
it already wrote, so a caller with nothing to add -- none exist in this
engine any more; every call site was updated to pass one -- still gets
today's bare line (tested here as the CONTROL).

test_group_set_aside_reported.py and test_s7_sim_solve_failure.py pin
TODAY'S bare text exactly (``'skipped M31 2-2'`` and a full ``safety_events``
dict with no reason key) and are a DELIBERATE re-pin this fix owes them,
named in issue #524's own adjudication comment; they are not this WP's
files and are listed in its return rather than edited.
"""
from __future__ import annotations

import astrodeck.hub as hub_module
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.report import SessionReporter
from _group_harness import GROUP_NAME, grid_plan, group_hub, group_store  # noqa: F401


class _Target:
    def __init__(self, name: str) -> None:
        self.name = name


def _reporter(tmp_path, monkeypatch) -> SessionReporter:
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    return SessionReporter(SequencePlan(name="t"), report_id="r")


def test_mark_skipped_with_a_reason_carries_it_in_the_report(tmp_path, monkeypatch):
    """The primary case (#524): a reason is appended to the skip's own text.

    Mutant "reason dropped from the report" (the named mutant issue #524
    itself asks for: ``mark_skipped`` takes ``reason`` but never uses it):
    RED --
        AssertionError: 'skipped M31 2-2' does not carry the reason:
        'centring failed on 2-2 on 3 consecutive visits'
    """
    r = _reporter(tmp_path, monkeypatch)
    r.mark_skipped(_Target("M31 2-2"),
                   "centring failed on 2-2 on 3 consecutive visits")
    events = r.build().safety_events
    assert len(events) == 1, events
    assert events[0]["action"] == "skip", events[0]
    assert events[0]["reason"] == (
        "skipped M31 2-2: centring failed on 2-2 on 3 consecutive visits"), (
        f"the reason is not carried: {events[0]['reason']!r}")


def test_control_mark_skipped_with_no_reason_keeps_the_bare_line(
        tmp_path, monkeypatch):
    """CONTROL. ``reason=None`` (the default) writes exactly today's text, so
    a reader that pins the bare form -- every call site in this engine now
    passes a reason, but the method itself must not require one -- still
    matches.
    """
    r = _reporter(tmp_path, monkeypatch)
    r.mark_skipped(_Target("M31 2-2"))
    events = r.build().safety_events
    assert events[0]["reason"] == "skipped M31 2-2", events[0]


async def test_a_panel_set_aside_for_the_night_reports_its_reason(
        group_hub, monkeypatch):
    """End to end: the real engine's `_set_panel_aside` (the #524 call site
    named in the issue) now passes the reason it already held to
    `mark_skipped`, and the report carries it. The scenario is
    test_group_set_aside_reported.py's own (2-2 never centres); only the
    assertion changes, from the bare name to the name AND the reason.

    Not pinned to WHICH reason (the D-03 held-pass escalation, backlog
    ruling D-03 owner-approved 2026-09-30, reaches 2-2's actual final
    set-aside here, not the plainer centring-streak one the older comment
    in test_group_set_aside_reported.py describes): this WP changes
    `mark_skipped`, not the group rules that pick the reason text.
    """
    def goto(who, n, result):
        if who == f"{GROUP_NAME} 2-2":
            return {**result, "centered": False, "error_arcmin": None}
        return result

    from test_group_set_aside_reported import _night  # the shared harness wiring

    night = await _night(group_hub, monkeypatch, grid_plan(), goto=goto)
    assert night.done, night.trace[-3:]
    events = night.engine.reporter.build().safety_events
    skips = [e for e in events if e.get("action") == "skip"]
    assert len(skips) == 1, skips
    bare = f"skipped {GROUP_NAME} 2-2"
    assert skips[0]["reason"].startswith(f"{bare}: "), (
        f"the set-aside's own reason is not on the report entry: {skips[0]}")
    assert skips[0]["reason"] != bare, skips[0]
    assert len(skips[0]["reason"]) > len(bare) + 2, skips[0]
