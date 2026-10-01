"""WP-15 (#135): the calibration-REUSE path must prove the mount is live
before it claims "calibrated and guiding" and persists what it just reused.

The defect, measured on astrotown the night of 2026-09-22/23 (serial link
dead, #133): every mount read on the reuse path -- ``_read_guide_rates``,
the cos(dec) probe, both reads inside ``_apply_scope_pointing``, the pier
check -- is wrapped in ``contextlib.suppress(Exception)``, on purpose (an
unreadable pier or declination must not cost a calibration walk by itself).
Taken together, though, a mount that answers NOTHING passes every one of
them, and the reuse path never touches the mount at all before announcing
success: the camera finding a star was the only evidence it ever had. That
night it announced "calibrated and guiding" four times, each claim
rewriting the persisted calibration file, and each guide loop dying on its
first pulse 5.2-5.4 s later once it finally touched the mount.

The fix (``NativeGuider.start_guiding``) makes the reuse path earn its claim
with one UNSUPPRESSED position read before it loads the persisted
calibration onto the engine. Raising falls back to a fresh calibration and
logs a warning, exactly like a corrupt persisted file already does; it does
not propagate like a missing guide star does, because unlike that case a
fresh calibration is a genuinely different attempt (it may yet find a live
mount where the read above read stale state).

WHY NOT ALSO A STANDALONE LIVENESS PULSE. WP-15's fix shape (owner-approved
2026-09-30, in docs/superpowers/plans/2026-09-30-open-issue-backlog.md)
also asks for "one pulse" to succeed before the claim. A second, synthetic
zero-duration ``pulse_guide`` call here would satisfy that literally, but
it would also fire on every SUCCESSFUL reuse -- and
``test_native_guider_recovery.py``'s
``test_persisted_calibration_reused_across_guider_instances`` asserts
(deliberately; see its own docstring) that a reuse-based ``start_guiding``
makes ZERO ``pulse_guide`` calls, because ``_maybe_recover_guiding``'s
fast-restart contract after a real star loss depends on reuse staying
device-I/O-free. That test is outside this WP's owned files, so this fix
does not add the standalone probe; see ``test_successful_reuse_still_makes_
no_pulse_guide_calls`` below, which pins the same invariant from this
file's side. The "pulse" half of the ruling is instead discharged by the
FALLBACK: once the read above has failed, ``_calibrate()``'s first
``cal_step`` sends a real, non-suppressed ``pulse_guide`` -- so a mount
that cannot actually be pulsed still fails loudly through the walk it falls
back to, which is exactly what ``test_reuse_refuses_a_dead_mount_instead_
of_claiming_guiding`` below measures.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from astrodeck.devices.sim import build_sim_rig
from astrodeck.events import bus
from astrodeck.guide.native import NativeGuider
from astrodeck.providers import NATIVE_AVAILABLE

pytestmark = [
    pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent"),
]


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path, monkeypatch):
    """Point ``CONFIG_DIR`` at ``tmp_path`` so this file never reads or writes
    the real ``server/config/guider/`` (both ``_persist_calibration`` and
    ``_load_persisted_calibration`` import ``CONFIG_DIR`` at CALL time, so
    patching the module attribute takes effect immediately)."""
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return tmp_path


def _profile_id(tag: str) -> str:
    return f"test-w2-reuse-{tag}-{uuid.uuid4().hex[:8]}"


def _cal_path(root, profile: str):
    return root / "guider" / f"{profile}.json"


def _cal_dict(pier: str, scale: float = 2.0) -> dict:
    """A persisted-calibration dict that passes every ``_cal_reusable`` arm
    for a guider built with ``image_scale_arcsec=2.0``, ``binning=1`` (the
    shape ``_persist_calibration`` writes: the engine's ``dump_calibration``
    keys plus the ``image_scale_arcsec`` sidecar). A made-up declination and
    near-orthogonal axis angles -- not a measurement of anything real."""
    return {"x_rate": 0.0035, "y_rate": 0.0031, "x_angle": 0.7853981634,
            "y_angle": 2.3561944902, "y_angle_error": 0.0,
            "declination": -0.0941, "pier_side": pier,
            "ra_parity": "unknown", "dec_parity": "unknown",
            "rotator_angle": 0.0, "binning": 1, "is_valid": True,
            "image_scale_arcsec": scale}


def _plant_cal(root, profile: str, pier: str, scale: float = 2.0) -> dict:
    d = root / "guider"
    d.mkdir(parents=True, exist_ok=True)
    cal = _cal_dict(pier, scale)
    (d / f"{profile}.json").write_text(json.dumps(cal), encoding="utf-8")
    return cal


class _Logs:
    """Every ``bus.log`` line the guider emitted, in order. Spies on the bus
    singleton rather than subscribing a queue: the guide loop publishes a
    ``guide`` event per frame and the bus drops the OLDEST event once a
    subscriber's queue fills, which could silently lose the one line a test
    is asserting on."""

    def __init__(self, monkeypatch) -> None:
        self.lines: list[str] = []
        real = bus.log

        def _spy(level, message, source="hub"):
            self.lines.append(str(message))
            return real(level, message, source)

        monkeypatch.setattr(bus, "log", _spy)

    def has(self, needle: str) -> bool:
        return any(needle in line for line in self.lines)


def _spy_calibrate(g) -> list[int]:
    """Count ``_calibrate`` calls while still running the real thing -- a
    stub would prove the fallback branch was taken but not that a real
    calibration walk follows it."""
    calls: list[int] = []
    real = g._calibrate

    async def _counted():
        calls.append(1)
        return await real()

    g._calibrate = _counted
    return calls


async def _wait_until(predicate, timeout: float, interval: float = 0.05) -> bool:
    """Poll ``predicate`` (a zero-arg callable) until it's truthy or
    ``timeout`` seconds elapse. Returns whether it became true."""
    import time as _time
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return bool(predicate())


async def _connected_guider(profile: str, *, config: dict | None = None):
    """A connected ``NativeGuider`` over a REAL sim rig. Callers override
    individual ``tel`` methods afterwards -- the same technique
    ``test_native_guider_pier_change.py`` uses for ``pier_side`` -- so
    everything this test does not care about (the camera, the engine, the
    rest of the mount contract) stays the genuine sim behaviour."""
    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    cfg = {"image_scale_arcsec": 2.0, "exposure_s": 0.2}
    cfg.update(config or {})
    g = NativeGuider(cam, tel, config=cfg, profile_id=profile)
    await g.connect()
    return g, cam, tel


# ---------------------------------------------------------------- the defect


@pytest.mark.asyncio
async def test_reuse_refuses_a_dead_mount_instead_of_claiming_guiding(
        _isolated_config_dir, monkeypatch):
    """THE #135 NIGHT: every mount call raises (the dead-serial-link shape),
    the guide CAMERA is fine (it is a separate device), and a reusable
    calibration is already on disk. Today's bug: ``start_guiding`` returns
    normally, logs "native guider calibrated and guiding", and persists the
    reused calibration back to disk -- the guide loop only dies, 5ish
    seconds later, on its FIRST pulse, off camera from this call entirely.

    ``pulse_guide`` also raises here, but ONLY the fresh-calibration fallback
    ever calls it (see this file's module docstring for why the reuse path
    itself does not) -- so the pulse failure is what the fallback walk hits,
    not a standalone probe.

    Named mutant (restores the defect): delete the ``mount_live = False``
    line in the liveness-proof ``except`` block in
    ``NativeGuider.start_guiding`` (server/astrodeck/guide/native.py), so a
    failed read is logged but not acted on. Run 2026-09-30 against a
    byte-identical worktree copy: this test went red with

        Failed: DID NOT RAISE <class 'OSError'>

    (the mutant's ``start_guiding()`` returned normally, exactly like
    today's bug) and the follow-on log/file assertions were never reached
    because the ``pytest.raises`` block raised first.
    """
    logs = _Logs(monkeypatch)
    profile = _profile_id("dead")
    rig = build_sim_rig()
    real_pier = (await rig["telescope"].pier_side()).value
    _plant_cal(_isolated_config_dir, profile, real_pier)
    path = _cal_path(_isolated_config_dir, profile)
    before = path.read_bytes()

    pulse_calls: list[tuple[str, int]] = []

    async def _dead_get_position():
        raise OSError("WriteFile failed (PermissionError(13, 'the device "
                      "does not recognize the command.', None, 22))")

    async def _dead_pulse_guide(direction, ms):
        pulse_calls.append((direction, ms))
        raise OSError("WriteFile failed (PermissionError(13, 'the device "
                      "does not recognize the command.', None, 22))")

    g, _cam, tel = await _connected_guider(profile)
    tel.get_position = _dead_get_position
    tel.pulse_guide = _dead_pulse_guide

    with pytest.raises(OSError):
        await asyncio.wait_for(g.start_guiding(), timeout=120.0)

    assert not logs.has("native guider calibrated and guiding"), (
        "a dead mount must never be announced as guiding")
    assert logs.has("the mount did not answer"), (
        f"the liveness gate must say why it is falling back; log was "
        f"{logs.lines}")
    assert pulse_calls, (
        "the fresh-calibration fallback must have reached a real "
        "pulse_guide call -- otherwise nothing proved the mount could not "
        "actually be pulsed either")
    assert g.stats().guiding is False
    assert await g.is_active() is False

    # #135's second harm: each false claim rewrote the persisted file. With
    # the gate in place the claim never happens, so the file this session
    # found on disk must be exactly what it found.
    assert path.read_bytes() == before, (
        "a reuse that never proved the mount live must not persist anything")

    await g.disconnect()


# ----------------------------------------------------------------- the control


@pytest.mark.asyncio
async def test_one_unreadable_position_read_does_not_block_reuse(
        _isolated_config_dir, monkeypatch):
    """Control (named mutant G2-equivalent protection): the dossier's
    existing rule -- an unreadable mount read is not, by itself, a reason to
    pay for a calibration walk -- must survive WP-15's new gate. Only the
    cos(dec) probe `start_guiding` makes BEFORE it even decides whether to
    reuse fails here; the liveness proof's own read a few lines later
    succeeds, exactly as it would on a rig with one glitchy read rather than
    a dead link. Reuse must still happen."""
    logs = _Logs(monkeypatch)
    profile = _profile_id("oneglitch")
    rig = build_sim_rig()
    real_pier = (await rig["telescope"].pier_side()).value
    _plant_cal(_isolated_config_dir, profile, real_pier)

    g, _cam, tel = await _connected_guider(profile)
    real_get_position = tel.get_position
    reads = {"n": 0}

    async def _glitchy_get_position():
        reads["n"] += 1
        if reads["n"] == 1:
            raise OSError("one glitchy read")
        return await real_get_position()

    tel.get_position = _glitchy_get_position
    calibrate_calls = _spy_calibrate(g)

    await asyncio.wait_for(g.start_guiding(), timeout=120.0)

    assert calibrate_calls == [], (
        "one glitchy position read before the reuse decision must not force "
        "a calibration walk")
    assert logs.has("reusing persisted calibration")
    assert await g.is_active()
    assert reads["n"] >= 2, (
        "the liveness proof must have made its OWN read after the glitchy "
        "one, not reused a cached failure")

    await g.stop_guiding()
    await g.disconnect()


# ------------------------------------------------- the architecture this fix must not cost


@pytest.mark.asyncio
async def test_successful_reuse_still_makes_no_pulse_guide_calls(
        _isolated_config_dir, monkeypatch):
    """Pins, from this file's side, the constraint the module docstring
    explains: ``_maybe_recover_guiding``'s fast-restart contract (dossier
    §8.4/§9; the comment above ``persisted = self._load_persisted_
    calibration()`` in ``start_guiding``) needs a HEALTHY reuse to stay
    device-I/O-free, so WP-15's mount-liveness gate must be satisfied by a
    READ alone. ``test_native_guider_recovery.py``'s own
    ``test_persisted_calibration_reused_across_guider_instances`` already
    asserts this for a cross-instance reuse; this is the same invariant,
    owned here so a future edit to ``native.py`` cannot silently add a
    pulse to the happy path without a WP-15 test noticing too."""
    profile = _profile_id("healthy")
    rig = build_sim_rig()
    real_pier = (await rig["telescope"].pier_side()).value
    _plant_cal(_isolated_config_dir, profile, real_pier)

    g, _cam, tel = await _connected_guider(profile)
    pulse_calls: list[tuple[str, int]] = []
    real_pulse_guide = tel.pulse_guide

    async def _spy_pulse_guide(direction, ms):
        pulse_calls.append((direction, ms))
        return await real_pulse_guide(direction, ms)

    tel.pulse_guide = _spy_pulse_guide

    await asyncio.wait_for(g.start_guiding(), timeout=120.0)
    tel.pulse_guide = real_pulse_guide  # before the loop task gets a turn

    assert await g.is_active()
    assert pulse_calls == [], (
        f"a healthy reuse must stay device-I/O-free for PulseGuide; got "
        f"{pulse_calls}")

    await g.stop_guiding()
    await g.disconnect()


# -------------------------------- W2 integration: the first REAL pulse, not
# -------------------------------- just the mount read, gates the claim

# ORCHESTRATOR RULING (W2 integration, 2026-09-30, on top of WP-15's own
# commit above): the mount-READ gate proves the link answers, not that it
# can be MOVED. A standalone liveness pulse here was rejected a second time
# (it would break the same zero-pulse invariant the module docstring already
# explains), so instead ``start_guiding``'s reuse branch defers BOTH the
# "calibrated and guiding" claim and the persisted-calibration write to the
# guide loop's own first real "pulse"/"pulse_pair" dispatch
# (``NativeGuider._prove_reuse_with_first_pulse``) -- the one the loop was
# going to send anyway, never a synthetic extra. A mount that answers the
# read but cannot be pulsed falls back to a fresh calibration, in place,
# with a warning; one that can be pulsed earns the claim at that moment,
# not before.


@pytest.mark.asyncio
async def test_a_reused_calibration_never_claims_before_its_first_pulse(
        _isolated_config_dir, monkeypatch):
    """MUTANT "claim before the first pulse" (``start_guiding``'s ``if not
    reused:`` guards on the persist/log at the end of the reuse branch both
    replaced with ``if True:``, so both run unconditionally the moment the
    reuse path reaches them, as the code stood before this fix), restored
    byte-identically after (sha256-verified): RED on both this test and
    ``test_a_failed_first_pulse_recalibrates_instead_of_claiming`` below,
    observed (this test):

        AssertionError: the claim must not be logged before the loop has
        even been scheduled, let alone pulsed: ['native guider connected',
        '...calibration image scale...', 'native guider: reusing persisted
        calibration for profile ...', 'native guider: saved calibration for
        profile ...', 'native guider calibrated and guiding']

    This is the control (a mount that answers both the read AND its first
    pulse): the SAME assertion -- the claim is not yet in the log the
    instant ``start_guiding`` returns -- then confirms the claim and the
    persist both land once the loop's first real pulse has actually
    succeeded, with no recalibration warning anywhere in between.
    """
    logs = _Logs(monkeypatch)
    profile = _profile_id("proof-ok")
    rig = build_sim_rig()
    real_pier = (await rig["telescope"].pier_side()).value
    planted = _plant_cal(_isolated_config_dir, profile, real_pier)
    path = _cal_path(_isolated_config_dir, profile)

    g, _cam, tel = await _connected_guider(profile)
    calibrate_calls = _spy_calibrate(g)

    await asyncio.wait_for(g.start_guiding(), timeout=120.0)
    # THE GATE ITSELF: nothing has pulsed yet (the loop task this just
    # scheduled has not had a single turn), so neither claim may exist.
    assert not logs.has("calibrated and guiding"), (
        f"the claim must not be logged before the loop has even been "
        f"scheduled, let alone pulsed: {logs.lines}")
    assert planted == json.loads(path.read_text(encoding="utf-8")), (
        "the file must not move before the first pulse proves the reuse")

    claimed = await _wait_until(
        lambda: logs.has("calibrated and guiding"), timeout=15.0)
    assert claimed, (
        f"a mount that pulses fine never earned the claim: {logs.lines}")
    assert not logs.has("recalibrating instead"), (
        f"a healthy first pulse must not trigger a fallback: {logs.lines}")
    assert calibrate_calls == [], (
        "a healthy first pulse must not walk a fresh calibration"
    )
    assert await g.is_active()

    await g.stop_guiding()
    await g.disconnect()


@pytest.mark.asyncio
async def test_a_failed_first_pulse_recalibrates_instead_of_claiming(
        _isolated_config_dir, monkeypatch):
    """The reused calibration's mount READ succeeds (the WP-15 gate above
    passes, so the engine reaches ``begin_guiding()``), but the very first
    PULSE the guide loop issues on it fails -- a link that degrades between
    the read and the first write, which the read-only gate above cannot
    see. The claim must never be made for this attempt; the loop instead
    recalibrates fresh, in place, and claims only once THAT succeeds.

    RED under the same mutant as the control above (the persist/log made
    unconditional), observed:

        AssertionError: the claim must not be logged before the loop has
        even been scheduled, let alone pulsed: ['native guider connected',
        '...calibration image scale...', 'native guider: reusing persisted
        calibration for profile ...', 'native guider: saved calibration for
        profile ...', 'native guider calibrated and guiding']

    (the first assertion catches it identically, before this test's extra
    fallback checks ever run -- the mutant never even gets a chance to fail
    the first pulse, since it claims before anything has been dispatched at
    all).
    """
    logs = _Logs(monkeypatch)
    profile = _profile_id("proof-fails")
    rig = build_sim_rig()
    real_pier = (await rig["telescope"].pier_side()).value
    planted = _plant_cal(_isolated_config_dir, profile, real_pier)
    path = _cal_path(_isolated_config_dir, profile)

    g, _cam, tel = await _connected_guider(profile)
    calibrate_calls = _spy_calibrate(g)
    real_pulse_guide = tel.pulse_guide
    pulse_calls: list[tuple[str, int]] = []
    first_pulse_failed = {"v": False}

    async def _flaky_pulse_guide(direction, ms):
        pulse_calls.append((direction, ms))
        if not first_pulse_failed["v"]:
            first_pulse_failed["v"] = True
            raise OSError("the first guide pulse found a dead write side "
                          "of an otherwise readable link")
        return await real_pulse_guide(direction, ms)

    tel.pulse_guide = _flaky_pulse_guide

    await asyncio.wait_for(g.start_guiding(), timeout=120.0)
    assert not logs.has("calibrated and guiding"), (
        f"the claim must not be logged before the loop has even been "
        f"scheduled, let alone pulsed: {logs.lines}")

    warned = await _wait_until(
        lambda: logs.has("recalibrating instead"), timeout=15.0)
    assert warned, (
        f"a failed first pulse must say it is falling back: {logs.lines}")
    claimed = await _wait_until(
        lambda: logs.has("calibrated and guiding"), timeout=15.0)
    assert claimed, (
        f"the fallback calibration must still reach guiding once it "
        f"succeeds: {logs.lines}")
    # ORDER: the warning is said BEFORE the claim, never after.
    warn_i = next(i for i, m in enumerate(logs.lines)
                 if "recalibrating instead" in m)
    claim_i = next(i for i, m in enumerate(logs.lines)
                  if "calibrated and guiding" in m)
    assert warn_i < claim_i, (
        f"the claim must follow the fallback, not precede it: {logs.lines}")
    assert calibrate_calls == [1], (
        f"a failed first pulse must walk exactly one fresh calibration: "
        f"{calibrate_calls}")
    assert len(pulse_calls) >= 2, (
        f"premise: the failed first pulse, then the fallback walk's real "
        f"pulses, both reached tel.pulse_guide: {pulse_calls}")
    assert await g.is_active(), (
        "the session must still end up guiding, on the fresh calibration")

    # The persisted file is now the FRESH calibration, not a re-save of the
    # never-proven reused one.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk != planted, (
        "the persisted file must be the fresh calibration, not the "
        "never-proven reused dict re-saved")

    await g.stop_guiding()
    await g.disconnect()
