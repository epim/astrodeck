# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The pointing-disagreement baseline is the mount's report, in the frame the
capture snapshot reads it in (#851, Oct-08 integration finding 8).

``_current_field_solve`` counts a disagreement when the mount's report moves
more than the stale threshold from the report recorded when the field solve
was adopted, and the engine answers each count with an in-place solve and
sync. After a centring sync that baseline used to be the SOLVED J2000
position, while the next capture recorded the mount's raw report. A NINA or
ASIAIR mount reports the JNOW of the J2000 it was synced to, and
``from_mount_frame`` does not convert those mounts, so on a field narrower
than about 0.7 deg every centring was followed by a counted disagreement and
an extra blind solve.

The hub is the real ``Hub`` on the simulator. The sim telescope's ``sync`` is
replaced by one that does what a NINA mount does: it stores, and reports, the
JNOW of what it was sent. The sim mount's backend is "", which
``from_mount_frame`` leaves alone exactly as it leaves "nina" alone. The
coordinates are fictional (M42's catalogue position), away from the pole, and
no line prints one.

Every mutant named below was applied to a byte copy of
``astrodeck/hub.py``, run under the suite's normal command, and the file
restored from the copy with its sha256 checked.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.config as config_mod
import astrodeck.hub as hub_module
import astrodeck.providers as providers_module
from astrodeck.catalog.coords import angular_sep_deg
from astrodeck.config import ConfigStore, Optics
from astrodeck.hub import Hub, precess_j2000_to_jnow
from astrodeck.solve.base import SolveResult, WcsSolution

#: M42, J2000 (fictional for the rig; nowhere near the pole).
SOLVED_RA_H = 83.822 / 15.0
SOLVED_DEC = -5.391


def _wcs(w: int = 1000, h: int = 800) -> WcsSolution:
    return WcsSolution(crval1=SOLVED_RA_H * 15.0, crval2=SOLVED_DEC,
                       crpix1=(w - 1) / 2.0 + 1.0, crpix2=(h - 1) / 2.0 + 1.0,
                       cd11=-2.78e-4, cd12=0.0, cd21=0.0, cd22=2.78e-4)


class _FixedSolver:
    """A solve that always lands on the same J2000 field, with a WCS, so
    ``solve_and_sync`` adopts it as the field solve."""
    name = "Fixed"

    async def solve(self, fits_path, *, ra_hint=None, dec_hint=None,
                    fov_deg_hint=None, downsample=0):
        return SolveResult(True, ra_hours=SOLVED_RA_H, dec_deg=SOLVED_DEC,
                           pixel_scale_arcsec=0.28, message="fixed",
                           wcs=_wcs())


@pytest.fixture
async def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(config_mod, "config_store", store)
    # A narrow field: 22.56 mm / 2800 mm = 0.46 deg long side, so the stale
    # threshold is max(0.25, 0.5 x 0.46) = 0.25 deg, under the J2000-JNOW
    # offset the test asserts below.
    store.set_optics(Optics(
        focal_length_mm=2800.0, pixel_size_um=3.76,
        sensor_width_px=6000, sensor_height_px=4000,
        auto_from_camera=False), expected_version=None)
    monkeypatch.setattr(providers_module, "pick_solver",
                        lambda hub: _FixedSolver())
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _reports_jnow_after_sync(monkeypatch, tel) -> None:
    """The NINA / ASIAIR mount: a sync to a J2000 pair leaves it reporting
    that pair's JNOW."""
    async def sync(ra_hours: float, dec_deg: float) -> None:
        ra, dec = precess_j2000_to_jnow(ra_hours, dec_deg)
        tel.rig.ra_hours = ra
        tel.rig.dec_deg = dec
        tel.rig.pointing_error_deg = 0.003

    monkeypatch.setattr(tel, "sync", sync)


async def test_a_centring_on_a_jnow_reporting_mount_counts_no_disagreement(
        hub, monkeypatch):
    """A centring sync, then a light frame: the mount has not moved, so
    nothing is counted and the field solve survives. Then a real move of
    six degrees is still counted, once.

    MUTANT "the solved baseline" (the call in ``solve_and_sync`` put back as
    ``self._note_pointing(result.ra_hours, result.dec_deg)``): RED -
        AssertionError: a centring counted a disagreement: 1
    MUTANT "no baseline after the sync" (the ``_note_pointing_after_sync``
    call in ``solve_and_sync`` deleted, so the field solve carries no mount
    report): RED on the control -
        AssertionError: a six-degree move went uncounted: 0
    """
    h = hub
    tel = h.devices["telescope"]
    _reports_jnow_after_sync(monkeypatch, tel)
    jra, jdec = precess_j2000_to_jnow(SOLVED_RA_H, SOLVED_DEC)
    offset = angular_sep_deg(SOLVED_RA_H, SOLVED_DEC, jra, jdec)
    assert offset > h._field_stale_threshold_deg(), (
        "the fixture does not exercise the frame mismatch")

    await h.solve_and_sync(exposure_s=0.05)
    assert h.field_solve is not None, "the centring adopted no field solve"
    await h.capture(0.2, 100, 30, 1, save=True, target="")
    h.field_identification()
    assert h.pointing_disagreements == 0, (
        f"a centring counted a disagreement: {h.pointing_disagreements}")
    assert h.field_solve is not None, "the field solve was dropped"

    # CONTROL: the detector still sees a real move.
    tel.rig.dec_deg = tel.rig.dec_deg + 6.0
    await h.capture(0.2, 100, 30, 1, save=True, target="")
    h.field_identification()
    assert h.pointing_disagreements == 1, (
        f"a six-degree move went uncounted: {h.pointing_disagreements}")


async def test_a_failed_read_after_the_sync_falls_back_to_the_solve(
        hub, monkeypatch):
    """The read after the sync fails: the baseline is the solved position,
    not the report from before the sync, which can be off by the whole sync.

    MUTANT "no fallback" (``if not usable: ra, dec = solved_ra, solved_dec``
    in ``_note_pointing_after_sync`` replaced by ``if not usable: return``):
    RED -
        AssertionError: the baseline is 10.00 deg from the solve
    """
    h = hub
    tel = h.devices["telescope"]
    real_sync = tel.sync
    real_position = tel.get_position
    state = {"synced": False}

    async def sync(ra_hours: float, dec_deg: float) -> None:
        await real_sync(ra_hours, dec_deg)
        state["synced"] = True

    async def get_position():
        if state["synced"]:
            raise RuntimeError("link dropped")
        return await real_position()

    monkeypatch.setattr(tel, "sync", sync)
    monkeypatch.setattr(tel, "get_position", get_position)
    # The report from before the sync, ten degrees off.
    h._note_pointing(SOLVED_RA_H, SOLVED_DEC + 10.0)

    await h.solve_and_sync(exposure_s=0.05)
    fs = h.field_solve
    assert fs is not None and fs.mount_ra is not None
    sep = angular_sep_deg(fs.mount_ra, fs.mount_dec, SOLVED_RA_H, SOLVED_DEC)
    assert sep < 1e-6, f"the baseline is {sep:.2f} deg from the solve"


async def test_a_centring_on_a_jnow_reporting_mount_raises_no_caption(
        hub, monkeypatch):
    """The all-NINA rig: the baseline is now the mount's JNOW report, and
    the field block compares that report with the J2000 plate centre. It
    must not say the mount disagrees with the plate after a centring, on
    the solve preview or on the light that follows.

    MUTANT "no alternate in the note" (the ``for a_ra, a_dec in
    fs.center_alternates`` loop in ``_field_block`` deleted): RED -
        AssertionError: a false caption after the centring: 0.286
    """
    h = hub
    tel = h.devices["telescope"]
    _reports_jnow_after_sync(monkeypatch, tel)

    await h.solve_and_sync(exposure_s=0.05)
    block = h._field_block(None)
    assert block is not None and block.get("source") == "solve"
    assert "pointing_disagrees_deg" not in block, (
        f"a false caption after the centring: "
        f"{block.get('pointing_disagrees_deg')}")
    await h.capture(0.2, 100, 30, 1, save=True, target="")
    block = h._field_block(None)
    assert block is not None and block.get("source") == "solve"
    assert "pointing_disagrees_deg" not in block, (
        f"a false caption on the light: "
        f"{block.get('pointing_disagrees_deg')}")


@pytest.mark.parametrize("backend, captioned", [("", False),
                                                ("alpaca", True)])
async def test_the_note_accepts_jnow_only_from_an_unconverted_mount(
        hub, backend, captioned):
    """A report sitting at the plate centre's JNOW is the same place on a
    mount ``from_mount_frame`` leaves raw (sim/NINA/ASIAIR), and a real
    0.33 deg disagreement on an Alpaca mount, whose report the hub has
    already brought to J2000. The solve is adopted with no mount report on
    record, so the staleness check stays out of the way and only the note
    is under test.

    MUTANT "no alternate in the note" (as above): RED on [""] -
        AssertionError: backend '': caption 0.332, expected none
    MUTANT "alternates for every backend" (``or getattr(tel, "backend",
    "") == "alpaca"`` dropped from ``_report_frame_alternates``): RED on
    ["alpaca"] -
        AssertionError: backend 'alpaca': no caption for a 0.33 deg
        disagreement
    """
    h = hub
    tel = h.devices["telescope"]
    tel.backend = backend
    h._last_pointing = None
    await h.note_field_solve(_wcs(), preview_id=None, data_w=1000, data_h=800)
    assert h.field_solve is not None and h.field_solve.mount_ra is None
    h._note_pointing(*precess_j2000_to_jnow(SOLVED_RA_H, SOLVED_DEC))
    block = h._field_block(None)
    assert block is not None and block.get("source") == "solve"
    got = block.get("pointing_disagrees_deg")
    if captioned:
        assert got is not None, (
            f"backend {backend!r}: no caption for a 0.33 deg disagreement")
    else:
        assert got is None, f"backend {backend!r}: caption {got}, expected none"


async def test_a_centring_on_a_jnow_alpaca_mount_counts_no_disagreement(
        hub, monkeypatch):
    """A JNOW Alpaca mount: the hub precesses the sync to JNOW and brings
    every report back to J2000. The baseline read after the sync must go
    through the same conversion as the capture snapshot, or the next light
    counts the precession as a move.

    MUTANT "no from_mount_frame" (``ra, dec = await
    self.from_mount_frame(tel, ra, dec)`` in ``_note_pointing_after_sync``
    replaced by ``pass``): RED -
        AssertionError: a centring counted a disagreement: 1
    """
    h = hub
    tel = h.devices["telescope"]

    async def expects_jnow(_tel, **_bound) -> bool:
        return True

    monkeypatch.setattr(h, "_mount_expects_jnow", expects_jnow)

    async def sync(ra_hours: float, dec_deg: float) -> None:
        # Stores, and reports, exactly what it is sent: the JNOW pair
        # ``to_mount_frame`` made of the solve.
        tel.rig.ra_hours = ra_hours
        tel.rig.dec_deg = dec_deg
        tel.rig.pointing_error_deg = 0.003

    monkeypatch.setattr(tel, "sync", sync)
    await h.solve_and_sync(exposure_s=0.05)
    assert h.field_solve is not None, "the centring adopted no field solve"
    await h.capture(0.2, 100, 30, 1, save=True, target="")
    h.field_identification()
    assert h.pointing_disagreements == 0, (
        f"a centring counted a disagreement: {h.pointing_disagreements}")
    assert h.field_solve is not None, "the field solve was dropped"


async def test_a_hung_read_after_the_sync_does_not_hold_the_centring(
        hub, monkeypatch):
    """The read after the sync does not answer: ``solve_and_sync`` returns
    once ``_POST_SYNC_READ_TIMEOUT_S`` runs out, with the solved position as
    the baseline. Unbounded, the read would hold every centring and
    in-place re-check for the transport timeout (up to 60 s on Alpaca).

    MUTANT "no timeout" (``asyncio.wait_for(tel.get_position(),
    self._POST_SYNC_READ_TIMEOUT_S)`` replaced by ``tel.get_position()``):
    RED - TimeoutError from the 10 s ``wait_for`` around the centring.
    """
    h = hub
    tel = h.devices["telescope"]
    real_sync = tel.sync
    real_position = tel.get_position
    state = {"hang": False}

    async def sync(ra_hours: float, dec_deg: float) -> None:
        await real_sync(ra_hours, dec_deg)
        state["hang"] = True

    async def get_position():
        if state["hang"]:
            await asyncio.sleep(30.0)
        return await real_position()

    monkeypatch.setattr(tel, "sync", sync)
    monkeypatch.setattr(tel, "get_position", get_position)
    monkeypatch.setattr(h, "_POST_SYNC_READ_TIMEOUT_S", 0.2)
    # The report from before the sync, ten degrees off.
    h._note_pointing(SOLVED_RA_H, SOLVED_DEC + 10.0)

    try:
        await asyncio.wait_for(h.solve_and_sync(exposure_s=0.05), 10.0)
    finally:
        state["hang"] = False
    fs = h.field_solve
    assert fs is not None and fs.mount_ra is not None
    sep = angular_sep_deg(fs.mount_ra, fs.mount_dec, SOLVED_RA_H, SOLVED_DEC)
    assert sep < 1e-6, f"the baseline is {sep:.2f} deg from the solve"
