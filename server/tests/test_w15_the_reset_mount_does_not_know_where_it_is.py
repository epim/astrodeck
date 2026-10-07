# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-103 (#144, server half): a reset mount does not know where it is.

THE STATE THESE PIN. After a power cycle the AM5 reports its HOME position
(counterweight down, pointing at the pole) wherever the tube physically is. It
has no home sensor and no brake, so the report is the mount's belief and not a
measurement, and everything that reads "where the mount points" and then
computes from it is computing from a guess: a nudge adds an offset to it, the
solar-cone check on a manual jog measures the Sun's distance from it, and
``:hP#`` ("home") on it moves nothing because the mount thinks it is already
there (#133's second finding, reproduced by power-cycling the mount on
2026-09-23).

``Telescope.position_known`` is the capability that says so. ``POST
/api/mount/nudge`` has refused on it since WP-20, but no driver ever set it
False, so the guard was inert. These cases pin the whole chain with the REAL
AM5 driver and the REAL routes: the reopen reads the pole, the driver latches
the flag, ``status.mount`` publishes it, nudge answers 409 ``position_unknown``,
the manual move declines to compute the solar cone from the pole, and only a
sync (or the operator's attestation) clears it. Never a goto: a goto from a
wrong model proves nothing.

The UI half (locking the step controls, picking the ceiling rung, the
TRUST POSITION button) and the ``/api/mount/trust-position`` route that button
needs are separate work packages. ``trust_position()`` exists on the driver so
that route has something to reach.

PRIVACY. The home position IS the pole, so its declination is the site latitude's
hemisphere signature and an altitude read of it IS the latitude (#140). Nothing
here prints, logs or asserts an alt/az; the log-line cases assert that no digit
reaches the log at all. Every coordinate below is made up.

Named mutants, each run from a byte backup inside the worktree and restored
byte-identically (sha256 compared, and the mutant text grepped out of the
restored file); every one turned at least one case red. The first failing
assertion is quoted, with the case that raised it.

* m1_latch_forced_false -- ``zwo_am5.py`` ``_note_reset_signature``:
  ``at_pole = False and abs(abs(dec) - 90.0) <= POLE_SIGNATURE_DEG``. 17 cases
  fail, among them ``test_the_status_frame_publishes_position_known_false_for_a_reset_mount``
  and ``test_nudge_answers_409_position_unknown_for_a_real_reset_am5``. First:
  ``test_a_reopen_that_reads_the_home_pole_latches_position_unknown``,
  ``AssertionError: position_known must read False after a (re)open that read
  the home pole, and it read True``.
* m2_sync_does_not_clear -- ``zwo_am5.py`` ``sync``: the
  ``self._position_untrusted = False`` after a successful sync deleted.
  ``test_a_sync_clears_it``, ``AssertionError: position_known must read True
  after a sync the mount accepted, and it read False`` (and two more).
* m3_goto_clears -- ``zwo_am5.py`` ``slew``: ``self._position_untrusted =
  False`` added where the settle poll returns. ``test_a_goto_does_not_clear_it``,
  ``AssertionError: position_known must read False after a goto that settled,
  and it read True``.
* m4_solar_check_left_in_unknown_branch -- ``app.py`` ``/api/mount/move``:
  ``elif rate != 0.0 and callable(check_solar):`` made ``if``.
  ``test_manual_move_does_not_compute_the_solar_cone_from_an_unknown_position``,
  ``AssertionError: the solar cone was computed from an unknown position (1
  call(s))`` (and ``test_a_sync_puts_the_cone_check_back``).
* m5_latch_assigned_from_reading -- ``zwo_am5.py``: ``self._position_untrusted
  = was_untrusted or at_pole`` made ``= at_pole``.
  ``test_the_latch_outlives_a_reopen_that_reads_somewhere_else``,
  ``AssertionError: position_known must read False after a reopen that read
  somewhere else (a latch, not a reading), and it read True``.
* m6_window_widened -- ``POLE_SIGNATURE_DEG`` 0.05 to 0.5.
  ``test_a_read_just_outside_the_pole_signature_does_not_latch``,
  ``AssertionError: position_known must read True after a read 0.10 deg short
  of the pole, and it read False``.
* m7_window_narrowed -- ``POLE_SIGNATURE_DEG`` 0.05 to 0.005.
  ``test_a_read_just_inside_the_pole_signature_latches``, ``AssertionError:
  position_known must read False after a read 0.04 deg short of the pole, and
  it read True``.
* m8_throttle_removed -- ``app.py`` ``/api/mount/move``: the 60 s test
  ``or now - move_unknown_warned_at[0] >= 60.0`` made ``or True``.
  ``test_the_unknown_position_warning_is_once_a_minute``, ``AssertionError:
  three jogs inside a minute wrote 3 warning lines, not one``.
* m9_status_always_true -- ``hub.py`` status mount block: ``"position_known"``
  published as the constant ``True``.
  ``test_the_status_frame_publishes_position_known_false_for_a_reset_mount``,
  ``AssertionError: status.mount.position_known must be False for a reset
  mount``.
* m10_home_warning_deleted -- ``zwo_am5.py`` ``find_home``: ``if
  self._position_untrusted:`` made ``if False:``.
  ``test_home_on_an_unknown_position_says_the_tube_may_not_have_moved``,
  ``AssertionError: homing an unknown position wrote 0 'may not have moved'
  warnings, not one``.
* m11_stop_takes_unknown_branch -- ``app.py`` ``/api/mount/move``: ``rate !=
  0.0 and`` dropped from ``position_unknown``.
  ``test_a_stop_is_untouched_by_an_unknown_position``, ``AssertionError: a stop
  answers as it always did``.
* m12_base_default_false -- ``base.py``: ``position_known: bool = True`` made
  ``False``. ``test_a_driver_that_says_nothing_is_taken_to_know_where_it_is``
  (and two more).
* m13_trust_position_does_not_clear -- ``zwo_am5.py`` ``trust_position``: the
  clear deleted. ``test_trust_position_clears_it``, ``AssertionError:
  position_known must read True after the operator's attestation, and it read
  False``.
* m14_refused_sync_clears -- ``zwo_am5.py`` ``sync``: the clear moved ahead of
  the refusal's ``raise``. ``test_a_refused_sync_does_not_clear_it``,
  ``AssertionError: position_known must read False after a sync the mount
  refused, and it read True``.
* m15_handshake_reads_both_axes -- ``zwo_am5.py``: ``dec = lx200.parse_dec(await
  self._get("GD"))`` made ``dec = (await self.get_position())[1]``.
  ``test_the_handshake_asks_the_declination_only``, ``AssertionError: the
  handshake read the RA it has no use for``.
* m16_unreadable_handshake_latches -- ``zwo_am5.py``: the advisory read's
  ``except`` also sets ``_position_untrusted = True``.
  ``test_a_handshake_that_cannot_read_the_position_does_not_latch``,
  ``AssertionError: position_known must read True when the handshake could not
  read the position, and it read False``.
* m17_north_pole_only -- ``zwo_am5.py``: ``abs(abs(dec) - 90.0)`` made
  ``abs(dec - 90.0)``. ``test_the_south_pole_is_the_same_signature``,
  ``AssertionError: position_known must read False after a (re)open that read
  the south pole, and it read True``.
* m18_latch_line_every_reopen -- ``zwo_am5.py``: ``if self._position_untrusted
  and not was_untrusted:`` made ``if self._position_untrusted:``.
  ``test_a_second_reopen_at_the_pole_does_not_say_it_again``, ``AssertionError:
  a second reopen at the pole wrote the latch line again (2 in all)``.
* m19_unknown_move_reply_has_no_key -- ``app.py`` ``/api/mount/move``: the
  unknown-position answer made plain ``{"ok": True}``.
  ``test_manual_move_does_not_compute_the_solar_cone_from_an_unknown_position``,
  ``AssertionError: the answer must say the move ran without the cone check``.

Four more, found by the independent verifier as SURVIVORS of the sweep above
and killed by the cases that now follow them:

* m20_throttle_never_expires -- ``app.py`` ``/api/mount/move``: ``now -
  move_unknown_warned_at[0] >= 60.0`` made ``>= 1e9``. It survived because each
  batch of jogs built its own ``create_app()`` (the limiter's state is in the
  route's closure), so "a minute later" always started from a clean slate. Now
  one app spans the minute; ``test_the_unknown_position_warning_is_once_a_minute``,
  ``AssertionError: a jog a minute later must say it again: 1 line(s) in all``.
* m21_status_default_false -- ``hub.py`` status mount block:
  ``getattr(tel, "position_known", True)`` made ``..., False)``. Survived because
  ``SimTelescope`` inherits the class default, so no double in the suite lacked
  the attribute. ``test_the_status_frame_says_true_for_a_driver_that_has_no_such_flag``
  now uses a double that really has none, ``AssertionError: a driver with no such
  flag must publish position_known True, not False``.
* m22_move_default_false -- ``app.py`` ``/api/mount/move``: the same default
  flipped. ``test_a_driver_with_no_flag_is_taken_to_know_where_it_is_on_a_jog``,
  ``AssertionError: a driver with no flag must answer a jog exactly as it always
  did``.
* m23_deadman_not_armed -- ``app.py`` ``/api/mount/move``: ``hub.note_move(...)``
  made ``pass``. The fixture recorded the arming but nothing asserted it, so a
  jog on an unknown position (the one jog nobody is checking) could have lost
  its deadman unseen. ``test_manual_move_does_not_compute_the_solar_cone_from_an_unknown_position``,
  ``AssertionError: a jog on an unknown position must still arm the deadman``.
"""
from __future__ import annotations

import time as real_time

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.devices.backends.zwo_am5 as am5
import astrodeck.events as events_mod
from astrodeck.devices.base import Telescope
from astrodeck.devices.sim import SimTelescope
from _simhub import sim_hub  # noqa: F401  (fixture import)
from test_zwo_am5 import FakeLink, _connect_script, fixed_env  # noqa: F401

#: A made-up mid-latitude declination; not anybody's site and not the pole.
_AWAY = "+40*00:00"
_POLE = "+90*00:00"


def _tel(link: FakeLink) -> am5.ZwoAm5Telescope:
    # A name with no digit in it, so the "no digit reaches the log" assertions
    # below are about coordinates and not about the model number.
    return am5.ZwoAm5Telescope(link, name="Mount")


async def _connect(dec: str, **over) -> tuple[FakeLink, am5.ZwoAm5Telescope]:
    link = FakeLink(_connect_script(GD=dec, **over))
    tel = _tel(link)
    await tel.connect()
    return link, tel


def _record_logs(monkeypatch) -> list[tuple[str, str, str]]:
    lines: list[tuple[str, str, str]] = []

    def record(level, message, source="hub", **_kw):
        lines.append((level, message, source))
    monkeypatch.setattr(events_mod.bus, "log", record)
    return lines


def _unknown(tel, why: str) -> None:
    assert tel.position_known is False, (
        f"position_known must read False {why}, and it read True")


def _known(tel, why: str) -> None:
    assert tel.position_known is True, (
        f"position_known must read True {why}, and it read False")


# ===========================================================================
# the capability
# ===========================================================================


def test_a_driver_that_says_nothing_is_taken_to_know_where_it_is():
    """The base default is True, so every backend and every test double that
    predates the flag behaves exactly as it did."""
    assert Telescope.position_known is True, (
        "the base default must be True: absent means known")
    assert SimTelescope.position_known is True, (
        "a driver that never set the flag must read True")


async def test_trust_position_is_a_no_op_on_a_driver_with_nothing_to_trust():
    """A driver with no reason to doubt its frame has nothing to clear, so the
    base method must not raise (the route it backs is offered to every mount)."""
    class _Bare(SimTelescope):
        pass
    tel = _Bare("bare")
    await tel.trust_position()
    _known(tel, "on a driver that never doubted its frame")


# ===========================================================================
# the AM5 latch
# ===========================================================================


async def test_a_reopen_that_reads_the_home_pole_latches_position_unknown(
        fixed_env):
    """THE LATCH (m1). A mount that has just powered up answers GD with its
    home declination, and the driver cannot tell that from a mount really
    pointing there, so it says it does not know."""
    link, tel = await _connect(_POLE)

    _unknown(tel, "after a (re)open that read the home pole")


async def test_a_mount_that_reads_somewhere_else_is_taken_at_its_word(
        fixed_env):
    """No evidence of a reset is no latch: the flag is for a mount with a
    reason to doubt, not a default of doubt."""
    link, tel = await _connect(_AWAY)

    _known(tel, "after a (re)open that read somewhere other than the pole")


async def test_the_south_pole_is_the_same_signature(fixed_env):
    """Home is the celestial pole of the mount's hemisphere. A southern rig
    reads -90, and a latch keyed on +90 alone would never fire there."""
    link, tel = await _connect("-90*00:00")

    _unknown(tel, "after a (re)open that read the south pole")


async def test_a_read_just_inside_the_pole_signature_latches(fixed_env):
    """m7 (the window narrowed to 0.005 deg). 89 deg 57 min 36 s is 0.04 deg
    short of the pole, inside the 0.05 deg window the reset read sits in."""
    link, tel = await _connect("+89*57:36")

    _unknown(tel, "after a read 0.04 deg short of the pole")


async def test_a_read_just_outside_the_pole_signature_does_not_latch(
        fixed_env):
    """m6 (the window widened to 0.5). 89 deg 54 min is 0.10 deg short of the
    pole: a real pointing close to the pole, not the home read."""
    link, tel = await _connect("+89*54:00")

    _known(tel, "after a read 0.10 deg short of the pole")


async def test_the_latch_logs_one_line_that_carries_no_coordinates(
        fixed_env, monkeypatch):
    """The home position IS the pole, and an altitude read of it is the site
    latitude (#140). The line says what the driver concluded and what clears
    it; it names no angle."""
    logs = _record_logs(monkeypatch)

    link, tel = await _connect(_POLE)

    said = [(lvl, m) for (lvl, m, _src) in logs if "position" in m
            and "unknown" in m]
    assert len(said) == 1, f"the latch wrote {len(said)} lines, not one"
    level, message = said[0]
    assert level == "warning"
    assert "sync" in message and "at home" in message, (
        f"the line must say what clears it: {message!r}")
    assert not any(ch.isdigit() for ch in message), (
        f"a coordinate reached the log: {message!r}")


async def test_a_second_reopen_at_the_pole_does_not_say_it_again(
        fixed_env, monkeypatch):
    """One line per latch, not one per reopen: a mount that keeps dropping its
    link must not bury the night log in the same sentence."""
    logs = _record_logs(monkeypatch)
    link, tel = await _connect(_POLE)
    link.drop()
    await tel.connect()          # the reopen the dawn-park net makes

    said = [m for (_l, m, _s) in logs if "position is unknown" in m]
    assert len(said) == 1, (
        f"a second reopen at the pole wrote the latch line again ({len(said)} in all)")


async def test_the_relink_path_latches_too(fixed_env):
    """A reset is noticed as a dropped link, and the reopen that follows is
    ``_relink``, which shares ``_open_and_handshake`` with ``connect``. A mount
    that was trusted, lost its link and came back at the pole is exactly the
    2026-09-23 power cycle."""
    link, tel = await _connect(_AWAY)
    _known(tel, "before the reset (precondition)")
    link.script["GD"] = _POLE        # the mount restarted while the link was down
    link.drop()

    await tel.get_position()         # any command: the reopen is automatic

    assert link.opens >= 1, "the link was never reopened, so nothing was read"
    _unknown(tel, "after the reopen that followed a reset")


async def test_the_latch_outlives_a_reopen_that_reads_somewhere_else(
        fixed_env):
    """m5. The flag is a LATCH, set by the pole read and cleared only by a sync
    or an attestation. A goto from the wrong model moved the tube somewhere, a
    later reopen reads that somewhere, and nothing about it has been proven."""
    link, tel = await _connect(_POLE)
    link.script["GD"] = _AWAY
    link.drop()

    await tel.connect()

    _unknown(tel, "after a reopen that read somewhere else (a latch, not a reading)")


async def test_a_handshake_that_cannot_read_the_position_does_not_latch(
        fixed_env):
    """No answer is no evidence either way. The connect must still succeed (the
    read is advisory), and a mount nobody could ask is not accused of a reset."""
    script = _connect_script()
    script.pop("GD")                 # unscripted: the double raises on the read
    link = FakeLink(script)
    tel = _tel(link)

    await tel.connect()

    assert tel.connected is True
    _known(tel, "when the handshake could not read the position")


async def test_the_handshake_asks_the_declination_only(fixed_env):
    """The signature is one axis, so one read: a second command in every
    handshake is wire traffic with no use, and a read that also consumed the RA
    would disturb a halt window the reopen has nothing to do with."""
    link, tel = await _connect(_POLE)

    assert link.sent.count("GD") == 1, (
        f"the handshake read GD {link.sent.count('GD')} times, not once")
    assert "GR" not in link.sent, "the handshake read the RA it has no use for"


# --------------------------------------------------------------- what clears it


async def test_a_sync_clears_it(fixed_env):
    """m2. A plate-solved sync is the one measurement of where the tube really
    points, so it is what re-establishes the frame."""
    link, tel = await _connect(_POLE)
    link.script.update({"Sr10:00:00": "1", "Sd+40*00:00": "1", "CM": "Synced"})
    _unknown(tel, "before the sync (precondition)")

    await tel.sync(10.0, 40.0)

    _known(tel, "after a sync the mount accepted")


async def test_a_refused_sync_does_not_clear_it(fixed_env):
    """A sync the mount refused established nothing."""
    link, tel = await _connect(_POLE)
    link.script.update({"Sr10:00:00": "1", "Sd+40*00:00": "1", "CM": "e14"})

    with pytest.raises(Exception):
        await tel.sync(10.0, 40.0)

    _unknown(tel, "after a sync the mount refused")


async def test_trust_position_clears_it(fixed_env):
    """The operator's attestation that the tube is physically at home."""
    link, tel = await _connect(_POLE)

    await tel.trust_position()

    _known(tel, "after the operator's attestation")


async def test_a_goto_does_not_clear_it(fixed_env, monkeypatch):
    """m3. A goto from a wrong model lands wherever the wrong model sends it,
    and the settle poll then reads the mount's own opinion of the arrival. That
    is the weakest witness on the rig, and it proves nothing."""
    monkeypatch.setattr(am5, "SETTLE_POLL_S", 0.01)
    link, tel = await _connect(_POLE)
    link.script.update({"Sr11:00:00": "1", "Sd+45*00:00": "1", "MS": "0",
                        "GR": "11:00:00", "GD": "+45*00:00"})

    await tel.slew(11.0, 45.0)

    _unknown(tel, "after a goto that settled")


# ===========================================================================
# find_home on a mount that does not know where it is
# ===========================================================================


async def test_home_on_an_unknown_position_says_the_tube_may_not_have_moved(
        fixed_env, monkeypatch):
    """m10. After a reset the mount believes it is already home, so ``:hP#``
    completes in about two seconds with no slew (#133, reproduced 2026-09-23).
    The driver cannot make the tube move, but it can stop pretending it did."""
    logs = _record_logs(monkeypatch)
    link, tel = await _connect(_POLE, Gps="0")
    link.script["hP"] = ""
    monkeypatch.setattr(tel, "_park_now", _no_op)

    await tel.find_home()

    said = [m for (lvl, m, _s) in logs
            if lvl == "warning" and "may not have moved" in m]
    assert len(said) == 1, (
        f"homing an unknown position wrote {len(said)} 'may not have moved' "
        "warnings, not one")
    assert not any(ch.isdigit() for ch in said[0]), said[0]
    _unknown(tel, "after homing, which proved nothing about the tube")


async def test_home_on_a_known_position_says_nothing_extra(
        fixed_env, monkeypatch):
    logs = _record_logs(monkeypatch)
    link, tel = await _connect(_AWAY, Gps="0")
    monkeypatch.setattr(tel, "_park_now", _no_op)

    await tel.find_home()

    assert [m for (_l, m, _s) in logs if "may not have moved" in m] == []


async def _no_op() -> None:
    return None


# ===========================================================================
# the routes, with the real driver behind them
# ===========================================================================


class _Clock:
    """``app.time`` with a monotonic clock the test owns; everything else is the
    real module, so nothing else in the request notices."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def __getattr__(self, name):
        return getattr(real_time, name)


@pytest.fixture
def jog(monkeypatch, fixed_env):
    """The shipped ``/api/mount/move`` with ``hub.require`` answering a real
    AM5 driver, and the two hub touches the route makes recorded."""
    solar: list[tuple[float, float]] = []
    armed: list[tuple[str, float]] = []
    logs = _record_logs(monkeypatch)
    clock = _Clock()
    monkeypatch.setattr(app_module, "time", clock)
    monkeypatch.setattr(app_module.hub, "note_move",
                        lambda axis, rate: armed.append((axis, rate)))
    monkeypatch.setattr(app_module.hub, "_check_solar",
                        lambda ra, dec, **kw: solar.append((ra, dec)))

    class Rig:
        pass
    rig = Rig()
    rig.solar, rig.armed, rig.logs, rig.clock = solar, armed, logs, clock

    async def connect(dec):
        rig.link, rig.tel = await _connect(dec)
        monkeypatch.setattr(app_module.hub, "require", lambda role: rig.tel)
        return rig.tel
    rig.connect = connect
    return rig


def _post_move(rate: float, axis: str = "ra"):
    with TestClient(app_module.create_app()) as c:
        return c.post("/api/mount/move", json={"axis": axis, "rate_deg_s": rate})


def _post_moves(client, rates):
    return [client.post("/api/mount/move",
                        json={"axis": "ra", "rate_deg_s": r}) for r in rates]


async def test_manual_move_does_not_compute_the_solar_cone_from_an_unknown_position(
        jog):
    """m4. The cone check measures the Sun's distance from where the mount says
    it points. On a reset mount that is the pole, so the answer is a precise
    number about nothing, and a refusal built on it (or worse, a pass) is a
    lie the operator would trust. The route says it cannot vouch, and moves."""
    tel = await jog.connect(_POLE)

    r = _post_move(0.3)

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "position_known": False}, (
        "the answer must say the move ran without the cone check")
    assert jog.solar == [], (
        "the solar cone was computed from an unknown position "
        f"({len(jog.solar)} call(s))")
    assert ("R7" in jog.link.sent) and ("Me" in jog.link.sent), (
        "the jog itself must still go to the mount")
    assert jog.armed == [("ra", 0.3)], (
        "a jog on an unknown position must still arm the deadman, the one "
        f"thing that stops an axis nobody is watching; armed: {jog.armed}")


async def test_manual_move_still_checks_the_cone_when_the_position_is_known(
        jog):
    """The control for the case above: with a known position nothing changes,
    and the answer carries no extra key."""
    tel = await jog.connect(_AWAY)

    r = _post_move(0.3)

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}, "a known position adds no key to the answer"
    assert len(jog.solar) == 1, (
        f"a known position must be checked against the cone once, "
        f"it was checked {len(jog.solar)} time(s)")


class _OldDriver:
    """A telescope double that predates ``position_known``: it does not have the
    attribute at all, not even by inheriting ``Telescope``'s class default, so it
    is the ``getattr`` default (and nothing else) that answers for it."""

    max_rate_deg_s = None

    def __init__(self) -> None:
        self.moves: list[tuple[str, float]] = []

    async def get_position(self) -> tuple[float, float]:
        return (10.0, 40.0)         # made up; not the pole, not anybody's site

    async def move_axis(self, axis: str, rate: float) -> None:
        self.moves.append((axis, rate))



async def test_a_driver_with_no_flag_is_taken_to_know_where_it_is_on_a_jog(
        jog, monkeypatch):
    """Absent means known (the contract ``Telescope.position_known`` states), so
    the cone is still checked and the answer carries no extra key. Every real
    driver inherits the class default, so only a double like this one reaches
    the ``getattr`` default: a default flipped to False would stop checking the
    cone for any mount that cannot say, and nothing else in the suite would
    notice."""
    old = _OldDriver()
    assert not hasattr(old, "position_known"), "precondition: no such attribute"
    monkeypatch.setattr(app_module.hub, "require", lambda role: old)

    r = _post_move(0.3)

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}, (
        "a driver with no flag must answer a jog exactly as it always did")
    assert len(jog.solar) == 1, (
        "a driver with no flag must be checked against the cone once, it was "
        f"checked {len(jog.solar)} time(s)")
    assert old.moves == [("ra", 0.3)], "the jog itself must still go to the mount"


async def test_a_sync_puts_the_cone_check_back(jog):
    """The check is gated on the flag, not removed: once a sync has cleared the
    latch the very next jog is checked again."""
    tel = await jog.connect(_POLE)
    jog.link.script.update({"Sr10:00:00": "1", "Sd+40*00:00": "1", "CM": "ok"})
    _post_move(0.3)
    assert jog.solar == [], "the unknown-position jog computed the cone (precondition)"

    await tel.sync(10.0, 40.0)
    r = _post_move(0.3)

    assert r.json() == {"ok": True}, "after a sync the jog answers as it always did"
    assert len(jog.solar) == 1, (
        f"after a sync the cone is checked again, once; it was checked "
        f"{len(jog.solar)} time(s)")


async def test_a_stop_is_untouched_by_an_unknown_position(jog):
    """m11. A rate-0 stop is always allowed, never logged about, and answers as
    it always has."""
    tel = await jog.connect(_POLE)

    r = _post_move(0.0)

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}, "a stop answers as it always did"
    assert jog.solar == []
    assert [m for (_l, m, _s) in jog.logs if "solar-cone" in m] == [], (
        "a rate-0 stop wrote the unknown-position warning")
    assert ("Qe" in jog.link.sent) and ("Qw" in jog.link.sent)


async def test_the_unknown_position_warning_is_once_a_minute(jog):
    """m8. The hold-to-move pad re-asserts its rate about every 600 ms, so a line
    per jog is a hundred identical lines a minute. One a minute says it and
    leaves the log readable; the next minute says it again for the next jog."""
    await jog.connect(_POLE)

    # ONE app for the whole case: the rate limit's state lives in the route's
    # closure, so a fresh ``create_app()`` per batch would hand the later batch a
    # clean slate and the "a minute later" half could not fail (a throttle that
    # never expired passed it).
    with TestClient(app_module.create_app()) as c:
        _post_moves(c, [0.3, 0.3, 0.3])
        first = [m for (_l, m, _s) in jog.logs if "solar-cone" in m]
        assert len(first) == 1, (
            f"three jogs inside a minute wrote {len(first)} warning lines, "
            "not one")

        jog.clock.now += 59.0
        _post_moves(c, [0.3])
        still = [m for (_l, m, _s) in jog.logs if "solar-cone" in m]
        assert len(still) == 1, (
            f"a jog 59 s later wrote the warning again ({len(still)} in all)")

        jog.clock.now += 2.0
        _post_moves(c, [0.3, 0.3])
        again = [m for (_l, m, _s) in jog.logs if "solar-cone" in m]
        assert len(again) == 2, (
            f"a jog a minute later must say it again: {len(again)} line(s) in all")


async def test_the_unknown_position_warning_says_what_to_do_and_names_no_angle(
        jog):
    await jog.connect(_POLE)

    _post_move(0.3)

    lines = [(lvl, m) for (lvl, m, _s) in jog.logs if "solar-cone" in m]
    assert len(lines) == 1, jog.logs
    level, message = lines[0]
    assert level == "warning"
    assert "position is unknown" in message
    assert "watch the tube" in message
    assert not any(ch.isdigit() for ch in message), message


async def test_nudge_answers_409_position_unknown_for_a_real_reset_am5(jog):
    """m1, end to end. The route's guard was correct and inert: nothing set the
    flag. With the real driver latched by the pole read, the same request is
    refused with the code the pad keys on, before any position is read."""
    tel = await jog.connect(_POLE)

    handshake = len(jog.link.sent)
    with TestClient(app_module.create_app()) as c:
        r = c.post("/api/mount/nudge", json={"axis": "ra", "arcmin": 10.0})

    assert r.status_code == 409, (
        f"a nudge from a reset mount answered {r.status_code}, not 409: {r.text[:200]}")
    assert r.json()["detail"]["code"] == "position_unknown"
    assert jog.link.sent[handshake:] == [], (
        "the nudge read a position it cannot trust: "
        f"{jog.link.sent[handshake:]}")


# ===========================================================================
# the status frame
# ===========================================================================


async def test_the_status_frame_publishes_position_known_false_for_a_reset_mount(
        sim_hub, fixed_env):
    """m1 and m9. Both UIs lock the step controls and pick the ceiling rung off
    this key, so it is always present in the mount block, and it follows the
    driver both ways (a sync takes it back)."""
    link, tel = await _connect(_POLE)
    link.script.update({"Sr10:00:00": "1", "Sd+40*00:00": "1", "CM": "ok"})
    sim_hub.devices["telescope"] = tel

    before = (await sim_hub.poll_status())["mount"]
    assert before["position_known"] is False, (
        "status.mount.position_known must be False for a reset mount")

    await tel.sync(10.0, 40.0)
    after = (await sim_hub.poll_status())["mount"]
    assert after["position_known"] is True, (
        "status.mount.position_known must follow the driver back after a sync")


class _FlagBlind:
    """The sim telescope with ``position_known`` taken away entirely: the
    attribute does not exist on it, by instance or by inheritance, which is what
    a driver written before the flag looks like to the status poll."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def __getattr__(self, name):
        if name == "position_known":
            raise AttributeError(name)
        return getattr(self._inner, name)


async def test_the_status_frame_says_true_for_a_driver_that_has_no_such_flag(
        sim_hub):
    """The key is ALWAYS present: a double that predates the capability reads
    True through the same ``getattr`` the nudge route uses, so a client never
    has to tell 'absent' from 'known'. The double really has no such attribute
    (``SimTelescope`` alone would not do: it inherits the class default, so a
    ``getattr`` default flipped to False could not be seen through it)."""
    tel = _FlagBlind(sim_hub.devices["telescope"])
    assert not hasattr(tel, "position_known"), "precondition: no such attribute"
    sim_hub.devices["telescope"] = tel

    m = (await sim_hub.poll_status())["mount"]

    assert m.get("position_known") is True, (
        "a driver with no such flag must publish position_known True, "
        f"not {m.get('position_known', 'nothing')!r}")

