"""The adjust phase must say, WHILE IT IS HAPPENING, that it has stopped updating.

The loop already counted consecutive failed live updates — and kept the count in
the log until the session ended, up to 30 failures and half a minute later. In
between, the panel showed a confident number that had quietly stopped answering
the bolts. That is the one thing an adjust phase must not do: the entire
interaction is "turn it, watch the number move".

These drive ``_publish_stale`` directly against a recording session. The adjust
loop itself is exercised by ``test_polar_native.py``; what is pinned here is
that the count reaches the wire at all, and that it CLEARS.
"""
from __future__ import annotations

import pytest

from astrodeck.polar.native import _publish_stale


class _RecordingSession:
    """The publish surface the real session exposes, and nothing else."""

    def __init__(self) -> None:
        self.state: dict = {}
        self.published: list[dict] = []

    def _publish(self, **kw) -> None:
        self.state = {**self.state, **kw}
        self.published.append(dict(self.state))


def test_the_count_reaches_the_wire():
    s = _RecordingSession()
    _publish_stale(s, 3)
    assert s.published[-1]["stale_updates"] == 3, (
        "the panel cannot render a staleness it is never sent")


def test_the_message_says_what_is_wrong_not_just_a_number():
    """A bare counter on a panel is not actionable at 2am under red light."""
    s = _RecordingSession()
    _publish_stale(s, 4)
    msg = s.published[-1]["message"]
    assert "not updating" in msg.lower(), msg
    assert "4" in msg, msg


def test_recovery_clears_it():
    """A warning that appears and never clears trains the operator to ignore it."""
    s = _RecordingSession()
    _publish_stale(s, 5)
    _publish_stale(s, 0)
    assert s.published[-1]["stale_updates"] == 0
    assert "not updating" not in s.published[-1]["message"].lower()


def test_it_stays_in_the_adjust_phase_and_does_not_fake_a_reading():
    """It must not publish an error figure.

    A failed update has no new error to report; carrying the staleness on
    ``_publish_error`` would have republished the STALE reading as though it
    were freshly measured — the same defect wearing the fix's clothes.
    """
    s = _RecordingSession()
    _publish_stale(s, 2)
    last = s.published[-1]
    assert last["phase"] == "adjusting"
    assert last["state"] == "running"
    assert "az_error" not in last and "alt_error" not in last, (
        f"a failed update published an error reading: {last}")
