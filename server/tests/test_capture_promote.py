"""D-SES-4: an unsaved frame can be promoted to disk, and its header describes
the rig that TOOK it.

The hazard the whole feature turns on: the operator takes a test exposure, does
not save it, looks at it, decides it was worth keeping - and by then the mount
may have dithered, slewed or drifted, the wheel may have turned and the focuser
may have moved. A header rebuilt at save time would stamp all of that onto
pixels that know nothing about any of it, and it would look perfectly
self-consistent while doing so. GN-07 is the same lie with a smaller offset.

So ``Hub.capture`` freezes a ``CaptureSnapshot`` on the exposure's own timeline
and ``promote_last_frame`` writes from that alone.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).parent))

import time

from astrodeck.catalog import coords
from astrodeck.hub import PromoteRefused

from _simhub import sim_hub  # noqa: F401 (fixture import)


def _fits_files(root: Path) -> list[Path]:
    """Every FITS the capture library holds, thumbnails excluded."""
    return sorted(p for p in Path(root).rglob("*.fits") if p.is_file())


async def test_the_promoted_header_carries_the_pointing_at_exposure_time(
        sim_hub, tmp_path):
    """The row this feature exists for. Capture unsaved, MOVE THE MOUNT, then
    promote: the file must describe where the tube was when the shutter was
    open, not where it is now.

    Sabotage: rebuild ``FrameMeta`` from the live rig inside
    ``promote_last_frame``."""
    hub = sim_hub
    tel = hub.devices["telescope"]
    at_exposure_ra, at_exposure_dec = tel.rig.ra_hours, tel.rig.dec_deg

    await hub.capture(0.2, 100, 30, 1, save=False, target="NGC 7331")
    assert hub.promotable_summary()["available"] is True

    # ... and now the rig moves, a long way, exactly as it would between the
    # test frame and the decision to keep it.
    tel.rig.ra_hours = at_exposure_ra + 1.5
    tel.rig.dec_deg = at_exposure_dec + 12.0

    out = await hub.promote_last_frame()
    assert out["saved"] is True
    assert out["path"] and not Path(out["path"]).is_absolute()

    saved = _fits_files(tmp_path)
    assert len(saved) == 1
    with fits.open(saved[0]) as hdul:
        h = hdul[0].header
    assert h["OBJCTRA"] == coords.format_ra_fits(at_exposure_ra)
    assert h["OBJCTDEC"] == coords.format_dec_fits(at_exposure_dec)
    assert h["RA"] == pytest.approx(at_exposure_ra * 15.0, abs=1e-3)
    assert h["DEC"] == pytest.approx(at_exposure_dec, abs=1e-4)
    # and the mount's own report is the one it made THEN, too
    assert h["MOUNTRA"] == coords.format_ra_fits(at_exposure_ra)
    # prove the fixture really exercised the defect
    assert h["OBJCTRA"] != coords.format_ra_fits(tel.rig.ra_hours)


async def test_a_promoted_frame_and_a_saved_one_carry_the_same_cards(
        sim_hub, tmp_path):
    """Promotion is not a lesser save. Apart from the instant it was taken, a
    promoted frame's header must be indistinguishable from one the same rig
    would have written had the operator ticked SAVE up front - same cards, same
    filter, same optics, same identification.

    Sabotage: resolve the filter name only when saving (the pre-D-SES-4
    condition), so the promoted file loses its FILTER card."""
    hub = sim_hub
    await hub.capture(0.2, 100, 30, 1, save=False, target="M 31")
    await hub.promote_last_frame()
    await hub.capture(0.2, 100, 30, 1, save=True, target="M 31")

    saved = _fits_files(tmp_path)
    assert len(saved) == 2
    with fits.open(saved[0]) as a, fits.open(saved[1]) as b:
        ha, hb = a[0].header, b[0].header
    # DATE-OBS/DATE-LOC are the two cards that MUST differ: they are the only
    # record of which exposure this is.
    varying = {"DATE-OBS", "DATE-LOC"}
    assert set(ha.keys()) == set(hb.keys())
    for key in set(ha.keys()) - varying:
        assert ha[key] == hb[key], key
    assert "FILTER" in ha


async def test_a_solved_pointing_survives_into_a_promoted_frame(sim_hub, tmp_path):
    """A plate solve recorded before the exposure outranks the mount's own
    report (GN-07), and the promote path must inherit that verdict rather than
    re-deciding it against a rig whose solve has since been invalidated.

    Sabotage: re-run ``_resolve_pointing`` inside ``_save_captured_frame``
    instead of using the snapshot's ``best_ra``/``best_dec``."""
    hub = sim_hub
    tel = hub.devices["telescope"]
    solved_ra, solved_dec = tel.rig.ra_hours, tel.rig.dec_deg
    # the mount's own report drifts 50', exactly the GN-10 measurement
    tel.rig.dec_deg = solved_dec + 50.0 / 60.0
    hub._pointing_verified = True
    hub._solved_pointing = (solved_ra, solved_dec, time.time())

    await hub.capture(0.2, 100, 30, 1, save=False, target="")

    # the solve is invalidated between the exposure and the save - a slew, a
    # park, a reconnect. The FRAME's verdict is not.
    hub.note_pointing_moved()
    assert hub._solved_pointing is None

    await hub.promote_last_frame()
    saved = _fits_files(tmp_path)
    assert len(saved) == 1
    with fits.open(saved[0]) as hdul:
        h = hdul[0].header
    assert h["PNTGSRC"] == "solved"
    assert h["OBJCTDEC"] == coords.format_dec_fits(solved_dec)
    assert h["OBJCTDEC"] != h["MOUNTDEC"]


async def test_promoting_twice_refuses_and_writes_exactly_one_file(
        sim_hub, tmp_path):
    """The second press is a mis-click, and it must not produce a duplicate.
    It also must not read as "there was never anything to save", which is a
    different sentence for the operator.

    Sabotage: leave the entry in ``_promotable`` instead of popping it."""
    hub = sim_hub
    await hub.capture(0.2, 100, 30, 1, save=False, target="M 13")
    first = await hub.promote_last_frame()

    with pytest.raises(PromoteRefused) as e:
        await hub.promote_last_frame()
    assert e.value.code == "already_saved"
    assert e.value.status == 409
    assert e.value.detail == "that frame has already been saved"
    assert len(_fits_files(tmp_path)) == 1

    # and asking for a frame this hub never held is the OTHER refusal
    hub._promoted_ids.clear()
    with pytest.raises(PromoteRefused) as e2:
        await hub.promote_last_frame()
    assert e2.value.code == "nothing_to_promote"
    assert e2.value.status == 404
    assert first["id"] >= 1


async def test_naming_a_stale_frame_is_its_own_refusal(sim_hub, tmp_path):
    """A client that names the frame it is looking at must not be handed a
    newer one. That is a distinct refusal from "already saved": nothing was
    saved, and the frame it asked for is simply gone.

    Sabotage: ignore ``frame_id`` and promote whatever is held."""
    hub = sim_hub
    await hub.capture(0.2, 100, 30, 1, save=False, target="A")
    stale = hub.promotable_summary()["id"]
    await hub.capture(0.2, 100, 30, 1, save=False, target="B")

    with pytest.raises(PromoteRefused) as e:
        await hub.promote_last_frame(frame_id=stale)
    assert e.value.code == "frame_id_mismatch"
    assert _fits_files(tmp_path) == []
    # the held frame is untouched and still promotable
    assert hub.promotable_summary()["target"] == "B"


async def test_a_saved_capture_leaves_nothing_to_promote(sim_hub, tmp_path):
    """Saving outright already wrote the file. A buffer left holding the
    previous unsaved frame would let a later press write a stale one under the
    operator's assumption that "last" meant the frame on screen.

    Sabotage: drop the ``_promotable.pop`` on the saving branch of
    ``capture()``."""
    hub = sim_hub
    await hub.capture(0.2, 100, 30, 1, save=False, target="M 51")
    assert hub.promotable_summary()["available"] is True

    await hub.capture(0.2, 100, 30, 1, save=True, target="M 51")
    summary = hub.promotable_summary()
    assert summary["available"] is False
    assert summary["saved"] is False
    with pytest.raises(PromoteRefused) as e:
        await hub.promote_last_frame()
    assert e.value.code == "nothing_to_promote"
    assert len(_fits_files(tmp_path)) == 1


async def test_the_buffer_holds_one_frame_and_it_is_the_newest(sim_hub):
    """ONE frame per role, replaced every capture. A 26 MP uint16 frame is
    ~50 MB, so a buffer that grew is a buffer that ends the night as an
    out-of-memory kill.

    Sabotage: append instead of replace."""
    hub = sim_hub
    ids = []
    for name in ("one", "two", "three"):
        await hub.capture(0.2, 100, 30, 1, save=False, target=name)
        ids.append(hub.promotable_summary()["id"])

    assert len(hub._promotable) == 1
    assert ids == sorted(ids) and len(set(ids)) == 3      # monotonic
    summary = hub.promotable_summary()
    assert summary["id"] == ids[-1]
    assert summary["target"] == "three"
    assert summary["exposure_s"] == pytest.approx(0.2)
    assert summary["gain"] == 100
    assert summary["binning"] == 1
    assert summary["frame_type"] == "Light"


async def test_disconnecting_the_rig_empties_the_buffer(sim_hub):
    """The snapshot describes a camera, a wheel and a mount that are no longer
    connected, and a reconnect can bring back different ones (#182). There is
    nothing honest left to promote.

    Sabotage: drop ``_promotable.clear()`` from ``_teardown``."""
    hub = sim_hub
    await hub.capture(0.2, 100, 30, 1, save=False, target="M 27")
    assert hub._promotable

    await hub.disconnect_all()
    assert hub._promotable == {}
    assert hub.promotable_summary()["available"] is False


async def test_only_the_target_may_be_changed_on_the_way_to_disk(
        sim_hub, tmp_path):
    """The one value that was never a measurement. Naming the field correctly
    is often the reason the operator is saving at all, so it may be supplied
    late - and it must reach both the OBJECT card and the filename.

    Sabotage: ignore the ``target`` argument."""
    hub = sim_hub
    await hub.capture(0.2, 100, 30, 1, save=False, target="")
    out = await hub.promote_last_frame(target="Veil east")

    assert out["target"] == "Veil east"
    saved = _fits_files(tmp_path)
    assert len(saved) == 1
    with fits.open(saved[0]) as hdul:
        assert hdul[0].header["OBJECT"] == "Veil east"
    assert "Veil" in out["path"]
