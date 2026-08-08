"""A run whose failures can reach nobody must say so, once, at the start.

The unattended-guards audit put this first: "alerts + deadman first, because
without them no other failure is even observable". The dispatcher, the sinks and
the wall-clock dead-man's-switch are all built and running — the rig simply has
``alerts: []`` and an empty ``deadman_url``, so every escalation path the engine
can take ends in a log line on a machine nobody is watching.

The run still proceeds. An unattended destination is not part of a working rig
and refusing to image without one would be absurd. But a silence that looks
identical to "everything is fine" is the thing being fixed — same shape as the
armed-safety-with-no-monitor warning this sits beside.
"""
from __future__ import annotations

import pytest

from astrodeck.events import bus
from astrodeck.sequence.engine import SequenceEngine


class _Sink:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled


def _engine(*, alerts=None, deadman: str = "", watchdog_s: int = 0):
    """An engine with only the config surface this warning reads.

    Built by hand rather than through a real run: the warning is a pure function
    of config plus a once-per-run latch, and a full sequence would drag in a rig.
    """
    eng = SequenceEngine.__new__(SequenceEngine)
    eng._warned_no_destination = False
    esc = type("Esc", (), {"deadman_url": deadman,
                           "no_progress_watchdog_s": watchdog_s})()
    eng._cfg = type("Cfg", (), {"escalation": esc,
                                "alerts": alerts if alerts is not None else []})()
    return eng


def _warnings(fn) -> list[str]:
    q = bus.subscribe()
    try:
        fn()
        out = []
        while not q.empty():
            ev = q.get_nowait()
            if ev.type == "log" and ev.data.get("level") == "warning":
                out.append(ev.data.get("message", ""))
        return out
    finally:
        bus.unsubscribe(q)


def test_a_run_with_no_sinks_and_no_deadman_says_so():
    eng = _engine()
    msgs = _warnings(eng._warn_if_nothing_can_report_a_failure)
    assert any("nothing can report a failure" in m for m in msgs), msgs


def test_it_also_names_the_watchdog_when_that_is_off_too():
    """A different silence: 0 means the no-progress task never starts, so a run
    that stops producing frames is not merely unreported, it is undetected."""
    eng = _engine(watchdog_s=0)
    msgs = _warnings(eng._warn_if_nothing_can_report_a_failure)
    assert any("watchdog" in m for m in msgs), msgs


def test_an_armed_watchdog_is_not_complained_about():
    eng = _engine(watchdog_s=600)
    msgs = _warnings(eng._warn_if_nothing_can_report_a_failure)
    assert any("nothing can report a failure" in m for m in msgs), msgs
    assert not any("watchdog" in m for m in msgs), msgs


def test_one_configured_sink_is_enough_to_stay_quiet():
    """The positive control. Without it this could warn unconditionally and
    every test above would still pass."""
    eng = _engine(alerts=[_Sink()])
    assert _warnings(eng._warn_if_nothing_can_report_a_failure) == []


def test_a_deadman_url_alone_is_enough():
    eng = _engine(deadman="https://hc-ping.example/abc")
    assert _warnings(eng._warn_if_nothing_can_report_a_failure) == []


def test_a_disabled_sink_does_not_count():
    """A sink that is switched off delivers exactly as much as no sink at all."""
    eng = _engine(alerts=[_Sink(enabled=False)])
    msgs = _warnings(eng._warn_if_nothing_can_report_a_failure)
    assert any("nothing can report a failure" in m for m in msgs), msgs


def test_it_is_said_once_per_run_not_once_per_frame():
    """A configuration fact, not an event. Repeating it would bury the night's
    real warnings."""
    eng = _engine()
    first = _warnings(eng._warn_if_nothing_can_report_a_failure)
    second = _warnings(eng._warn_if_nothing_can_report_a_failure)
    assert first and second == []
