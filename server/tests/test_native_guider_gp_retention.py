"""A5 (P4-T1 ruling B + fix round, amended spec §3-A5): NativeGuider persists/
loads the PPEC model window beside the calibration — ``{"dumped_at": <epoch s>,
"window": [[t, m, v, c], ...]}`` — restores it through the engine's
retain-or-reset downtime gate, and clear_calibration removes BOTH files.

Stub tests cover the persistence-file mechanics with a fake engine; the
real-wheel test (review test-blind-spot #2) drives REAL PPEC frames through the
actual wheel: engine -> dump -> file -> restore -> engine, on both sides of the
gate. (The Rust dump/restore/gate math itself is covered by astro-guide
goldens; the restore->prediction quality e2e is the unattended-night gate.)"""
import json
import math
import time

import pytest

import astrodeck.config as configmod
from astrodeck.guide.native import NativeGuider
from astrodeck.providers import NATIVE_AVAILABLE


class _FakeEngine:
    def __init__(self, window, restore_result=True):
        self._window = window
        self._restore_result = restore_result
        self.restored = None

    def dump_gp_window(self):
        return self._window

    def restore_gp_window(self, points, downtime_s):
        self.restored = (points, downtime_s)
        return self._restore_result


def _guider(tmp_path, monkeypatch, window, restore_result=True):
    monkeypatch.setattr(configmod, "CONFIG_DIR", tmp_path)
    g = NativeGuider.__new__(NativeGuider)  # bypass __init__ (no devices needed)
    g.profile_id = "prof1"
    g._engine = _FakeEngine(window, restore_result)
    return g


def test_persist_then_load_roundtrip(tmp_path, monkeypatch):
    window = [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, -0.05]]
    g = _guider(tmp_path, monkeypatch, window)
    before = time.time()
    g._persist_gp_window()
    p = tmp_path / "guider" / "prof1-gp.json"
    assert p.exists()
    saved = json.loads(p.read_text())
    assert saved["window"] == window
    assert before <= saved["dumped_at"] <= time.time()
    loaded = g._load_gp_window()
    assert loaded is not None
    dumped_at, points = loaded
    assert dumped_at == saved["dumped_at"]
    assert points == [(0.0, 0.1, 1.0, 0.0), (5.0, 0.2, 1.0, -0.05)]


def test_restore_passes_downtime_not_a_percentage(tmp_path, monkeypatch):
    # Fix round: the threshold lives in the Rust engine
    # (GpParams::retain_max_pct_period); Python passes ONLY the measured
    # downtime (now - dumped_at), which for an immediate restore is ~0 s —
    # not the old 40.0-percent constant.
    window = [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, -0.05]]
    g = _guider(tmp_path, monkeypatch, window)
    g._persist_gp_window()
    g._restore_gp_window()
    assert g._engine.restored is not None
    points, downtime_s = g._engine.restored
    assert 0.0 <= downtime_s < 30.0, "downtime is wall seconds since the dump"
    assert points[0] == (0.0, 0.1, 1.0, 0.0)


def test_restore_gate_rejection_is_nonfatal(tmp_path, monkeypatch):
    # Engine returns False (downtime outside the retention window): the
    # restore path logs the fresh-start line and never raises.
    window = [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, -0.05]]
    g = _guider(tmp_path, monkeypatch, window, restore_result=False)
    g._persist_gp_window()
    g._restore_gp_window()  # must not raise
    assert g._engine.restored is not None


def test_untrained_or_empty_window_not_persisted(tmp_path, monkeypatch):
    g = _guider(tmp_path, monkeypatch, [[0.0, 0.0, 0.0, 0.0]])  # 1 point (<2)
    g._persist_gp_window()
    assert not (tmp_path / "guider" / "prof1-gp.json").exists()


def test_corrupt_gp_file_is_ignored(tmp_path, monkeypatch):
    g = _guider(tmp_path, monkeypatch, [])
    d = tmp_path / "guider"
    d.mkdir(parents=True, exist_ok=True)
    (d / "prof1-gp.json").write_text("{not json", encoding="utf-8")
    assert g._load_gp_window() is None  # logged + fresh model, never raises
    # Legacy/foreign shapes (bare array — the pre-fix-round format — or a
    # dict without "window") are equally ignored.
    (d / "prof1-gp.json").write_text(
        json.dumps([[0.0, 0.1, 1.0, 0.0]]), encoding="utf-8")
    assert g._load_gp_window() is None
    (d / "prof1-gp.json").write_text(json.dumps({"dumped_at": 1.0}),
                                     encoding="utf-8")
    assert g._load_gp_window() is None


def test_clear_calibration_removes_both_files(tmp_path, monkeypatch):
    g = _guider(tmp_path, monkeypatch,
                [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, 0.0]])
    d = tmp_path / "guider"
    d.mkdir(parents=True, exist_ok=True)
    (d / "prof1.json").write_text("{}", encoding="utf-8")
    g._persist_gp_window()
    assert (d / "prof1-gp.json").exists()
    assert g.clear_calibration() is True
    assert not (d / "prof1.json").exists()
    assert not (d / "prof1-gp.json").exists()


# --- the id is interpolated into a path (#24 rider) -------------------------
#
# Until #24 these five sites read `CONFIG_DIR/guider/{self.profile_id}.json`
# with a literal f-string, and the id was a hardcoded None or the sim's "sim",
# so nothing hostile could ever reach them. Real profile ids now flow in. They
# are uuid4-derived (profiles.py:76) and therefore safe TODAY — the point of
# routing them through the same `safe_id_path` the profile store itself uses is
# that they stay safe when the next feature lets someone name a profile.


@pytest.mark.parametrize("hostile", ["../victim", "..\\victim", "sub/victim"])
def test_a_profile_id_can_never_address_a_file_outside_the_guider_dir(
        tmp_path, monkeypatch, hostile):
    """`clear_calibration` UNLINKS what the id resolves to. A decoy one level up
    is the concrete stake: `../victim` deleted `CONFIG_DIR/victim.json`."""
    g = _guider(tmp_path, monkeypatch,
                [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, 0.0]])
    g.profile_id = hostile
    victim = tmp_path / "victim.json"
    victim.write_text("{}", encoding="utf-8")

    g._persist_gp_window()
    assert g.clear_calibration() is False
    assert victim.exists(), "a profile id must not address a file it does not own"
    assert g._load_gp_window() is None
    assert g._load_persisted_calibration() is None
    # ...and nothing was written under the id either, on any platform: a
    # separator the RUNNING os does not recognise is a literal filename here,
    # which is containment but not the refusal the guard promises.
    assert not list(tmp_path.rglob("*victim*.json"))[1:]


def test_an_ordinary_profile_id_still_round_trips(tmp_path, monkeypatch):
    """The guard must not cost the normal case. uuid4 ids have hyphens in them
    and hyphens are the one thing the `-gp.json` suffix also uses."""
    g = _guider(tmp_path, monkeypatch,
                [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, 0.0]])
    g.profile_id = "7f3a1c2e-9b40-4d51-8a6f-2c0d5e7b1a94"
    g._persist_gp_window()
    assert (tmp_path / "guider" /
            "7f3a1c2e-9b40-4d51-8a6f-2c0d5e7b1a94-gp.json").exists()
    assert g._load_gp_window() is not None
    assert g.clear_calibration() is True


# --- real-wheel round-trip (fix round, review test-blind-spot #2) -----------

def _star_frame(cx, cy, w=64, h=64, amp=4000.0, sg=1.6, bg=100):
    import numpy as np
    yy, xx = np.mgrid[0:h, 0:w]
    g = amp * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sg * sg)))
    return np.clip(bg + g, 0, 65535).astype(np.uint16)


_IDENT_CAL = {"x_rate": 0.01, "y_rate": 0.01, "x_angle": 0.0,
              "y_angle": math.pi / 2, "y_angle_error": 0.0,
              "declination": 0.0, "pier_side": "west",
              "ra_parity": "even", "dec_parity": "even",
              "rotator_angle": 0.0, "binning": 1, "is_valid": True}


def _real_guider(tmp_path, engine):
    g = NativeGuider.__new__(NativeGuider)
    g.profile_id = "prof1"
    g._engine = engine
    return g


@pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")
def test_real_wheel_roundtrip_engine_dump_file_restore_engine(
        tmp_path, monkeypatch):
    """engine -> dump -> file -> restore -> engine through the REAL wheel —
    the exact path the fake-engine stubs cannot see (the A-T5 review's central
    finding hid there: a real dump previously ended with the pending
    ``(0,0,0,c)`` row, making the retention decision inert)."""
    import astrodeck_native as native
    monkeypatch.setattr(configmod, "CONFIG_DIR", tmp_path)

    eng = native.GuideEngine(
        {"ra_algorithm": "ppec", "image_scale_arcsec": 2.0})
    eng.load_calibration(dict(_IDENT_CAL))
    eng.begin_guiding()
    t = 0.0
    eng.process(_star_frame(32.0, 32.0), t, 5.0)  # establishes lock
    for i in range(1, 13):  # 12 accepted guide frames -> 12 real GP points
        t += 5.0
        eng.process(_star_frame(32.0 + 0.3 * math.sin(i / 3.0), 32.0), t, 5.0)

    window = eng.dump_gp_window()
    assert len(window) >= 2, "real frames trained a real window"
    ts = [row[0] for row in window]
    assert ts == sorted(ts) and ts[-1] > 0.0, (
        "monotone timestamps ending on a real measurement -> no trailing "
        "pending (0,0,0,c) row in a REAL dump")

    _real_guider(tmp_path, eng)._persist_gp_window()
    saved = json.loads((tmp_path / "guider" / "prof1-gp.json").read_text())
    assert saved["window"] == window and saved["dumped_at"] > 0

    # Within the gate: an immediate restore (downtime ~0 s << 40% of 200 s)
    # brings the ENTIRE window back — the fresh engine's dump equals the file.
    eng2 = native.GuideEngine(
        {"ra_algorithm": "ppec", "image_scale_arcsec": 2.0})
    _real_guider(tmp_path, eng2)._restore_gp_window()
    assert eng2.dump_gp_window() == window, (
        "engine -> dump -> file -> restore -> engine round-trips exactly")

    # Beyond the gate: age the file 1000 s (> 80 s threshold) -> fresh model.
    saved["dumped_at"] -= 1000.0
    (tmp_path / "guider" / "prof1-gp.json").write_text(json.dumps(saved),
                                                       encoding="utf-8")
    eng3 = native.GuideEngine(
        {"ra_algorithm": "ppec", "image_scale_arcsec": 2.0})
    _real_guider(tmp_path, eng3)._restore_gp_window()
    assert eng3.dump_gp_window() == [], (
        "past-gate restore leaves a fresh (untrained -> empty-dump) model")


# --- #210: dumped_at is when the model was last FED, and only a live stop ----
#
# `stop_guiding` used to call `_persist_gp_window()` on every call, and that
# stamped `dumped_at = time.time()` whenever the engine object still held a
# trained window (a stop does not clear `_engine`). `_restore_gp_window` then
# re-phases the model by `now - dumped_at` and keeps it only inside the
# engine's retain window (40% of the 200 s kernel period: 80 s). So a SECOND
# stop on an already stopped guider re-stamped the file, and the next start
# on the calibration-reuse path kept a model that was really minutes old and
# re-phased it by seconds. S1-12 (#148) stands the guider down before every
# slew, which made that second stop routine.
#
# These drive the REAL wheel through a REAL `start_guiding` on the reuse path,
# on a virtual wall clock (`guide.native.time`), because the retain-or-reset
# gate lives in the Rust engine: "reset, not restored" is read off the fresh
# engine's own dump, not off a fake's opinion of the gate. Each was shown RED
# under a named mutation of guide/native.py, run from a byte-for-byte backup
# and restored byte-identical; the observed failure is quoted verbatim.


class _WallClock:
    """``guide.native``'s ``time`` module with a settable ``time()``;
    ``monotonic`` and the rest delegate to the real module."""

    def __init__(self, wall: float = 50_000.0) -> None:
        self.wall = wall

    def time(self) -> float:
        return self.wall

    def __getattr__(self, name):
        return getattr(time, name)


class _StarCam:
    """A guide camera that serves ``serve`` star frames, advancing the virtual
    clock one 5 s exposure per frame, and then STARVES: the next exposure
    never returns (a hung camera), until ``stop_guiding`` cancels it. The
    first frame of every start is the reuse path's star-existence check."""

    name = "fake guide camera"

    def __init__(self, clock: _WallClock, serve: int) -> None:
        import asyncio
        self.clock = clock
        self.serve = serve
        self.served = 0
        self.starved = asyncio.Event()
        self._never = asyncio.Event()

    async def expose(self, exposure_s, gain, offset, binning=1):
        from types import SimpleNamespace
        if self.served >= self.serve:
            self.starved.set()
            await self._never.wait()
        i = self.served
        self.served += 1
        self.clock.wall += 5.0
        # The real-wheel round-trip's training frames: a star wobbling 0.3 px.
        return SimpleNamespace(
            data=_star_frame(32.0 + 0.3 * math.sin(i / 3.0), 32.0),
            timestamp=self.clock.wall)


class _Mount:
    """Pier and declination matching ``_IDENT_CAL``, so the persisted
    calibration passes every reuse gate and the start takes the reuse path."""

    name = "fake mount"
    can_pulse_guide = True

    async def pulse_guide(self, direction, ms):
        return None

    async def guide_rates(self):
        return (0.004178, 0.004178)

    async def get_position(self):
        return (5.0, 0.0)

    async def pier_side(self):
        from types import SimpleNamespace
        return SimpleNamespace(value="west")


_TRAIN_FRAMES = 16      # 1 star check + 1 lock + 14 accepted -> a real window


@pytest.fixture
def _reuse_rig(tmp_path, monkeypatch):
    """A PPEC guider for profile ``prof1`` whose persisted calibration is
    reusable, on the virtual clock. Returns ``(guider, clock)``."""
    import astrodeck.guide.native as nativemod
    monkeypatch.setattr(configmod, "CONFIG_DIR", tmp_path)
    clock = _WallClock()
    monkeypatch.setattr(nativemod, "time", clock)
    d = tmp_path / "guider"
    d.mkdir(parents=True, exist_ok=True)
    (d / "prof1.json").write_text(
        json.dumps({**_IDENT_CAL, "image_scale_arcsec": 2.0}), encoding="utf-8")
    g = NativeGuider(None, _Mount(),
                     config={"ra_algorithm": "ppec", "image_scale_arcsec": 2.0,
                             "image_scale_known": True, "exposure_s": 5.0},
                     profile_id="prof1")
    return g, clock


async def _session(g, clock, serve: int) -> None:
    """Start on the reuse path and let the loop process ``serve - 1`` frames,
    then return with the camera starved (the loop is parked in an exposure)."""
    import asyncio
    g.cam = _StarCam(clock, serve)
    await g.start_guiding()
    await asyncio.wait_for(g.cam.starved.wait(), timeout=30.0)


def _gp_file(tmp_path) -> dict:
    return json.loads((tmp_path / "guider" / "prof1-gp.json").read_text())


@pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")
@pytest.mark.asyncio
async def test_a_second_stop_does_not_restamp_the_model(_reuse_rig, tmp_path):
    """The #210 scenario. Guide, stop, wait past the retain window, stop
    AGAIN, then start on the reuse path: the model is RESET. The idle stop
    must not have written anything.

    MUTANT "unconditional persist" (``if ended_a_session:`` removed, so every
    stop persists) -- RED, observed verbatim:

        AssertionError: an idle stop re-stamped the PPEC file (dumped_at
        50080.0 -> 51080.0), so a model 1005 s old was restored as fresh
    """
    g, clock = _reuse_rig
    await _session(g, clock, _TRAIN_FRAMES)
    await g.stop_guiding()
    first = _gp_file(tmp_path)
    assert len(first["window"]) >= 2, "session 1 trained no PPEC window"

    clock.wall += 1000.0                    # far past the 80 s retain window
    await g.stop_guiding()                  # the idle stop
    second = _gp_file(tmp_path)

    await _session(g, clock, 1)             # reuse path, no frames fed
    restored = g._engine.dump_gp_window()
    await g.stop_guiding()
    assert second["dumped_at"] == first["dumped_at"] and restored == [], (
        f"an idle stop re-stamped the PPEC file (dumped_at "
        f"{first['dumped_at']} -> {second['dumped_at']}), so a model "
        f"{clock.wall - first['dumped_at']:.0f} s old was restored as fresh")


@pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")
@pytest.mark.asyncio
async def test_dumped_at_is_the_last_fed_frame_not_the_stop(_reuse_rig, tmp_path):
    """A loop starved of frames (a hung exposure) stops feeding the model the
    moment the frames stop, and the file must say so. Here the camera hangs,
    500 s pass, and then the guider is stopped: ``dumped_at`` is the last
    processed frame, and the next start on the reuse path resets the model.

    MUTANT "stamp at write time" (``dumped_at = time.time()`` in
    ``_persist_gp_window``) -- RED, observed verbatim:

        AssertionError: dumped_at is the stop (50580.0), not the last fed
        frame (50080.0)

    (It also turns ``test_a_restored_model_keeps_its_feed_time_until_fed``
    RED, by the same stamp.)
    """
    g, clock = _reuse_rig
    await _session(g, clock, _TRAIN_FRAMES)
    last_fed = clock.wall                   # the clock moves only per frame
    clock.wall += 500.0                     # starved: no frame arrives
    await g.stop_guiding()
    saved = _gp_file(tmp_path)
    assert saved["dumped_at"] == last_fed, (
        f"dumped_at is the stop ({saved['dumped_at']}), not the last fed "
        f"frame ({last_fed})")

    await _session(g, clock, 1)
    restored = g._engine.dump_gp_window()
    await g.stop_guiding()
    assert restored == [], (
        "a model starved for 500 s was restored as if it had just been fed")


@pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")
@pytest.mark.asyncio
async def test_control_one_live_stop_persists_and_restores(_reuse_rig, tmp_path):
    """CONTROL, green under both mutants above: one stop after live guiding
    persists the model, stamped with its last frame, and a start 35 s later
    (inside the retain window) restores the WHOLE window, as before #210."""
    g, clock = _reuse_rig
    await _session(g, clock, _TRAIN_FRAMES)
    last_fed = clock.wall
    await g.stop_guiding()
    saved = _gp_file(tmp_path)
    assert saved["dumped_at"] == last_fed
    assert len(saved["window"]) >= 2

    clock.wall += 30.0
    await _session(g, clock, 1)             # +5 s: the star-existence check
    restored = g._engine.dump_gp_window()
    await g.stop_guiding()
    assert [list(r) for r in restored] == saved["window"], \
        "a model 35 s old was not restored"


@pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")
@pytest.mark.asyncio
async def test_a_restored_model_keeps_its_feed_time_until_fed(_reuse_rig, tmp_path):
    """The same defect by the other door. A start on the reuse path restores
    the model and is stopped before its loop feeds a frame (a stand-down
    right after a start). That stop ended a live session and persists, and
    what it persists was last fed when the FILE says, not now: the engine
    keeps the restored points' own timestamps and carries the downtime
    separately. Here the model is fed at T, restored at T+35, stopped at
    T+65 and started again at T+100: 100 s old, past the 80 s window.

    MUTANT "the restore does not seed the feed time" (the
    ``self._gp_fed_at = dumped_at`` line in ``_restore_gp_window`` removed)
    -- RED, observed verbatim:

        AssertionError: the re-persisted restored model is stamped 50145.0,
        not its real feed time 50080.0
    """
    g, clock = _reuse_rig
    await _session(g, clock, _TRAIN_FRAMES)
    fed = clock.wall
    await g.stop_guiding()

    clock.wall += 30.0
    await _session(g, clock, 1)             # restored at fed + 35
    assert g._engine.dump_gp_window() != [], "the 35 s restore did not happen"
    clock.wall += 30.0
    await g.stop_guiding()                  # live (a loop task existed)
    saved = _gp_file(tmp_path)
    assert saved["dumped_at"] == fed, (
        f"the re-persisted restored model is stamped {saved['dumped_at']}, "
        f"not its real feed time {fed}")

    clock.wall += 30.0
    await _session(g, clock, 1)             # fed + 100
    restored = g._engine.dump_gp_window()
    await g.stop_guiding()
    assert restored == [], "a model 100 s old was restored"


@pytest.mark.asyncio
async def test_a_star_lost_frame_does_not_feed_the_model(tmp_path, monkeypatch):
    """A star-lost frame does not stamp the feed time: the engine had no star
    to measure, so the model learned nothing, and a loss that ends in the
    reacquire budget's honest death would otherwise carry the dead frames'
    time into the file.

    The fake keeps the engine's contract for ``recent`` (``engine.rs``
    ``push_recent``, the accept path only), which is what the loop reads
    since #189 H2 item 13: frame 2's measurement enters it, the lock frame
    and the two star-lost frames leave it alone.

    MUTANT "every processed frame feeds the model" (the stamp in
    ``_guide_loop`` made unconditional) -- RED, observed verbatim:

        AssertionError: the feed time is the last star-lost frame (50020.0),
        not the last measured one (50010.0)

    Green under "today's rule" (``!= "lock_lost"`` counts as a feed), the
    control #189 H2 item 13 asks for: that rule also skips a star-lost frame.
    """
    import asyncio
    import astrodeck.guide.native as nativemod
    clock = _WallClock()
    monkeypatch.setattr(nativemod, "time", clock)
    lost = {"action": "lock_lost", "reason": "star_lost"}
    # (Action, whether the engine accepted a measurement on that frame)
    script = [({"action": "idle"}, False),
              ({"action": "pulse_pair", "ra": {"dir": "west", "ms": 100},
                "dec": None}, True),
              (lost, False), (lost, False)]

    class _Engine:
        def __init__(self) -> None:
            self.recent: list[list[float]] = []

        def process(self, data, ts, exposure_s):
            action, measured = script.pop(0)
            if measured:
                self.recent.append([ts, 0.1, 0.0])
            return action

        def stats(self):
            return {"guiding": True, "settling": False,
                    "recent": [list(r) for r in self.recent]}

    g = NativeGuider(None, _Mount(), config={"exposure_s": 0.01},
                     profile_id=None)
    monkeypatch.setattr(nativemod, "_native", None)   # no star-finds needed
    g._engine = _Engine()
    g._active = True
    g._stop.clear()
    served = []

    class _Cam:
        name = "fake guide camera"

        async def expose(self, exposure_s, gain, offset, binning=1):
            from types import SimpleNamespace
            served.append(clock.wall)
            clock.wall += 5.0
            if len(served) == 4:
                g._stop.set()
            await asyncio.sleep(0)
            return SimpleNamespace(data=len(served), timestamp=clock.wall)

    g.cam = _Cam()
    await g._guide_loop()
    measured = 50_000.0 + 2 * 5.0          # frame 2, the pulse_pair
    assert g._gp_fed_at == measured, (
        f"the feed time is the last star-lost frame ({g._gp_fed_at}), not "
        f"the last measured one ({measured})")


# --- #189 H2 item 13: only a real measurement stamps the feed time ----------
#
# The loop used to stamp ``_gp_fed_at`` on every frame whose Action was not a
# ``lock_lost``. The Action says what the MOUNT should do, not whether the
# engine measured anything, and it is wrong in both directions:
#
# * ``idle`` is the lock-establishing frame, a mass reject and a distance
#   reject (``engine.rs`` ``ingest_guiding`` steps 1, 3 and 4), none of which
#   measured the star; and a dead-reckoned lost-star frame under PPEC is a
#   ``pulse_pair`` predicted with no measurement at all (``deduce_move``).
# * An ACCEPTED frame whose correction falls under the min-move deadband is
#   ``idle`` too, and it did measure.
#
# The engine's own evidence is its ``recent`` window: ``push_recent`` runs on
# the accept path (step 5) and nowhere else a guiding frame reaches. These
# drive the REAL wheel through the real ``_guide_loop``, so each frame takes
# the engine path its test names because the engine took it, and each test
# asserts that premise from the engine's own Action before it asserts the
# stamp. Each was shown RED under the named mutation of guide/native.py, run
# from a byte-for-byte backup and restored byte-identical.


def _live_ppec_engine():
    """A real PPEC engine, calibrated and asked to guide: its next found star
    establishes the lock."""
    import astrodeck_native as native
    eng = native.GuideEngine(
        {"ra_algorithm": "ppec", "image_scale_arcsec": 2.0})
    eng.load_calibration(dict(_IDENT_CAL))
    eng.begin_guiding()
    return eng


class _Recorded:
    """The REAL engine with each ``process()`` Action kind kept, so a test can
    say which path a frame took from the engine's own answer. Everything else
    passes straight through to the engine."""

    def __init__(self, engine) -> None:
        self._engine = engine
        self.actions: list[str] = []

    def process(self, data, ts, exposure_s):
        a = self._engine.process(data, ts, exposure_s)
        self.actions.append(a["action"])
        return a

    def __getattr__(self, name):
        return getattr(self._engine, name)


class _ScriptCam:
    """Serves ``frames`` in order, advancing the virtual clock ``dt`` s before
    each and stamping the frame with it, and sets the stop flag on the last so
    the loop processes exactly the script. ``fed[k]`` is the guider's
    ``_gp_fed_at`` after frame ``k``: read here, when frame ``k + 1`` is asked
    for, and for the last frame by ``_drive`` once the loop has returned.
    ``frame_ts``, when given, is a camera clock that never ticks: every frame
    carries it while the host's clock moves on."""

    name = "fake guide camera"

    def __init__(self, g, clock: _WallClock, frames, dt: float,
                 frame_ts: float | None = None) -> None:
        self.g = g
        self.clock = clock
        self.frames = list(frames)
        self.dt = dt
        self.frame_ts = frame_ts
        self.times: list[float] = []
        self.fed: list[float | None] = []

    async def expose(self, exposure_s, gain, offset, binning=1):
        import asyncio
        from types import SimpleNamespace
        if self.times:
            self.fed.append(self.g._gp_fed_at)
        i = len(self.times)
        self.clock.wall += self.dt
        self.times.append(self.clock.wall)
        if i == len(self.frames) - 1:
            self.g._stop.set()
        await asyncio.sleep(0)
        ts = self.clock.wall if self.frame_ts is None else self.frame_ts
        return SimpleNamespace(data=self.frames[i], timestamp=ts)


async def _drive(monkeypatch, frames, dt: float, frame_ts: float | None = None):
    """Run the real ``_guide_loop`` over ``frames`` on a fresh live PPEC
    engine and the virtual clock. Returns ``(engine, cam)``."""
    import astrodeck.guide.native as nativemod
    clock = _WallClock()
    monkeypatch.setattr(nativemod, "time", clock)
    g = NativeGuider(None, _Mount(),
                     config={"ra_algorithm": "ppec", "image_scale_arcsec": 2.0,
                             "image_scale_known": True, "exposure_s": dt},
                     profile_id=None)
    eng = _Recorded(_live_ppec_engine())
    g._engine = eng
    g._active = True
    g._stop.clear()
    cam = _ScriptCam(g, clock, frames, dt, frame_ts)
    g.cam = cam
    await g._guide_loop()
    cam.fed.append(g._gp_fed_at)
    assert len(cam.fed) == len(frames), "the loop did not process the script"
    return eng, cam


_BLANK = None           # marker in a script: a frame with no star in it


def _frames(script):
    return [_star_frame(32.0, 32.0, amp=0.0) if f is _BLANK else f
            for f in script]


@pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")
@pytest.mark.asyncio
async def test_the_lock_frame_does_not_stamp_but_an_idle_accept_does(monkeypatch):
    """Frame 0 establishes the lock: ``idle``, and nothing measured. Frame 1
    is the same star at the lock, ACCEPTED, and ``idle`` as well, because a
    zero error asks for no pulse. Only frame 1 is a feed.

    MUTANT "today's rule" (``action.get("action") != "lock_lost"`` counts as a
    feed) -- RED, observed verbatim:

        AssertionError: the lock-establishing frame stamped the feed time
        (50005.0); it measured nothing

    MUTANT "only a pulse is a feed" (``action.get("action") in ("pulse",
    "pulse_pair")``) -- RED, observed verbatim:

        AssertionError: an accepted frame that asked for no pulse did not
        stamp the feed time (None, frame at 50010.0)
    """
    star = _star_frame(32.0, 32.0)
    eng, cam = await _drive(monkeypatch, [star, star], dt=5.0)
    # Premise, from the engine: both frames are idle, and the second (only)
    # put a measurement in the engine's window.
    assert eng.actions == ["idle", "idle"], eng.actions
    assert len(eng.stats()["recent"]) == 1
    assert cam.fed[0] is None, (
        f"the lock-establishing frame stamped the feed time ({cam.fed[0]}); "
        f"it measured nothing")
    assert cam.fed[1] == cam.times[1], (
        f"an accepted frame that asked for no pulse did not stamp the feed "
        f"time ({cam.fed[1]}, frame at {cam.times[1]})")


@pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")
@pytest.mark.asyncio
@pytest.mark.parametrize("amp, rejected", [(12_000.0, True), (4_000.0, False)],
                         ids=["mass_reject", "control_same_mass"])
async def test_a_mass_reject_does_not_stamp_the_feed_time(monkeypatch, amp,
                                                          rejected):
    """A lock and seven accepted frames give the mass checker its five-sample
    history; the last frame is the same star at the lock at three times the
    brightness, past the checker's 50% limit (``track.rs`` ``check``), so the
    engine rejects it and measures nothing. The feed time stays on the frame
    before. CONTROL: the same last frame at the same brightness is accepted
    and stamps, so the reject is the brightness and nothing else.

    MUTANT "today's rule" -- RED (mass_reject; the control stays green),
    observed verbatim:

        AssertionError: the mass-rejected frame stamped the feed time
        (50009.0); the last measured frame was 50008.0
    """
    script = [_star_frame(32.0, 32.0)] * 8 + [_star_frame(32.0, 32.0, amp=amp)]
    eng, cam = await _drive(monkeypatch, script, dt=1.0)
    # Premise, from the engine: the last frame is idle either way, and only
    # the control's entered the window (seven accepts after the lock frame).
    assert eng.actions[-1] == "idle", eng.actions
    assert len(eng.stats()["recent"]) == (7 if rejected else 8)
    expected = cam.times[-2] if rejected else cam.times[-1]
    if rejected:
        assert cam.fed[-1] == expected, (
            f"the mass-rejected frame stamped the feed time ({cam.fed[-1]}); "
            f"the last measured frame was {expected}")
    else:
        assert cam.fed[-1] == expected, (
            f"the control's accepted frame did not stamp ({cam.fed[-1]}, "
            f"frame at {expected})")


@pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")
@pytest.mark.asyncio
@pytest.mark.parametrize("x, rejected", [(35.0, True), (32.0, False)],
                         ids=["distance_reject", "control_back_on_lock"])
async def test_a_distance_reject_does_not_stamp_the_feed_time(monkeypatch, x,
                                                              rejected):
    """Thirteen frames on the lock, then one with no star, then the star back
    3 px off the lock. The blank is dead-reckoned (``idle``: an untrained
    model predicts nothing) and measured nothing. The jump is a FOUND star
    with the lock star's mass, and the distance gate rejects it
    (``ingest_guiding`` step 4, ``track.rs`` ``check_distance``): twelve
    accepts put the engine past the ten frames in which every jump counts as
    small, and a star that sat exactly on the lock leaves a smoothed error of
    zero, so any multiple of it is a zero tolerance and a 3 px jump is not
    small. Neither the blank nor the jump stamps. CONTROL: the star back ON
    the lock is accepted and stamps, so the reject is the distance.

    The blank is not what rejects the jump. It activates the checker, but the
    same jump straight after the thirteen lock frames is rejected too (probed
    on the wheel: the window stays at 13). It is here as a second frame that
    must not stamp: a dead-reckoned ``idle``.

    MUTANT "today's rule" -- RED (distance_reject; the control stays green),
    observed verbatim:

        AssertionError: the distance-rejected frame stamped the feed time
        (50015.0); the last measured frame was 50013.0
    """
    import astrodeck_native as native
    script = [_star_frame(32.0, 32.0)] * 13 + [_BLANK, _star_frame(x, 32.0)]
    eng, cam = await _drive(monkeypatch, _frames(script), dt=1.0)
    # Premise, from the engine: both last frames are idle; the star in the
    # last one is there to be found (so it is not a lost-star frame); and
    # only the control's entered the window (twelve accepts after the lock).
    assert eng.actions[-2:] == ["idle", "idle"], eng.actions
    found, _meta = native.guide_star_find(script[-1])
    assert any(abs(s["x"] - x) < 0.5 and abs(s["y"] - 32.0) < 0.5
               for s in found), found
    assert len(eng.stats()["recent"]) == (12 if rejected else 13)
    last_measured = cam.times[-3]
    if rejected:
        assert cam.fed[-1] == last_measured, (
            f"the distance-rejected frame stamped the feed time "
            f"({cam.fed[-1]}); the last measured frame was {last_measured}")
        assert cam.fed[-2] == last_measured, (
            f"the blank frame stamped the feed time ({cam.fed[-2]})")
    else:
        assert cam.fed[-1] == cam.times[-1], (
            f"the control's star back on the lock did not stamp "
            f"({cam.fed[-1]}, frame at {cam.times[-1]})")


@pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")
@pytest.mark.asyncio
async def test_a_dead_reckoned_ppec_pulse_does_not_stamp_the_feed_time(
        monkeypatch):
    """110 accepted frames of a 3 px, 200 s periodic error train PPEC past the
    two periods (400 s of gear time) it needs before it predicts, and fill the
    engine's ``recent`` window to its 100-entry cap. Then the star vanishes:
    the engine dead-reckons a real ``pulse_pair`` from the model's prediction
    (``deduce_move``) with no measurement behind it. That frame does not
    stamp. The accepted frames AT the cap do, although the window's length no
    longer moves.

    MUTANT "today's rule" -- RED, observed verbatim:

        AssertionError: the dead-reckoned pulse stamped the feed time
        (50560.0); the last measured frame was 50555.0

    MUTANT "a growing window is the only evidence" (the mark reduced to
    ``len(recent)``) -- RED, observed verbatim:

        AssertionError: the accepted frames at the window's cap stopped
        stamping: the feed time is 50505.0, the last accepted frame 50555.0
    """
    script = [_star_frame(32.0, 32.0)]
    for k in range(1, 111):
        t = 5.0 * k
        script.append(_star_frame(
            32.0 + 3.0 * math.sin(2.0 * math.pi * t / 200.0), 32.0))
    script.append(_BLANK)
    eng, cam = await _drive(monkeypatch, _frames(script), dt=5.0)
    # Premise, from the engine: the last frame is a real predicted pulse, and
    # the window was already at its cap before it.
    assert eng.actions[-1] == "pulse_pair", eng.actions[-3:]
    assert "lock_lost" not in eng.actions and eng.actions[0] == "idle"
    assert len(eng.stats()["recent"]) == 100, "the window is not at its cap"
    last_accepted = cam.times[-2]
    assert cam.fed[-2] == last_accepted, (
        f"the accepted frames at the window's cap stopped stamping: the feed "
        f"time is {cam.fed[-2]}, the last accepted frame {last_accepted}")
    assert cam.fed[-1] == last_accepted, (
        f"the dead-reckoned pulse stamped the feed time ({cam.fed[-1]}); the "
        f"last measured frame was {last_accepted}")


@pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")
@pytest.mark.asyncio
async def test_frames_sharing_one_timestamp_are_still_told_apart(monkeypatch):
    """A camera clock coarser than the frame rate stamps several frames alike
    (``time.time()`` on Windows moves in system-timer ticks of up to 15.6 ms,
    and frames can come closer together than that). Here every frame carries
    one timestamp. Each
    accept still stamps, because the window's LENGTH moves, and the mass
    reject that shares the accepted frames' timestamp still does not, because
    nothing in the window moves.

    MUTANT "the newest timestamp alone" (the mark reduced to
    ``recent[-1][0]``) -- RED, observed verbatim:

        AssertionError: the second accepted frame on the camera's one
        timestamp did not stamp (50002.0, frame at 50003.0)

    MUTANT "the newest entry is this frame's" (stamp when ``recent[-1][0] ==
    frame.timestamp``) -- RED, observed verbatim:

        AssertionError: the mass-rejected frame stamped the feed time
        (50009.0): it shares the accepted frames' timestamp, not their
        measurement; the last measured frame was 50008.0

    ("today's rule" turns it RED with that same line: the reject is ``idle``.)
    """
    script = [_star_frame(32.0, 32.0)] * 8 + [
        _star_frame(32.0, 32.0, amp=12_000.0)]
    eng, cam = await _drive(monkeypatch, script, dt=1.0, frame_ts=7.0)
    # Premise, from the engine: seven accepts on one timestamp, then a reject.
    assert eng.actions[-1] == "idle", eng.actions
    recent = eng.stats()["recent"]
    assert len(recent) == 7 and {r[0] for r in recent} == {7.0}, recent
    assert cam.fed[2] == cam.times[2], (
        f"the second accepted frame on the camera's one timestamp did not "
        f"stamp ({cam.fed[2]}, frame at {cam.times[2]})")
    assert cam.fed[-1] == cam.times[-2], (
        f"the mass-rejected frame stamped the feed time ({cam.fed[-1]}): it "
        f"shares the accepted frames' timestamp, not their measurement; the "
        f"last measured frame was {cam.times[-2]}")


class _StarThenBlankCam(_StarCam):
    """Serves ``serve`` star frames like ``_StarCam``, then blank sky for as
    long as it is asked: the star is gone (a cloud that never clears), so the
    engine dead-reckons for its 20 s staleness window and then reports
    ``star_lost`` on every frame until the host's reacquire budget kills the
    loop on its own."""

    async def expose(self, exposure_s, gain, offset, binning=1):
        from types import SimpleNamespace
        if self.served < self.serve:
            return await super().expose(exposure_s, gain, offset, binning)
        self.served += 1
        self.clock.wall += 5.0
        return SimpleNamespace(data=_star_frame(32.0, 32.0, amp=0.0),
                               timestamp=self.clock.wall)


@pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")
@pytest.mark.asyncio
async def test_a_stop_after_the_loops_own_death_persists(_reuse_rig, tmp_path):
    """The other arm of "ended a live session": a loop task EXISTED on entry.
    When the loop dies on its own (the reacquire budget, a re-lock stop, a
    camera fault) it drops ``_active`` itself, so by the time anyone calls
    ``stop_guiding`` only the dead task says a session was running. That stop
    must still save what the session trained, stamped from the loop, not from
    the stop that came 500 s later.

    MUTANT "only _active counts" (``ended_a_session = self._active``) -- RED,
    observed verbatim:

        AssertionError: the stop after the loop's own death saved nothing: the
        session's trained PPEC model was discarded
    """
    import asyncio
    g, clock = _reuse_rig
    g.cam = _StarThenBlankCam(clock, _TRAIN_FRAMES)
    await g.start_guiding()
    await asyncio.wait_for(g._loop_task, timeout=30.0)     # died on its own
    assert g._lost and not g._active, "the loop did not die of the star loss"
    died_at = clock.wall
    clock.wall += 500.0

    await g.stop_guiding()
    p = tmp_path / "guider" / "prof1-gp.json"
    assert p.exists(), (
        "the stop after the loop's own death saved nothing: the session's "
        "trained PPEC model was discarded")
    saved = _gp_file(tmp_path)
    assert len(saved["window"]) >= 2
    assert saved["dumped_at"] <= died_at, (
        f"dumped_at {saved['dumped_at']} is after the loop died ({died_at})")
