"""GN-03: a re-lock is a WALK, and three of them in ten minutes end the frame.

The defect, measured on the AM5N on the night of 2026-09-05/06
(``docs/superpowers/specs/2026-09-06-guider-night-defects-triage.md``): the
native guider reported 2.3 arcsec RMS all the way through a session in which
the field walked 40 arcmin in 30 minutes -- proved after the fact by the sub
headers and by star-matching the frames.

Nothing lied. The guider lost the star (``lock_lost``/``star_lost``), the
engine re-established lock on the NEXT frame on whatever star was now under
the search box, and the error reset to zero around that new star. Every
re-lock hid a jump, the RMS was an honest measure of a meaningless quantity,
and the sequence engine's ``_maybe_recover_guiding`` never fired because it
only ever asks ``is_active()`` -- which a guider that re-locks in one frame
never answers False.

So: count the re-locks, measure how far each one moved the lock, say so on the
bus, carry them in ``GuideStats``, and give the sequence engine a threshold
(``guide.relock_limit`` in ``guide.relock_window_min``) at which it stops
shooting a walking field and re-centres + recalibrates instead.

The last test here is GN-02's follow-through, which belongs to this file
because it lives in ``_build_engine_config``: a mount that caps one pulse
(the AM5's ``max_pulse_ms`` = 1000) silently TRUNCATES a longer calibration
step, and a truncated calibration pulse understates the measured px/ms rate
by exactly the truncation ratio -- so every correction that night was scaled
down by it.
"""
from __future__ import annotations

import asyncio
import math
import time

import pytest

import astrodeck.guide.native as nativemod
import astrodeck.hub as hub_module
from astrodeck.events import bus
from astrodeck.guide.base import GuideStats
from astrodeck.guide.native import NativeGuider
from astrodeck.hub import Hub


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    """test_engine_guide_quiet.py's fixture: a sim rig with the sun cone
    disarmed and the fast legacy sim guider — what is under test here is the
    ENGINE's gate, not the native guider's calibration walk."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


# --------------------------------------------------------------------- doubles


class _Logs:
    """Every ``bus.log`` line, with its level, in order (the
    ``test_native_guider_pier_change.py`` spy: the guide loop publishes a
    ``guide`` event per frame, so a queue subscriber could drop the one line a
    test asserts on)."""

    def __init__(self, monkeypatch) -> None:
        self.lines: list[tuple[str, str]] = []
        real = bus.log

        def _spy(level, message, source="hub"):
            self.lines.append((str(level), str(message)))
            return real(level, message, source)

        monkeypatch.setattr(bus, "log", _spy)

    def at(self, level: str, needle: str) -> list[str]:
        return [m for lv, m in self.lines if lv == level and needle in m]

    def any(self, needle: str) -> list[str]:
        return [m for _lv, m in self.lines if needle in m]


class _Frame:
    """What ``Camera.expose`` returns: ``data`` is the frame INDEX here, which
    the fake ``guide_star_find`` below uses to look up where the star is on
    that frame."""

    def __init__(self, index: int) -> None:
        self.data = index
        self.timestamp = time.time()


class _Cam:
    """A guide camera that hands out numbered frames and then ends the run.

    Setting ``stop`` on the LAST scripted frame is what makes these tests
    deterministic: ``_guide_loop`` tests the flag at the top of each turn, so
    the loop processes exactly the scripted frames and returns on its own —
    no polling, no wall clock, no cancellation."""

    name = "fake guide camera"

    def __init__(self, total: int, stop: asyncio.Event) -> None:
        self.total = total
        self.stop = stop
        self.n = 0

    async def expose(self, exposure_s, gain, offset, binning=1):
        if self.n >= self.total - 1:
            self.stop.set()
        index = min(self.n, self.total - 1)
        self.n += 1
        await asyncio.sleep(0)
        return _Frame(index)


class _Tel:
    """A mount that records its pulses. ``max_pulse_ms`` is left unset (as on
    the base class) unless a test pins one."""

    name = "fake mount"

    def __init__(self, guide_rate_deg_s: float = 0.004178) -> None:
        self.pulses: list[tuple[str, int]] = []
        self._rate = guide_rate_deg_s

    async def pulse_guide(self, direction, ms):
        self.pulses.append((direction, int(ms)))

    async def guide_rates(self):
        return (self._rate, self._rate)

    async def pier_side(self):
        raise NotImplementedError          # _maybe_flip_for_pier suppresses it


class _ScriptEngine:
    """A ``GuideEngine`` stand-in that plays back one Action per frame.

    ``stats()["guiding"]`` stays True throughout, which is what the real engine
    reports from the moment it is asked to guide — including on the
    lock-establishment frames after a star loss, which return ``idle`` (see
    ``engine.rs``'s ``ingest_guiding``). That is precisely the state the host
    could not previously tell apart from steady guiding."""

    def __init__(self, actions) -> None:
        self.actions = list(actions)
        self.frames = 0

    def process(self, data, ts, exposure_s):
        self.frames += 1
        if self.actions:
            return self.actions.pop(0)
        return {"action": "idle"}

    def stats(self):
        return {"guiding": True, "settling": False, "recent": []}

    def dump_calibration(self):
        return None


class _FakeNative:
    """Stands in for the ``astrodeck_native`` wheel: ``guide_star_find`` answers
    from a per-frame script of star positions (``None`` = no star found), so a
    test can place the star wherever it likes on whichever frame it likes."""

    def __init__(self, positions) -> None:
        self.positions = list(positions)
        self.finds = 0

    def guide_star_find(self, data):
        self.finds += 1
        index = int(data)
        pos = self.positions[index] if index < len(self.positions) else \
            self.positions[-1]
        if pos is None:
            return [], {}
        return [{"x": float(pos[0]), "y": float(pos[1]), "snr": 30.0}], {}


def _harness(monkeypatch, actions, positions, *, scale=2.0, known=True):
    """A guider wired to the three doubles above, armed for one ``_guide_loop``
    run over exactly ``len(actions)`` frames."""
    fake = _FakeNative(positions)
    monkeypatch.setattr(nativemod, "_native", fake)
    tel = _Tel()
    engine = _ScriptEngine(actions)
    g = NativeGuider(None, tel, config={"image_scale_arcsec": scale,
                                        "image_scale_known": known,
                                        "exposure_s": 0.01},
                     profile_id=None)
    g.cam = _Cam(len(actions), g._stop)
    g._engine = engine
    g._active = True
    g._stop.clear()
    return g, engine, tel, fake


_LOST = {"action": "lock_lost", "reason": "star_lost"}
_IDLE = {"action": "idle"}
_PULSE = {"action": "pulse_pair", "ra": {"dir": "west", "ms": 100}, "dec": None}


# ------------------------------------------------------------ the re-lock walk


async def test_a_relock_is_counted_and_narrated_with_its_displacement(monkeypatch):
    """The 2026-09-06 mechanism, in four frames: lock, guide, lose the star,
    lock again 12 px away. At 2.0 arcsec/px that is 24 arcsec of field the RMS
    will never mention, because the error resets to zero around the new star."""
    logs = _Logs(monkeypatch)
    g, engine, tel, fake = _harness(
        monkeypatch,
        actions=[_IDLE, _PULSE, _LOST, _IDLE],
        positions=[(30.0, 30.0), (30.0, 30.0), None, (42.0, 30.0)])

    await g._guide_loop()

    s = g.stats()
    assert s.relocks == 1, f"the re-lock was not counted: {s.relocks}"
    assert s.relock_arcsec_total == pytest.approx(24.0, abs=0.05)
    assert len(s.relock_events) == 1
    assert s.relock_events[0]["arcsec"] == pytest.approx(24.0, abs=0.05)
    assert s.relock_events[0]["t"] > 0
    warned = logs.at("warning", "24.0 arcsec")
    assert warned, ("the displacement was never narrated; warnings were "
                    f"{logs.at('warning', '')}")
    assert "re-lock 1" in warned[0]


async def test_reacquiring_the_same_star_is_not_a_walk(monkeypatch):
    """A star that flickers and comes back is the case the reacquire budget was
    built for. It still counts (three of them in ten minutes is a rig that
    cannot hold a lock, whatever the displacement), but calling it a walk would
    cry wolf on every passing cloud edge."""
    logs = _Logs(monkeypatch)
    g, engine, tel, fake = _harness(
        monkeypatch,
        actions=[_IDLE, _LOST, _IDLE],
        positions=[(30.0, 30.0), None, (31.0, 30.0)])

    await g._guide_loop()

    s = g.stats()
    assert s.relocks == 1
    assert s.relock_arcsec_total == pytest.approx(2.0, abs=0.05)   # 1 px, 2"/px
    assert logs.at("info", "re-acquired the same star")
    assert not logs.at("warning", "from the last lock"), \
        "a 1 px reacquire was narrated as a walk"


async def test_the_displacement_is_pixels_when_no_image_scale_is_known(monkeypatch):
    """``GuideStats.is_arcsec`` governs the unit of ``recent``; it governs this
    too. A guide scope with no focal length configured reports raw pixels, and
    labelling those arcsec is the UX-15 lie."""
    logs = _Logs(monkeypatch)
    g, _e, _t, _f = _harness(
        monkeypatch,
        actions=[_IDLE, _LOST, _IDLE],
        positions=[(30.0, 30.0), None, (42.0, 30.0)],
        scale=1.0, known=False)

    await g._guide_loop()

    assert g.stats().relock_arcsec_total == pytest.approx(12.0, abs=0.05)
    assert logs.at("warning", "12.0 px")


async def test_a_star_that_stays_lost_is_not_a_relock(monkeypatch):
    """No star found on the recovering frames means no lock was established, so
    there is nothing to compare and nothing to count. (The reacquire budget,
    unchanged, is what ends this run.)"""
    g, _e, _t, _f = _harness(
        monkeypatch,
        actions=[_IDLE, _LOST, _IDLE, _IDLE],
        positions=[(30.0, 30.0), None, None, None])

    await g._guide_loop()

    assert g.stats().relocks == 0


async def test_relock_events_are_bounded(monkeypatch):
    """A night is thousands of frames. The list is a UI/threshold feed, not a
    log — 50 entries is the cap."""
    n = 60
    actions: list[dict] = [_IDLE]
    positions: list = [(30.0, 30.0)]
    for i in range(n):
        # loss -> lock-establishment frame -> corrections resume, which is the
        # shape the real engine produces AND what spends the reacquire budget
        # (``_dispatch``), so 60 losses never reach the budget of 8.
        actions += [_LOST, _IDLE, _PULSE]
        pos = (30.0 + 12.0 * (i + 1), 30.0)
        positions += [None, pos, pos]
    g, _e, _t, _f = _harness(monkeypatch, actions=actions, positions=positions)

    await g._guide_loop()

    s = g.stats()
    assert s.relocks == n
    assert len(s.relock_events) == 50


async def test_stats_reset_per_session(monkeypatch):
    """A fresh ``start_guiding`` is a fresh session, and its counters start at
    zero. (A RECOVERY restart goes through the same call and so resets too —
    the guider is not told which of the two it is; the sequence engine's hold
    window is what carries across a restart.)"""
    g, engine, tel, fake = _harness(
        monkeypatch,
        actions=[_IDLE, _LOST, _IDLE],
        positions=[(30.0, 30.0), None, (42.0, 30.0)])
    await g._guide_loop()
    assert g.stats().relocks == 1

    async def _no_walk():
        return None
    monkeypatch.setattr(g, "_calibrate", _no_walk)
    fake.GuideEngine = lambda cfg: _ScriptEngine([])
    fake.positions = [(30.0, 30.0)]
    g.cam = _Cam(4, g._stop)

    await g.start_guiding()
    assert g.stats().relocks == 0
    assert g.stats().relock_events == []
    await g.stop_guiding()


def test_the_bridge_guider_leaves_the_relock_fields_alone():
    """PHD2/NINA do not expose a lock position, so the bridge reports the
    honest default rather than a zero it did not measure."""
    s = GuideStats()
    assert s.relocks == 0
    assert s.relock_arcsec_total == 0.0
    assert s.relock_events == []


# ------------------------------------------------- the sequence engine's hold


def _relock_stats(*ages_min: float) -> GuideStats:
    now = time.time()
    return GuideStats(
        guiding=True, phase="guiding", is_arcsec=True, image_scale=2.0,
        relocks=len(ages_min),
        relock_arcsec_total=24.0 * len(ages_min),
        relock_events=[{"t": now - a * 60.0, "arcsec": 24.0}
                       for a in ages_min])


class _HoldSpy:
    """Records the order of the four things a hold must do."""

    def __init__(self, hub, monkeypatch, stats: GuideStats) -> None:
        self.calls: list[str] = []
        g = hub.guider

        async def _stop():
            self.calls.append("stop")

        def _clear():
            self.calls.append("clear")
            return True

        async def _center(ra, dec, rotation_deg=None):
            self.calls.append("center")

        async def _start():
            self.calls.append("start")

        monkeypatch.setattr(g, "stats", lambda: stats)
        monkeypatch.setattr(g, "stop_guiding", _stop)
        monkeypatch.setattr(g, "clear_calibration", _clear, raising=False)
        monkeypatch.setattr(g, "start_guiding", _start)
        monkeypatch.setattr(hub, "goto_and_center", _center)


def _engine(sim_hub, monkeypatch, *, limit=3, window_min=10.0):
    from astrodeck.config import config_store
    from astrodeck.sequence.engine import SequenceEngine
    from astrodeck.sequence.models import SequencePlan
    import astrodeck.sequence.engine as engine_mod

    monkeypatch.setattr(engine_mod, "GUIDE_QUIET_POLL_S", 0.01)
    cfg = config_store.cfg()
    monkeypatch.setattr(cfg.guide, "relock_limit", limit)
    monkeypatch.setattr(cfg.guide, "relock_window_min", window_min)
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(guide=True, recover_guiding=True)
    e._cfg = cfg
    return e


def _target(**kw):
    from astrodeck.sequence.models import ExposureStep, Target
    base = dict(name="NGC 604", ra_hours=1.5661, dec_deg=30.7889, center=True,
                autofocus_first=False,
                steps=[ExposureStep(filter="L", exposure_s=0.01, count=1)])
    base.update(kw)
    return Target(**base)


async def test_engine_holds_after_three_relocks_in_ten_minutes(sim_hub, monkeypatch):
    """Three re-locks inside the window is a field that is walking, whatever
    the RMS says. Stop, throw the calibration away (so the restart measures a
    new one — GN-01's latch), re-centre by plate solve, and only then guide
    again. In that order: re-centring slews, and a slew would tear down guiding
    just paid for."""
    logs = _Logs(monkeypatch)
    e = _engine(sim_hub, monkeypatch)
    spy = _HoldSpy(sim_hub, monkeypatch, _relock_stats(1.0, 2.0, 3.0))

    await e._maybe_hold_for_relocks(_target())

    assert spy.calls == ["stop", "clear", "center", "start"], \
        f"wrong order: {spy.calls}"
    assert logs.at("warning", "the field is walking"), \
        f"the hold was silent; warnings were {logs.at('warning', '')}"

    # ...and the very next frame must not hold again on the SAME three events.
    spy.calls.clear()
    await e._maybe_hold_for_relocks(_target())
    assert spy.calls == [], f"held twice on one set of re-locks: {spy.calls}"


async def test_two_relocks_or_old_relocks_do_not_hold(sim_hub, monkeypatch):
    """Below the threshold nothing changes at all — a hold costs a slew, a
    calibration walk and several minutes of the night."""
    e = _engine(sim_hub, monkeypatch)
    spy = _HoldSpy(sim_hub, monkeypatch, _relock_stats(1.0, 2.0))
    await e._maybe_hold_for_relocks(_target())
    assert spy.calls == []

    e2 = _engine(sim_hub, monkeypatch)
    spy2 = _HoldSpy(sim_hub, monkeypatch, _relock_stats(15.0, 16.0, 17.0))
    await e2._maybe_hold_for_relocks(_target())
    assert spy2.calls == [], "held on re-locks from a quarter of an hour ago"


async def test_relock_limit_zero_disables(sim_hub, monkeypatch):
    """0 is off, the same convention every other threshold in this config uses."""
    e = _engine(sim_hub, monkeypatch, limit=0)
    spy = _HoldSpy(sim_hub, monkeypatch, _relock_stats(1.0, 2.0, 3.0, 4.0))
    await e._maybe_hold_for_relocks(_target())
    assert spy.calls == []


async def test_a_guider_without_the_relock_fields_is_a_no_op(sim_hub, monkeypatch):
    """The PHD2/NINA bridge and every test double report a bare ``GuideStats``.
    A gate that raised on them would take the night down for a guider that
    simply cannot measure this."""
    e = _engine(sim_hub, monkeypatch)
    spy = _HoldSpy(sim_hub, monkeypatch, GuideStats(guiding=True))
    await e._maybe_hold_for_relocks(_target())
    assert spy.calls == []


async def test_a_target_that_opted_out_of_centring_is_not_recentred(sim_hub, monkeypatch):
    """``center=False`` is an existing statement of intent (a mosaic panel, a
    deliberately offset framing) — the hold honours it exactly as
    ``_maybe_recover_guiding`` does, and still recalibrates."""
    e = _engine(sim_hub, monkeypatch)
    spy = _HoldSpy(sim_hub, monkeypatch, _relock_stats(1.0, 2.0, 3.0))
    await e._maybe_hold_for_relocks(_target(center=False))
    assert spy.calls == ["stop", "clear", "start"]


async def test_the_hold_runs_on_every_frame_boundary(sim_hub, monkeypatch):
    """Wiring, not behaviour: the gate is worthless unless the per-frame path
    calls it, and it must run beside ``_maybe_recover_guiding`` — before the
    next exposure, after the flip check."""
    import inspect

    from astrodeck.sequence.engine import SequenceEngine

    src = inspect.getsource(SequenceEngine._run_step)
    assert "_maybe_hold_for_relocks" in src, \
        "the per-frame path never calls the re-lock gate"
    assert src.index("_maybe_recover_guiding") < src.index("_maybe_hold_for_relocks")


# --------------------------------------------- GN-02 follow-through: the cap


def test_engine_config_clamps_pulse_durations_to_the_mount_cap(monkeypatch):
    """GN-02 published ``Telescope.max_pulse_ms`` (the AM5 caps one pulse at
    1000 ms) and nothing read it. A calibration step longer than the cap is
    silently TRUNCATED by the driver, so the engine divides the measured travel
    by a duration that never ran and the px/ms rate comes out low by the
    truncation ratio — 1247/1000 here, a 25% under-correction on every pulse
    for the rest of the night."""
    logs = _Logs(monkeypatch)
    # 5.5 arcsec/px and 0.61x sidereal derive a 1247 ms step: the calibration
    # distance (25 px floor) crossed in _CAL_TARGET_STEPS pulses.
    rates = (0.0025524, 0.0025524)
    cfg = {"image_scale_arcsec": 5.5, "image_scale_known": True}

    free = NativeGuider(None, _Tel(), config=cfg, profile_id=None)
    uncapped = free._build_engine_config(rates)
    assert uncapped["calibration_duration_ms"] == 1247
    assert not logs.any("clamped"), "clamped a mount that declares no cap"

    tel = _Tel()
    tel.max_pulse_ms = 1000
    capped = NativeGuider(None, tel, config=cfg, profile_id=None)
    got = capped._build_engine_config(rates)
    assert got["calibration_duration_ms"] == 1000
    assert got["max_ra_duration_ms"] <= 1000
    assert got["max_dec_duration_ms"] <= 1000
    assert logs.at("info", "clamped"), \
        f"the clamp was silent; logs were {logs.any('')}"
    # A shorter step crosses less sky, so the leg needs proportionally more of
    # them before the engine calls the calibration failed.
    assert got["max_steps"] >= math.ceil(
        nativemod._CAL_TARGET_STEPS * 1247 / 1000)


def test_a_pinned_calibration_duration_is_clamped_too(monkeypatch):
    """The derivation is not the only way a too-long pulse gets in: a config
    that pins ``calibration_duration_ms`` outright would otherwise walk
    straight past the cap."""
    _Logs(monkeypatch)
    tel = _Tel()
    tel.max_pulse_ms = 1000
    g = NativeGuider(None, tel, profile_id=None,
                     config={"image_scale_arcsec": 2.0,
                             "calibration_duration_ms": 2400})
    assert g._build_engine_config(None)["calibration_duration_ms"] == 1000


# ------------------------------------------------------------------ the config


def test_guide_config_declares_the_relock_thresholds(tmp_path, monkeypatch):
    """The two settings exist, carry the shipped defaults, round-trip — and
    REACH the guider through the factory production actually uses
    (``build_native_guider`` -> ``guide_algo_config``). A hand-built guider
    proves nothing about that wire (GN-01's lesson: ``flip_requires_dec_flip``
    was read from the config dict for months with nothing declaring it)."""
    import astrodeck.config as config
    from astrodeck.config import ConfigStore, GuideConfig
    from astrodeck.devices.sim import build_sim_rig
    from astrodeck.guide.native import build_native_guider, guide_algo_config
    from astrodeck.providers import NATIVE_AVAILABLE

    assert GuideConfig().relock_limit == 3
    assert GuideConfig().relock_window_min == 10.0
    dumped = GuideConfig().model_dump()
    assert dumped["relock_limit"] == 3
    assert GuideConfig.model_validate(dumped).relock_window_min == 10.0
    with pytest.raises(Exception):
        GuideConfig(relock_limit=-1)
    with pytest.raises(Exception):
        GuideConfig(relock_window_min=0)

    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config, "config_store", store)
    store.set_guide(GuideConfig(relock_limit=5, relock_window_min=20.0))

    assert guide_algo_config()["relock_limit"] == 5
    assert guide_algo_config()["relock_window_min"] == 20.0

    if not NATIVE_AVAILABLE:                      # pragma: no cover
        pytest.skip("native wheel absent")
    rig = build_sim_rig()
    g = build_native_guider(rig["guide_camera"], rig["telescope"])
    assert g is not None
    assert g._relock_limit == 5
    assert g._relock_window_min == 20.0
    # ...and they are HOST-side: the engine allowlist must not forward them.
    engine_cfg = g._build_engine_config(None)
    assert "relock_limit" not in engine_cfg
    assert "relock_window_min" not in engine_cfg
