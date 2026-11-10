# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1075: a reconnect of a mount AstroDeck parked at home keeps its position.

THE DEFECT. A parked AM5 always reads the home pole, so ``_note_reset_signature``
latched "position is unknown" on every (re)open of a mount the engine itself had
parked and that never moved (a USB drop, a Windows Update reboot, our own
deploy). At this site the home view is obstructed, so a solve at home can never
clear the latch and only a human tapping Trust position could.

THE FIX UNDER TEST. ``park()`` writes a small record (device key, time,
"parked by AstroDeck") beside the other per-device state; every command that
can move the tube removes it first; a reopen that reads the pole keeps its
position only when a record for THIS device exists AND the mount still reports
parked. Any other case is today's latch and today's warning.

Every test points ``config.CONFIG_DIR`` at ``tmp_path`` (the suite's autouse
fixture does too; it is repeated here so the file stands alone). Coordinates
are fictional.

Every mutant below was applied to a byte backup of its production file, run
under the normal command, and the file restored from the backup.
"""
from __future__ import annotations

import contextlib

import pytest

import astrodeck.config as config_mod
import astrodeck.devices.backends.zwo_am5 as am5
from astrodeck.devices.base import rig_position_known

from test_zwo_am5 import FakeLink, _connect_script, fixed_env  # noqa: F401
from test_zwo_am5_park_noop import PARKS, _ParkingAm5, clock  # noqa: F401

_WARNING = "position is unknown"
_KEPT = "position kept"
_POLE = "+90*00:00"
_AWAY = "+40*00:00"


@pytest.fixture(autouse=True)
def park_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    assert config_mod.mount_park_record_path().parent == tmp_path
    return tmp_path


@pytest.fixture
def logged(monkeypatch):
    lines: list[tuple[str, str]] = []
    monkeypatch.setattr(
        am5.bus, "log",
        lambda level, msg, source="system", *a, **k: lines.append((level, msg)))
    return lines


def _unknown_warnings(lines) -> list[str]:
    return [m for _lvl, m in lines if _WARNING in m]


def _kept_lines(lines) -> list[str]:
    return [m for _lvl, m in lines if _KEPT in m]


async def _parked_by_us(clock, *, port="COM9", name="Mount"):
    """A driver that connected away from the pole, then parked, through the
    driver's own park(). Returns (link, tel)."""
    fl = _ParkingAm5(clock, [PARKS])
    fl.port_path = port
    tel = am5.ZwoAm5Telescope(fl, name=name)
    await tel.connect()
    assert tel.position_known is True, "premise: connected away from the pole"
    await tel.park()
    assert await tel.is_parked() is True, "premise: park() confirmed parked"
    return fl, tel


def _reads_pole_and(link, *, parked: bool) -> None:
    link.script["GD"] = _POLE
    link.script["Gps"] = "2" if parked else "0"


async def _reopen(tel) -> None:
    """The (re)open a USB drop, a reboot or a deploy ends in."""
    await tel._link.close()
    tel.connected = False
    await tel.connect()


def _fresh_instance(*, port="COM9", name="Mount", parked=True, dec=_POLE):
    fl = FakeLink(_connect_script(GD=dec, Gps="2" if parked else "0"))
    fl.port_path = port
    return fl, am5.ZwoAm5Telescope(fl, name=name)


# ---------------------------------------------------------------- 1 and 2

async def test_reopen_of_a_mount_we_parked_keeps_its_position(
        fixed_env, clock, logged):
    """Park, then reopen reading the pole and parked: nothing latches, no
    warning, one info line with no coordinate in it, and the rig-level read
    agrees.

    MUTANT M1 "ignore the record" (``_park_record_holds`` made ``return
    False``): RED - AssertionError: assert False is True
    """
    fl, tel = await _parked_by_us(clock)
    _reads_pole_and(fl, parked=True)
    logged.clear()
    await _reopen(tel)
    assert tel.position_known is True
    assert _unknown_warnings(logged) == []
    kept = _kept_lines(logged)
    assert len(kept) == 1 and "#1075" in kept[0]
    assert not any(ch.isdigit() for ch in kept[0].replace("#1075", ""))
    hub = type("Hub", (), {"devices": {"telescope": tel}})()
    assert rig_position_known(hub) is True


async def test_never_parked_by_us_still_latches(fixed_env, logged):
    """No record: the mount reads the pole, so the latch and today's warning,
    and the rig-level read agrees.

    MUTANT M1b "record check always true" (``_park_record_holds`` made
    ``return True``): RED - AssertionError: assert True is False
    """
    fl, tel = _fresh_instance()
    await tel.connect()
    assert tel.position_known is False
    warnings = _unknown_warnings(logged)
    assert len(warnings) == 1 and "Trust position" in warnings[0]
    assert _kept_lines(logged) == []
    hub = type("Hub", (), {"devices": {"telescope": tel}})()
    assert rig_position_known(hub) is False


# ------------------------------------------------------------------ 3

async def test_a_record_with_a_mount_that_reports_unparked_latches(
        fixed_env, clock, logged):
    """The record is not enough on its own: a power cycle that cleared the
    parked flag fails the live read and the latch stays.

    MUTANT M2 "drop the parked read" (``return await self.is_parked()`` made
    ``return True``): RED - AssertionError: assert False is True is not False
    """
    fl, tel = await _parked_by_us(clock)
    _reads_pole_and(fl, parked=False)
    logged.clear()
    await _reopen(tel)
    assert tel.position_known is False
    assert len(_unknown_warnings(logged)) == 1
    assert _kept_lines(logged) == []


# ------------------------------------------------------------------ 4

_MOTIONS = ["unpark", "goto", "pad", "pulse", "tracking_on", "find_home"]


#: The command each motion puts on the wire, and what it is scripted to answer.
#: A motion that runs to its end needs every command it sends answered, with
#: the exact string it sends (``FakeLink`` matches whole strings, so a bare
#: ``"Sr"`` key never matches ``Sr01:00:00``). The goto's target is 01:00:00
#: and +20, and the position reads agree, so the slew arrives.
_GOTO_RA_H, _GOTO_DEC = 1.0, 20.0
_MOTION_REPLIES = {"Spu": "1", "MS": "0", "Te": "1", "Td": "1",
                   "Sr01:00:00": "1", "Sd+20*00:00": "1",
                   "GR": "01:00:00", "GD": "+20*00:00"}
#: A command only that motion sends, so the case can say the motion ran.
_MOTION_VERB = {"unpark": "Spu", "goto": "MS", "pad": "Me", "pulse": "Mn",
                "tracking_on": "Te", "find_home": "hP"}


async def _move(tel, fl, how: str) -> None:
    """Run one motion to its end. An exception is the test's failure, not a
    thing to swallow: a move that stops at its first refused command proves
    only that the record was cleared before the wire."""
    fl.script.update(_MOTION_REPLIES)
    if how == "unpark":
        await tel.unpark()
    elif how == "goto":
        # No unpark first: unpark clears the record itself, which would
        # stand in for the slew's own clear.
        await tel.slew(_GOTO_RA_H, _GOTO_DEC)
    elif how == "pad":
        await tel.move_axis("ra", 0.5)
    elif how == "pulse":
        await tel.pulse_guide("n", 100)
    elif how == "tracking_on":
        await tel.set_tracking(True)
    elif how == "find_home":
        await tel.find_home()
    else:
        raise AssertionError(how)


@pytest.mark.parametrize("how", _MOTIONS)
async def test_any_motion_command_clears_the_record(
        how, fixed_env, clock, logged):
    """A command that can move the tube removes the record, and the motion
    runs to its end: it reaches its own command on the wire and returns, so a
    clear that only a refused or half-run motion reaches is not enough. A
    later reopen at the pole then latches even though the mount reports
    parked.

    MUTANT M3 "do not clear on motion" (``_clear_park_record`` body made
    ``return``): RED for every parametrisation - AssertionError: assert True
    is False

    WP 8.2 (#1176). The goto used to run ``unpark`` first, which clears the
    record, and the rest of the goto stopped at ``Sr01:00:00`` (the script
    keyed ``Sr``), so the slew's own clear was never what this graded.
    MUTANT "slew does not clear" (``self._clear_park_record()`` replaced by
    ``pass`` in ``ZwoAm5Telescope.slew``): GREEN before this change, RED now,
    for ``goto`` - AssertionError: assert {...} is None.
    """
    fl, tel = await _parked_by_us(clock)
    assert config_mod.load_mount_park_record(tel._park_key()) is not None
    fl.sent.clear()
    await _move(tel, fl, how)
    assert _MOTION_VERB[how] in fl.sent, (
        f"premise: {how} did not reach its own command; sent {fl.sent}")
    assert config_mod.load_mount_park_record(tel._park_key()) is None
    _reads_pole_and(fl, parked=True)
    logged.clear()
    await _reopen(tel)
    assert tel.position_known is False
    assert len(_unknown_warnings(logged)) == 1


@pytest.mark.parametrize("how", _MOTIONS)
async def test_the_record_is_cleared_before_the_command_goes_out(
        how, fixed_env, clock):
    """A crash mid-command must leave no record: at the instant the motion's
    FIRST command reaches the wire the record is already gone. Graded on
    the first command of each motion, whatever it is, so a motion that clears
    only after it has sent something is caught for every motion and not for
    ``unpark`` alone.

    WP 8.2 (#1176). Was ``unpark`` alone.
    MUTANT "pulse clears after the wire" (the ``self._clear_park_record()``
    in ``ZwoAm5Telescope.pulse_guide`` replaced by ``pass`` and the same call
    placed after ``await asyncio.to_thread(pulse.run)``): GREEN before this
    change (the record is gone by the time the old case looked), RED now for
    ``pulse`` - AssertionError: assert [{...}] == [None].
    """
    fl, tel = await _parked_by_us(clock)
    key = tel._park_key()
    assert config_mod.load_mount_park_record(key) is not None
    seen: list = []
    send = fl._exchange

    def watch(cmd: str, reply: str):
        if not seen:
            seen.append(config_mod.load_mount_park_record(key))
        return send(cmd, reply)

    fl._exchange = watch
    await _move(tel, fl, how)
    assert seen == [None]


async def test_a_sync_does_not_clear_the_record(fixed_env, clock):
    """Sync does not move the tube; it is not in the clearing set.

    WP 2.8 (#978). This used to look for ``_clear_park_record`` in the source
    of ``sync``, which a helper that clears the record on sync's behalf would
    have slipped past. It syncs a parked-by-us mount and reads the record.

    MUTANT "sync clears the record" (``self._clear_park_record()`` as the
    first statement of ``ZwoAm5Telescope.sync``): RED - AssertionError: a sync
    cleared the park record
    """
    fl, tel = await _parked_by_us(clock)
    assert config_mod.load_mount_park_record(tel._park_key()) is not None
    fl.script.update({"Sr01:00:00": "1", "Sd+20*00:00": "1", "CM": "N/A"})
    fl.sent.clear()
    with contextlib.suppress(Exception):    # the read-back may refuse; only the record matters
        await tel.sync(1.0, 20.0)
    assert "CM" in fl.sent, "premise: the sync reached the wire"
    assert config_mod.load_mount_park_record(tel._park_key()) is not None, (
        "a sync cleared the park record")


async def test_find_home_leaves_no_record(fixed_env, clock):
    """find_home parks and then unparks; the park inside it must not record,
    because the mount ends unparked."""
    fl, tel = await _parked_by_us(clock)
    fl.answers.append(PARKS)
    await _move(tel, fl, "find_home")
    assert config_mod.load_mount_park_record(tel._park_key()) is None


# ------------------------------------------------------------------ 5

async def test_the_record_survives_a_new_driver_instance(
        fixed_env, clock, logged):
    """A server restart or an OS reboot: park on instance A, reopen on a NEW
    instance B pointed at the same store. B keeps the position.

    MUTANT M4 "do not persist" (``save_mount_park_record`` made a no-op):
    RED - AssertionError: assert False is True
    """
    _fl_a, _tel_a = await _parked_by_us(clock)
    fl_b, tel_b = _fresh_instance()
    logged.clear()
    await tel_b.connect()
    assert tel_b.position_known is True
    assert _unknown_warnings(logged) == []
    assert len(_kept_lines(logged)) == 1


async def test_the_record_is_on_disk_in_the_documented_shape(
        fixed_env, clock):
    _fl, tel = await _parked_by_us(clock)
    rec = config_mod.load_mount_park_record(tel._park_key())
    assert rec["parked_by"] == "AstroDeck"
    assert rec["device"] == tel._park_key()
    assert rec["at"].endswith("+00:00")
    assert config_mod.mount_park_record_path().exists()


# ------------------------------------------------------------------ 6

@pytest.mark.parametrize("port,name", [("COM1", "Mount"), ("COM9", "Other")])
async def test_a_record_for_another_device_is_ignored(
        port, name, fixed_env, clock, logged):
    """A different port or a different configured name never inherits the
    record.

    MUTANT M5 "ignore the device key" (``_park_key`` made ``return
    "zwo-am5"``): RED - AssertionError: assert True is False
    """
    await _parked_by_us(clock, port="COM9", name="Mount")
    fl_b, tel_b = _fresh_instance(port=port, name=name)
    logged.clear()
    await tel_b.connect()
    assert tel_b.position_known is False
    assert len(_unknown_warnings(logged)) == 1
    assert _kept_lines(logged) == []


async def test_a_record_that_is_not_ours_is_ignored(fixed_env, logged):
    """A hand-written or foreign record under the right key does not count."""
    fl, tel = _fresh_instance()
    config_mod.save_mount_park_record(
        tel._park_key(), {"device": tel._park_key(), "parked_by": "someone"})
    await tel.connect()
    assert tel.position_known is False


# ------------------------------------------------- bootstrap (review round 1)

async def test_park_on_an_already_parked_known_mount_writes_the_record(
        fixed_env, logged):
    """A mount parked before records existed: park() returns early, and used
    to write nothing. With the position known it now remembers the park, and
    a reopen at the pole keeps it.

    MUTANT M6 "drop the already-parked write" (the ``self._write_park_record()``
    in park()'s early return removed): RED - AssertionError: assert False is True
    """
    fl = FakeLink(_connect_script(GD=_AWAY, Gps="2"))
    fl.port_path = "COM9"
    tel = am5.ZwoAm5Telescope(fl, name="Mount")
    await tel.connect()
    assert tel.position_known is True
    assert config_mod.load_mount_park_record(tel._park_key()) is None
    await tel.park()
    assert config_mod.load_mount_park_record(tel._park_key()) is not None
    _reads_pole_and(fl, parked=True)
    await _reopen(tel)
    assert tel.position_known is True


async def test_park_on_an_already_parked_unknown_mount_writes_nothing(
        fixed_env, logged):
    """The position is in doubt: park() must not vouch for it.

    MUTANT M7 "drop the position_known condition" (``if self.position_known:``
    made ``if True:``): RED - AssertionError: assert None is None
    """
    fl, tel = _fresh_instance()
    await tel.connect()
    assert tel.position_known is False
    await tel.park()
    assert config_mod.load_mount_park_record(tel._park_key()) is None
    await _reopen(tel)
    assert tel.position_known is False


async def test_trust_position_on_a_parked_mount_writes_the_record(
        fixed_env, logged):
    """The operator attests the tube is where the mount reports, and it
    reports parked: a reopen keeps the position.

    MUTANT M8 "drop the trust_position write": RED - AssertionError: assert
    False is True
    """
    fl, tel = _fresh_instance()
    await tel.connect()
    assert tel.position_known is False
    await tel.trust_position()
    assert config_mod.load_mount_park_record(tel._park_key()) is not None
    await _reopen(tel)
    assert tel.position_known is True


async def test_trust_position_on_an_unparked_mount_writes_nothing(
        fixed_env, logged):
    fl, tel = _fresh_instance(parked=False)
    await tel.connect()
    await tel.trust_position()
    assert tel.position_known is True
    assert config_mod.load_mount_park_record(tel._park_key()) is None


async def test_trust_position_writes_nothing_when_the_parked_read_fails(
        fixed_env, logged):
    fl, tel = _fresh_instance()
    await tel.connect()
    del fl.script["Gps"]
    await tel.trust_position()
    assert config_mod.load_mount_park_record(tel._park_key()) is None
