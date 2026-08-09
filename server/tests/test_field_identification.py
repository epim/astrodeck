"""The rig naming its own target (#182) -- the hub wiring and the line it holds.

The feature is: a plate solve says what patch of sky the camera is on, and the
Capture screen stops depending on the operator having typed a name. Everything
here is about the ways that can go wrong rather than the way it goes right:

  * A DERIVED NAME MUST NEVER REACH A PATH OR THE FRAME COUNTER. That is the
    regression guard this whole module exists for -- see
    ``test_a_derived_name_never_reaches_the_path_or_the_frame_counter``. The
    counter is persisted and keyed on the sanitized target string, so a name
    that could change between frame 3 and frame 4 would split one night across
    two folders with two overlapping ``0001...`` runs and no error anywhere.
  * The FITS ``OBJECT`` card is adopted only under three simultaneous
    conditions, and the provenance cards are written either way.
  * A solve is invalidated the moment the sky under the camera might have
    changed, and per-frame markers only ever ride the frame they were solved
    from.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from astropy.io import fits

import astrodeck.config as config_mod
import astrodeck.hub as hub_module
from astrodeck.config import ConfigStore
from astrodeck.hub import Hub
from astrodeck.solve.base import SolveResult, WcsSolution


# --------------------------------------------------------------------- fixtures

def _m42_wcs(w: int = 1000, h: int = 800) -> WcsSolution:
    """A plate centred on M42, scaled so M42 dominates the frame outright."""
    return WcsSolution(crval1=83.822, crval2=-5.391,
                       crpix1=(w - 1) / 2.0 + 1.0, crpix2=(h - 1) / 2.0 + 1.0,
                       cd11=-2.78e-4, cd12=0.0, cd21=0.0, cd22=2.78e-4)


class _StubSolver:
    """Returns a fixed successful solve without touching ASTAP or a real file."""
    name = "Stub"

    def __init__(self, wcs: WcsSolution | None = None) -> None:
        self.wcs = wcs if wcs is not None else _m42_wcs()

    async def solve(self, fits_path, *, ra_hint=None, dec_hint=None,
                    fov_deg_hint=None, downsample=0):
        return SolveResult(True, ra_hours=83.822 / 15.0, dec_deg=-5.391,
                           pixel_scale_arcsec=1.0, message="stub", wcs=self.wcs)


@pytest.fixture
async def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(config_mod, "config_store", store)
    h = Hub()
    await h.connect_sim()
    yield h, store, tmp_path
    await h.disconnect_all()


async def _adopt(h: Hub, *, preview_id=None, w=1000, h_px=800, wcs=None):
    """Put a confident M42 identification on the hub the way a real solve does."""
    return await h.note_field_solve(wcs or _m42_wcs(w, h_px),
                                    preview_id=preview_id, data_w=w, data_h=h_px)


# ============================================================ THE LINE (§4.3)

async def test_a_derived_name_never_reaches_the_path_or_the_frame_counter(hub):
    """THE regression guard for the whole feature.

    Capture with an EMPTY target while the hub holds a confident identification.
    The frame must still be filed under ``untargeted`` and the persisted counter
    must still be keyed on ``untargeted``. If a derived name ever leaks into
    ``_capture_path`` this test is the only thing in the tree that notices, and
    the failure it prevents is silent: one night, two folders, two overlapping
    0001 runs, and a stacker that finds half the subs.
    """
    h, _store, root = hub
    await _adopt(h)
    assert h.field_identification()["id"] == "M42", "fixture is not exercising anything"

    await h.capture(0.2, 100, 30, 1, save=True, target="")
    saved = Path(h.last_frame.saved_path)

    parts = {p.lower() for p in saved.relative_to(root).parts}
    assert "untargeted" in parts, f"path lost its untargeted bucket: {saved}"
    assert not any("m42" in p for p in parts), \
        f"a DERIVED name reached the path: {saved}"

    counters = json.loads((root / ".frame_counters.json").read_text())
    assert list(counters) == ["untargeted"], \
        f"a derived name became a frame-counter key: {counters}"


async def test_the_counter_stays_in_one_bucket_when_the_identification_changes(hub):
    """The specific mechanism the rule protects against: an identification that
    drifts onto a neighbour mid-sequence. Two frames, two different confident
    answers, one folder and one continuous 0001/0002 run."""
    h, _store, root = hub
    await _adopt(h)
    await h.capture(0.2, 100, 30, 1, save=True, target="")
    first = Path(h.last_frame.saved_path)

    # The solve drifts onto a different object between frames.
    await _adopt(h, wcs=WcsSolution(
        crval1=10.68, crval2=41.27, crpix1=500.5, crpix2=400.5,
        cd11=-2.78e-4, cd12=0.0, cd21=0.0, cd22=2.78e-4))
    await h.capture(0.2, 100, 30, 1, save=True, target="")
    second = Path(h.last_frame.saved_path)

    assert first.parent == second.parent, \
        f"one night split across two folders: {first.parent} vs {second.parent}"
    counters = json.loads((root / ".frame_counters.json").read_text())
    assert counters == {"untargeted": 2}, counters


# ================================================= the OBJECT card + provenance

def _hdr(path: Path):
    with fits.open(path) as hdul:
        return hdul[0].header


async def test_empty_name_plus_a_confident_solve_fills_object(hub):
    """Today an empty name writes NO OBJECT card at all, so this fills a hole
    rather than overriding anything -- every stacker downstream currently reads
    those frames as having no target."""
    h, _store, _root = hub
    await _adopt(h)
    await h.capture(0.2, 100, 30, 1, save=True, target="")
    hdr = _hdr(Path(h.last_frame.saved_path))
    assert hdr["OBJECT"] == "M42"
    assert hdr["OBJCTID"] == "M42"
    assert hdr["OBJIDSRC"] == "solve"
    assert hdr["OBJIDSEP"] < 5.0


async def test_an_unconfident_identification_writes_provenance_but_not_object(hub):
    """Two comparable objects in one frame. The app records what it saw and
    refuses to assert which one -- an absent OBJECT beats a wrong one, the same
    rule the WCS stamp already follows."""
    h, _store, _root = hub
    await _adopt(h)
    h.field_solve.frame["identification"]["confident"] = False
    h.field_solve.frame["identification"]["runner_up"] = "NGC 1976"

    await h.capture(0.2, 100, 30, 1, save=True, target="")
    hdr = _hdr(Path(h.last_frame.saved_path))
    assert "OBJECT" not in hdr, \
        "an unconfident guess was stamped as THE target"
    assert hdr["OBJCTID"] == "M42"
    assert hdr["OBJIDSRC"] == "solve"


async def test_the_operator_string_always_wins_and_the_disagreement_is_recorded(hub):
    """F3. The human's string is never overwritten, and the file records the
    conflict rather than resolving it silently in either direction."""
    h, _store, _root = hub
    await _adopt(h)
    await h.capture(0.2, 100, 30, 1, save=True, target="Running Man")
    hdr = _hdr(Path(h.last_frame.saved_path))
    assert hdr["OBJECT"] == "Running Man"
    assert hdr["OBJCTID"] == "M42"


async def test_no_identification_writes_no_provenance_cards(hub):
    h, _store, _root = hub
    assert h.field_identification() is None
    await h.capture(0.2, 100, 30, 1, save=True, target="")
    hdr = _hdr(Path(h.last_frame.saved_path))
    assert "OBJCTID" not in hdr and "OBJIDSRC" not in hdr and "OBJIDSEP" not in hdr


async def test_a_dark_frame_is_never_identified(hub):
    """There is no sky in a dark to have solved. The last field's name stamped
    on one is a lie the calibration library would believe -- it rebuilds masters
    by walking headers and nothing else."""
    h, _store, _root = hub
    await _adopt(h)
    await h.capture(0.2, 100, 30, 1, save=True, target="", frame_type="Dark")
    hdr = _hdr(Path(h.last_frame.saved_path))
    assert "OBJECT" not in hdr
    assert "OBJCTID" not in hdr


# ==================================================== staleness and invalidation

async def test_a_slew_clears_the_identification(hub):
    """A solve older than the last slew is stale by definition, and an
    identification that outlives its slew is the most confidently wrong thing
    this feature could produce."""
    h, _store, _root = hub
    await _adopt(h)
    h.invalidate_field_solve("the mount is slewing to a new target")
    assert h.field_solve is None
    assert h.field_identification() is None


async def test_the_mounts_own_report_moving_invalidates_a_solve(hub):
    """The catch-all for every motion path that does not know this feature
    exists -- the API's slew/park/home routes among them. Not a substitute for
    the explicit invalidation: a mount that loses steps keeps reporting the old
    position, which is exactly the AM5 failure this rig has already had."""
    h, _store, _root = hub
    h._note_pointing(83.822 / 15.0, -5.391)
    await _adopt(h)
    assert h.field_identification() is not None

    h._note_pointing(0.712, 41.27)              # a real slew, 60-odd degrees
    ident = h.field_identification()
    assert ident is None, (
        f"the rig slewed 60 degrees and the app still calls this field "
        f"{ident and ident['id']!r}. An identification that outlives its slew is "
        f"the most confidently wrong output this feature can produce")
    assert h.field_solve is None


async def test_a_dither_sized_nudge_does_not_invalidate_a_solve(hub):
    """The other half of the same test, and the reason the threshold is half a
    field rather than zero: guiding and dithering move the reported position
    constantly, and an identification that vanished on every dither would be
    useless."""
    h, _store, _root = hub
    h._note_pointing(83.822 / 15.0, -5.391)
    await _adopt(h)
    h._note_pointing(83.822 / 15.0 + 0.0002, -5.391 + 0.003)    # ~11 arcsec
    assert h.field_identification() is not None


async def test_markers_ride_only_the_frame_they_were_solved_from(hub):
    """A WCS from the previous frame drawn on this one is markers that look
    right and are not. The NAME survives (it describes the sky, not the
    pixels); the placed objects and the plate do not."""
    h, _store, _root = hub
    await _adopt(h, preview_id=7)

    same = h._field_block(7)
    assert same["objects"], "the solved frame got no markers"
    assert "wcs" in same

    other = h._field_block(8)
    assert other["objects"] == [], "a foreign frame was given this frame's markers"
    assert "wcs" not in other
    assert other["id"]["id"] == "M42", "the NAME should outlive its frame"


async def test_a_pointing_derived_answer_is_labelled_and_never_written(hub):
    """F1. Offered to a human because it genuinely helps them see whether the
    scope is roughly right; never adopted, because this mount has been found 50
    degrees from where it claimed."""
    h, _store, _root = hub
    h._note_pointing(83.822 / 15.0, -5.391)
    block = h._field_block(1)
    assert block is not None and block["source"] == "pointing"
    assert block["objects"] == [], "reported pointing cannot place a marker"
    # ...and nothing derived that way reaches a header.
    assert h.field_identification() is None
    await h.capture(0.2, 100, 30, 1, save=True, target="")
    assert "OBJCTID" not in _hdr(Path(h.last_frame.saved_path))


async def test_a_disagreement_between_the_plate_and_the_mount_is_stated(hub):
    """F4, and free: the condition that cost this rig a night. The app must not
    silently agree with the pointing readout when the solve contradicts it."""
    h, _store, _root = hub
    await _adopt(h)
    h._note_pointing(83.822 / 15.0 + 0.5, -5.391 + 4.0)     # the mount is lost
    block = h._field_block(None)
    assert block is not None
    assert block["pointing_disagrees_deg"] > 3.0


# ============================================================ the publish itself

async def test_solve_and_sync_publishes_the_wcs_it_used_to_throw_away(hub, monkeypatch):
    """The plumbing gap this feature closes. ``solve_and_sync`` had
    ``result.wcs`` in hand and dropped it, and every goto pays for that solve --
    so identification is free on a rig with per-frame solving still OFF."""
    h, _store, _root = hub
    monkeypatch.setattr("astrodeck.providers.pick_solver", lambda _hub: _StubSolver())
    assert h.field_solve is None
    await h.solve_and_sync(0.2)
    assert h.field_solve is not None
    assert h.field_identification()["id"] == "M42"


async def test_a_preview_carries_the_field_block(hub):
    h, _store, _root = hub
    await _adopt(h)
    info = await h.capture(0.2, 100, 30, 1, save=False)
    assert info["field"]["id"]["id"] == "M42"
    assert info["field"]["source"] == "solve"


async def test_a_preview_carries_no_field_block_when_nothing_has_solved(hub):
    """ABSENT is the honest answer, and it is what lets the UI say WHICH thing
    is missing instead of printing 'unknown'."""
    h, _store, _root = hub
    info = await h.capture(0.2, 100, 30, 1, save=False)
    assert "field" not in info


async def test_note_field_solve_survives_a_broken_catalog(hub, monkeypatch):
    """Identification is never load-bearing: a solve that cannot be turned into
    a name is still a perfectly good solve for the centering it was run for."""
    h, _store, _root = hub

    def _boom(*a, **kw):
        raise RuntimeError("catalogue on fire")

    monkeypatch.setattr("astrodeck.catalog.region.objects_in_frame", _boom)
    assert await h.note_field_solve(_m42_wcs(), preview_id=1,
                                    data_w=1000, data_h=800) is None
    assert h.field_solve is None


async def test_the_late_solve_patch_names_the_frame_it_belongs_to(hub):
    """A late solve PATCHES; it does not re-publish. Re-sending the whole
    preview to carry a name would push the JPEG again over field WiFi."""
    from astrodeck.events import bus

    h, _store, _root = hub
    seen: list[dict] = []
    q = bus.subscribe()
    try:
        await _adopt(h, preview_id=42)
        await asyncio.sleep(0)
        while not q.empty():
            ev = q.get_nowait()
            if ev.type == "preview_field":
                seen.append(ev.data)
    finally:
        bus.unsubscribe(q)
    assert seen, "nothing was published, so the browser still never sees a WCS"
    assert seen[-1]["preview_id"] == 42
    assert seen[-1]["field"]["id"]["id"] == "M42"
