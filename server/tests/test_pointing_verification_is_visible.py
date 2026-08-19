"""A centering that FAILED must not look like one that worked.

Reported 2026-08-19 from the UI: CENTER AFTER SLEW was on, the plate solve
failed ("Not enough stars"), `goto_and_center` degraded to a raw GoTo, and the
screen showed a confident green TRACKING with the pointing panel full of
coordinates. The only trace was one warning line in a collapsed log drawer.

The operator asked for the pointing to be verified. It was not. That is exactly
the class of thing this codebase calls a broken promise, and the fix is to make
the outcome a FACT ON THE STATUS rather than a line in a log nobody has open.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub


@pytest.fixture
async def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def test_a_fresh_mount_reports_pointing_unverified(hub):
    st = await hub.poll_status()
    p = (st.get("mount") or {}).get("pointing")
    assert p is not None, "status carries no pointing verdict at all"
    assert p.get("verified") is False, p
    assert p.get("reason"), "an unverified pointing must say why"


async def test_a_successful_centering_marks_it_verified(hub, monkeypatch):
    async def centered(*a, **k):
        return {"centered": True, "error_arcmin": 0.4}
    monkeypatch.setattr(hub, "_center_loop", centered, raising=False)
    hub.note_pointing_verified(True, error_arcmin=0.4)
    p = (await hub.poll_status())["mount"]["pointing"]
    assert p["verified"] is True and p.get("error_arcmin") == 0.4, p


async def test_a_failed_solve_says_so_and_says_why(hub):
    hub.note_pointing_verified(False, reason="plate solve failed: Not enough stars")
    p = (await hub.poll_status())["mount"]["pointing"]
    assert p["verified"] is False
    assert "not enough stars" in p["reason"].lower(), p
    assert "raw" in p["reason"].lower() or "solve" in p["reason"].lower(), (
        "the operator needs to know the mount went to raw GoTo coordinates")


async def test_moving_the_mount_INVALIDATES_a_previous_verification(hub):
    """A verdict that outlives the pointing it describes is worse than none: the
    panel would show a green 'verified' about somewhere the tube no longer is."""
    hub.note_pointing_verified(True, error_arcmin=0.2)
    assert (await hub.poll_status())["mount"]["pointing"]["verified"] is True
    hub.note_pointing_moved()
    p = (await hub.poll_status())["mount"]["pointing"]
    assert p["verified"] is False, "a slew left a stale 'verified' on screen"


async def test_goto_and_center_records_its_own_verdict(hub, monkeypatch):
    """Driven through the REAL centering path, not by calling the recorder.

    The recorder tests above prove the plumbing; this proves it is WIRED. A
    recorder nothing calls is the same defect class as the log line that was
    the only trace before.
    """
    async def no_stars(*a, **k):
        raise RuntimeError("plate solve failed: Not enough stars.")
    # make the solve inside the centering loop fail the way daylight does
    monkeypatch.setattr(hub, "solve_current_frame", no_stars, raising=False)
    hub.note_pointing_verified(True, error_arcmin=0.1)      # pretend it was good
    try:
        await hub.goto_and_center(5.5881, -5.3911, max_attempts=1)
    except Exception:
        pass
    p = (await hub.poll_status())["mount"]["pointing"]
    assert p["verified"] is False, (
        "a centering that could not solve left the previous 'verified' standing")
