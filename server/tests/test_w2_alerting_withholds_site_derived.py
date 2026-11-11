# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A line flagged ``site_derived`` pages with its words withheld (#166, #302,
#1165).

An admin configures the alert sinks (ntfy / webhook / Discord / Slack /
email), but the channel itself is not a principal ``view.site_derived`` can
be checked against -- a shared Discord room, say, has no capability of its
own. So the words of a flagged line never reach a sink. Until #1165 the whole
line was dropped, which made the flip-point cloud-hold stop silent while its
neighbours paged. Now a flagged warning or error pages at its level with a
FIXED sentence in place of its words: the operator learns that something
happened and where to read it (the night log on the rig), and the page
carries nothing computed from the site.

A line that has its own site-free wording (``EventBus.log_with_alert``)
pages that wording, once, and the fixed sentence is not added to it.

The shape of the case (``AlertDispatcher``, a bare sink, a ``_send`` spy,
``_on_bus_event`` fed a ``SimpleNamespace`` event) follows
``tests/test_resume_arm_no_light_backoff.py``'s
``test_the_alert_line_reaches_a_configured_sink`` (read for the pattern only).
"""
from __future__ import annotations

import types

import httpx

from _simhub import sim_hub  # noqa: F401

from astrodeck import events
from astrodeck.alerting import AlertDispatcher
from astrodeck.config import AlertSink
from astrodeck.sequence import SequenceEngine

#: Words that stand for a value computed from the site. None may reach a sink.
_SECRET = "HA+0.123h at azimuth 187 degrees"


def _dispatcher(monkeypatch):
    # ``events`` widened past the default (which subscribes "error" but not
    # "warning", `_sinks_for`) so a warning-level case is gated on the
    # ``site_derived`` check alone, not confounded by the ordinary events
    # filter also being the thing that would have dropped it.
    sink = AlertSink(id="phone", kind="ntfy", url="https://ntfy.example/x",
                     events=["warning", "error"])
    cfg = types.SimpleNamespace(alerts=[sink])
    disp = AlertDispatcher(types.SimpleNamespace(log=lambda *a: None),
                           lambda: cfg)
    sent: list[tuple[str, str, str]] = []
    everything: list[str] = []

    async def send(s, ev):
        sent.append((s.id, ev.type, ev.message))
        everything.append(repr({k: getattr(ev, k) for k in ev.__slots__}))
        return True, None

    monkeypatch.setattr(disp, "_send", send)
    disp.everything = everything
    return disp, sent


def _flagged(level: str, message: str, source: str = "sequence"):
    return types.SimpleNamespace(
        type="log", data={"level": level, "message": message,
                          "source": source, "site_derived": True})


async def test_a_flagged_warning_pages_with_the_words_withheld(monkeypatch):
    """A warning flagged ``site_derived`` reaches the sink at its level, the
    sink gets the fixed sentence, and nothing of the line's own text is in
    any field the sink was handed.

    MUTANT "the flag suppresses the page again" (the flagged line mapped to
    no alert in `_alert_for`'s ``log`` branch, the ``and not
    data.get(SITE_DERIVED_KEY)`` clause back): RED (observed):
        AssertionError: a site-derived warning did not page: []
    """
    disp, sent = _dispatcher(monkeypatch)
    await disp._on_bus_event(_flagged(
        "warning", f"T is at {_SECRET} - stopping tracking"))
    assert len(sent) == 1, f"a site-derived warning did not page: {sent}"
    sink_id, kind, message = sent[0]
    assert (sink_id, kind) == ("phone", "warning")
    assert "night log" in message, message
    assert not any(w in " ".join(disp.everything)
                   for w in ("HA+0.123h", "azimuth", "187", "stopping")), (
        disp.everything)


async def test_a_flagged_error_pages_with_the_words_withheld(monkeypatch):
    """The same, at error level, where a default sink would otherwise
    always be subscribed."""
    disp, sent = _dispatcher(monkeypatch)
    await disp._on_bus_event(_flagged("error", f"failed at {_SECRET}"))
    assert [(s[0], s[1]) for s in sent] == [("phone", "error")], sent
    assert "night log" in sent[0][2]
    assert _SECRET not in " ".join(disp.everything), disp.everything


async def test_a_flagged_info_line_still_does_not_page(monkeypatch):
    """An info line never paged (only warning and error log lines do), and
    flagging it does not change that."""
    disp, sent = _dispatcher(monkeypatch)
    await disp._on_bus_event(_flagged("info", "the meridian wait is over"))
    assert sent == [], sent


async def test_the_fixed_sentence_names_the_source_and_nothing_else(
        monkeypatch):
    """The page says which part of the system spoke (a source such as
    ``polar`` or ``sequence`` is a subsystem name, not a computed value), so
    two flagged warnings from different subsystems read differently."""
    disp, sent = _dispatcher(monkeypatch)
    await disp._on_bus_event(_flagged("warning", f"one {_SECRET}", "polar"))
    await disp._on_bus_event(_flagged("warning", f"two {_SECRET}", "sequence"))
    assert len(sent) == 2
    assert "polar" in sent[0][2] and "sequence" in sent[1][2], sent
    assert sent[0][2] != sent[1][2]


async def test_an_unflagged_warning_still_reaches_a_sink(monkeypatch):
    """Control, the known positive: an ordinary warning with no
    ``site_derived`` key at all is unaffected and still reaches the sink
    with its own words. Without this control, paging the fixed sentence for
    EVERY line would leave the tests above green for the wrong reason."""
    disp, sent = _dispatcher(monkeypatch)
    await disp._on_bus_event(types.SimpleNamespace(
        type="log", data={"level": "warning", "message": "an ordinary line",
                          "source": "sequence"}))
    assert sent == [("phone", "warning", "an ordinary line")], sent


async def test_a_falsely_flagged_warning_still_reaches_a_sink(monkeypatch):
    """A ``site_derived`` that is explicitly ``False`` reads as unflagged:
    the predicate reads truthiness, not mere key presence."""
    disp, sent = _dispatcher(monkeypatch)
    await disp._on_bus_event(types.SimpleNamespace(
        type="log", data={"level": "warning", "message": "an ordinary line",
                          "source": "sequence", "site_derived": False}))
    assert sent == [("phone", "warning", "an ordinary line")], sent


async def test_a_line_with_its_own_alert_wording_pages_once(monkeypatch):
    """`log_with_alert` publishes the flagged line AND its site-free
    wording. The sink gets the wording, once; the fixed sentence is not paged
    on top of it."""
    disp, sent = _dispatcher(monkeypatch)
    sub = events.bus.subscribe()
    try:
        events.bus.log_with_alert("warning", f"line with {_SECRET}", "safety",
                                  alert_message="parked for the dawn")
        evs = []
        while not sub.empty():
            evs.append(sub.get_nowait())
    finally:
        events.bus.unsubscribe(sub)
    for ev in evs:
        await disp._on_bus_event(ev)
    assert sent == [("phone", "warning", "parked for the dawn")], sent


# ------------------------------------------- the flip-point cloud-hold stop

async def test_the_flip_point_cloud_hold_stop_pages_a_site_free_class(
        sim_hub):
    """The flip-point stop of a cloud hold is flagged ``site_derived`` (its
    moment is the computed crossing). Driven through the real engine and
    the real bus into a webhook sink: exactly one page, it names the event
    class and the target, and the log line's own tail ("judges no sky until
    the mount can track the target again") is not what the sink was handed.

    MUTANT "the site-free class dropped" (the flip branch of `_hold_park`
    back to a plain flagged `bus.log`, so only the fixed sentence pages): RED
    (observed): AssertionError: {"type":"warning", ... "message":"A warning
    was logged by sequence that cannot be sent here because it depends on the
    site. Read it in the night log on the rig." ...}
    """
    sub = events.bus.subscribe()
    try:
        e = SequenceEngine(sim_hub)
        e._cfg = None
        await e._hold_park(
            "flip", "T has reached its meridian flip point and the flip "
                    "cannot be taken now")
        evs = []
        while not sub.empty():
            evs.append(sub.get_nowait())
    finally:
        events.bus.unsubscribe(sub)
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, text="ok")

    cfg = types.SimpleNamespace(alerts=[AlertSink(
        id="w", kind="webhook", url="https://example/hook",
        events=["warning", "error"], min_level="warning")])
    disp = AlertDispatcher(events.bus, lambda: cfg)
    disp._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        for ev in evs:
            alert = disp._alert_for(ev)
            if alert is not None:
                await disp._dispatch(alert)
    finally:
        await disp._client.aclose()
    assert len(seen) == 1, (
        f"the flip-point cloud-hold stop paged {len(seen)} times: {seen}")
    body = seen[0].content.decode()
    assert "meridian flip point" in body and "cloud hold" in body.lower(), body
    assert "night log" in body, body
    assert "judges no sky" not in body, body
