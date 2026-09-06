"""GN-07: the FITS header must carry the BEST KNOWN pointing, not necessarily
the mount's own report -- see
docs/superpowers/specs/2026-09-06-guider-night-defects-triage.md, GN-07/GN-10.

Evidence the row exists for: consecutive subs whose star fields matched
star-for-star carried OBJCTDEC that walked from +30 47 to +31 51 across a
single night, because the header was built from whatever the mount happened
to report at capture time -- and the AM5's report has been measured walking
up to 50 arcmin across a run while the field itself held to a dither.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from astropy.io import fits

import astrodeck.hub as hub_module
from astrodeck.catalog import coords
from astrodeck.hub import Hub


@pytest.fixture
async def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def test_capture_prefers_a_current_solve_over_the_mounts_drifted_report(hub):
    tel = hub.devices["telescope"]
    # The TRUE field -- what a plate solve would (and, in this fixture, does)
    # measure. The mount's own report then drifts 50' in Dec while the field
    # itself holds still, exactly the defect measured on the sky (GN-10).
    solved_ra, solved_dec = tel.rig.ra_hours, tel.rig.dec_deg
    tel.rig.dec_deg = solved_dec + 50.0 / 60.0

    hub._pointing_verified = True
    hub._solved_pointing = (solved_ra, solved_dec, time.time())

    await hub.capture(0.2, 100, 30, 1, save=True, target="")
    saved = Path(hub.last_frame.saved_path)
    with fits.open(saved) as hdul:
        h = hdul[0].header

    # OBJCTRA/OBJCTDEC (and the numeric RA/DEC cards) carry the SOLVED
    # pointing, matched to the arcsecond after formatting -- not the mount's
    # drifted report.
    assert h["OBJCTRA"] == coords.format_ra_fits(solved_ra)
    assert h["OBJCTDEC"] == coords.format_dec_fits(solved_dec)
    assert h["RA"] == pytest.approx(solved_ra * 15.0, abs=1e-3)
    assert h["DEC"] == pytest.approx(solved_dec, abs=1e-4)
    # The mount's raw (drifted) report still rides along, honestly labelled.
    assert h["MOUNTRA"] == coords.format_ra_fits(tel.rig.ra_hours)
    assert h["MOUNTDEC"] == coords.format_dec_fits(tel.rig.dec_deg)
    assert h["PNTGSRC"] == "solved"
    # Prove the fixture actually exercises the defect: OBJCTDEC and MOUNTDEC
    # really do disagree by ~50'.
    assert h["OBJCTDEC"] != h["MOUNTDEC"]

    # A real slew/park/sync clears the solved record (note_pointing_moved());
    # the very next capture must fall back to the mount's own report, and say
    # so honestly via PNTGSRC rather than keep serving the stale solve.
    hub.note_pointing_moved()
    assert hub._solved_pointing is None

    await hub.capture(0.2, 100, 30, 1, save=True, target="")
    saved2 = Path(hub.last_frame.saved_path)
    with fits.open(saved2) as hdul:
        h2 = hdul[0].header
    assert h2["PNTGSRC"] == "mount"
    assert h2["OBJCTRA"] == h2["MOUNTRA"]
    assert h2["OBJCTDEC"] == h2["MOUNTDEC"]


async def test_a_dither_between_captures_does_not_invalidate_the_solved_centre(hub):
    """The regression this row's own bug produced: `note_pointing_moved()` used
    to fire unconditionally inside `capture()`, so a solved centre never
    survived even the FIRST frame it should have described, let alone a
    dither-only sequence of several. Two captures back to back, no motion
    event in between, must both read "solved"."""
    tel = hub.devices["telescope"]
    solved_ra, solved_dec = tel.rig.ra_hours, tel.rig.dec_deg
    hub._pointing_verified = True
    hub._solved_pointing = (solved_ra, solved_dec, time.time())

    for _ in range(2):
        await hub.capture(0.2, 100, 30, 1, save=True, target="")
        with fits.open(hub.last_frame.saved_path) as hdul:
            assert hdul[0].header["PNTGSRC"] == "solved"
        # a small dither: the mount's own report moves a few arcsec, exactly
        # the case the spec says must NOT invalidate a solved centre
        tel.rig.ra_hours += 3.0 / 3600.0 / 15.0

    assert hub._pointing_verified is True
    assert hub._solved_pointing is not None


async def test_no_mount_position_means_no_pointing_cards_at_all(hub, monkeypatch):
    """omit-not-placeholder: a telescope that cannot report a position must not
    fabricate any pointing card, solved or otherwise."""
    tel = hub.devices["telescope"]

    async def no_position():
        return None, None
    monkeypatch.setattr(tel, "get_position", no_position)

    await hub.capture(0.2, 100, 30, 1, save=True, target="")
    with fits.open(hub.last_frame.saved_path) as hdul:
        h = hdul[0].header
    for absent in ("OBJCTRA", "OBJCTDEC", "MOUNTRA", "MOUNTDEC", "PNTGSRC"):
        assert absent not in h, absent
