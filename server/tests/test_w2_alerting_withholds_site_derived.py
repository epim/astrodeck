# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A line flagged ``site_derived`` never reaches an external alert sink
(#166, #302, backlog WP-11 (a), owner-approved 2026-09-30).

An admin configures the alert sinks (ntfy / webhook / Discord / Slack /
email), but the channel itself is not a principal ``view.site_derived`` can
be checked against -- a shared Discord room, say, has no capability of its
own. `AlertDispatcher._alert_for` forwards every warning- and error-level
``log`` event to the configured sinks with no check of the flag, so once
the engine started flagging a line (#166's own fix) its moment -- a flip
taken at the crossing, a flip-point idle hold -- would reach every sink
unconditionally, the site leak this plan exists to close, by a channel
``api.redact`` never touches.

The shape of the case (``AlertDispatcher``, a bare sink, a ``_send`` spy,
``_on_bus_event`` fed a ``SimpleNamespace`` event) follows
``tests/test_resume_arm_no_light_backoff.py``'s
``test_the_alert_line_reaches_a_configured_sink`` (not owned by this work
package, read for the pattern only; nothing there is imported or edited).
"""
from __future__ import annotations

import types

from astrodeck.alerting import AlertDispatcher
from astrodeck.config import AlertSink


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

    async def send(s, ev):
        sent.append((s.id, ev.type, ev.message))
        return True, None

    monkeypatch.setattr(disp, "_send", send)
    return disp, sent


async def test_a_flagged_warning_never_reaches_a_sink(monkeypatch):
    """A warning flagged ``site_derived`` (the idle park-hold's flip-point
    line, in the engine's own words) is withheld from every sink: the
    admin who configured the sink is entitled to it (an operator or admin
    holds ``view.site_derived``), but the sink -- a webhook URL, a Discord
    room -- is not a principal the cap can be checked against, so the safe
    default is to never forward one.

    MUTANT "the flag is never read" (the ``not data.get(SITE_DERIVED_KEY)``
    clause dropped from `_alert_for`'s ``log`` branch): RED (observed):
        AssertionError: a site-derived line reached a sink: [('phone',
        'warning', 'T has reached its meridian flip point and the flip
        cannot be taken now - stopping tracking')]
    """
    disp, sent = _dispatcher(monkeypatch)
    await disp._on_bus_event(types.SimpleNamespace(
        type="log", data={
            "level": "warning",
            "message": "T has reached its meridian flip point and the "
                       "flip cannot be taken now - stopping tracking",
            "source": "sequence", "site_derived": True}))
    assert sent == [], f"a site-derived line reached a sink: {sent}"


async def test_a_flagged_error_never_reaches_a_sink(monkeypatch):
    """The same, at error level, where a default sink would otherwise
    always be subscribed (``test_resume_arm_no_light_backoff.py``'s own
    control reads a default sink as error-only)."""
    disp, sent = _dispatcher(monkeypatch)
    await disp._on_bus_event(types.SimpleNamespace(
        type="log", data={
            "level": "error", "message": "something site-timed failed",
            "source": "sequence", "site_derived": True}))
    assert sent == [], f"a site-derived error reached a sink: {sent}"


async def test_an_unflagged_warning_still_reaches_a_sink(monkeypatch):
    """Control, the known positive: an ordinary warning with no
    ``site_derived`` key at all -- every line logged before this plan,
    and every line this plan leaves unflagged (the two "nothing flipped"
    branches, hub's own "stopping guiding and re-slewing") -- is
    unaffected and still reaches the sink, exactly as
    ``test_resume_arm_no_light_backoff.py`` already pins for the
    no-``site_derived``-key case. Without this control, dropping EVERY
    line (a stuck-True mutant on the new clause, or the level check
    itself) would leave the pair above green for the wrong reason."""
    disp, sent = _dispatcher(monkeypatch)
    await disp._on_bus_event(types.SimpleNamespace(
        type="log", data={"level": "warning", "message": "an ordinary line",
                          "source": "sequence"}))
    assert sent == [("phone", "warning", "an ordinary line")], sent


async def test_a_falsely_flagged_warning_still_reaches_a_sink(monkeypatch):
    """A truthy-but-false ``site_derived`` (explicitly ``False``, the
    default `bus.log` writes when a caller passes the keyword at all)
    still reaches the sink: the predicate reads truthiness, not mere key
    presence, matching ``events.is_site_derived``'s own contract."""
    disp, sent = _dispatcher(monkeypatch)
    await disp._on_bus_event(types.SimpleNamespace(
        type="log", data={"level": "warning", "message": "an ordinary line",
                          "source": "sequence", "site_derived": False}))
    assert sent == [("phone", "warning", "an ordinary line")], sent
