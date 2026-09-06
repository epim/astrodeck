"""GN-01: a pier-side change RECALIBRATES the native guider; it never reuses a
flipped calibration.

The defect, measured on the AM5N on the night of 2026-09-05/06
(``docs/superpowers/specs/2026-09-06-guider-night-defects-triage.md``): every
session that started from a FLIPPED calibration ran the field away within
minutes (00:43 and 03:30), and every session that started from a FRESH one
guided (01:05, 02:47, 03:42). Two separate paths produced a flipped
calibration:

1. ``start_guiding`` reused a persisted calibration and ``_maybe_flip_for_pier``
   mirrored it because the mount reported the other side.
2. ``flip_calibration`` (hub.meridian_flip / the engine's limit-recovery path)
   flipped the cal AND PERSISTED it; the restart then reloaded that flipped cal
   and — the mount still reporting the pre-flip side — flipped it a second time.

...and a third made the operator's escape hatch useless: ``clear_calibration``
(``DELETE /api/guide/calibration``) only deleted files, so the next
``_persist_calibration`` (a stop, a flip) wrote the in-memory calibration
straight back.

The fix is ``guide.recalibrate_after_pier_change`` (default True): refuse the
reuse before it happens, discard rather than flip on a meridian flip, and latch
the discard so nothing re-persists it. The old flip path stays reachable behind
the setting for a mount that is known to need it — ``test_legacy_flip_path_
behind_the_setting`` is what keeps it honest.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from astrodeck.devices.base import PierSide
from astrodeck.devices.sim import build_sim_rig
from astrodeck.events import bus
from astrodeck.providers import NATIVE_AVAILABLE

pytestmark = [
    pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent"),
]


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path, monkeypatch):
    """Point ``CONFIG_DIR`` at ``tmp_path`` so no test here reads or writes the
    real ``server/config/guider/`` (both ``_persist_calibration`` and
    ``_load_persisted_calibration`` import ``CONFIG_DIR`` at CALL time, so
    patching the module attribute takes effect immediately)."""
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return tmp_path


def _profile_id(tag: str) -> str:
    """A fresh profile id per test invocation, on top of the per-test
    ``tmp_path``: each scenario's persistence file is provably its own."""
    return f"test-pier-{tag}-{uuid.uuid4().hex[:8]}"


def _cal_path(root, profile: str):
    return root / "guider" / f"{profile}.json"


def _cal_dict(pier: str, scale: float = 2.0) -> dict:
    """A persisted-calibration dict that passes every ``_cal_reusable`` arm for
    a guider built with ``image_scale_arcsec=2.0``, ``binning=1`` — the shape
    ``_persist_calibration`` writes (the engine's ``dump_calibration`` keys plus
    the ``image_scale_arcsec`` sidecar).

    ``x_angle`` is deliberately NOT 0 and ``x_rate``/``y_rate`` are deliberately
    not the sim's: this dict has to be distinguishable, field by field, from a
    calibration the sim actually measured.
    """
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


class _FakePier:
    """The mount's pier-side report, under the test's control.

    The sim mount derives its side from where it is pointing, which is exactly
    right for the sim and useless here: the scenario under test is "the mount
    says a DIFFERENT side than the calibration was stamped with", and on the
    real rig that is a report, not a deduction (the 03:30 double flip happened
    because the AM5 still reported the PRE-flip side after the flip). So the
    report is faked and the test drives it."""

    def __init__(self, side: str = "east") -> None:
        self.side = side
        self.reads = 0

    async def __call__(self) -> PierSide:
        self.reads += 1
        return PierSide(self.side)


class _Logs:
    """Every ``bus.log`` line the guider emitted, in order.

    Spies on the bus singleton rather than subscribing a queue: the guide loop
    publishes a ``guide`` event per frame and the bus drops the OLDEST event
    when a subscriber's 500-slot queue fills, so a subscriber could silently
    lose the one log line a test is asserting on."""

    def __init__(self, monkeypatch) -> None:
        self.lines: list[str] = []
        real = bus.log

        def _spy(level, message, source="hub"):
            self.lines.append(str(message))
            return real(level, message, source)

        monkeypatch.setattr(bus, "log", _spy)

    def has(self, needle: str) -> bool:
        return any(needle in line for line in self.lines)

    def matching(self, needle: str) -> list[str]:
        return [line for line in self.lines if needle in line]


async def _guider(tag: str, *, pier: str = "east", cfg: dict | None = None,
                  profile: str | None = None):
    """A connected ``NativeGuider`` over a sim rig whose pier report is faked."""
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    fake = _FakePier(pier)
    tel.pier_side = fake
    config = {"image_scale_arcsec": 2.0, "exposure_s": 0.2}
    config.update(cfg or {})
    g = NativeGuider(cam, tel, config=config,
                     profile_id=profile or _profile_id(tag))
    await g.connect()
    return g, tel, fake


def _spy_calibrate(g) -> list[int]:
    """Count ``_calibrate`` calls while still running the real thing — a stub
    would prove the branch was taken but not that a real calibration follows
    it, and "exactly one calibration per restart" is half the contract."""
    calls: list[int] = []
    real = g._calibrate

    async def _counted():
        calls.append(1)
        return await real()

    g._calibrate = _counted
    return calls


# --------------------------------------------------------------- the reuse gate


@pytest.mark.asyncio
async def test_persisted_calibration_from_the_other_pier_is_not_reused(
        _isolated_config_dir, monkeypatch):
    """THE 00:43 defect: a persisted calibration stamped for the other pier is
    refused outright, not loaded and mirrored."""
    logs = _Logs(monkeypatch)
    profile = _profile_id("otherside")
    planted = _plant_cal(_isolated_config_dir, profile, "east")
    g, _tel, _fake = await _guider("otherside", pier="west", profile=profile)
    calls = _spy_calibrate(g)

    await asyncio.wait_for(g.start_guiding(), timeout=120.0)

    assert len(calls) == 1, (
        f"expected exactly one fresh calibration on the new pier, got "
        f"{len(calls)}")
    assert not logs.has("flipped calibration"), (
        f"the calibration must be re-measured, never mirrored: "
        f"{logs.matching('flipped calibration')}")
    assert logs.has("mount pier side changed (east->west)"), (
        f"the refusal must say why it is recalibrating; log was {logs.lines}")
    assert await g.is_active()
    assert g.stats().guiding

    saved = json.loads(_cal_path(_isolated_config_dir, profile)
                       .read_text(encoding="utf-8"))
    # The fresh calibration's pier stamp comes from ``_apply_scope_pointing``,
    # which reads the MOUNT (our fake), so it is the side the mount now
    # reports — "west" — not the planted "east".
    assert saved["pier_side"] == "west"
    # ...and it is genuinely a NEW measurement, not the planted geometry with a
    # new label on it.
    assert saved["x_rate"] != planted["x_rate"]
    assert saved["x_angle"] != planted["x_angle"]

    await g.stop_guiding()
    await g.disconnect()


@pytest.mark.asyncio
async def test_same_pier_persisted_calibration_is_still_reused(
        _isolated_config_dir, monkeypatch):
    """The control: reuse is what makes ``_maybe_recover_guiding``'s restart
    fast, and GN-01 must not cost it. Same side -> straight to guiding."""
    logs = _Logs(monkeypatch)
    profile = _profile_id("sameside")
    _plant_cal(_isolated_config_dir, profile, "east")
    g, _tel, _fake = await _guider("sameside", pier="east", profile=profile)
    calls = _spy_calibrate(g)

    await asyncio.wait_for(g.start_guiding(), timeout=120.0)

    assert calls == [], "a same-pier persisted calibration must be reused"
    assert logs.has("reusing persisted calibration")
    assert not logs.has("flipped calibration")
    assert await g.is_active()

    await g.stop_guiding()
    await g.disconnect()


# ------------------------------------------------------------ the flip discard


@pytest.mark.asyncio
@pytest.mark.parametrize("side_after_flip", ["east", "west"])
async def test_flip_discards_instead_of_flipping_and_restart_calibrates_once(
        _isolated_config_dir, monkeypatch, side_after_flip):
    """THE 03:30 defect: the meridian flip discards the calibration instead of
    flipping and persisting it, so the restart calibrates fresh EXACTLY ONCE —
    whatever the mount then reports.

    ``side_after_flip == "east"`` is last night's actual case: the AM5 still
    reported the pre-flip side after the flip, so a persisted flipped cal was
    flipped a second time on reload. ``"west"`` is the case the mount reports
    correctly. Neither may mirror a calibration, and neither may calibrate
    twice."""
    logs = _Logs(monkeypatch)
    profile = _profile_id(f"flip{side_after_flip}")
    g, _tel, fake = await _guider(f"flip{side_after_flip}", pier="east",
                                  profile=profile)
    calls = _spy_calibrate(g)
    path = _cal_path(_isolated_config_dir, profile)

    # A real, fresh calibration through the sim, stamped "east".
    await asyncio.wait_for(g.start_guiding(), timeout=120.0)
    assert len(calls) == 1
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["pier_side"] == "east"
    await g.stop_guiding()

    # hub.meridian_flip's step, between stopping and restarting guiding.
    ok = await g.flip_calibration()
    assert ok is True, (
        "flip_calibration must report that it handled the flip (discarded "
        "counts: the flip completed and the restart will recalibrate)")
    assert logs.has("discarding"), (
        f"the discard must be narrated; log was {logs.lines[-8:]}")
    assert not path.exists(), (
        "a discarded calibration must not survive on disk — reloading it is "
        "exactly how the 03:30 double flip happened")

    # The restart, with the mount reporting whichever side it feels like.
    fake.side = side_after_flip
    await asyncio.wait_for(g.start_guiding(), timeout=120.0)

    assert len(calls) == 2, (
        f"expected exactly one calibration on the restart, got "
        f"{len(calls) - 1}")
    assert not logs.has("flipped calibration"), (
        f"nothing may mirror a calibration on this path: "
        f"{logs.matching('flipped calibration')}")
    assert await g.is_active()
    assert json.loads(path.read_text(encoding="utf-8"))["pier_side"] == \
        side_after_flip

    await g.stop_guiding()
    await g.disconnect()


# --------------------------------------------------- the old path, behind the setting


@pytest.mark.asyncio
async def test_legacy_flip_path_behind_the_setting(
        _isolated_config_dir, monkeypatch):
    """``recalibrate_after_pier_change=False`` restores the pre-GN-01
    behaviour, whole: the reuse mirrors, and the meridian flip mirrors and
    persists. A mount whose flip really is a clean geometric mirror can still
    have it; the point is that it is now a CHOICE."""
    logs = _Logs(monkeypatch)
    profile = _profile_id("legacy")
    _plant_cal(_isolated_config_dir, profile, "east")
    g, _tel, _fake = await _guider(
        "legacy", pier="west", profile=profile,
        cfg={"recalibrate_after_pier_change": False})
    calls = _spy_calibrate(g)
    path = _cal_path(_isolated_config_dir, profile)

    await asyncio.wait_for(g.start_guiding(), timeout=120.0)

    assert calls == [], (
        "with the setting off, the persisted calibration is reused and "
        "mirrored — no calibration walk")
    assert logs.has("flipped calibration"), (
        f"the guiding-start auto-flip must still fire; log was {logs.lines}")
    # start_guiding persists what it will guide with, so the mirrored cal is
    # already on disk carrying the mount's side.
    assert json.loads(path.read_text(encoding="utf-8"))["pier_side"] == "west"

    await g.stop_guiding()
    ok = await g.flip_calibration()
    assert ok is True
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["pier_side"] == "east", (
        "with the setting off, flip_calibration mirrors the calibration and "
        "PERSISTS it (the pre-GN-01 contract)")

    await g.disconnect()


# ------------------------------------------------- the operator's escape hatch


@pytest.mark.asyncio
async def test_clear_survives_stop_and_flip(_isolated_config_dir, monkeypatch):
    """THE 02:14 defect: a clear made while guiding was silently undone.

    ``DELETE /api/guide/calibration`` deleted the files and left the in-memory
    calibration alone, so the next ``_persist_calibration`` — a stop, a flip —
    wrote it straight back and the next start reused it. The clear now latches:
    nothing re-persists until a fresh calibration has actually been measured."""
    _Logs(monkeypatch)
    profile = _profile_id("clear")
    g, _tel, _fake = await _guider("clear", pier="east", profile=profile)
    calls = _spy_calibrate(g)
    path = _cal_path(_isolated_config_dir, profile)

    await asyncio.wait_for(g.start_guiding(), timeout=120.0)
    assert len(calls) == 1
    assert path.exists()

    # The operator clears it MID-SESSION (the route's contract: it must not
    # stop an in-flight loop).
    assert g.clear_calibration() is True
    assert not path.exists()
    assert await g.is_active(), "clearing must not stop the guide loop"

    await g.stop_guiding()
    assert not path.exists(), "the stop re-persisted a cleared calibration"

    await g.flip_calibration()
    assert not path.exists(), "the flip re-persisted a cleared calibration"

    await asyncio.wait_for(g.start_guiding(), timeout=120.0)
    assert len(calls) == 2, (
        f"the restart after a clear must calibrate fresh, got "
        f"{len(calls) - 1} extra calibration(s)")
    assert await g.is_active()

    await g.stop_guiding()
    await g.disconnect()


# --------------------------------------------------------------- the setting itself


def test_guide_config_declares_the_pier_change_setting(tmp_path, monkeypatch):
    """The setting exists, defaults to recalibrating, round-trips — and REACHES
    the guider through the factory production actually uses
    (``build_native_guider`` -> ``guide_algo_config``). A hand-built guider
    proves nothing about that wire: ``flip_requires_dec_flip`` was read from
    the config dict for months with nothing on the config side declaring it, so
    no rig could ever set it."""
    import astrodeck.config as config
    from astrodeck.config import ConfigStore, GuideConfig
    from astrodeck.guide.native import build_native_guider

    assert GuideConfig().recalibrate_after_pier_change is True
    assert GuideConfig().flip_requires_dec_flip is False
    dumped = GuideConfig().model_dump()
    assert dumped["recalibrate_after_pier_change"] is True
    assert GuideConfig.model_validate(dumped).recalibrate_after_pier_change is True
    assert GuideConfig.model_validate(
        {**dumped, "recalibrate_after_pier_change": False}
    ).recalibrate_after_pier_change is False

    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config, "config_store", store)

    rig = build_sim_rig()
    default = build_native_guider(rig["guide_camera"], rig["telescope"])
    assert default is not None
    assert default._recalibrate_after_pier_change is True

    store.set_guide(GuideConfig(recalibrate_after_pier_change=False,
                                flip_requires_dec_flip=True))
    off = build_native_guider(rig["guide_camera"], rig["telescope"])
    assert off is not None
    assert off._recalibrate_after_pier_change is False
    assert off._flip_requires_dec_flip is True
