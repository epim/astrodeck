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


async def _move(tel, fl, how: str) -> None:
    fl.script.update({"Spu": "1", "Sr": "1", "Sd": "1", "MS": "e14",
                      "Te": "1", "Td": "1", "RG": "1", "RS": "1", "RM": "1",
                      "RC": "1"})
    try:
        if how == "unpark":
            await tel.unpark()
        elif how == "goto":
            await tel.unpark()
            await tel.slew(1.0, 20.0)
        elif how == "pad":
            await tel.move_axis("ra", 0.5)
        elif how == "pulse":
            await tel.pulse_guide("n", 100)
        elif how == "tracking_on":
            await tel.set_tracking(True)
        elif how == "find_home":
            await tel.find_home()
    except Exception:  # noqa: BLE001 - only the record matters here
        pass


@pytest.mark.parametrize("how", _MOTIONS)
async def test_any_motion_command_clears_the_record(
        how, fixed_env, clock, logged):
    """A command that can move the tube removes the record before it is sent,
    so a later reopen at the pole latches even though the mount reports
    parked.

    MUTANT M3 "do not clear on motion" (``_clear_park_record`` body made
    ``return``): RED for every parametrisation - AssertionError: assert True
    is False
    """
    fl, tel = await _parked_by_us(clock)
    assert config_mod.load_mount_park_record(tel._park_key()) is not None
    await _move(tel, fl, how)
    assert config_mod.load_mount_park_record(tel._park_key()) is None
    _reads_pole_and(fl, parked=True)
    logged.clear()
    await _reopen(tel)
    assert tel.position_known is False
    assert len(_unknown_warnings(logged)) == 1


async def test_the_record_is_cleared_before_the_command_goes_out(
        fixed_env, clock):
    """A crash mid-command must leave no record: at the instant the unpark
    reaches the wire the record is already gone."""
    fl, tel = await _parked_by_us(clock)
    seen = []
    fl.script["Spu"] = lambda _c: (
        seen.append(config_mod.load_mount_park_record(tel._park_key())) or "1")
    await tel.unpark()
    assert seen == [None]


async def test_a_sync_does_not_clear_the_record(fixed_env, clock):
    """Sync does not move the tube; it is not in the clearing set."""
    import inspect
    assert "_clear_park_record" not in inspect.getsource(am5.ZwoAm5Telescope.sync)


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
