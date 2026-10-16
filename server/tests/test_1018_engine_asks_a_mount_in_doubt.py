# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1018: the engine still ASKS a mount whose link blipped.

#989 made an Alpaca device's ``connected`` a measurement: one transport
failure on a connected device reads ``connected == False`` with
``link_lost == True`` (in doubt) until the next good reply or the hub's link
probe puts it back. The consumers that must not mistake a blip for an absence
ask ``base.is_present`` (connected OR in doubt). Nine engine sites that SKIP
AN ACTION for a mount still tested the bare flag:

* ``_fenced_park`` (the roof close's park) returned "not parked" without
  asking, so the roof close that follows refused to close over a tube nobody
  tried to park;
* ``_stop_tracking_quietly`` sent no stop of tracking;
* ``_hold_repoint`` returned "the mount is not connected" and left a cloud
  hold pointing nowhere for a look;
* ``_mount_floor_verdict`` returned NO VERDICT, so a slew gate asked at that
  moment checked neither the pier-collision guard nor the altitude floor,
  horizon, no-go wedge or zenith keep-out, and the slew that followed healed
  the link and moved the mount;
* ``_maybe_meridian_flip`` took no flip and logged that the mount "is not
  reachable";
* ``_pier_side_now`` (and its hub twin ``Hub.pier_side_now``), ``_read_tracking``,
  ``_enforce_tracking`` and ``_recover_from_tracking_refusal`` answered "nobody
  can say", "carry on" and "no recovery" for a mount that was there.

Every one now asks ``is_present``: the call is attempted, a failure is a
failure, and a good reply puts the device back. A mount that is NOT in doubt
(never connected, put away on purpose, told NotConnected by its server) is
still skipped, which each case's second parametrisation pins as its control.

Left alone on purpose (the report lists them): ``_wind_down_park`` (it carries
the measured not-connected arm, ``_reopen_mount_for_park``, which reconnects
and so ends the doubt itself), the dawn park and the sun watch (their own
reopen arms), the reconnect gate (it reads the flag BECAUSE it must see the
drop), and the status poll's device blocks (best-effort display reads; the
hub's link probe is what asks a device in doubt, one in flight, off the poll).

THE HARNESS. The simulator's mount with ``connected``/``link_lost`` set as the
Alpaca client leaves them, driving the engine's own methods (the pinned-mount
and flip harnesses of test_recovery_centring_is_measured and
test_the_flip_stops_paying_for_itself); one case at the end puts the REAL
``AlpacaTelescope`` over a fake Alpaca server (test_989's) and shows the
engine's read heal the device. Each case names the mutant it was shown RED
under, applied to a byte backup of engine.py (or hub.py) and restored from it.
"""
from __future__ import annotations

import pytest

from astrodeck.config import SafetyConfig
from astrodeck.devices.base import PierSide
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine

from test_850_engine_sync_refused import flip_hub  # noqa: F401 (fixture)
from test_989_a_blip_is_not_an_absence import (  # noqa: F401 (fixtures)
    FakeAlpaca, _blip, build)
from test_989_a_blip_is_not_an_absence import temp_store as _store_989  # noqa: F401
from test_recovery_centring_is_measured import (  # noqa: F401 (fixtures)
    _engine, _pinned_mount, sim_hub, temp_store)
import test_the_flip_stops_paying_for_itself as flp

#: Both ways a mount can read ``connected == False``: a blip (in doubt, the
#: device was there and is to be asked) and an absence (never connected, put
#: away on purpose, or told NotConnected: nothing to ask).
_WAYS = pytest.mark.parametrize("in_doubt", [True, False],
                                ids=["blip", "absent"])


def _not_connected(tel, in_doubt: bool) -> None:
    """The mount as the Alpaca client leaves it: ``connected`` False, and
    ``link_lost`` True only for a CONNECTED device that failed at the
    transport (`_AlpacaDevice._note_link_lost`)."""
    tel.connected = False
    tel.link_lost = in_doubt


# ===================================================== the roof close's park

@_WAYS
async def test_the_roof_close_parks_a_mount_whose_link_blipped(
        sim_hub, in_doubt):
    """`_fenced_park` returned False at once for a mount reading not-connected,
    so the auto-reopen close went to ``close_observatory`` with a tube nobody
    had tried to park, and the roof stayed open in the weather that closed it.

    MUTANT 1 "park trusts the flag": the ``is_present(tel)`` test in
    `_fenced_park` reverted to ``getattr(tel, "connected", False)``: RED
    (observed), the blip case, ``assert False is True`` on the return.
    """
    e, _t = _engine(sim_hub)
    tel = sim_hub.devices["telescope"]
    _not_connected(tel, in_doubt)

    parked = await e._fenced_park()

    assert parked is in_doubt
    assert await tel.is_parked() is in_doubt


@_WAYS
async def test_the_stop_of_tracking_is_sent_to_a_mount_whose_link_blipped(
        sim_hub, in_doubt):
    """`_stop_tracking_quietly` (the pause, the cloud hold, the idle stop and
    the roof close all stop the mount through it) sent nothing to a mount in
    doubt and swallowed nothing: the stop simply was not made.

    MUTANT 2 "stop trusts the flag": the ``is_present(tel)`` test in
    `_stop_tracking_quietly` reverted to ``tel.connected``: RED (observed),
    the blip case, ``assert True is not True`` (still tracking).
    """
    e, _t = _engine(sim_hub)
    tel = sim_hub.devices["telescope"]
    await tel.set_tracking(True)
    _not_connected(tel, in_doubt)

    await e._stop_tracking_quietly()

    assert await tel.get_tracking() is (not in_doubt)


# ===================================================== the cloud hold's re-point

def _hold_engine(sim_hub, monkeypatch):
    e, t = _engine(sim_hub)
    tel = sim_hub.devices["telescope"]
    moves: list[str] = []

    async def slew(ra, dec):
        moves.append("slew")

    async def unpark():
        moves.append("unpark")

    async def gate(*, context, target):
        pass
    monkeypatch.setattr(tel, "slew", slew)
    monkeypatch.setattr(tel, "unpark", unpark)
    e._safety_gate = gate
    return e, t, tel, moves


@_WAYS
async def test_the_hold_re_points_a_mount_whose_link_blipped(
        sim_hub, monkeypatch, in_doubt):
    """`_hold_repoint` answered "the mount is not connected" and sent no slew
    for a mount in doubt: the hold stayed where it was for another look. A
    mount that is not there still answers it.

    MUTANT 3 "re-point trusts the flag": the ``is_present(tel)`` test in
    `_hold_repoint` reverted to ``getattr(tel, "connected", False)``: RED
    (observed), the blip case, ``assert 'the mount is not connected' is
    None``.
    """
    e, t, tel, moves = _hold_engine(sim_hub, monkeypatch)
    _not_connected(tel, in_doubt)

    why = await e._hold_repoint(t, elsewhere=True)

    if in_doubt:
        assert why is None
        assert moves == ["slew"], moves
    else:
        assert why == "the mount is not connected"
        assert moves == []


# ================================================== the slew gate's limits

@_WAYS
async def test_the_altitude_limits_are_judged_for_a_mount_whose_link_blipped(
        sim_hub, temp_store, in_doubt):
    """`_mount_floor_verdict` returned None (no verdict) before it reached the
    altitude half for a mount reading not-connected, so a slew gate asked
    during a blip checked no floor, horizon, no-go wedge or zenith keep-out,
    and the slew that followed healed the link and moved the mount. The
    altitude half is arithmetic and touches no device; only the pier half asks
    the mount. A target that is never above the floor (declination -70 from a
    latitude of 40 degrees against a floor of 80) is refused whatever the
    hour.

    MUTANT 4 "gate trusts the flag": the ``is_present(tel)`` test in
    `_mount_floor_verdict` reverted to ``getattr(tel, "connected", False)``:
    RED (observed), the blip case, ``assert None is not None``.
    """
    temp_store.set_safety(SafetyConfig(enabled=False, min_alt_deg=80.0))
    e, t = _engine(sim_hub)
    t.dec_deg = -70.0
    _not_connected(sim_hub.devices["telescope"], in_doubt)

    verdict = await e._mount_floor_verdict(
        t, projected=False, cfg=temp_store.cfg(), plan=e.plan)

    if in_doubt:
        assert verdict is not None and verdict.kind == "floor"
    else:
        assert verdict is None


@_WAYS
async def test_the_pier_guard_asks_a_mount_whose_link_blipped(
        sim_hub, temp_store, monkeypatch, in_doubt):
    """The pier half of the same gate: a destination side that differs from
    the side the mount is on, flips off, is a pier collision, and the guard
    asked a mount in doubt nothing and let the slew through.

    MUTANT 4 (see above): RED (observed), the blip case, ``assert None is not
    None``.
    """
    temp_store.set_safety(SafetyConfig(enabled=False, enforce_pier_limits=True))
    e, t = _engine(sim_hub)
    tel = sim_hub.devices["telescope"]
    assert tel.reports_destination_pier_side

    async def pier_side():
        return PierSide.WEST

    async def destination_pier_side(ra_hours, dec_deg):
        return PierSide.EAST
    monkeypatch.setattr(tel, "pier_side", pier_side)
    monkeypatch.setattr(tel, "destination_pier_side", destination_pier_side)
    _not_connected(tel, in_doubt)

    verdict = await e._mount_floor_verdict(
        t, projected=False, cfg=temp_store.cfg(), plan=e.plan)

    if in_doubt:
        assert verdict is not None and verdict.kind == "pier"
    else:
        assert verdict is None


# ========================================================= the meridian flip

@_WAYS
async def test_the_flip_is_taken_by_a_mount_whose_link_blipped(
        flip_hub, monkeypatch, bus_lines, in_doubt):
    """An armed flip that meets a mount in doubt took no flip and logged "the
    mount is not reachable - it will not be taken": a warning that reaches
    every alert sink, about a mount that answered the next request. In doubt
    the flip goes ahead; a mount that is not there still says so, once.

    MUTANT 5 "flip trusts the flag": the ``is_present(tel)`` test in
    `_maybe_meridian_flip` reverted to ``tel.connected``: RED (observed), the
    blip case, ``assert 0 == 1`` on the goto count.
    """
    e, t, st = flp._flip_engine(flip_hub, monkeypatch, the_slew_flips=True)
    _not_connected(flip_hub.devices["telescope"], in_doubt)

    await e._maybe_meridian_flip(t, next_exposure_s=180.0)

    said = [m for lvl, m, _s in bus_lines
            if lvl == "warning" and "is not reachable" in m]
    if in_doubt:
        assert len(st["gotos"]) == 1, st["gotos"]
        assert said == [], said
    else:
        assert st["gotos"] == []
        assert len(said) == 1, said


# ================================================ the reads under the flip

@_WAYS
async def test_the_engine_reads_the_pier_side_of_a_mount_whose_link_blipped(
        sim_hub, monkeypatch, in_doubt):
    """`_pier_side_now` answered None ("nobody can say") without asking, so a
    flip's before and after reads, a mosaic's side record and the flip-owed
    hold's "did it flip" all read a mount in doubt as unreadable.

    MUTANT 6 "pier read trusts the flag": the ``is_present(tel)`` test in
    `SequenceEngine._pier_side_now` reverted to ``getattr(tel, "connected",
    False)``: RED (observed), the blip case, ``assert None == 'east'``.
    """
    e, _t = _engine(sim_hub)
    tel = sim_hub.devices["telescope"]

    async def pier_side():
        return PierSide.EAST
    monkeypatch.setattr(tel, "pier_side", pier_side)
    _not_connected(tel, in_doubt)

    assert await e._pier_side_now() == ("east" if in_doubt else None)


@_WAYS
async def test_the_hub_reads_the_pier_side_of_a_mount_whose_link_blipped(
        sim_hub, monkeypatch, in_doubt):
    """`Hub.pier_side_now` is the engine's read made again from inside the
    flip, and its docstring says the two must not be able to disagree about
    what the mount said. The engine's read above answers a mount in doubt, so
    this one must too, or the flip's cross-check logs a disagreement that is
    only the flag.

    MUTANT 7 "hub pier read trusts the flag": the ``is_present(tel)`` test in
    `Hub.pier_side_now` reverted to ``getattr(tel, "connected", False)``: RED
    (observed), the blip case, ``assert None == 'east'``.
    """
    tel = sim_hub.devices["telescope"]

    async def pier_side():
        return PierSide.EAST
    monkeypatch.setattr(tel, "pier_side", pier_side)
    _not_connected(tel, in_doubt)

    assert await sim_hub.pier_side_now() == ("east" if in_doubt else None)


@_WAYS
async def test_the_engine_reads_the_tracking_of_a_mount_whose_link_blipped(
        sim_hub, in_doubt):
    """`_read_tracking` is the one read every tracking decision goes through
    (`_tracking_now`: the light-frame check, the autofocus sweep, the idle
    stop's read-back, the flip's refusal test). It answered None, "nobody can
    say", for a mount in doubt.

    MUTANT 8 "tracking read trusts the flag": the ``is_present(tel)`` test in
    `_read_tracking` reverted to ``getattr(tel, "connected", False)``: RED
    (observed), the blip case, ``assert None is True``.
    """
    e, _t = _engine(sim_hub)
    tel = sim_hub.devices["telescope"]
    await tel.set_tracking(True)
    _not_connected(tel, in_doubt)

    assert await e._tracking_now() is (True if in_doubt else None)


# ============================================= tracking enforcement, recovery

@_WAYS
async def test_a_light_frame_on_a_blipped_mount_is_still_checked_for_tracking(
        sim_hub, monkeypatch, in_doubt):
    """`_enforce_tracking` returned before it read anything for a mount in
    doubt, so a light frame went out on a mount that had stopped at its limit
    and was never recovered. The pinned mount refuses tracking until a park
    clears it (the 2026-08-21 case): in doubt, the recovery runs; a mount that
    is not there is still not asked.

    MUTANT 9 "enforce trusts the flag": the ``is_present(tel)`` test in
    `_enforce_tracking` reverted to ``getattr(tel, "connected", False)``: RED
    (observed), the blip case, ``assert [] == ['park', 'unpark', 'goto']``.
    """
    st = _pinned_mount(sim_hub, monkeypatch, [{"centered": True}])
    e, t = _engine(sim_hub)
    _not_connected(sim_hub.devices["telescope"], in_doubt)

    await e._enforce_tracking(t.steps[0], t)

    assert st["events"] == (["park", "unpark", "goto"] if in_doubt else [])


@_WAYS
async def test_the_tracking_recovery_runs_for_a_mount_whose_link_blipped(
        sim_hub, monkeypatch, in_doubt):
    """`_recover_from_tracking_refusal` returned False at its entry for a
    mount in doubt, and its caller took that as "the recovery cannot help": a
    set-aside target. In doubt it parks, unparks and re-centres, and returns
    True.

    MUTANT 10 "recovery trusts the flag": the ``is_present(tel)`` test in
    `_recover_from_tracking_refusal` reverted to ``getattr(tel, "connected",
    False)``: RED (observed), the blip case, ``assert False is True``.
    """
    st = _pinned_mount(sim_hub, monkeypatch, [{"centered": True}])
    e, t = _engine(sim_hub)
    _not_connected(sim_hub.devices["telescope"], in_doubt)

    assert await e._recover_from_tracking_refusal(t) is in_doubt

    assert st["events"] == (["park", "unpark", "goto"] if in_doubt else [])


# ============================================ the real client, end to end

async def test_the_engine_heals_a_blipped_alpaca_mount_by_asking_it(
        _store_989, build):
    """The real `AlpacaTelescope` over a fake Alpaca server, in doubt after one
    transport failure. The engine's tracking read is a request, and a good
    reply is what puts the device back: no probe, no operator.

    MUTANT 8 (see above): RED (observed), ``assert None is True``.
    """
    server = FakeAlpaca(tracking=True)
    tel = build("telescope", server)
    hub = Hub()
    hub.devices["telescope"] = tel
    await _blip(tel, server)

    assert await SequenceEngine(hub)._read_tracking() is True
    assert (tel.connected, tel.link_lost) == (True, False)
    assert ("GET", "tracking") in server.calls


async def test_a_blipped_alpaca_mount_whose_link_is_still_down_stays_in_doubt(
        _store_989, build):
    """The same mount, the server still gone: the read fails, and the engine's
    answer is the unreadable None it always gave, not an exception. The doubt
    stands for the hub's probe, and the stop of tracking raises nothing."""
    server = FakeAlpaca(tracking=True)
    tel = build("telescope", server)
    hub = Hub()
    hub.devices["telescope"] = tel
    await _blip(tel, server)
    server.mode = "down"
    e = SequenceEngine(hub)

    assert await e._read_tracking() is None
    await e._stop_tracking_quietly()

    assert ("PUT", "tracking") in server.calls
    assert (tel.connected, tel.link_lost) == (False, True)


async def test_an_alpaca_mount_told_not_connected_is_not_asked(
        _store_989, build):
    """CONTROL. A server that answered NotConnected (0x407) ended the doubt:
    the mount is not there, and the engine sends it nothing."""
    server = FakeAlpaca(tracking=True)
    tel = build("telescope", server)
    hub = Hub()
    hub.devices["telescope"] = tel
    await _blip(tel, server)
    server.mode = "not_connected"
    assert await tel.probe_link() is False
    assert (tel.connected, tel.link_lost) == (False, False)
    server.calls.clear()
    e = SequenceEngine(hub)

    assert await e._read_tracking() is None
    await e._stop_tracking_quietly()

    assert server.calls == []

