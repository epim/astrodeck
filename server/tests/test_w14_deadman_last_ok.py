# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#125: the dead-man counts as watched only after a ping has been ACCEPTED.

``health()["deadman"]`` said ``healthy`` for a URL that had never been pinged:
``healthy`` is "no failure has been warned about", and nothing has been warned
about before the first request leaves. So a freshly pasted URL, a typo, and a
dead monitor all read as a green "Pinging" until the first attempt failed, and
``rig_precheck`` and the Settings badge could say "configured" about a rig no
external service had ever heard from. ``last_ok_age_s`` is the missing fact:
seconds since the monitor last answered 2xx/3xx, or None if it never has (for
the URL that is configured NOW).

Booleans and ages only: the URL carries a per-ping secret, so none of these
cases may be able to make it appear in the health block.

MUTATIONS RUN (each from a byte backup inside the worktree, restored
byte-identically, mutant text grepped out afterwards):

  m1, stamp ``_last_deadman_ok`` at the top of ``_deadman_ping_now``, before
  the request (after the blocked-url return, before the try). 3 failed, 7
  passed: test_a_404_a_500_and_a_transport_error_leave_it_none[404]
  (``assert 0.0 is None``), [500] (``assert 0.01600000000325963 is None``) and
  [transport-error] (``assert 0.0 is None``). The ages are the Windows clock's
  15.6 ms granularity showing through; the test only ever asks None or not.

  m1b, stamp it after the request but ABOVE the 2xx/3xx status check. 2 failed:
  test_a_404_a_500_and_a_transport_error_leave_it_none[404] and [500], each
  ``assert 0.0 is None``; the transport-error case still passes because it
  never reaches the stamp, which is why the status cases are in the matrix.

  m4, health() stops comparing the url and reports the stamp for whatever url
  is configured now. 1 failed, 9 passed:
  test_changing_or_clearing_the_url_forgets_the_old_ones_ping,
  ``assert 0.0 is None`` -- a different url read as answering from the
  previous url's ping.
"""
from __future__ import annotations

import json

import httpx
import pytest

from astrodeck.alerting import AlertDispatcher
from astrodeck.config import AppConfig
from astrodeck.events import EventBus

PING = "https://hc-ping.example/3f9a1c2e-SECRET-PING-TOKEN"


def _dispatcher(cfg: AppConfig, handler):
    disp = AlertDispatcher(EventBus(), lambda: cfg)
    disp._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return disp


def _ok(_req: httpx.Request) -> httpx.Response:
    return httpx.Response(200)


async def test_none_before_any_ping_and_a_number_after_one_accepted_ping():
    cfg = AppConfig(deadman_url=PING)
    disp = _dispatcher(cfg, _ok)
    try:
        dm = disp.health()["deadman"]
        assert dm["configured"] is True
        assert dm["last_ok_age_s"] is None, (
            "a configured URL nothing has answered is not 'watched'")
        await disp._deadman_ping_now()
        age = disp.health()["deadman"]["last_ok_age_s"]
        assert age is not None and 0.0 <= age < 30.0, age
    finally:
        await disp._client.aclose()


async def test_the_age_is_measured_from_the_last_accepted_ping():
    """Not a frozen number: pull the stamp 100 s into the past and the reported
    age grows by 100 s. (No sleep, and no reliance on the clock's resolution.)"""
    disp = _dispatcher(AppConfig(deadman_url=PING), _ok)
    try:
        await disp._deadman_ping_now()
        first = disp.health()["deadman"]["last_ok_age_s"]
        disp._last_deadman_ok -= 100.0
        second = disp.health()["deadman"]["last_ok_age_s"]
        assert second - first >= 99.0, (first, second)
    finally:
        await disp._client.aclose()


@pytest.mark.parametrize("answer", [
    httpx.Response(404),
    httpx.Response(500),
    httpx.ConnectError("no route to host"),
], ids=["404", "500", "transport-error"])
async def test_a_404_a_500_and_a_transport_error_leave_it_none(answer):
    """The monitor answered NO (a deleted check, an outage) or did not answer
    at all: that is not a ping accepted, so it is not 'watched'."""
    def handler(_req):
        if isinstance(answer, Exception):
            raise answer
        return answer

    disp = _dispatcher(AppConfig(deadman_url=PING), handler)
    try:
        await disp._deadman_ping_now()
        dm = disp.health()["deadman"]
        assert dm["last_ok_age_s"] is None
        assert dm["healthy"] is False, "the existing key still reports the failure"
    finally:
        await disp._client.aclose()


async def test_a_blocked_url_leaves_it_none():
    """A URL that can never be a monitor is skipped without a request."""
    disp = _dispatcher(AppConfig(deadman_url="http://0.0.0.0/ping"), _ok)
    try:
        await disp._deadman_ping_now()
        assert disp.health()["deadman"]["last_ok_age_s"] is None
    finally:
        await disp._client.aclose()


async def test_no_url_means_no_ping_and_no_age():
    disp = _dispatcher(AppConfig(deadman_url=""), _ok)
    try:
        await disp._deadman_ping_now()
        dm = disp.health()["deadman"]
        assert dm["configured"] is False
        assert dm["last_ok_age_s"] is None
    finally:
        await disp._client.aclose()


async def test_a_later_failure_keeps_the_last_accepted_age_and_clears_healthy():
    """Both facts are true at once and the reader needs both: the monitor
    answered N s ago, and the most recent attempt failed."""
    state = {"up": True}

    def handler(_req):
        return httpx.Response(200 if state["up"] else 404)

    disp = _dispatcher(AppConfig(deadman_url=PING), handler)
    try:
        await disp._deadman_ping_now()
        state["up"] = False
        await disp._deadman_ping_now()
        dm = disp.health()["deadman"]
        assert dm["healthy"] is False
        assert dm["last_ok_age_s"] is not None
    finally:
        await disp._client.aclose()


async def test_changing_or_clearing_the_url_forgets_the_old_ones_ping():
    """An accepted ping is an accepted ping to THAT url. Paste a typo over a
    working URL and, until the next attempt, the old stamp would still read
    'answering' for a monitor that has never heard of the new one."""
    cfg = AppConfig(deadman_url=PING)
    disp = _dispatcher(cfg, _ok)
    try:
        await disp._deadman_ping_now()
        assert disp.health()["deadman"]["last_ok_age_s"] is not None
        cfg.deadman_url = "https://hc-ping.example/a-different-check"
        assert disp.health()["deadman"]["last_ok_age_s"] is None
        cfg.deadman_url = ""
        assert disp.health()["deadman"]["last_ok_age_s"] is None
        cfg.deadman_url = PING              # back to the one that answered
        assert disp.health()["deadman"]["last_ok_age_s"] is not None
    finally:
        await disp._client.aclose()


async def test_the_health_block_cannot_carry_the_url():
    """The ping secret rides in the path; the block is read by anyone with
    view.status, and rig_precheck prints from it."""
    disp = _dispatcher(AppConfig(deadman_url=PING), _ok)
    try:
        await disp._deadman_ping_now()
        blob = json.dumps(disp.health())
        for fragment in ("SECRET-PING-TOKEN", "hc-ping", "3f9a1c2e"):
            assert fragment not in blob, f"the health block carried {fragment!r}"
        assert set(disp.health()["deadman"]) == {
            "configured", "healthy", "last_ping_age_s", "last_ok_age_s"}
    finally:
        await disp._client.aclose()
