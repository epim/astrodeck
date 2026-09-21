"""No site, no meridian (#24).

Every flip decision in this tree is hour angle, and hour angle is longitude. At
the 0,0 default the meridian is hours from the rig's own, and nothing says so:
the engine's three flip functions each read the site inside a ``try`` whose
``except`` returns, and the hub's strip publishes a countdown to a crossing that
happens somewhere else.

Two answers, because the two places want different ones. The strip says
"unknown", which it already has a word for. The run refuses to start, because
the alternative to a wrong flip time is no flip at all, and a GEM tracking past
its own meridian is how a tube meets a pier.
"""
from __future__ import annotations

import pytest

from astrodeck.sequence.engine import SafetyAbort, SequenceEngine


DEFAULT_SITE = {"latitude": 0.0, "longitude": 0.0, "is_default": True}
REAL_SITE = {"latitude": 40.0, "longitude": -74.0}


class _Tel:
    connected = True


class _Hub:
    def __init__(self, site):
        self.site = site
        self.devices: dict = {"telescope": _Tel()}


class _Plan:
    def __init__(self, meridian_flip):
        self.meridian_flip = meridian_flip


class _Target:
    name = "NGC 7331"
    ra_hours = 22.62
    dec_deg = 34.42


def _engine(site):
    e = SequenceEngine(_Hub(site))
    # `_maybe_meridian_flip` returns immediately unless the latch is set, so
    # without this every case below would pass on the FIRST line of the function
    # and grade nothing.
    e._flip_armed = True
    return e


@pytest.fixture
async def log_lines():
    """Every `bus.log` message published during the case.

    A SUBSCRIBER rather than index arithmetic against `bus.log_history`, which
    is the pattern test_camera_yield_for_solve.py established and for its
    reason: that ring holds 200 events and is shared by the worker, so once it
    wraps, "everything after my mark" silently evaluates to nothing and the
    assertion passes for the wrong reason.
    """
    from astrodeck.events import bus
    q = bus.subscribe()
    seen: list[str] = []

    class _Lines(list):
        def _drain(self):
            while not q.empty():
                ev = q.get_nowait()
                if getattr(ev, "type", None) == "log":
                    seen.append(str(getattr(ev, "data", ev)))
            self[:] = seen
            return self

        def __iter__(self):
            return iter(self._drain()[:])

        def __len__(self):
            return len(self._drain()[:])

        def __repr__(self):
            return repr(self._drain()[:])

    yield _Lines()
    bus.unsubscribe(q)


async def test_the_flip_says_why_it_is_not_happening(log_lines):
    """The defect, and the shape of the fix. At the 0,0 default the engine used
    to compute a crossing over the Gulf of Guinea and TAKE it: an unannounced
    flip at an arbitrary time, rotating the field 180 degrees mid-stack.

    Not flipping is the better of the two, because a flip at the wrong time is
    itself the hazard - but only if it is audible. It used to fall into the
    `except: return` below it and the run went on in silence.

    MUTATION: delete the `if not site_is_set(self.hub.site)` block from
    `_maybe_meridian_flip`. Observed: no warning is logged and the flip is
    computed from longitude 0.
    """
    e = _engine(DEFAULT_SITE)
    e.plan = _Plan(True)
    await e._maybe_meridian_flip(_Target())
    said = [line for line in log_lines if "no configured site" in line]
    assert said, f"the flip was skipped without saying so: {log_lines}"
    assert "meridian" in said[0], said[0]
    assert "settings" in said[0] or "switch the flip off" in said[0], (
        f"the warning does not say what to do about it: {said[0]!r}")


async def test_it_says_it_once_and_not_every_frame(log_lines):
    """The condition holds for the whole run, so one line is the news and the
    rest is a log nobody can read. Same rate limit, and same reason, as the
    mount-offline case directly above it in the engine.

    MUTATION: delete the `self._flip_no_site_logged = True`. Observed: three
    warnings for three frames and this fails.
    """
    e = _engine(DEFAULT_SITE)
    e.plan = _Plan(True)
    for _ in range(3):
        await e._maybe_meridian_flip(_Target())
    assert len([x for x in log_lines if "no configured site" in x]) == 1, log_lines


async def test_a_configured_site_is_not_warned_at(log_lines):
    """The guard against the fix: a real rig must reach the countdown, not this
    warning.

    Two assertions, and the second is the one with teeth. "No warning" alone
    would also be true of a gate that returned early for some other reason, so
    this additionally requires the call to get PAST the gate - evidenced by it
    reaching plan machinery this stub deliberately does not provide. A gate that
    fired would return quietly and raise nothing.

    MUTATION: drop the `not` from the `site_is_set` test. Observed: no
    AttributeError is raised, because the configured rig is turned back at the
    gate, and this fails on the `pytest.raises` rather than on the log.
    """
    e = _engine(REAL_SITE)
    e.plan = _Plan(True)
    with pytest.raises(AttributeError):
        await e._maybe_meridian_flip(_Target())
    assert not [x for x in log_lines if "no configured site" in x], log_lines


async def test_the_strip_says_unknown_rather_than_counting_down_to_nothing(
        monkeypatch):
    """The hub's half. `_compute_meridian` publishes the operator-facing
    countdown, and the honest answer at an unset site is the one it already has
    a word for.

    MUTATION: drop the `and sited` from the `ttf is None and ra_hours is not
    None` condition. Observed: status is "counting" with a real
    `hours_to_flip`, computed from longitude 0.
    """
    from astrodeck.hub import Hub

    class _Tel:
        async def time_to_meridian_flip(self):
            return None                      # the non-NINA path, which derives it

        async def pier_side(self):
            class _S:
                value = "east"
            return _S()

    hub = Hub.__new__(Hub)                   # no rig, no config, no I/O
    hub._site_override = None
    for name in ("_pier_side_cache", "_pier_side_at"):
        setattr(hub, name, None)

    # `_compute_meridian` reads the site, the pier-side cache and the plan's
    # flip flag, and nothing else. Stubbing those three is what lets this grade
    # the site rule without standing a rig up.
    # `Hub.site` is a read-only config-backed property, so the site is swapped
    # by replacing the property on the CLASS. Patching the instance is what was
    # written first and it raises "property 'site' has no setter".
    held = {"site": DEFAULT_SITE}
    monkeypatch.setattr(Hub, "site", property(lambda self: held["site"]))
    hub._note_pier_side = lambda side: {"pier_side": side,           # type: ignore[assignment]
                                        "pier_side_source": "mount",
                                        "pier_side_age_s": 0.0}
    hub._is_gem = lambda side: True          # type: ignore[assignment]
    hub._plan_flip_enabled = lambda: True    # type: ignore[assignment]
    hub._engine_flip_owed = lambda: False    # type: ignore[assignment]

    out = await hub._compute_meridian(_Tel(), ra_hours=3.0, dec_deg=20.0)
    assert out["status"] == "unknown", out
    assert out["hours_to_flip"] is None, out

    # ...and with a site it counts, so the case above is not passing because
    # the whole function gave up.
    held["site"] = REAL_SITE
    sited = await hub._compute_meridian(_Tel(), ra_hours=3.0, dec_deg=20.0)
    assert sited["status"] in ("counting", "due"), sited
    assert sited["hours_to_flip"] is not None, sited
