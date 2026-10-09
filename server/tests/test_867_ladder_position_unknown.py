# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#867: the resume ladder does not slew a mount whose driver says it does
not know where it points.

Step 2 of the ladder (the blind solve and sync after a restart) went on to
step 3's goto when the mount refused the sync but its reported pointing was
within ``RECOVERY_REFUSED_SYNC_MAX_DEG`` of the solved field. Near the pole an
angular separation cannot see the RA axis angle: a tube at Dec +90 points at
the pole whatever the hour angle, so a mount whose model reports home while
its RA axis has turned reads a separation near zero, and the goto is aimed
from a wrong hour angle (into the pier, at worst).

The evidence that is not blind is the driver's ``position_known``: the AM5
latches it False when a (re)open reads its home pole, which is what a reset
looks like, and only a sync or Trust position clears it. So:

* the refused near arm holds (``POSITION_UNKNOWN_WORDS``) while the driver
  says the position is unknown;
* an ACCEPTED sync holds too (``POSITION_UNKNOWN_SYNC_WORDS``) when the
  driver still says unknown. The AM5 driver no longer clears the latch on a
  sync within ``SYNC_POLE_BLIND_DEG`` of a pole, where its own read-back is
  blind the same way: the bench saw one ``N/A`` at home that moved nothing,
  and its read-back passes as taken when the reported pole is within 0.05
  deg of the solved field. INTERMITTENT on the rig, so scripted here;
* a rig with no solver holds too (ruling R2,
  ``POSITION_UNKNOWN_NO_SOLVER_WORDS``).
* and step 3 asks the driver again right before its goto, since a link
  that reopens during the slew-limit check latches the AM5 on the link's
  clock (``POSITION_UNKNOWN_RECENTRE_WORDS``).

Every hold gives the safe order: Trust position if the tube is at home;
else bring it home by eye with a pad key, then Trust it. Never a goto.

Each test names the mutant it was shown red under (of resume_arm.py, or of
zwo_am5.py for the seam); every mutant was applied to a byte copy of its
file and the file was restored from that copy (sha256 checked).

Every coordinate here is made up.
"""
from __future__ import annotations

import re

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)
from test_850_resume_sync_refused import (  # noqa: F401 (fixtures too)
    _CUT, _Connected, _Hub, _arm, _held_line, _humanizer_rewrites,
    _isolated_sessions, _refusal, _session, _surfaced_ok, _warnings, fp)
from test_zwo_am5 import FakeLink, _connect_script, fixed_env  # noqa: F401

import astrodeck.devices.backends.zwo_am5 as am5
import astrodeck.sequence.resume_arm as ra
from astrodeck.devices import lx200

#: A made-up RA for the synced field. Never printed.
_RA_H = 7.4321

_CENTRED = {"centered": True, "error_arcmin": 0.3, "attempts": 1}


class _Unknown:
    connected = True
    position_known = False


class _Known:
    connected = True
    position_known = True


class _SyncingHub(_Hub):
    """A hub whose solve really syncs the telescope it holds, with the real
    keyword signature."""

    def __init__(self, *, sync_to, **kw):
        super().__init__(**kw)
        self._sync_to = sync_to

    async def solve_and_sync(self, exposure_s: float = 3.0, **kw):
        self.calls.append("solve")
        await self.devices["telescope"].sync(*self._sync_to)
        return {"ok": True}


def _hub_with(tel, **kw) -> _Hub:
    hub = _Hub(**kw)
    hub.devices["telescope"] = tel
    return hub


async def _latched_am5() -> tuple[FakeLink, am5.ZwoAm5Telescope]:
    """A real AM5 driver on a FakeLink whose connect reads the home pole, so
    it latches ``position_known`` False. Named without a digit so the
    no-digit checks are about figures, not the model number."""
    link = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(link, name="Mount")
    await tel.connect()
    return link, tel


def _script_noop(link: FakeLink, ra_h: float, dec: float) -> None:
    """``:CM#`` answers ``N/A`` and nothing moves: the reported position
    stays at the home pole (the bench's ignored sync at home)."""
    link.script.update({f"Sr{lx200.format_ra(ra_h)}": "1",
                        f"Sd{lx200.format_dec(dec)}": "1", "CM": "N/A"})


def _script_moved(link: FakeLink, ra_h: float, dec: float) -> None:
    """``:CM#`` answers ``N/A`` and the reported position then reads the
    synced coordinates: a sync the mount took."""
    ra_s, dec_s = lx200.format_ra(ra_h), lx200.format_dec(dec)

    def cm(_cmd):
        link.script["GR"] = ra_s
        link.script["GD"] = dec_s
        return "N/A"
    link.script.update({f"Sr{ra_s}": "1", f"Sd{dec_s}": "1", "CM": cm})


def _still_unknown_warnings(bus_lines) -> list[str]:
    return [m for lvl, m, s in bus_lines
            if lvl == "warning" and s == "mount"
            and "position still unknown" in m]


# ===================================================== the ladder (doubles)


@pytest.mark.parametrize("residual", [0.002, 1.0, 4.25, 5.0])
async def test_a_near_refusal_with_the_position_unknown_holds(
        monkeypatch, bus_lines, residual):
    """The mount refused the blind solve's sync with its reported pointing
    within the 5 deg bound, and its driver says its position is unknown: no
    slew, a hold in the safe order, and one warning with the measurement.

    NAMED MUTANT M867a "the near arm ignores position_known" (the new ``if
    not self._mount_position_known(): ...`` block in the refused arm
    deleted): RED, ``hub.calls == ["solve", "center"]``.
    """
    hub = _hub_with(_Unknown(), solve_raises=_refusal(residual),
                    centring=_CENTRED)
    arm = _arm(hub, monkeypatch)
    reason = await arm._recover(_session())
    assert hub.calls == ["solve"], "nothing may slew"
    assert reason == ra.POSITION_UNKNOWN_WORDS, reason
    assert arm._recentred is None
    assert arm._ladder_light == "lit", "the frame solved; light reached it"
    warns = _warnings(bus_lines)
    said = [m for m in warns
            if "nothing has confirmed its position since a reconnect or a "
               "stopped run put it in doubt" in m]
    assert len(said) == 1, warns
    assert f"{residual:.1f} deg" in said[0], said
    assert "(reply 'e11')" in said[0], said
    assert not any("the re-centre goes ahead" in m for m in warns), warns
    for m in warns:
        _surfaced_ok(m)
    _surfaced_ok(_held_line(reason))


@pytest.mark.parametrize("tel", [_Known(), _Connected()],
                         ids=["known", "no-attribute"])
async def test_a_near_refusal_with_the_position_known_still_goes_ahead(
        monkeypatch, bus_lines, tel):
    """Controls: a driver that vouches for its position, and one with no
    ``position_known`` at all (read as known, the contract every consumer
    keeps), go ahead as before.

    NAMED MUTANT M867c "every near refusal holds" (``if not
    self._mount_position_known()`` in the refused arm made ``if True``):
    RED on both.
    NAMED MUTANT M867b "the default flipped" (the helper's ``getattr(tel,
    "position_known", True)`` read with default ``False``): RED on
    ``no-attribute`` only.
    """
    hub = _hub_with(tel, solve_raises=_refusal(0.4), centring=_CENTRED)
    arm = _arm(hub, monkeypatch)
    assert await arm._recover(_session()) is None
    assert hub.calls == ["solve", "center"]
    assert any("the re-centre goes ahead" in m
               for m in _warnings(bus_lines)), bus_lines


async def test_an_accepted_sync_with_the_position_unknown_holds(
        monkeypatch, bus_lines):
    """The solve and its sync returned, but the driver still says unknown
    (an AM5 synced near the pole): no slew, and no ladder warning of its own
    (the driver's warning beside it says what was measured). Control in the
    same test: a driver with no ``position_known`` goes ahead.

    NAMED MUTANT M867h "no check after an accepted sync" (the new
    success-path ``if not self._mount_position_known(): return ...``
    deleted): RED, ``calls == ["solve", "center"]``.
    """
    hub = _hub_with(_Unknown(), solve_raises=None, centring=_CENTRED)
    arm = _arm(hub, monkeypatch)
    reason = await arm._recover(_session())
    assert hub.calls == ["solve"], "nothing may slew"
    assert reason == ra.POSITION_UNKNOWN_SYNC_WORDS, reason
    assert arm._ladder_light == "lit"
    assert not [m for lvl, m, s in bus_lines
                if lvl == "warning" and s == "sequence"], bus_lines

    control = _hub_with(_Connected(), solve_raises=None, centring=_CENTRED)
    assert await _arm(control, monkeypatch)._recover(_session()) is None
    assert control.calls == ["solve", "center"]


async def test_no_solver_and_the_position_unknown_holds(monkeypatch,
                                                        bus_lines):
    """Ruling R2: a rig with no solver resumes on the mount's model by the
    owner's standing choice, which assumes the model is real. A driver that
    says its position is unknown holds instead, with no motion. Control: a
    driver that vouches goes ahead with today's warning.

    NAMED MUTANT M867o "no-solver gate deleted" (the new ``if not
    self._mount_position_known(): return POSITION_UNKNOWN_NO_SOLVER_WORDS``
    in the no-solver branch deleted): RED, ``calls == ["center"]``.
    """
    hub = _hub_with(_Unknown(), centring=_CENTRED)
    arm = _arm(hub, monkeypatch)
    monkeypatch.setattr(arm, "_can_solve", lambda: False)
    reason = await arm._recover(_session())
    assert hub.calls == [], "nothing may slew"
    assert reason == ra.POSITION_UNKNOWN_NO_SOLVER_WORDS, reason
    assert not any("WITHOUT verifying" in m for m in _warnings(bus_lines))

    control = _hub_with(_Known(), centring=_CENTRED)
    arm = _arm(control, monkeypatch)
    monkeypatch.setattr(arm, "_can_solve", lambda: False)
    assert await arm._recover(_session()) is None
    assert control.calls == ["center"]
    assert any("WITHOUT verifying" in m for m in _warnings(bus_lines))


class _Relinking:
    """A telescope that vouches until something reopens its link."""
    connected = True

    def __init__(self):
        self.position_known = True


def _relinking_arm(monkeypatch, *, relink: bool):
    """The ladder over a telescope that vouches through step 2, with the
    slew-limit check standing in for the read that runs to its bound while
    the link reopens: inside it the driver latches (``relink``) or not."""
    tel = _Relinking()
    hub = _hub_with(tel, solve_raises=None, centring=_CENTRED)
    arm = _arm(hub, monkeypatch)

    async def limits(target, *, cfg=None, plan=None, projected=True):
        hub.calls.append("limits")
        if relink:
            tel.position_known = False
        return None
    monkeypatch.setattr(arm.engine, "check_slew_limits", limits)
    return hub, arm


async def test_a_relink_during_the_limit_check_stops_the_goto(
        monkeypatch, bus_lines):
    """Step 2 passed (the solve's sync returned and the driver vouched), and
    the driver latches while step 3's slew-limit check awaits: the goto does
    not go out. The latch is set on the link's clock, so the ladder reads it
    again right before the goto. Control: no relink, the re-centre goes
    ahead.

    NAMED MUTANT M867q "no re-check before the goto" (step 3's new ``if not
    self._mount_position_known(): return POSITION_UNKNOWN_RECENTRE_WORDS``
    deleted): RED, ``calls == ["solve", "limits", "center"]``.
    """
    hub, arm = _relinking_arm(monkeypatch, relink=True)
    reason = await arm._recover(_session())
    assert hub.calls == ["solve", "limits"], "nothing may slew"
    assert reason == ra.POSITION_UNKNOWN_RECENTRE_WORDS, reason
    assert arm._recentred is None

    hub, arm = _relinking_arm(monkeypatch, relink=False)
    assert await arm._recover(_session()) is None
    assert hub.calls == ["solve", "limits", "center"]


# ================================================ the seam (the real driver)


@pytest.mark.parametrize("kind,dec", [("noop", 89.97), ("moved", 88.0),
                                      ("moved", -88.0), ("moved", 85.1)],
                         ids=["noop-89.97", "moved-88.0", "moved-south-88.0",
                              "moved-85.1"])
async def test_an_accepted_sync_near_the_pole_leaves_the_position_unknown(
        fixed_env, bus_lines, kind, dec):
    """A sync within ``SYNC_POLE_BLIND_DEG`` of either pole does not clear
    the latch, whether the mount ignored it (``noop``: the read-back is 0.03
    deg from the reported pole, so it passes as taken) or moved; the driver
    says so once, in the safe order, with no figure.

    NAMED MUTANT M867i "the seam reverted" (the clear back to an
    unconditional ``self._position_untrusted = False``): RED on all four.
    NAMED MUTANT M867j "abs dropped" (``if dec_deg <= 90.0 -
    SYNC_POLE_BLIND_DEG``): RED on ``moved-south-88.0``.
    NAMED MUTANT M867k "radius zero" (``SYNC_POLE_BLIND_DEG = 0.0``): RED on
    all four.
    """
    link, tel = await _latched_am5()
    assert tel.position_known is False, "premise: the connect latched"
    (_script_noop if kind == "noop" else _script_moved)(link, _RA_H, dec)
    await tel.sync(_RA_H, dec)
    assert tel.position_known is False
    said = _still_unknown_warnings(bus_lines)
    assert len(said) == 1, bus_lines
    assert not re.search(r"\d", said[0]), said
    assert "then Trust it." in said[0][:_CUT], said
    assert not _humanizer_rewrites(said[0]), said


#: A mount name the operator might give (``conn.extra["name"]``), long enough
#: that a name put before the action pushes "then Trust it." past the cut.
_LONG_NAME = "Backyard observatory east pier"


@pytest.mark.parametrize("name", [None, "Twenty-char mount AB", _LONG_NAME],
                         ids=["default", "twenty", "thirty"])
async def test_the_still_unknown_warning_keeps_its_action_whatever_the_name(
        fixed_env, bus_lines, name):
    """The driver's warning holds the whole action, in the safe order,
    inside the UI's 137-char cut whatever the mount is called: the default
    name, a twenty-character one, a thirty-character one. The name goes
    after the action, so its length cannot cut it.

    NAMED MUTANT M867p "name first" (the warning starts ``f"{self.name}: "``
    ahead of the action, as the other mount lines do): RED on ``thirty``,
    "then Trust it." ends past the cut.
    """
    link = FakeLink(_connect_script())
    tel = (am5.ZwoAm5Telescope(link) if name is None
           else am5.ZwoAm5Telescope(link, name=name))
    await tel.connect()
    assert tel.position_known is False, "premise: the connect latched"
    _script_noop(link, _RA_H, 89.97)
    await tel.sync(_RA_H, 89.97)
    said = _still_unknown_warnings(bus_lines)
    assert len(said) == 1, bus_lines
    assert tel.name in said[0], said
    head = said[0][:_CUT]
    for part in ("position still unknown", "Trust position", "pad key",
                 "then Trust it."):
        assert part in head, (part, head)
    assert (head.index("Trust position") < head.index("pad key")
            < head.index("then Trust it.")), head
    low = said[0].lower()
    for banned in ("goto", "go to", "slew to", "head to"):
        assert banned not in low, (banned, said[0])


@pytest.mark.parametrize("dec", [84.9, 40.0, -40.0])
async def test_an_accepted_sync_away_from_the_pole_still_clears_it(
        fixed_env, bus_lines, dec):
    """Outside the radius a sync the read-back proved re-establishes the
    frame, as before.

    NAMED MUTANT M867l "radius widened" (``SYNC_POLE_BLIND_DEG = 10.0``): RED
    on ``84.9``.
    """
    link, tel = await _latched_am5()
    assert tel.position_known is False, "premise: the connect latched"
    _script_moved(link, _RA_H, dec)
    await tel.sync(_RA_H, dec)
    assert tel.position_known is True
    assert _still_unknown_warnings(bus_lines) == []


async def test_a_near_pole_sync_on_a_known_position_says_nothing(
        fixed_env, bus_lines):
    """A position already known stays known, and nothing is said: the seam
    only stops a near-pole sync from CLEARING the latch, it never sets it.

    NAMED MUTANT M867m "warns whatever the latch" (``elif
    self._position_untrusted:`` made ``else:``): RED.
    """
    link, tel = await _latched_am5()
    await tel.trust_position()
    _script_noop(link, _RA_H, 89.97)
    await tel.sync(_RA_H, 89.97)
    assert tel.position_known is True
    assert _still_unknown_warnings(bus_lines) == []


# =================================== the seam with the ladder (real driver)


async def test_a_real_am5_that_read_its_home_pole_holds_until_trust_position(
        fixed_env, monkeypatch, bus_lines):
    """The real driver, latched by a connect that read the home pole, behind
    the ladder: the refused near arm holds; after Trust position the same
    driver lets the re-centre go ahead. This pins that the ladder's helper
    and the REAL driver agree on the contract's name: tests above use
    doubles that define ``position_known`` themselves.

    NAMED MUTANT M867d "the driver's property renamed" (in a byte-backup
    copy of zwo_am5.py, ``def position_known`` renamed ``def
    position_is_known``): RED here (the ladder reads the default True and
    goes ahead), while the double-driven ladder tests above stay GREEN.
    """
    _link, tel = await _latched_am5()
    assert any(lvl == "warning" and s == "mount"
               and "position is unknown" in m
               for lvl, m, s in bus_lines), "premise: the connect latched"

    hub = _hub_with(tel, solve_raises=_refusal(0.4), centring=_CENTRED)
    reason = await _arm(hub, monkeypatch)._recover(_session())
    assert reason == ra.POSITION_UNKNOWN_WORDS, reason
    assert hub.calls == ["solve"]

    await tel.trust_position()
    hub = _hub_with(tel, solve_raises=_refusal(0.4), centring=_CENTRED)
    assert await _arm(hub, monkeypatch)._recover(_session()) is None
    assert hub.calls == ["solve", "center"]


async def test_a_real_am5_that_ignored_a_near_pole_sync_holds_on_every_retry(
        fixed_env, monkeypatch, bus_lines):
    """The intermittent half of #867, scripted: the solve's sync at the home
    pole answers ``N/A`` and moves nothing, which the read-back passes as
    taken. The ladder holds, and holds again on the next retry (the driver
    kept the latch), until Trust position.

    NAMED MUTANT M867i (the seam reverted): RED on the first ladder (it goes
    ahead). NAMED MUTANT M867h (no success-path check): RED on the first
    ladder.
    """
    link, tel = await _latched_am5()
    _script_noop(link, _RA_H, 89.97)
    for _ in range(2):
        hub = _SyncingHub(sync_to=(_RA_H, 89.97), centring=_CENTRED)
        hub.devices["telescope"] = tel
        reason = await _arm(hub, monkeypatch)._recover(_session())
        assert reason == ra.POSITION_UNKNOWN_SYNC_WORDS, reason
        assert hub.calls == ["solve"], "nothing may slew"

    await tel.trust_position()
    hub = _SyncingHub(sync_to=(_RA_H, 89.97), centring=_CENTRED)
    hub.devices["telescope"] = tel
    assert await _arm(hub, monkeypatch)._recover(_session()) is None
    assert hub.calls == ["solve", "center"]


# ================================================================ the words

_HOLDS = {
    "refused": ("POSITION_UNKNOWN_WORDS", "refused", "accepted"),
    "accepted": ("POSITION_UNKNOWN_SYNC_WORDS", "accepted", "refused"),
    "no-solver": ("POSITION_UNKNOWN_NO_SOLVER_WORDS", "No solver",
                  "refused"),
    "recentre": ("POSITION_UNKNOWN_RECENTRE_WORDS", "stopped vouching",
                 "refused"),
}


@pytest.mark.parametrize("which", list(_HOLDS))
async def test_the_position_unknown_holds_are_fixed_words_in_the_safe_order(
        monkeypatch, bus_lines, which):
    """Each hold: no digit; the UI shows our words; the action whole inside
    the 137-char cut of the "auto-resume held: ..." line, in the safe order
    (Trust position, then the pad key, then Trust again); no goto, slew or
    target advised; its cause named and no other; and the identical words
    whatever the measurement, so the hold keeps its ``since``.

    NAMED MUTANT M867e "cause first" (POSITION_UNKNOWN_WORDS reordered to
    start "The mount refused the sync, and nothing has confirmed its
    position since it reconnected; " + the action): RED on ``refused``,
    "pad key" falls past the cut.
    NAMED MUTANT M867f "a goto advised" (", or goto a star away from the
    pole" inserted after "Trust position" in the action): RED on all four.
    NAMED MUTANT M867g "figure in the hold" (the refused arm returns
    ``f"{POSITION_UNKNOWN_WORDS} ({residual:.1f} deg)"``): RED on
    ``refused``, the identical-hold check.
    NAMED MUTANT M867n "step 2 without its Trust" (", then Trust it." cut
    from the action): RED on all four.
    """
    name, cause, not_cause = _HOLDS[which]
    words = getattr(ra, name)
    assert not re.search(r"\d", words), words
    for text in (words, _held_line(words), "auto-resume held: " + words):
        assert not _humanizer_rewrites(text), text
    for head in (_held_line(words)[:_CUT],
                 ("auto-resume held: " + words)[:_CUT]):
        for part in ("not slewing", "position unknown", "Trust position",
                     "by eye", "pad key", "then Trust it."):
            assert part in head, (part, head)
        assert (head.index("Trust position") < head.index("pad key")
                < head.index("then Trust it")), head
    low = words.lower()
    for banned in ("goto", "go to", "slew to", "head to"):
        assert banned not in low, (banned, words)
    assert cause in words and not_cause not in words, words

    # The same constant whatever the measurement, driven through the ladder.
    got = []
    for residual in (0.002, 4.25):
        if which == "refused":
            hub = _hub_with(_Unknown(), solve_raises=_refusal(residual),
                            centring=_CENTRED)
            arm = _arm(hub, monkeypatch)
        elif which == "accepted":
            hub = _hub_with(_Unknown(), solve_raises=None, centring=_CENTRED)
            arm = _arm(hub, monkeypatch)
        elif which == "recentre":
            hub, arm = _relinking_arm(monkeypatch, relink=True)
        else:
            hub = _hub_with(_Unknown(), centring=_CENTRED)
            arm = _arm(hub, monkeypatch)
            monkeypatch.setattr(arm, "_can_solve", lambda: False)
        got.append(await arm._recover(_session()))
    assert got == [words, words], got
