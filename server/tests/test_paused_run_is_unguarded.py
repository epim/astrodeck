"""A paused run is an unguarded run. 2026-09-10/11 cost nine hours to it.

The night: the meridian flip was refused by a mount that would not track, the
park/unpark recovery re-acquired the target but never flipped, the field walked
at 65 arcsec/min, and the operator's session paused the run at 00:40. A fresh
guider calibration then completed at 00:50:37 and STARTED GUIDING on that
paused run. From 04:56 to 05:29 the guider re-locked twelve times, each onto a
star 530 to 6141 arcsec away, accumulating about 230600 arcsec -- 64 degrees --
and walked the mount to 10 degrees altitude. At 06:19:55 the dawn park declined
to park, logging "a sequence run is in progress and owns its own wind-down".
The mount sat unparked with the camera at -9.9 C until 09:41.

Two defects, one shape: every safety check in this system rides a
value-producing path, so it stops when the work stops while the hazard
continues.

  * the GUIDER had all twelve re-lock displacements in its own loop and no
    authority to act on them -- every re-lock gate belonged to the sequence
    engine's per-frame loop, and a paused run has no frames;
  * the DAWN PARK treated "a run exists" as "somebody is handling it", which is
    false for a paused run and is the one case where the safety net matters
    most.

What these tests must not assert: anything that only holds while a run is
producing frames. The whole point is the paused and idle cases.
"""
import time

import pytest

pytestmark = pytest.mark.asyncio


# ----------------------------------------------------------------- the guider


def _guider(monkeypatch, *, scale=5.5, known=True, **cfg):
    """A NativeGuider with only the fields these limits read.

    Deliberately not a connected guider: `_relock_limit_exceeded` and
    `_relock_arcsec_in_window` are pure decisions over `self.config`,
    `self._relock_events` and the image scale, and driving a real guide loop to
    reach them would test the camera, not the rule.
    """
    from astrodeck.guide.native import NativeGuider

    g = NativeGuider.__new__(NativeGuider)
    g.config = dict(cfg)
    g._image_scale = scale
    g._image_scale_known = known
    g._relock_events = []
    return g


def _events(g, *pairs):
    """(age_seconds_ago, arcsec) pairs, oldest first."""
    now = time.time()
    g._relock_events = [{"t": now - age, "arcsec": arc} for age, arc in pairs]


async def test_one_big_jump_stops_guiding(monkeypatch):
    """A single re-lock of hundreds of arcsec is a DIFFERENT star, not a
    flickering one. Last night's first was 573.6 arcsec and the largest 6141."""
    g = _guider(monkeypatch, relock_jump_arcsec=120.0, relock_arcsec_limit=300.0)
    assert g._relock_limit_exceeded(573.6), "573.6 arcsec did not trip the gate"
    assert "different star" in g._relock_limit_exceeded(573.6)
    assert not g._relock_limit_exceeded(40.0), "a 40 arcsec re-lock is normal"


async def test_accumulated_displacement_stops_guiding(monkeypatch):
    """The quantity that MOVES THE FIELD is the sum, and it is what the
    engine's count-based gate misses when the re-locks are few and large.

    The engine's gate needs 3 re-locks in 10 min; on 2026-09-10 the night
    produced 2 spread over 22.5 minutes and it never fired.
    """
    g = _guider(monkeypatch, relock_jump_arcsec=0.0, relock_arcsec_limit=300.0,
                relock_window_min=10.0)
    _events(g, (300, 110.0), (200, 110.0))          # 220 total, two events
    assert not g._relock_limit_exceeded(110.0), "220 arcsec should not trip 300"
    _events(g, (300, 110.0), (200, 110.0), (100, 110.0))
    reason = g._relock_limit_exceeded(110.0)
    assert reason and "walking" in reason, f"330 arcsec did not trip: {reason!r}"


async def test_old_relocks_age_out_of_the_window(monkeypatch):
    """A WINDOW, not a session total: a long clear night legitimately collects
    re-locks as stars flicker behind thin cloud, and a session-total gate would
    eventually stop a healthy run for having been long."""
    g = _guider(monkeypatch, relock_jump_arcsec=0.0, relock_arcsec_limit=300.0,
                relock_window_min=10.0)
    _events(g, (4000, 200.0), (3800, 200.0), (3600, 200.0))   # all >1h old
    assert not g._relock_limit_exceeded(50.0), \
        "re-locks from an hour ago stopped guiding"


async def test_inert_without_a_known_image_scale(monkeypatch):
    """Without the guide scope's focal length these displacements are PIXELS,
    and an arcsec threshold against pixels fires at a different sensitivity on
    every rig. "" is the honest answer."""
    g = _guider(monkeypatch, known=False, relock_jump_arcsec=120.0,
                relock_arcsec_limit=300.0)
    assert g._relock_limit_exceeded(5000.0) == "", \
        "an arcsec gate fired on a rig whose numbers are pixels"


async def test_zero_disables_each_limit(monkeypatch):
    """0 is off, the convention every other threshold in this config uses."""
    g = _guider(monkeypatch, relock_jump_arcsec=0.0, relock_arcsec_limit=0.0)
    _events(g, (10, 9000.0))
    assert g._relock_limit_exceeded(9000.0) == ""


async def test_last_nights_actual_relocks_would_have_stopped_it(monkeypatch):
    """The regression test proper: replay the first few real displacements from
    2026-09-11 04:56-05:29 and require the guider to stop itself.

    It must trip on the FIRST one -- 573.6 arcsec at 04:56:09 -- which is about
    9.6 arcmin of field, against the 64 degrees it went on to accumulate.
    """
    g = _guider(monkeypatch, relock_jump_arcsec=120.0, relock_arcsec_limit=300.0,
                relock_window_min=10.0)
    real = [573.6, 57.0, 5550.7, 5542.3, 4846.8, 5668.3, 2547.4, 1792.8]
    tripped_at = None
    for i, d in enumerate(real):
        g._relock_events.append({"t": time.time(), "arcsec": d})
        if g._relock_limit_exceeded(d):
            tripped_at = i
            break
    assert tripped_at == 0, \
        f"the guider did not stop itself until re-lock {tripped_at}"


# -------------------------------------------------------------- the dawn park


class _Eng:
    def __init__(self, running, paused):
        self.running = running
        self.paused = paused


def _parker(monkeypatch, eng):
    from astrodeck.dawn_park import DawnPark

    p = DawnPark.__new__(DawnPark)
    p.engine = eng
    monkeypatch.setattr(p, "_busy_lanes", lambda: set(), raising=False)
    return p


def _cfg(solar=True):
    from types import SimpleNamespace
    return SimpleNamespace(safety=SimpleNamespace(solar_avoidance=solar))


async def test_a_running_run_still_vetoes_the_dawn_park(monkeypatch):
    """Unchanged, and it must stay unchanged: a run that is actually running --
    including one holding for a flip or recovering from a limit -- is managing
    itself, and racing it really would be worse."""
    p = _parker(monkeypatch, _Eng(running=True, paused=False))
    reason = p._hands_off_reason(_cfg())
    assert reason and "owns its own wind-down" in reason


async def test_a_PAUSED_run_does_not_veto_the_dawn_park(monkeypatch):
    """The 06:19:55 defect. `running` stays True across a pause, so the park
    deferred to a run that would never progress, and the rig stayed out all
    day with its cooler holding -9.9 C."""
    p = _parker(monkeypatch, _Eng(running=True, paused=True))
    assert p._hands_off_reason(_cfg()) is None, \
        "a paused run still vetoes the dawn park"


async def test_no_engine_at_all_does_not_veto(monkeypatch):
    p = _parker(monkeypatch, None)
    assert p._hands_off_reason(_cfg()) is None


async def test_a_solar_session_still_vetoes(monkeypatch):
    """The other hands-off reasons are untouched by this change."""
    p = _parker(monkeypatch, _Eng(running=False, paused=False))
    reason = p._hands_off_reason(_cfg(solar=False))
    assert reason and "solar session" in reason
