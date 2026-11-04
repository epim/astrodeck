# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 2.1 (#1108): the one classifier for a starless frame.

``astrodeck.imaging.frame_class.classify`` names why a frame holds no usable
stars (stars, obstructed, no_light, cloud, solver_missing, unknown), so the
centring hold, the no-light hold, the autofocus sweep judge and the horizon
suggestion stop deciding it four different ways (#1093 #599 #721 #1077 #817
#308).

The frames are synthetic and seeded (``fixtures/frame_class/synth_frames.py``);
none comes from a real rig frame. The cases hold the classes the combined issue
names, the order that makes them mutually exclusive, and the one trap each
neighbour sets: an obstruction whose median reads at the no-light level, a
sharp-edged cloud bank, a smooth sky-glow ramp, a black frame with no
reference.

MUTANT RUN, 2026-11-03, from a byte backup of
``astrodeck/imaging/frame_class.py``, restored and sha256-verified: the
obstruction edge test swapped (``steps.max() >= OBSTRUCTION_EDGE_FRACTION *
drop`` changed to ``<`` in ``_has_sharp_edge``). Case
``test_an_obstruction_that_reads_at_the_no_light_level_is_still_obstructed``
goes red, observed verbatim:

    E   AssertionError: assert <FrameClass.NO_LIGHT: 'no_light'> is <FrameClass.OBSTRUCTED: 'obstructed'>

and so do the other cases that expect OBSTRUCTED.

TWO MORE MUTANTS, 2026-11-04, same procedure (byte backup, sha256 restore):
``_has_sharp_edge`` returning True always goes red in
``test_a_gradual_ramp_into_the_dark_is_not_an_obstruction_though_the_lit_side_has_stars``
(``assert <FrameClass.OBSTRUCTED: 'obstructed'> is not <FrameClass.OBSTRUCTED:
'obstructed'>``), and the smoothness test disabled (``if False:``) goes red in
``test_a_sharp_edge_over_a_many_level_dark_region_is_not_an_obstruction``
(the same assertion). Both fixtures have enough lit-side stars to pass the
star rule, so only the test under mutation refuses them.
"""
from __future__ import annotations

import dataclasses
import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from astrodeck.devices.base import CameraFrame
from astrodeck.imaging import frame_class
from astrodeck.imaging.frame_class import FrameClass, FrameContext, classify
from astrodeck.imaging.stars import detect_stars
from astrodeck.solve import light
from astrodeck.solve.light import Reference

# APPENDED, as the other fixture and tool helpers are.
_HERE = Path(__file__).resolve().parent
for _extra in (_HERE / "fixtures" / "frame_class", _HERE.parents[1] / "tools"):
    if str(_extra) not in sys.path:
        sys.path.append(str(_extra))
import no_light_audit as audit  # noqa: E402
import synth_frames as synth  # noqa: E402

#: The no-light reference the synthetic black level stands for: a dark master
#: at 240 ADU, the band 3 ADU wide around it (``light.classify``).
REFERENCE = Reference(level=synth.BLACK_LEVEL, kind=light.DARK_MASTER)
WITH_REFERENCE = FrameContext(reference=REFERENCE)


def _camera_frame(data: np.ndarray, **kw) -> CameraFrame:
    return CameraFrame(data=data, exposure_s=4.0, gain=200, offset=30,
                       binning=2, bayer_pattern=None, temperature_c=-5.0,
                       timestamp=0.0, **kw)


# ----------------------------------------------------------------- the classes

def test_a_clear_star_field_is_stars():
    assert classify(synth.stars_frame()) is FrameClass.STARS


def test_a_capped_optic_is_no_light_with_a_reference():
    frame = synth.no_light_frame()
    assert light.classify(frame, REFERENCE).kind == light.NO_LIGHT
    assert classify(frame, WITH_REFERENCE) is FrameClass.NO_LIGHT


def test_a_black_frame_without_a_reference_is_never_no_light():
    """``solve.light``'s rule: a median alone cannot tell a cap from a dark
    sky, so no reference means no no_light. The frame is starless and flat, and
    ``cloud_score`` reads a black frame cloudy (its documented calibration)."""
    assert classify(synth.no_light_frame()) is FrameClass.CLOUD


def test_a_bright_starless_deck_is_cloud():
    assert classify(synth.cloud_frame(), WITH_REFERENCE) is FrameClass.CLOUD


def test_noise_peaks_on_a_bright_noisy_deck_are_cloud_not_stars():
    assert classify(synth.noisy_cloud_frame()) is FrameClass.CLOUD


def test_a_treeline_over_a_lit_sky_is_obstructed_with_no_reference():
    """The #1093 case: the library had no reference, the light check gave up,
    and the structure alone is what names the tree."""
    frame = synth.obstructed_frame(dark_level=700.0, sky_level=1000.0)
    assert classify(frame) is FrameClass.OBSTRUCTED


def test_an_obstruction_that_reads_at_the_no_light_level_is_still_obstructed():
    """Mostly behind a tree, the frame's median sits at the black level and
    ``solve.light`` calls it a capped optic. The structure comes first, so the
    horizon is learned from a tree and never from a "cap".

    RED under the mutant in the module docstring."""
    frame = synth.obstructed_frame(edge_row=130, wobble=10)
    assert light.classify(frame, REFERENCE).kind == light.NO_LIGHT
    assert classify(frame, WITH_REFERENCE) is FrameClass.OBSTRUCTED


def test_a_sharp_edged_cloud_bank_is_cloud_not_an_obstruction():
    """The same sharp edge as a treeline, but the bright side is starless: a
    deck lit from the ground, not a sky."""
    assert classify(synth.cloud_bank_frame()) is FrameClass.CLOUD


def test_a_smooth_sky_glow_ramp_is_not_an_obstruction():
    """A gradient steps a fraction of its drop per block, so it has no edge."""
    assert classify(synth.ramp_frame()) is not FrameClass.OBSTRUCTED


def test_flat_noise_is_not_an_obstruction():
    assert classify(synth.uniform_dim_frame()) is not FrameClass.OBSTRUCTED


def test_an_obstructed_frame_with_stars_enough_to_solve_is_stars():
    """A frame with a usable star field is not a problem frame, tree or not."""
    frame = synth.obstructed_frame(stars=60)
    assert classify(frame) is FrameClass.STARS


def _lit_side_stars(frame: np.ndarray, rows: int) -> int:
    """Detected stars in the top ``rows`` rows: the premise of the next two
    cases is that the lit side has enough stars for the star rule to pass, so
    that only the edge or the smoothness test can refuse the frame."""
    return sum(1 for s in detect_stars(frame) if s.y < rows)


def test_a_gradual_ramp_into_the_dark_is_not_an_obstruction_though_the_lit_side_has_stars():
    """Smooth dark below, stars above, but no edge: the block step is an eighth
    of the drop. Only the edge test refuses it.

    RED under mutant "the edge test always passes" (``_has_sharp_edge``
    returning True): see the module docstring."""
    frame = synth.gradual_dark_frame()
    assert _lit_side_stars(frame, 400) >= frame_class.OBSTRUCTION_MIN_LIT_STARS
    assert classify(frame) is not FrameClass.OBSTRUCTED
    assert classify(frame, WITH_REFERENCE) is not FrameClass.OBSTRUCTED


def test_a_sharp_edge_over_a_many_level_dark_region_is_not_an_obstruction():
    """Stars above a sharp edge, but the region below is patches at two levels
    and not one surface. Only the smoothness test refuses it.

    RED under mutant "the smoothness test is disabled" (its ``if`` made
    ``if False``): see the module docstring."""
    frame = synth.rough_dark_frame()
    assert _lit_side_stars(frame, 250) >= frame_class.OBSTRUCTION_MIN_LIT_STARS
    assert classify(frame) is not FrameClass.OBSTRUCTED
    assert classify(frame, WITH_REFERENCE) is not FrameClass.OBSTRUCTED


# ----------------------------------------------------- the rest of the contract

def test_a_missing_solver_decides_before_any_pixel_is_read():
    ctx = FrameContext(solver_missing=True)
    assert classify(None, ctx) is FrameClass.SOLVER_MISSING
    assert classify(synth.stars_frame(), ctx) is FrameClass.SOLVER_MISSING


@pytest.mark.parametrize("frame", [
    None,
    np.zeros((0, 0), dtype=np.uint16),
    synth.constant_frame(),
    synth.stars_frame()[0],
    np.stack([synth.stars_frame()] * 3),
    synth.stars_frame()[:2, :2],
    synth.stars_frame()[:20, :20],
], ids=["none", "empty", "constant", "1d", "3d", "2x2", "20x20"])
def test_a_frame_that_cannot_be_judged_is_unknown(frame):
    assert classify(frame, WITH_REFERENCE) is FrameClass.UNKNOWN


def test_a_rendered_preview_is_unknown():
    """A NINA-rendered 8-bit stretch says nothing about linear levels."""
    frame = _camera_frame(synth.stars_frame(), data_is_linear=False)
    assert classify(frame) is FrameClass.UNKNOWN


def test_a_camera_frame_is_judged_by_its_pixels():
    assert classify(_camera_frame(synth.stars_frame())) is FrameClass.STARS
    assert classify(_camera_frame(synth.obstructed_frame()),
                    WITH_REFERENCE) is FrameClass.OBSTRUCTED


def test_stars_the_caller_already_found_are_used_and_not_found_again(
        monkeypatch):
    frame = synth.stars_frame()
    found = detect_stars(frame)

    def boom(*_a, **_k):
        raise AssertionError("detect_stars ran although the caller gave stars")

    monkeypatch.setattr(frame_class, "detect_stars", boom)
    assert classify(frame, FrameContext(stars=found)) is FrameClass.STARS
    # The caller's list is the evidence: an empty one is "no stars found".
    assert classify(frame, FrameContext(stars=[])) is not FrameClass.STARS


def test_the_values_are_the_names_consumers_log():
    assert {c.value for c in FrameClass} == {
        "stars", "obstructed", "no_light", "cloud", "solver_missing",
        "unknown"}
    assert f"{FrameClass.OBSTRUCTED.value}" == "obstructed"
    assert FrameClass.CLOUD == "cloud"
    assert json.dumps({"class": FrameClass.NO_LIGHT}) == '{"class": "no_light"}'


def test_the_fixtures_are_the_same_frames_every_time():
    for make in (synth.stars_frame, synth.no_light_frame,
                 synth.obstructed_frame, synth.cloud_frame,
                 synth.cloud_bank_frame, synth.ramp_frame):
        assert np.array_equal(make(), make()), make.__name__
        assert make().shape == (synth.SIZE, synth.SIZE)
        assert make().dtype == np.uint16


# ---------------------------------------------------- the audit's class column

def _log_line(verdict: light.LightVerdict) -> str:
    return f"failed solve, light check: {verdict.evidence()}"


def _audit(tmp_path: Path, messages: list[str]) -> list[str]:
    log = tmp_path / "2026-10-14.jsonl"
    log.write_text("\n".join(
        json.dumps({"type": "log", "ts": 1_760_000_000.0 + i,
                    "data": {"level": "info", "message": m,
                             "source": "solve"}})
        for i, m in enumerate(messages)) + "\n", encoding="utf-8")
    out = io.StringIO()
    assert audit.main([str(tmp_path)], out=out) == 0
    return out.getvalue().splitlines()


def test_the_audit_table_has_a_class_column_for_every_verdict(tmp_path):
    capped = light.classify(synth.no_light_frame(), REFERENCE)
    cloud = light.classify(synth.cloud_frame(), REFERENCE)
    unknown = light.classify(synth.no_light_frame(), None)
    narrow = dataclasses.replace(
        capped, kind=light.NARROWBAND, why=light.narrowband_words("S"))
    assert (capped.kind, cloud.kind, unknown.kind) == (
        light.NO_LIGHT, light.CLOUD, light.UNKNOWN)

    lines = _audit(tmp_path, [_log_line(v)
                              for v in (capped, cloud, unknown, narrow)])

    header = lines[1].split()
    assert header[-2:] == ["class", "moonless"], header
    classes = {}
    for ln in lines:
        cells = ln.split()
        if len(cells) > 2 and cells[1] in ("no_light", "cloud", "unknown",
                                           "narrowband"):
            classes[cells[1]] = cells[12]
    assert classes == {"no_light": "no_light", "cloud": "cloud",
                       "unknown": "unknown", "narrowband": "unknown"}


def test_the_audits_table_names_only_classes_that_exist():
    """The tool is standard library only, so it carries the table; this holds
    it to the verdicts ``solve.light`` makes and to ``FrameClass``."""
    verdicts = {light.NO_LIGHT, light.CLOUD, light.UNKNOWN, light.NARROWBAND}
    assert set(audit._CLASS_OF_VERDICT) == verdicts
    assert set(audit._CLASS_OF_VERDICT.values()) <= {c.value
                                                     for c in FrameClass}
    # A verdict the classifier itself makes keeps its name.
    for kind in (light.NO_LIGHT, light.CLOUD, light.UNKNOWN):
        assert audit._CLASS_OF_VERDICT[kind] == FrameClass(kind).value
