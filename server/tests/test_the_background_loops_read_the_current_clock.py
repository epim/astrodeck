"""The safety loops bound `time.time` at import, so a patched clock never reached them.

Every one of these declares `clock=time.time` as a DEFAULT ARGUMENT, evaluated
once at import and holding the original builtin. Production constructs all of
them WITHOUT a clock (api/app.py:147-185), so `monkeypatch.setattr(time,
"time", ...)` -- the idiom this suite already uses, and the one a
virtual-clock harness depends on -- cannot reach them.

The consequence is specific and bad: a simulated night ticks DawnPark 720
times, 60 virtual seconds apart, and every tick evaluates the SAME real instant
the test started. `sun_altaz(lat, lon, self._clock())` returns a constant, so
the dawn park is a constant function and the test stays green while grading
nothing. Same for SunWatch and ResumeArm.

`clock=None` with late resolution fixes it without changing any caller: tests
that pass a clock explicitly (test_resume_arm.py and friends) keep working, and
production wiring becomes the wiring under test rather than a different one.
"""
import time

import pytest

CONSTRUCTORS = [
    ("DawnPark", "astrodeck.dawn_park", "DawnPark", ("hub", "engine")),
    ("SunWatch", "astrodeck.sun_watch", "SunWatch", ("hub", "engine")),
    ("ResumeArm", "astrodeck.sequence.resume_arm", "ResumeArm", ("engine", "hub")),
    ("WeatherService", "astrodeck.weather", "WeatherService", ()),
    ("CloudmapService", "astrodeck.cloudmap.service", "CloudmapService", ()),
    ("PushRunner", "astrodeck.sync.runner", "PushRunner", ()),
]


def _build(modname, clsname, argnames):
    import importlib
    mod = importlib.import_module(modname)
    cls = getattr(mod, clsname)
    return cls(*[object() for _ in argnames])       # constructed WITHOUT a clock


@pytest.mark.parametrize("label,modname,clsname,argnames", CONSTRUCTORS,
                         ids=[c[0] for c in CONSTRUCTORS])
def test_a_patched_clock_reaches_the_loop(monkeypatch, label, modname, clsname, argnames):
    """Exactly how production builds it, then patch time.time and read it back."""
    obj = _build(modname, clsname, argnames)
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000.0)
    got = obj._clock()
    assert got == 1_700_000_000.0, (
        f"{label} was constructed the way production constructs it "
        f"(api/app.py:147-185, no clock) and its clock returned {got} instead "
        f"of the patched value -- it captured the builtin at import. A "
        f"simulated night would tick it hundreds of times at one frozen "
        f"instant while every assertion passed.")


@pytest.mark.parametrize("label,modname,clsname,argnames", CONSTRUCTORS,
                         ids=[c[0] for c in CONSTRUCTORS])
def test_an_explicit_clock_still_wins(label, modname, clsname, argnames):
    """The existing seam must keep working -- test_resume_arm.py passes one."""
    import importlib
    cls = getattr(importlib.import_module(modname), clsname)
    obj = cls(*[object() for _ in argnames], clock=lambda: 42.0)
    assert obj._clock() == 42.0, f"{label} ignored an explicitly injected clock"
