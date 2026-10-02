# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``relay_client.recent_drop_count`` (#521 fix 3): the one signal the
session report's drops-per-hour figure reads.

Fix 1 (the relay logs a tunnel's end) and fix 2 (the rig logs a link check
after every drop) shipped in 7be51e5a (#189, H4); this is fix 3, deferred at
the time: "count drops per hour in the night report, so a bad-network night
is visible without log forensics" (#521). test_h4_relay_link_check.py
already drives the link check itself end to end (the probes, the bound, the
wording); this file adds only the bookkeeping fix 3 needed -- a timestamp
kept at the moment each "relay link check" line is written, and the
module-level read of it report.py uses.

No test here touches a real socket: ``_after_drop`` is called directly, the
way the supervisor calls it right after logging "relay connection lost",
with the dial state it would already hold set by hand.

Both mutants below were applied in a private copy of ``server/`` under the
session scratchpad (``w3-WP-28-mut``), restored byte-for-byte and verified
with a SHA-256 compare afterwards, never in the shared tree.
"""
from __future__ import annotations

import asyncio
import time

import astrodeck.remote.relay_client as rc
from _deadline import wait_until  # rootdir-relative
from astrodeck.remote.relay_client import LinkProbes, RelayClient


async def _noop_app(scope, receive, send):  # pragma: no cover - never reached
    pass


def _client_with_a_pending_drop(*, probes: LinkProbes | None) -> RelayClient:
    """A ``RelayClient`` with its dial state set as if gen 1 had just
    dropped -- what the supervisor's except branch already holds when it
    calls ``_after_drop`` -- with no test touching a real socket."""
    client = RelayClient(_noop_app, lambda: None, connect=None,
                         link_probes=probes)
    client._dial_host = "relay.test"
    client._last_frame_at = time.monotonic() - 1.0
    client._dropped_at = time.monotonic()
    return client


async def test_recent_drop_count_counts_a_logged_link_check(monkeypatch):
    """A drop whose check completes is counted from the moment it logged,
    bounded by ``since``: counted at the drop's own start time, not counted
    a moment after it.

    RED under the mutant "note_drop not called from _check_link" (the call
    in :meth:`RelayClient._check_link` removed, the way fix 3 would be
    silently incomplete if only the "skipped" branch recorded a drop),
    observed:
        E   assert 0 == 1
    """
    monkeypatch.setattr(rc, "_current_client", None)
    before = time.time()
    probes = LinkProbes(gateway=lambda t: True, dns=lambda host, t: True)
    client = _client_with_a_pending_drop(probes=probes)
    rc._current_client = client
    client._after_drop(1)
    # A wall-clock deadline (#610, WP-68 remainder): 300 x sleep(0.01) is
    # 3.0 s on Linux but longer on Windows (sleep rounds up to the ~15.6 ms
    # timer there), so a round count gives the two platforms different real
    # patience.
    ok = await wait_until(
        lambda: client._link_check is not None and client._link_check.done(),
        timeout_s=3.0, interval_s=0.01)
    assert ok, "premise: the link check finished"

    assert rc.recent_drop_count(before) == 1
    assert rc.recent_drop_count(time.time() + 10.0) == 0, (
        "a since in the future must not count a drop already in the past")


async def test_recent_drop_count_counts_a_skipped_check_too(monkeypatch):
    """A drop that lands while the previous check is still out logs the
    "skipped" line instead of running new probes, and still counts: the
    rig's internet can drop faster than ``_LINK_CHECK_BOUND_S``, and #521's
    own 2026-09-22 cluster had drops 10 s apart.

    RED under the mutant "note_drop not called from the skipped branch"
    (the call in :meth:`RelayClient._after_drop`'s early "skipped" return
    removed), observed:
        E   assert 0 == 1
    """
    monkeypatch.setattr(rc, "_current_client", None)
    before = time.time()
    probes = LinkProbes(gateway=lambda t: True, dns=lambda host, t: True)
    client = _client_with_a_pending_drop(probes=probes)
    rc._current_client = client
    # A check already in flight (never completing here): the next drop's
    # _after_drop must take the "skipped" branch, not start a second one.
    client._link_check = asyncio.get_running_loop().create_future()
    client._after_drop(2)
    assert rc.recent_drop_count(before) == 1


async def test_a_drop_with_no_dial_host_logs_and_counts_nothing(monkeypatch):
    """A failure before the dial resolved a host (a config that never
    validated) is not a link problem and is not counted: ``_after_drop``
    returns before either call site that notes a drop.

    RED under the mutant "note_drop unconditional" (moved to the top of
    ``_after_drop``, before the ``host`` guard), observed:
        E   assert 1 == 0
    """
    monkeypatch.setattr(rc, "_current_client", None)
    before = time.time()
    client = RelayClient(_noop_app, lambda: None, connect=None,
                         link_probes=LinkProbes(gateway=lambda t: True,
                                                dns=lambda host, t: True))
    assert client._dial_host is None, "premise: no dial ever resolved a host"
    rc._current_client = client
    client._after_drop(1)
    assert rc.recent_drop_count(before) == 0


async def test_recent_drop_count_is_zero_with_no_live_client(monkeypatch):
    """No client has ever run (a LAN-only install, or none built this
    process): the count is 0, never a raise, so a reporting figure never
    costs the report that reads it."""
    monkeypatch.setattr(rc, "_current_client", None)
    assert rc.recent_drop_count(0.0) == 0
