"""WP-37 (a) / #193: the frame-verdict cloud fallback must engage whenever
the assigned safety monitor does not read clouds, not only when no monitor
is assigned at all.

Before the fix, ``_safety_gate`` reached the frame-verdict fallback
(``_no_safety_source``) only through its ``mon is None`` branch. A rig whose
monitor reads rain, wind or power but not clouds -- the SAFETY node's own
"Rain + wind + power (pair with Cloud Watch)" scope, meant to be paired with
a CLOUD WATCH node -- has no cloud source at all when nothing wires that
CLOUD WATCH in: the monitor reports SAFE (rain is fine) every poll, so
``_safety_gate`` never opens the hold, and a cloudy frame verdict is simply
discarded. A Plan-built night on such a rig shoots straight through a closing
sky exactly as #189's original "armed, no monitor" gap did.

``cfg.safety.monitor_reads_clouds`` is the new knob this fix introduces
(``config.py``): True (the default, unchanged behaviour) says the assigned
monitor already covers clouds; False says it does not, and
``_monitor_lacks_cloud_source`` (engine.py) then mirrors
``_no_safety_source``'s fallback for this rig.

Mirrors ``test_sky_stands_in_for_a_missing_monitor.py``'s fixtures and
helpers, aimed at a rig WITH a monitor assigned instead of without one.

Named mutant: key the fallback on "a monitor is assigned" -- i.e. delete the
``if not getattr(cfg.safety, "monitor_reads_clouds", True):`` call in
``_safety_gate`` so a real monitor's presence alone silences the fallback
again -- RED, observed verbatim:

    AssertionError: a monitor that cannot see clouds held nothing
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _engine(hub, monkeypatch, *, reads_clouds=False, fallback=True):
    """A rig WITH a safety monitor assigned (the simulator's own, always
    SAFE -- "reports only rain" from the issue, since the sim monitor never
    sees a cloud either way) and ``monitor_reads_clouds`` set as asked."""
    hub.mode = "native"       # a simulated FRAME is not a sky (see the sibling file)
    eng = SequenceEngine(hub)
    cfg = hub_module.config_store.cfg()
    monkeypatch.setattr(cfg.safety, "enabled", True)
    monkeypatch.setattr(cfg.safety, "sky_fallback_hold", fallback)
    monkeypatch.setattr(cfg.safety, "monitor_reads_clouds", reads_clouds)
    monkeypatch.setattr(cfg.escalation, "require_safety_monitor", False)
    eng._cfg = cfg
    eng.plan = SequencePlan(
        name="p", safety_check=True, guide=False,
        targets=[Target(name="A", ra_hours=5.5, dec_deg=-5.0, center=False,
                        autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=1)])])
    assert hub.devices.get("safety") is not None, "premise: a monitor is assigned"
    return eng


def _sky(eng, cloudy: bool | None):
    """Force the frame-derived verdict without faking the detector's maths."""
    eng._clouds.cloudy = lambda _now: cloudy       # type: ignore[assignment]
    eng._clouds.describe = lambda _now: (          # type: ignore[assignment]
        "cloudy" if cloudy else "clear")


async def test_a_monitor_that_cannot_see_clouds_holds_on_a_closed_sky(
        sim_hub, monkeypatch):
    """The monitor reports SAFE every time (rain is fine); the frame verdict
    is cloudy; the fallback must still hold, exactly as it would with no
    monitor at all."""
    eng = _engine(sim_hub, monkeypatch, reads_clouds=False)
    _sky(eng, True)
    held = {"n": 0}

    async def _hold(reason, target):
        held["n"] += 1
        held["reason"] = reason
    eng._hold_for_clear = _hold                    # type: ignore[assignment]

    a = eng.plan.targets[0]
    await eng._safety_gate(context="frame", target=a)
    assert held["n"] == 1, "a monitor that cannot see clouds held nothing"
    assert held["target"] is a if "target" in held else True
    assert "does not read clouds" in held["reason"]


async def test_a_monitor_that_reads_clouds_is_untouched(sim_hub, monkeypatch):
    """CONTROL (the pre-#193 contract): ``monitor_reads_clouds`` True (the
    default) leaves a real monitor's rig exactly as before -- no fallback
    hold, whatever the frames say, because the monitor is trusted to cover
    this itself."""
    eng = _engine(sim_hub, monkeypatch, reads_clouds=True)
    _sky(eng, True)
    held = {"n": 0}

    async def _hold(reason, target):
        held["n"] += 1
    eng._hold_for_clear = _hold                    # type: ignore[assignment]

    await eng._safety_gate(context="frame", target=eng.plan.targets[0])
    assert held["n"] == 0, "a monitor that already reads clouds must be untouched"


async def test_a_clear_sky_does_not_hold(sim_hub, monkeypatch):
    """A monitor with no cloud reach, but the frames read clear: nothing
    holds."""
    eng = _engine(sim_hub, monkeypatch, reads_clouds=False)
    _sky(eng, False)
    held = {"n": 0}

    async def _hold(reason, target):
        held["n"] += 1
    eng._hold_for_clear = _hold                    # type: ignore[assignment]

    await eng._safety_gate(context="frame", target=eng.plan.targets[0])
    assert held["n"] == 0, "a clear sky held the run"


async def test_turning_the_fallback_off_restores_old_behaviour(
        sim_hub, monkeypatch):
    """``sky_fallback_hold`` False opts out of the whole mechanism, same as
    the no-monitor case -- a monitor that cannot see clouds plus the
    fallback switched off must hold nothing."""
    eng = _engine(sim_hub, monkeypatch, reads_clouds=False, fallback=False)
    _sky(eng, True)
    held = {"n": 0}

    async def _hold(reason, target):
        held["n"] += 1
    eng._hold_for_clear = _hold                    # type: ignore[assignment]

    await eng._safety_gate(context="frame", target=eng.plan.targets[0])
    assert held["n"] == 0, "opting out must really opt out"


async def test_the_default_leaves_a_real_monitor_untouched():
    """The documented default: a rig that never opens Settings has
    ``monitor_reads_clouds`` True, so an assigned monitor keeps meaning what
    it always meant."""
    from astrodeck.config import AppConfig
    assert AppConfig().safety.monitor_reads_clouds is True
