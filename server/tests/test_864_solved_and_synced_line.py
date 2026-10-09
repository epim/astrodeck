# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#864: the "solved & synced" line carries no coordinate.

``Hub.solve_and_sync`` used to log ``solved & synced: RA <h> Dec <d>
(J2000)`` after every sync the mount accepted. Near the pole on a stationary
mount the solved RA follows local sidereal time, so the RA plus the line's
timestamp gives the site longitude (the #140 / #166 class: a value the code
computes and prints itself). The line now says what happened and no more.

Both mount UIs match the line by its prefix only
(``startsWith("solved & synced")`` in ui/src/views/MountView.tsx and
ui/src/next/hubs/rig/sheets/mount.tsx), so the prefix is pinned too.

Every coordinate here is fictional and deliberately non-round, away from the
pole, so nothing about the AM5's near-pole sync rule (#867) bears on the sim
mount's sync.

Each test names the mutant of hub.py it was shown red under; the mutants were
applied to a byte copy of hub.py and the file was restored from that copy.
"""
from __future__ import annotations

from _simhub import sim_hub  # noqa: F401 (fixture import)
from test_zwo_am5 import fixed_env  # noqa: F401 (fixture import)

from astrodeck.solve.base import SolveResult

#: Where the fake solver says the field is. Fictional, non-round.
SOLVE_RA, SOLVE_DEC = 5.4321, 48.7654

#: The success line, exactly.
SUCCESS_LINE = "solved & synced: the mount accepted the solved position"


class _FixedSolver:
    """A solver that always finds the field at one place."""
    name = "Fixed"

    def __init__(self, ra: float, dec: float):
        self.ra, self.dec = ra, dec

    async def solve(self, fits_path, *, ra_hint=None, dec_hint=None,
                    fov_deg_hint=None):
        return SolveResult(True, ra_hours=self.ra, dec_deg=self.dec,
                           pixel_scale_arcsec=1.55, message="fixed")


def _use_solver(monkeypatch, ra: float, dec: float) -> None:
    import astrodeck.providers as providers_module
    solver = _FixedSolver(ra, dec)
    monkeypatch.setattr(providers_module, "pick_solver", lambda hub: solver)


def _coordinate_spellings(ra: float, dec: float) -> list[str]:
    """The ways a log line could spell the solve: the default
    ``repr``/``str``/f-string of each value, of the pair, and of the RA in
    degrees, and the fixed-precision forms at 2 to 4 places with and without
    a sign. Precision 1 is left out on purpose: "5.4" can collide with the
    figure of the "plate solving with ... (fov hint X)" line and give a red
    that has nothing to do with this line."""
    out = [repr((ra, dec)), str((ra, dec)), f"{(ra, dec)}", f"{ra}, {dec}",
           str([ra, dec])]
    for v in (ra, dec, ra * 15.0):
        out += [repr(v), f"{v}", str(v)]
        for p in range(2, 5):
            out += [f"{v:.{p}f}", f"{v:+.{p}f}"]
    return sorted(set(out))


async def test_the_success_line_carries_no_coordinate(
        sim_hub, monkeypatch, bus_lines):
    """The sim mount takes the sync; the line says so in fixed words, and no
    line at all spells the solve. The caller still gets the solve back.

    NAMED MUTANT M864a "coordinates put back" (the old f-string with RA and
    Dec restored at the success line in hub.py): RED, the exact-equality
    assertion fails on 'solved & synced: RA 5.4321h Dec +48.765...'.
    NAMED MUTANT M864b "coordinates moved to a second line" (the new line
    kept and ``bus.log("info", f"sync position {result.ra_hours:.4f}h",
    "solve")`` added after it): RED on the spelling scan ('5.4321'), which
    proves the scan covers every line, not just the success line.
    """
    _use_solver(monkeypatch, SOLVE_RA, SOLVE_DEC)
    out = await sim_hub.solve_and_sync(exposure_s=0.05)
    assert out["ra_hours"] == SOLVE_RA and out["dec_deg"] == SOLVE_DEC

    success = [line for line in bus_lines
               if line[1].startswith("solved & synced")]
    assert success == [("info", SUCCESS_LINE, "solve")], bus_lines
    for _, message, _ in bus_lines:
        for spelling in _coordinate_spellings(SOLVE_RA, SOLVE_DEC):
            assert spelling not in message, (spelling, message)


async def test_the_success_line_keeps_the_prefix_both_mount_uis_match(
        sim_hub, monkeypatch, bus_lines):
    """Both mount UIs fire their "synced" toast on a line that starts with
    "solved & synced"; the line keeps that prefix and fits the UI's
    137-character cut whole.

    NAMED MUTANT M864c "prefix dropped" (the line reworded to "synced: the
    mount accepted the solved position"): RED, no line starts with the
    prefix.
    """
    _use_solver(monkeypatch, SOLVE_RA, SOLVE_DEC)
    await sim_hub.solve_and_sync(exposure_s=0.05)
    lines = [m for lvl, m, s in bus_lines
             if s == "solve" and "the mount accepted the solved position" in m]
    assert len(lines) == 1, bus_lines
    assert lines[0].startswith("solved & synced"), lines
    assert len(lines[0]) <= 137, lines


async def test_a_sync_the_driver_cannot_vouch_for_is_not_solved_and_synced(
        sim_hub, monkeypatch, bus_lines, fixed_env):
    """The real AM5 driver behind the real ``solve_and_sync``: latched by a
    connect that read its home pole, then synced at +89.97 with an ``N/A``
    that moves nothing. The read-back passes, so the sync returns, but the
    driver keeps its position unknown (#867) and warns in the safe order.
    The hub's line must not then say "solved & synced", which both mount
    UIs toast as "the mount's model now agrees with where the camera is
    pointing", an invitation to slew from a position nobody knows. After
    Trust position the same sync gives the success line again (control).
    Made-up near-pole coordinates; nothing here prints them.

    NAMED MUTANT M864d "the branch removed" (in hub.py the new ``if
    getattr(tel, "position_known", True):`` made ``if True:``): RED, the
    first half finds a line starting "solved & synced".
    """
    from test_867_ladder_position_unknown import (_RA_H, _latched_am5,
                                                  _script_noop)
    link, tel = await _latched_am5()
    _script_noop(link, _RA_H, 89.97)
    sim_hub.devices["telescope"] = tel
    _use_solver(monkeypatch, _RA_H, 89.97)

    await sim_hub.solve_and_sync(exposure_s=0.05)
    assert tel.position_known is False, "premise: the driver kept the latch"
    solve_lines = [m for _, m, s in bus_lines if s == "solve"]
    assert not any(m.startswith("solved & synced") for m in solve_lines), (
        solve_lines)
    said = [m for m in solve_lines
            if m.startswith("solved; the mount accepted the sync, but it "
                            "still cannot vouch for its position")]
    assert len(said) == 1, solve_lines
    assert "plate" not in said[0] and len(said[0]) <= 137, said
    assert any(lvl == "warning" and s == "mount"
               and "position still unknown" in m
               for lvl, m, s in bus_lines), bus_lines

    await tel.trust_position()
    bus_lines.clear()
    await sim_hub.solve_and_sync(exposure_s=0.05)
    assert [line for line in bus_lines
            if line[1].startswith("solved & synced")] == [
        ("info", SUCCESS_LINE, "solve")], bus_lines
