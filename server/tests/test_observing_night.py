"""One answer to "when is tonight?" — #229 and #228.

#229: auto-resume had no outer bound. The default ``Schedule`` is
``start_mode="now"`` / ``stop_mode="none"``, so ``resolve_window`` hands back
``(now, None)`` and the open-test reduces to ``now <= now and True``. Measured
on the rig 2026-08-11: a 4 s exposure plus a full ASTAP run every ten minutes at
07:36, ninety minutes after sunrise, on a mount the dawn daemon had parked at
05:52. The refusal it logged each time was correct — it would not slew a mount
whose pointing it could not verify — but a loop with no terminating condition is
not diligence.

#228: three surfaces described one forecast three ways, and the fix was NOT what
the bug report guessed. The scan window was always right; the boot banner simply
formatted it in UTC and called it "tonight". 09:15–12:15 UTC is 02:15–05:15 PDT,
which is exactly what the modal showed. Same window, seven hours apart, printed
beside a log stamp that events.py renders in local time.
"""
from __future__ import annotations

import time

import pytest

from astrodeck.sequence import schedule

# A real mid-latitude site (NOT the observatory's — this is a test fixture).
SITE = {"latitude": 40.0, "longitude": -105.0, "is_default": False}
DEFAULT_SITE = {"latitude": 0.0, "longitude": 0.0, "is_default": True}
TWILIGHT = -12.0

# 2026-06-15, a date with a real night at 40°N. Chosen once and pinned so the
# test does not depend on when it runs.
JUNE = time.mktime((2026, 6, 15, 12, 0, 0, 0, 0, -1))


class TestObservingNight:
    def test_resolves_a_dusk_before_a_dawn(self):
        night = schedule.observing_night(SITE, TWILIGHT, JUNE)
        assert night is not None
        dusk, dawn = night
        assert dusk < dawn, "dusk must open the night that dawn closes"
        assert 0 < (dawn - dusk) < 16 * 3600, \
            f"a {(dawn - dusk) / 3600:.1f}h night at 40°N is not credible"

    def test_a_default_site_has_no_night(self):
        """We do not know where the observer is, so we must not pretend to know
        when their night is."""
        assert schedule.observing_night(DEFAULT_SITE, TWILIGHT, JUNE) is None

    def test_reads_a_pydantic_site_as_well_as_a_dict(self):
        """hub.site is a dict, cfg.site is a Site model. Both callers exist."""
        from astrodeck.config import Site
        s = Site(latitude=40.0, longitude=-105.0, is_default=False)
        assert schedule.observing_night(s, TWILIGHT, JUNE) is not None

    def test_the_operators_twilight_is_honoured(self):
        """A narrowband rig in a city and a broadband rig under Bortle 2 do not
        agree about when the night starts, so this is config, not a constant.
        A DEEPER angle means a SHORTER night."""
        shallow = schedule.observing_night(SITE, -6.0, JUNE)
        deep = schedule.observing_night(SITE, -18.0, JUNE)
        assert shallow is not None and deep is not None
        assert (deep[1] - deep[0]) < (shallow[1] - shallow[0])


class TestDarkEnough:
    def test_true_in_the_middle_of_the_night(self):
        dusk, dawn = schedule.observing_night(SITE, TWILIGHT, JUNE)
        assert schedule.dark_enough(SITE, TWILIGHT, (dusk + dawn) / 2) is True

    def test_false_an_hour_after_dawn(self):
        """#229 exactly: 07:36, ninety minutes after sunrise, still 'open'."""
        _dusk, dawn = schedule.observing_night(SITE, TWILIGHT, JUNE)
        assert schedule.dark_enough(SITE, TWILIGHT, dawn + 3600) is False

    def test_false_in_the_afternoon_too(self):
        """The SAME defect at the other end — a default schedule says
        start_mode='now', so a session armed at 15:00 was equally 'open'. One
        predicate has to close both ends or the bug just moves."""
        dusk, _dawn = schedule.observing_night(SITE, TWILIGHT, JUNE)
        assert schedule.dark_enough(SITE, TWILIGHT, dusk - 4 * 3600) is False

    def test_the_first_implementation_could_never_have_fired(self):
        """REGRESSION GUARD, and the reason this file exists twice over.

        The first `night_is_over` asked `now >= dawn` from observing_night().
        That can never be true: observing_night anchors to the current-or-
        imminent night, so stepping past dawn returns TOMORROW's pair and the
        comparison resets. A bound that cannot fire is the same defect as the
        unbounded loop it was written to close — so this pins the property that
        caught it, not just the current answer."""
        _dusk, dawn = schedule.observing_night(SITE, TWILIGHT, JUNE)
        after = dawn + 3600
        reanchored = schedule.observing_night(SITE, TWILIGHT, after)
        assert reanchored is not None
        assert after < reanchored[1], (
            "observing_night no longer re-anchors past dawn; if that changed, "
            "re-read why dark_enough is a measurement rather than arithmetic")
        # ...and the predicate is right anyway, because it does not use dawn.
        assert schedule.dark_enough(SITE, TWILIGHT, after) is False

    def test_true_when_the_site_is_unset(self):
        """Fail-OPEN. "We cannot tell" must not stand every automation down —
        the per-target schedule and the weather veto still apply. Fail-closed
        belongs in the gates that actually move hardware."""
        assert schedule.dark_enough(DEFAULT_SITE, TWILIGHT, JUNE) is True


def test_there_is_no_second_night_predicate():
    """A `night_has_ended` was written and then deleted: it hit the same
    re-anchoring trap, because past dawn NO comparison against dusk or dawn can
    separate the morning-after from the afternoon-before. It existed only to
    pick one word in one log line, and the line was reworded instead.

    Pinned so it does not come back by accident — the next author to want that
    distinction should have to read the comment explaining why it is harder
    than it looks."""
    assert not hasattr(schedule, "night_has_ended")
    assert not hasattr(schedule, "night_is_over")


class TestWeatherUsesTheSameDefinition:
    def test_tonight_delegates_rather_than_reimplementing(self):
        """The two used to be separate copies of the same four lines. An AST
        check, because "it happens to agree today" is what a value comparison
        would prove."""
        import ast
        import inspect
        from astrodeck.weather import WeatherService
        src = inspect.getsource(WeatherService._tonight)
        tree = ast.parse(src.lstrip())
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert "observing_night" in called
        assert "_night_dusk" not in called and "_night_dawn" not in called, \
            "weather is resolving its own night again"
