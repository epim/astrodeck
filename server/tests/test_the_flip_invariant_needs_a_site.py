"""The rest of the flip machinery asks whether there is a site (#24).

`_maybe_meridian_flip` already declines the flip at the 0,0 default and says so
(test_the_meridian_needs_a_site.py). Three functions around it still computed
hour angle from longitude 0:

  * `_enforce_flip_owed` - THE ONE WITH A CONSEQUENCE. At the default, its
    countdown crosses zero at the Gulf of Guinea's meridian, hours from the
    rig's own. Nothing asked the mount to flip, so the side is unchanged, and
    the invariant concluded a flip was owed: an error-level "refusing to
    expose", a hold of `flip_owed_hold_min` (20 by default), then StopTarget -
    a good target abandoned at an arbitrary hour. `meridian_flip` defaults to
    True on every plan, so this was every unconfigured rig's first night.
  * `_arm_meridian_flip` (lifted out of `_setup_target` so it can be reached
    without a slew) - armed the latch from the wrong hour angle. The gate then
    declined, but its warning sits behind the latch, so a latch that happened
    NOT to arm left the run silent about the flip it will never take.
  * `_wait_for_flip_point` - only reachable past the gate today; it asks anyway.

MUTATIONS RUN, and what each printed:

  M1, delete the `if latlon is None: ... return` from `_enforce_flip_owed`.
  1 failed: test_an_unsited_rig_is_not_held_for_a_flip_it_was_never_going_to_take,
  raising StopTarget("a meridian flip is owed ...") after the hold.

  M2, in `_arm_meridian_flip`, read `(site["latitude"], site["longitude"])`
  raw instead of `site_lat_lon` - the pre-fix behaviour. 3 failed: the latch
  case (armed from longitude 0.0), the warning case and the once-per-run case.
  A WEAKER M2 WAS RUN FIRST AND IS WORTH NAMING: replacing the no-site test
  with `False` sends an unsited rig into the computing branch, where
  `latlon[1]` raises inside the `try` and the latch disarms anyway - so the
  latch case stayed green and only the two warning cases failed. That mutant
  is not the defect; the raw read is.

  M3, delete `self._flip_no_site_logged = True` in `_arm_meridian_flip`.
  1 failed: the once-per-run case, three warnings for three targets.

  M4, delete the `latlon is None` return in `_wait_for_flip_point`.
  1 failed: the countdown stub was called with longitude 0.0.

The controls (a real site reaches the hold, arms from the configured longitude,
and waits) are what keep M1, M2 and M4 from passing on a harness that never
reaches the code.
"""
from __future__ import annotations

import time

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.config import AppConfig
from astrodeck.devices.base import PierSide
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.engine import StopTarget
from astrodeck.sequence.models import ExposureStep, Target

pytestmark = pytest.mark.asyncio

DEFAULT_SITE = {"latitude": 0.0, "longitude": 0.0, "elevation_m": 0.0,
                "is_default": True}
REAL_SITE = {"latitude": 40.0, "longitude": -74.0, "elevation_m": 10.0,
             "is_default": False}


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _site(monkeypatch, site):
    # `Hub.site` is a read-only config-backed property; replacing it on the
    # class is the pattern test_the_meridian_needs_a_site.py established.
    monkeypatch.setattr(Hub, "site", property(lambda self: site))


def _target(name="T"):
    return Target(name=name, ra_hours=0.0, dec_deg=20.0, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=0.05, count=3)])


def _engine(sim_hub):
    t = _target()
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(targets=[t], meridian_flip=True, guide=False)
    cfg = AppConfig()
    cfg.safety.flip_owed_hold_min = 0.02
    e._cfg = cfg
    return e, t


def _countdown(monkeypatch, ttf_h):
    seen: list[float] = []

    def _h(ra, lon, now=None):
        seen.append(lon)
        return ttf_h
    monkeypatch.setattr(engine_mod.schedule, "hours_to_meridian_flip", _h)
    return seen


def _mount_stays_west(sim_hub, e, monkeypatch):
    async def _pier():
        return PierSide.WEST
    monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _pier)

    async def _flip(target, next_exposure_s=0.0):
        return None                          # the flip never happens
    monkeypatch.setattr(e, "_maybe_meridian_flip", _flip)
    monkeypatch.setattr(engine_mod, "FLIP_OWED_POLL_S", 0.01)


def _capture_logs(monkeypatch):
    from astrodeck.events import bus
    lines: list[tuple[str, str]] = []
    real = bus.log

    def _log(lvl, msg, src=None, **kw):
        lines.append((lvl, msg))
        return real(lvl, msg, src, **kw)
    monkeypatch.setattr(bus, "log", _log)
    return lines


# ---------------------------------------------------------- the invariant


async def test_an_unsited_rig_is_not_held_for_a_flip_it_was_never_going_to_take(
        sim_hub, monkeypatch):
    """THE DEFECT. Past a meridian that is not the rig's, on the side it was
    on before it - which is where every unflipped mount is."""
    _site(monkeypatch, DEFAULT_SITE)
    e, t = _engine(sim_hub)
    e._pre_flip_side = {t.id: "west"}       # per target (I-19, #136)
    _mount_stays_west(sim_hub, e, monkeypatch)
    seen = _countdown(monkeypatch, -0.4)
    await e._enforce_flip_owed(t)            # must not raise StopTarget
    assert e._flip_owed is False
    assert seen == [], f"the invariant computed a crossing from longitude {seen}"


async def test_a_sited_rig_in_the_same_state_is_still_held(sim_hub, monkeypatch):
    """The control, and the reason the case above means anything: the same
    mount, the same side, the same countdown, at a real site, IS held. A guard
    that returned for every rig would pass the case above and remove the
    invariant that saved 2026-09-11."""
    _site(monkeypatch, REAL_SITE)
    e, t = _engine(sim_hub)
    e._pre_flip_side = {t.id: "west"}       # per target (I-19, #136)
    _mount_stays_west(sim_hub, e, monkeypatch)
    seen = _countdown(monkeypatch, -0.4)
    started = time.time()
    with pytest.raises(StopTarget):
        await e._enforce_flip_owed(t)
    assert seen and seen[0] == REAL_SITE["longitude"], seen
    assert time.time() - started < 30


# ---------------------------------------------------------------- the latch


async def test_the_latch_is_not_armed_from_the_gulf_of_guinea(sim_hub,
                                                              monkeypatch):
    _site(monkeypatch, DEFAULT_SITE)
    e, t = _engine(sim_hub)
    seen = _countdown(monkeypatch, 0.5)      # east, inside any arming window
    monkeypatch.setattr(engine_mod.schedule, "flip_should_arm",
                        lambda ttf, lead_min: True)
    e._arm_meridian_flip(t)
    assert e._flip_armed is False
    assert seen == [], f"the latch computed hour angle from longitude {seen}"


async def test_an_unsited_run_says_the_flip_will_not_happen(sim_hub,
                                                            monkeypatch):
    """The flip gate's own warning sits behind the latch, so a latch that is
    never armed never reaches it. Without this line an unsited run whose plan
    asks for a flip says nothing about it all night."""
    _site(monkeypatch, DEFAULT_SITE)
    lines = _capture_logs(monkeypatch)
    e, t = _engine(sim_hub)
    e._arm_meridian_flip(t)
    said = [m for lvl, m in lines if "no configured site" in m]
    assert said and "no flip will be taken" in said[0], lines
    assert "settings" in said[0], said[0]


async def test_it_is_said_once_per_run_not_per_target(sim_hub, monkeypatch):
    _site(monkeypatch, DEFAULT_SITE)
    lines = _capture_logs(monkeypatch)
    e, _ = _engine(sim_hub)
    for name in ("A", "B", "C"):
        e._arm_meridian_flip(_target(name))
    assert len([m for _, m in lines if "no configured site" in m]) == 1, lines


async def test_a_sited_rig_arms_from_its_own_longitude(sim_hub, monkeypatch):
    """The control for the latch cases."""
    _site(monkeypatch, REAL_SITE)
    e, t = _engine(sim_hub)
    seen = _countdown(monkeypatch, 0.5)
    monkeypatch.setattr(engine_mod.schedule, "flip_should_arm",
                        lambda ttf, lead_min: True)
    monkeypatch.setattr(e, "_flip_lead_s", lambda target: 0.0)
    e._arm_meridian_flip(t)
    assert e._flip_armed is True
    assert seen == [REAL_SITE["longitude"]], seen


async def test_a_plan_without_a_flip_is_not_told_about_one(sim_hub,
                                                           monkeypatch):
    """The warning is about a flip the plan asked for. A plan that switched it
    off has nothing to be warned about, and a calibration run would otherwise
    get a line about the meridian it never points at."""
    _site(monkeypatch, DEFAULT_SITE)
    lines = _capture_logs(monkeypatch)
    e, t = _engine(sim_hub)
    e.plan = SequencePlan(targets=[t], meridian_flip=False, guide=False)
    e._arm_meridian_flip(t)
    assert not [m for _, m in lines if "no configured site" in m], lines


# ------------------------------------------------------------------ the wait


async def test_the_wait_does_not_wait_for_someone_elses_meridian(sim_hub,
                                                                 monkeypatch):
    _site(monkeypatch, DEFAULT_SITE)
    e, t = _engine(sim_hub)
    seen = _countdown(monkeypatch, 0.0)
    await e._wait_for_flip_point(t, 0.0)
    assert seen == [], f"the wait computed hour angle from longitude {seen}"


async def test_the_wait_reads_the_configured_longitude(sim_hub, monkeypatch):
    _site(monkeypatch, REAL_SITE)
    e, t = _engine(sim_hub)
    seen = _countdown(monkeypatch, -0.01)    # already there: returns at once
    await e._wait_for_flip_point(t, 0.0)
    assert seen == [REAL_SITE["longitude"]], seen
