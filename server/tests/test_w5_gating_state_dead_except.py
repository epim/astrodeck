"""resume_arm.py's ``_gating_state`` no longer swallows a site-read error it
can no longer receive (#611, the #543 class: a defensive branch a fix made
dead).

WHAT WAS THERE. ``_gating_state`` wrapped ``schedule.gating_status(...)
["state"]`` in ``except (KeyError, TypeError, ValueError): return "ready"``,
documented as the fallback for a site the schedule could not read -- the
same fallback ``ResumeArm._walk`` made before #543 removed it for the same
reason.

WHY IT IS DEAD NOW. ``schedule.gating_status`` and its ``constraint_gate``
helper read coordinates through ``site_gate.site_lat_lon`` (the "NO SITE
(#540)" comments in ``schedule.py``), which answers ``None`` for a site it
cannot read rather than raising. Reachability was checked the way #543's own
verifier counted ``_walk``'s callers: every test file that calls
``_gating_state`` or ``recentre_candidates`` directly, PLUS the nine files
#543's verifier used for ``_walk`` (the same ladder machinery calls both
functions) -- 16 files, 366 tests. Run from a byte backup of
``resume_arm.py`` with the except narrowed to ``except ZeroDivisionError``,
every one of the 366 still passed: nothing reaches the branch any more.

THE REGRESSION GUARD THIS FILE ADDS. Deleting dead code changes no observed
behaviour today, so the pin here is on the CONTRACT the deletion leaves
behind: ``_gating_state`` now lets whatever ``schedule.gating_status`` raises
through, rather than reporting a site problem as "nothing is wrong". If a
later change ever reintroduces a raise on that path, this test is what
notices -- not a passerby reading old a comment that describes code no
longer there.

The mutant was applied to a byte-for-byte backup of the file it changes and
the file was restored byte-identical (SHA-256 compared) afterwards. The
failure is quoted as observed.
"""
from __future__ import annotations

import pytest

from astrodeck.sequence import resume_arm as resume_arm_mod
from astrodeck.sequence.models import Target


def _target() -> Target:
    # The fields are never read: ``gating_status`` itself is replaced below,
    # so only ``_gating_state``'s own handling (or lack of it) is under test.
    return Target(name="T", ra_hours=5.0, dec_deg=20.0)


@pytest.mark.parametrize("exc", [KeyError("latitude"), TypeError("x"),
                                 ValueError("y")])
def test_gating_state_lets_a_site_read_error_through(monkeypatch, exc):
    """Each of the three exceptions the old except named propagates out of
    ``_gating_state`` instead of being read as "ready".

    RED under mutant "the except comes back" (``_gating_state``: ``return
    schedule.gating_status(target, site, twilight_deg, now)["state"]``
    wrapped again in ``try: ... except (KeyError, TypeError, ValueError):
    return "ready"``), observed verbatim (``KeyError`` case; the other two
    parametrisations fail the same way, each on its own exception type):

        _______________ test_gating_state_lets_a_site_read_error_through[exc0] ________________
        E   Failed: DID NOT RAISE <class 'KeyError'>
    """
    def _boom(target, site, twilight_deg, now):
        raise exc

    monkeypatch.setattr(resume_arm_mod.schedule, "gating_status", _boom)

    with pytest.raises(type(exc)):
        resume_arm_mod._gating_state(_target(), {}, -12.0, 0.0)


def test_control_a_normal_gating_status_answer_passes_through(monkeypatch):
    """CONTROL: nothing about a well-behaved ``gating_status`` changes --
    ``_gating_state`` still just unwraps ``["state"]``."""
    monkeypatch.setattr(
        resume_arm_mod.schedule, "gating_status",
        lambda target, site, twilight_deg, now: {"state": "waiting"})

    assert resume_arm_mod._gating_state(_target(), {}, -12.0, 0.0) == "waiting"
