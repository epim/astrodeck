"""#229 — auto-resume kept trying in broad daylight, forever.

Measured on the rig 2026-08-11. The night ended, the dawn daemon parked the
mount at 05:52, and this was still in the log at 07:36, ninety minutes after
sunrise::

    07:15:37 [warn] auto-resume held: blind plate solve failed after restart
                    (plate solve failed: Not enough stars.)
    07:25:53 [warn] (same)
    07:36:08 [warn] (same)

Every one of those cost a 4 s exposure and a full ASTAP run. The REFUSAL was
right — it will not slew a mount whose pointing it cannot verify — but nothing
ever stopped it asking. The cause is that a default ``Schedule`` is
``start_mode="now"`` / ``stop_mode="none"``, so ``resolve_window`` returns
``(now, None)`` and ``_window_open``'s test reduces to ``now <= now and True``.

These tests drive the REAL ``_window_open`` (the existing suite monkeypatches it
out, which is exactly why the hole survived) against a real site and a real sky.
"""
from __future__ import annotations

import time

import pytest

import astrodeck.hub as hub_module
from astrodeck.sequence import schedule
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.resume_arm import ResumeArm
from astrodeck.sequence.session import Session

# A real mid-latitude site — NOT the observatory's. 40°N/105°W has a proper
# night in June, which is all this needs.
SITE = {"latitude": 40.0, "longitude": -105.0, "is_default": False,
        "horizon_min_deg": 15.0}
JUNE_NOON = time.mktime((2026, 6, 15, 12, 0, 0, 0, 0, -1))


class _FakeHub:
    def __init__(self, site):
        self.site = site


def _session(*, calibration=False) -> Session:
    plan = SequencePlan(name="n", targets=[Target(
        name="M42", ra_hours=5.5881, dec_deg=-5.3911, calibration=calibration,
        steps=[ExposureStep(filter="L", exposure_s=60, count=10)])])
    return Session(name="n", created_ts=1.0, status="dormant", plan=plan)


@pytest.fixture
def arm(monkeypatch):
    return ResumeArm(engine=None, hub=_FakeHub(SITE))


def _night():
    dusk, dawn = schedule.observing_night(SITE, -12.0, JUNE_NOON)
    return dusk, dawn


class TestWindowIsBoundedByDarkness:
    def test_open_in_the_middle_of_the_night(self, arm):
        dusk, dawn = _night()
        assert arm._window_open(_session(), (dusk + dawn) / 2) is True

    def test_CLOSED_ninety_minutes_after_dawn(self, arm):
        """The reported defect, at the reported time of day."""
        _dusk, dawn = _night()
        assert arm._window_open(_session(), dawn + 90 * 60) is False

    def test_closed_hours_after_dawn_too(self, arm):
        """It ran for at least 100 minutes on the rig; nothing about the fix may
        depend on being close to the boundary."""
        _dusk, dawn = _night()
        assert arm._window_open(_session(), dawn + 5 * 3600) is False

    def test_closed_in_the_afternoon(self, arm):
        """The same hole at the other end — start_mode='now' made an afternoon
        boot equally 'open'. A fix that only closed the morning would have left
        half the bug."""
        dusk, _dawn = _night()
        assert arm._window_open(_session(), dusk - 4 * 3600) is False

    def test_a_calibration_plan_still_shoots_at_any_hour(self, arm):
        """Darks and flats are SUPPOSED to run in daylight with the mount
        parked, and the dark-plan harness depends on it. The exemption must
        survive the bound."""
        _dusk, dawn = _night()
        s = _session(calibration=True)
        assert arm._window_open(s, dawn + 5 * 3600) is True
        assert arm._window_open(s, JUNE_NOON) is True

    def test_an_unset_site_does_not_stand_everything_down(self, arm):
        """Fail-open: "we cannot tell where you are" must not disable
        auto-resume outright. The weather veto and the recovery ladder are
        still in front of any motion."""
        a = ResumeArm(engine=None, hub=_FakeHub(
            {"latitude": 0.0, "longitude": 0.0, "is_default": True}))
        assert a._window_open(_session(), JUNE_NOON) is True


class TestItSaysSoOnceRatherThanRetrying:
    @pytest.mark.asyncio
    async def test_a_standing_by_session_is_announced_once_not_per_tick(
            self, monkeypatch, tmp_path):
        """The whole point: no exposure, no solve, and ONE line — not a warning
        every ten minutes for the rest of the day."""
        from astrodeck.events import bus
        from astrodeck.sequence import session as session_mod

        monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
        lines: list[tuple[str, str]] = []
        monkeypatch.setattr(bus, "log",
                            lambda level, msg, src="": lines.append((level, msg)))

        s = _session()
        s.auto_resume = True
        session_mod.session_store.save(s)

        class _Engine:
            running = False

        _dusk, dawn = _night()
        t = {"now": dawn + 90 * 60}
        a = ResumeArm(_Engine(), _FakeHub(SITE), clock=lambda: t["now"])
        for _ in range(5):
            await a.tick()
            t["now"] += 600            # the 10-minute retry cadence it used
        standing = [m for _l, m in lines if "standing by" in m]
        assert len(standing) == 1, f"said it {len(standing)} times: {standing}"
        assert "10 frames" in standing[0], standing[0]
        assert not any("plate solve" in m for _l, m in lines), \
            "it exposed and solved in daylight"
