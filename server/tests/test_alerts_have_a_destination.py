"""A run whose failures can reach nobody must say so, once, at the start.

The dispatcher, the sinks and the wall-clock dead-man's-switch are all built and
running. The gap the unattended-guards audit put first was configuration: a rig
with ``alerts: []`` and an empty ``deadman_url`` ends every escalation path in a
log line on a machine nobody is watching.

The run still proceeds. An unattended destination is not part of a working rig
and refusing to image without one would be absurd. But a silence that looks
identical to "everything is fine" is the thing being fixed - same shape as the
armed-safety-with-no-monitor warning this sits beside.

TWO DEFECTS FOUND 2026-08-15 WHILE AUDITING WHAT THE RIG ACTUALLY HAS.

1. The deadman half could never be true. The check read
   ``getattr(cfg.escalation, "deadman_url", "")``, and ``deadman_url`` is a
   field of ``AppConfig``, not of ``EscalationConfig`` - confirmed against the
   rig's own config, whose escalation block has no such key. That ``getattr``
   returned "" for every rig that has ever run, so an operator WITH a deadman
   and no sinks was told "no dead-man's-switch URL is set", which is false, and
   sent to configure a thing they had already configured.

   THE TEST THAT SHOULD HAVE CAUGHT IT IS WHY IT SURVIVED. The old helper built
   the config by hand - ``type("Esc", (), {"deadman_url": deadman})()`` - so
   ``test_a_deadman_url_alone_is_enough`` passed against a shape production
   never has. This file now builds a real ``AppConfig``; a double that carries
   the field cannot prove the field is read from the right object.

2. The check was all-or-nothing: ``if sinks or deadman: return``. Sinks and the
   deadman do not cover the same failure and are not substitutes. A sink is a
   push FROM this process, so the one failure it can never report is the
   process's own death; the deadman is the only mechanism where ABSENCE is the
   signal, so it is the only one that survives the box dying. The rig has a
   verified ntfy sink and no deadman, so the warning was silent about precisely
   the gap that has bitten this rig twice.
"""
from __future__ import annotations

import pytest

from astrodeck.config import AlertSink, AppConfig
from astrodeck.events import bus
from astrodeck.sequence.engine import SequenceEngine


def _engine(*, alerts=None, deadman: str = "", watchdog_s: int = 0):
    """An engine carrying a REAL AppConfig.

    Built by hand only in the sense that no run is started: the warning is a
    pure function of config plus a once-per-run latch. The config itself is the
    genuine model, so a field read off the wrong object fails here.
    """
    eng = SequenceEngine.__new__(SequenceEngine)
    eng._warned_no_destination = False
    cfg = AppConfig(deadman_url=deadman)
    cfg.escalation.no_progress_watchdog_s = watchdog_s
    cfg.alerts = list(alerts or [])
    eng._cfg = cfg
    return eng


def _sink(enabled: bool = True) -> AlertSink:
    return AlertSink(id="s1", kind="ntfy", enabled=enabled,
                     url="https://ntfy.example/topic")


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


class TestTheConfigIsReadFromTheRightObject:
    """The regression guard for defect 1. These would all pass against a double
    that happened to carry ``deadman_url`` in the wrong place."""

    def test_deadman_url_lives_on_the_app_config_not_on_escalation(self):
        """State the premise as an assertion so a later model change that moves
        the field makes this file fail loudly instead of quietly re-breaking."""
        cfg = AppConfig()
        assert hasattr(cfg, "deadman_url")
        assert not hasattr(cfg.escalation, "deadman_url"), (
            "deadman_url is on EscalationConfig again - the engine's warning "
            "and the dispatcher now disagree about where it lives")

    def test_a_configured_deadman_is_actually_seen(self):
        """The bug, stated directly. With a deadman and no sinks the rig has a
        destination, and must not be told it has none."""
        eng = _engine(deadman="https://hc-ping.example/abc")
        msgs = _warnings(eng._warn_if_nothing_can_report_a_failure)
        assert not any("no dead-man's-switch URL is set" in m for m in msgs), (
            f"a configured dead-man's-switch is reported as absent: {msgs}")


class TestNothingAtAll:
    def test_a_run_with_no_sinks_and_no_deadman_says_so(self):
        eng = _engine()
        msgs = _warnings(eng._warn_if_nothing_can_report_a_failure)
        assert any("nothing can report a failure" in m for m in msgs), msgs

    def test_it_also_names_the_watchdog_when_that_is_off_too(self):
        """A different silence: 0 means the no-progress task never starts, so a
        run that stops producing frames is not merely unreported, it is
        undetected."""
        eng = _engine(watchdog_s=0)
        msgs = _warnings(eng._warn_if_nothing_can_report_a_failure)
        assert any("watchdog" in m for m in msgs), msgs

    def test_an_armed_watchdog_is_not_complained_about(self):
        eng = _engine(watchdog_s=600)
        msgs = _warnings(eng._warn_if_nothing_can_report_a_failure)
        assert any("nothing can report a failure" in m for m in msgs), msgs
        assert not any("watchdog" in m for m in msgs), msgs

    def test_a_disabled_sink_does_not_count(self):
        """A sink that is switched off delivers exactly as much as no sink."""
        eng = _engine(alerts=[_sink(enabled=False)])
        msgs = _warnings(eng._warn_if_nothing_can_report_a_failure)
        assert any("nothing can report a failure" in m for m in msgs), msgs


class TestTheHalfThatIsMissingIsNamed:
    """Defect 2. The two mechanisms cover different failures, so having one is
    not having both, and the operator is told WHICH half they are short."""

    def test_sinks_without_a_deadman_still_warns_about_the_process_dying(self):
        """The rig's exact configuration on 2026-08-15: one verified ntfy sink,
        no deadman. Alerts are pushed BY this process, so its own death is the
        one failure they cannot report - and that is the failure that has
        actually happened here."""
        eng = _engine(alerts=[_sink()], watchdog_s=600)
        msgs = _warnings(eng._warn_if_nothing_can_report_a_failure)
        assert msgs, (
            "a rig with alerts but no dead-man's-switch is told nothing, so the "
            "one failure its alerts cannot report goes unmentioned")
        assert any("dead-man" in m for m in msgs), msgs

    def test_that_warning_does_not_claim_alerts_are_missing(self):
        """Over-reporting costs what under-reporting costs. Someone who has
        configured ntfy must not be told they have no alerts."""
        eng = _engine(alerts=[_sink()], watchdog_s=600)
        msgs = _warnings(eng._warn_if_nothing_can_report_a_failure)
        assert not any("no alert sinks are configured" in m for m in msgs), msgs

    def test_a_deadman_without_sinks_warns_that_detail_is_missing(self):
        """The mirror image. A missed ping pages you, but says only that the rig
        went quiet - never which thing failed."""
        eng = _engine(deadman="https://hc-ping.example/abc", watchdog_s=600)
        msgs = _warnings(eng._warn_if_nothing_can_report_a_failure)
        assert msgs and any("detail" in m or "which" in m for m in msgs), msgs

    def test_both_configured_is_the_only_silent_case(self):
        """The positive control. Without it every assertion above is satisfied
        by a method that warns unconditionally."""
        eng = _engine(alerts=[_sink()], deadman="https://hc-ping.example/abc",
                      watchdog_s=600)
        assert _warnings(eng._warn_if_nothing_can_report_a_failure) == []


class TestItIsSaidOnce:
    def test_it_is_said_once_per_run_not_once_per_frame(self):
        """A configuration fact, not an event. Repeating it would bury the
        night's real warnings."""
        eng = _engine()
        first = _warnings(eng._warn_if_nothing_can_report_a_failure)
        second = _warnings(eng._warn_if_nothing_can_report_a_failure)
        assert first and second == []

    @pytest.mark.parametrize("kw", [
        {},
        {"alerts": [_sink()]},
        {"deadman": "https://hc-ping.example/abc"},
    ])
    def test_the_latch_is_set_whichever_branch_fired(self, kw):
        """The latch must not depend on which half was missing, or a partly
        configured rig repeats its warning every frame."""
        eng = _engine(**kw)
        _warnings(eng._warn_if_nothing_can_report_a_failure)
        assert eng._warned_no_destination is True
