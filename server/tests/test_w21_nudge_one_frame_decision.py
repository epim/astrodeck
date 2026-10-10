# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A nudge decides the mount's coordinate frame ONCE (WP-196, #962).

``POST /api/mount/nudge`` reads the mount's position, brings it to J2000 with
``Hub.from_mount_frame``, adds the offset, and slews the result through the
plain goto, which converts to the mount's frame with ``Hub.to_mount_frame``.
Those two conversions decided the mount's frame separately, by different
rules: the read honours the EquatorialSystem reprobe hold-off (#861 N6) and,
since #934, gives up on a probe after ``STATUS_DEVICE_READ_TIMEOUT_S``; the
slew clears the hold-off and always asks, without a bound. For a mount that
reports J2000 whose first EquatorialSystem answer fails or is slow, or that
sits inside the 60 s hold-off after a failed probe, the read assumed JNOW and
precessed the start position backwards while the slew then got the definite
J2000 answer and sent the target unprecessed. The mount landed 0.04 to 0.38
deg from where the nudge asked.

THE FIX. The route asks once, the way a slew does (hold-off cleared, no
bound), and hands that one answer to BOTH conversions, so the start and the
target are in the same frame whatever the answer was. An answer that is
wrong (a failed probe is read as JNOW) is harmless when both ends use it: the
target is precessed back out of the frame the start was precessed into.

These cases drive ``api/app.py``'s real route over the real
``AlpacaTelescope`` and ``AlpacaConnection`` (``FakeAlpacaMount``), with the
precession stubbed to an unmistakable shift (RA +0.25 h, Dec +1 deg, and the
exact inverse), so every case grades WHICH coordinates reach the mount. Every
coordinate here is fictional.

Named mutants, each run from a byte backup of ``api/app.py`` and restored with
a byte copy (md5 compared).

* "the route does not decide" (``jnow = await hub.decide_mount_frame(tel)``
  made ``jnow = None``, which is the unfixed route) -> all four J2000 cases:
  ``test_a_j2000_mount_is_nudged_by_the_offset_asked_when_its_first_probe_is_
  bad[fail]`` and ``[slow]``, ``test_a_j2000_mount_inside_the_reprobe_holdoff_
  ...`` and ``test_a_slow_first_probe_is_waited_for_...``: AssertionError,
  "the nudge sent [(5.75, 39.5)] to a J2000 mount at (6.0, 40.0) asked for +30
  arcmin of Dec", the start precessed back and the target sent as it stood.
* "the slew asks again" (``_plain_goto``'s ``to_mount_frame(..., jnow=jnow)``
  made without ``jnow``) -> ``test_a_j2000_mount_is_nudged_by_the_offset_asked_
  when_its_first_probe_is_bad[fail]`` alone, the same line: the decision came
  back JNOW from a failed probe, and the slew then got a definite J2000. The
  other cases hold because the route's own ask is definite and cached.

THE STOP FENCE (fix round). The frame decision is a device read the nudge now
makes in its own handler, before it spawns the slew, so a STOP (which bumps the
motion epoch first) can land in it or in the position read before it. The plain
goto re-checks the epoch at the mount, but read at ITS start it would be the
epoch the STOP had already advanced. The nudge reads the epoch before its first
await and hands it to the plain goto.

* "the nudge does not hand its epoch" (``_plain_goto(to_ra, to_dec, jnow,
  entry_epoch)`` made ``_plain_goto(to_ra, to_dec, jnow)``) -> both cases of
  ``test_a_stop_that_lands_while_the_nudge_reads_the_mount_stops_the_nudge``:
  AssertionError, "a STOP landed during the mount's frame read and the nudge
  still slewed [(6.0, 40.5)]" (and "position read" for the other).
* "the epoch is read after the position read" (``entry_epoch = hub.
  _motion_epoch`` moved to just before ``hub.decide_mount_frame``) -> the
  ``position`` case alone.

The controls pass on the unfixed tree as well, which is what makes them
controls: a JNOW mount, a J2000 mount whose probe is answered at once, a
plain goto, and a nudge that follows a STOP rather than meeting one.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.devices.base import DeviceError
from test_861_every_slew_in_the_mount_frame import (  # noqa: F401 (fixture)
    _await_goto, _slew_puts, app_hub)
from test_862_sync_read_back import FakeAlpacaMount, alpaca_tel

#: The probe that answers late, and the status bound the read path gives it.
SLOW_PROBE_S = 0.6
SHORT_STATUS_BOUND_S = 0.25


def _script_probe(tel, monkeypatch, outcomes: tuple[str, ...]) -> list[str]:
    """The Nth ``equatorialsystem`` read of ``tel`` (1-based) follows
    ``outcomes[N-1]``: "fail" raises as an HTTP 500 does, "slow" answers
    after ``SLOW_PROBE_S``, anything else answers at once. Reads past the
    script answer at once. Every read is recorded."""
    real = tel._get
    asked: list[str] = []

    async def _get(method, **params):
        if method == "equatorialsystem":
            asked.append(method)
            how = outcomes[len(asked) - 1] if len(asked) <= len(outcomes) \
                else "ok"
            if how == "fail":
                raise DeviceError("Alpaca HTTP 500: fake")
            if how == "slow":
                await asyncio.sleep(SLOW_PROBE_S)
        return await real(method, **params)

    monkeypatch.setattr(tel, "_get", _get)
    return asked


def _nudge(app_hub, monkeypatch, tel, *, axis: str = "dec",
           arcmin: float = 30.0) -> dict:
    """Nudge through the shipped route and wait for the slew it spawned.
    The response, plus what the hub had cached about the mount's frame while
    the app was still up (``cached_jnow``: the app's shutdown tears the hub
    down and clears it)."""
    monkeypatch.setattr(app_hub, "require", lambda role: tel)
    app = app_module.create_app()
    with TestClient(app) as c:
        r = c.post("/api/mount/nudge", json={"axis": axis, "arcmin": arcmin})
        assert r.status_code == 200, r.text
        _await_goto(c)
        return {**r.json(), "cached_jnow": app_hub._mount_wants_jnow}


# ---------------------------------------------------- the J2000 mount, red


@pytest.mark.parametrize("first_probe", ["fail", "slow"])
def test_a_j2000_mount_is_nudged_by_the_offset_asked_when_its_first_probe_is_bad(
        app_hub, monkeypatch, first_probe):
    """The mount reports J2000 (6.0 h, +40) and its first EquatorialSystem
    answer is an HTTP 500, or later than the read path waits. The nudge asks
    30 arcmin of Dec. Unfixed, the start was precessed back to (5.75, 39.0)
    and the target (5.75, 39.5) then went out unprecessed, a degree and a
    quarter-hour of RA from the field: the mount received neither the offset
    asked nor the frame it reports in."""
    monkeypatch.setattr(hub_module, "STATUS_DEVICE_READ_TIMEOUT_S",
                        SHORT_STATUS_BOUND_S)
    fake = FakeAlpacaMount(ra=6.0, dec=40.0, equatorial_system=2)
    tel = alpaca_tel(fake)
    asked = _script_probe(tel, monkeypatch, (first_probe,))

    _nudge(app_hub, monkeypatch, tel)

    assert _slew_puts(fake) == [(pytest.approx(6.0), pytest.approx(40.5))], (
        f"the nudge sent {_slew_puts(fake)} to a J2000 mount at (6.0, 40.0) "
        f"asked for +30 arcmin of Dec")
    if first_probe == "fail":
        assert len(asked) == 1, (
            f"the frame was decided {len(asked)} times for one nudge: {asked}")


def test_a_j2000_mount_inside_the_reprobe_holdoff_is_nudged_by_the_offset_asked(
        app_hub, monkeypatch):
    """The status poll's probe failed a moment ago, so the read path would
    assume JNOW for the next 60 s without asking. The nudge is no status
    poll: it asks, gets the definite J2000, and both ends use it."""
    fake = FakeAlpacaMount(ra=6.0, dec=40.0, equatorial_system=2)
    tel = alpaca_tel(fake)
    asked = _script_probe(tel, monkeypatch, ())
    monkeypatch.setattr(app_hub, "_mount_jnow_reprobe",
                        (tel, time.monotonic() + 60.0))

    body = _nudge(app_hub, monkeypatch, tel)

    assert _slew_puts(fake) == [(pytest.approx(6.0), pytest.approx(40.5))], (
        f"a nudge inside the hold-off sent {_slew_puts(fake)}")
    assert len(asked) == 1, asked
    assert body["cached_jnow"] is False, "the definite answer was kept"


def test_a_slow_first_probe_is_waited_for_by_a_nudge_and_kept(
        app_hub, monkeypatch):
    """A probe slower than the status bound is not a failed one for a move:
    the nudge waits for it, as a slew does, takes the definite J2000, and the
    answer is cached for the status poll that follows."""
    monkeypatch.setattr(hub_module, "STATUS_DEVICE_READ_TIMEOUT_S",
                        SHORT_STATUS_BOUND_S)
    fake = FakeAlpacaMount(ra=6.0, dec=40.0, equatorial_system=2)
    tel = alpaca_tel(fake)
    _script_probe(tel, monkeypatch, ("slow",))

    body = _nudge(app_hub, monkeypatch, tel)

    assert body["from"]["ra_hours"] == pytest.approx(6.0), (
        "a J2000 report was precessed after the mount said it was J2000")
    assert body["cached_jnow"] is False
    assert _slew_puts(fake) == [(pytest.approx(6.0), pytest.approx(40.5))]


# ------------------------------------------------------------- the STOP fence


@pytest.mark.parametrize("where", ["position", "frame"])
def test_a_stop_that_lands_while_the_nudge_reads_the_mount_stops_the_nudge(
        app_hub, monkeypatch, where):
    """A STOP bumps the motion epoch before anything else. The nudge reads the
    mount's position and asks its frame before it spawns the slew, and both
    are device reads: a STOP can land in either, and the nudge must then send
    nothing. The mount is inside the reprobe hold-off, as it is after a status
    poll's failed probe, which is where the frame probe used to run after the
    plain goto had read its epoch."""
    fake = FakeAlpacaMount(ra=6.0, dec=40.0, equatorial_system=2)
    tel = alpaca_tel(fake)
    method = {"position": "rightascension", "frame": "equatorialsystem"}[where]
    real = tel._get
    stopped: list[str] = []

    async def _get(name, **params):
        if name == method and not stopped:
            stopped.append(name)
            app_hub.bump_motion_epoch()
        return await real(name, **params)

    monkeypatch.setattr(tel, "_get", _get)
    monkeypatch.setattr(app_hub, "_mount_jnow_reprobe",
                        (tel, time.monotonic() + 60.0))

    _nudge(app_hub, monkeypatch, tel)

    assert stopped == [method], "premise: the STOP landed in the read"
    assert _slew_puts(fake) == [], (
        f"a STOP landed during the mount's {where} read and the nudge still "
        f"slewed {_slew_puts(fake)}")


# --------------------------------------------------------------- controls


def test_a_stop_before_the_nudge_does_not_fence_it(app_hub, monkeypatch):
    """CONTROL. The fence is the epoch the nudge held when it began, not an
    epoch from some earlier STOP: a nudge that follows a STOP slews."""
    app_hub.bump_motion_epoch()
    fake = FakeAlpacaMount(ra=6.0, dec=40.0, equatorial_system=2)
    tel = alpaca_tel(fake)
    _script_probe(tel, monkeypatch, ())

    _nudge(app_hub, monkeypatch, tel)

    assert _slew_puts(fake) == [(pytest.approx(6.0), pytest.approx(40.5))]


@pytest.mark.parametrize("first_probe", ["fail", "ok"])
def test_a_jnow_mount_is_nudged_by_the_offset_asked(
        app_hub, monkeypatch, first_probe):
    """CONTROL. A JNOW mount at (6.0, 40.0) in its own frame: converted to
    J2000 once, offset, converted back once, whether or not its first probe
    answered. The frame it lands in is the one it reports in."""
    fake = FakeAlpacaMount(ra=6.0, dec=40.0, equatorial_system=1)
    tel = alpaca_tel(fake)
    _script_probe(tel, monkeypatch, (first_probe,))

    body = _nudge(app_hub, monkeypatch, tel)

    assert body["from"]["ra_hours"] == pytest.approx(5.75)
    assert body["from"]["dec_deg"] == pytest.approx(39.0)
    assert _slew_puts(fake) == [(pytest.approx(6.0), pytest.approx(40.5))]


def test_a_j2000_mount_that_answers_at_once_is_not_precessed(
        app_hub, monkeypatch):
    """CONTROL. The ordinary J2000 mount: the report is the J2000 position,
    the response says so, and the slew goes out as the position plus the
    offset."""
    fake = FakeAlpacaMount(ra=6.0, dec=40.0, equatorial_system=2)
    tel = alpaca_tel(fake)
    _script_probe(tel, monkeypatch, ())

    body = _nudge(app_hub, monkeypatch, tel)

    assert body["from"] == {"ra_hours": pytest.approx(6.0),
                            "dec_deg": pytest.approx(40.0)}
    assert body["to"] == {"ra_hours": pytest.approx(6.0),
                          "dec_deg": pytest.approx(40.5)}
    assert _slew_puts(fake) == [(pytest.approx(6.0), pytest.approx(40.5))]


def test_a_plain_goto_still_asks_for_its_own_frame_decision(
        app_hub, monkeypatch):
    """CONTROL, the other caller of the plain goto. A goto has no start
    position to agree with, so it decides on its own, as before: the hold-off
    is cleared and the mount is asked, and a J2000 mount inside the hold-off
    is sent the target unprecessed."""
    fake = FakeAlpacaMount(ra=6.0, dec=40.0, equatorial_system=2)
    tel = alpaca_tel(fake)
    asked = _script_probe(tel, monkeypatch, ())
    monkeypatch.setattr(app_hub, "_mount_jnow_reprobe",
                        (tel, time.monotonic() + 60.0))
    monkeypatch.setattr(app_hub, "require", lambda role: tel)

    with TestClient(app_module.create_app()) as c:
        r = c.post("/api/mount/goto",
                   json={"ra_hours": 10.0, "dec_deg": 40.0, "center": False,
                         "force": True})
        assert r.status_code == 200, r.text
        _await_goto(c)

    assert _slew_puts(fake) == [(pytest.approx(10.0), pytest.approx(40.0))]
    assert len(asked) == 1, asked
