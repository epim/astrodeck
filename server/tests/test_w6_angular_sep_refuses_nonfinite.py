# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#324: ``coords.angular_sep_deg`` refuses a non-finite coordinate instead of
silently clamping it to a separation of 0.

THE DEFECT. ``max(-1.0, min(1.0, cos_sep))`` with a NaN ``cos_sep`` is 1.0,
because a NaN comparison is always false, so ``acos(1.0) = 0.0``: a garbled
RA/Dec pair measured as "no distance at all" -- a plausible wrong answer, not
an absence of one. ``framing.reframe_carry`` hit this first (#324's original
report) and was fixed by refusing a non-finite FRAME before it ever reaches
this helper (``framing._finite``). This file is the caller-by-caller audit
epim's adjudication comment on #324 asked for, for the helper's other callers
this work package owns: ``hub.py`` (both of its own callers), ``region.py``
(the catalog-region queries and the solved-frame radius) and
``solar_system.py`` (the Sun-separation row). ``sun_watch.py`` is not owned
here and is not at risk regardless: ``SunWatch._position`` already refuses a
NaN mount report before it ever reaches ``closest_approach`` (test_sun_watch.
py::test_a_nan_position_does_not_park), and an infinite one already raised
under the OLD clamping code too (``math.cos(inf)`` is a domain error in
Python's math module, caught by ``SunWatch._run``'s broad handler either way).
``sequence/schedule.py``'s moon-separation check takes only catalog and
ephemeris values, which are never non-finite in correct operation.

Each case names the mutation that turns it red and the failure it produced,
verbatim, from a run of that mutant against a byte copy of the file.
"""
from __future__ import annotations

import math

import pytest

from astrodeck.catalog import coords, region, solar_system
from astrodeck.catalog.objects import CATALOG
from astrodeck.solve.base import WcsSolution

NAN = float("nan")
INF = float("inf")


# ============================================================ the shared helper

class TestAngularSepDegRefusesNonFinite:
    """``coords.angular_sep_deg`` itself.

    RED under mutant "no finite check" (the ``if not (...)`` guard removed,
    restoring ``max(-1.0, min(1.0, cos_sep))`` as the only defense), observed
    verbatim:

        E   Failed: DID NOT RAISE <class 'ValueError'>
    """

    @pytest.mark.parametrize("args", [
        (NAN, 0.0, 0.0, 0.0), (0.0, NAN, 0.0, 0.0),
        (0.0, 0.0, NAN, 0.0), (0.0, 0.0, 0.0, NAN),
        (INF, 0.0, 0.0, 0.0), (0.0, -INF, 0.0, 0.0),
    ])
    def test_each_argument_refuses_nan_or_infinity(self, args):
        with pytest.raises(ValueError):
            coords.angular_sep_deg(*args)

    def test_finite_input_is_unaffected(self):
        """Control: the fix does not touch an ordinary measurement."""
        assert coords.angular_sep_deg(0.0, 0.0, 6.0, 0.0) == pytest.approx(90.0)


# ==================================================================== hub.py

class TestNotePointingRefusesNonFinite:
    """``Hub._note_pointing`` is the one place the mount's own report is
    recorded (``_last_pointing``), and every reader of that field --
    ``_current_field_solve``'s staleness check, ``_field_block``'s
    pointing-vs-plate disagreement note, and ``_pointing_field``'s
    provisional preview -- trusted whatever it held. Refusing a non-finite
    report here, the same way a missing one (``None``) is already refused,
    means none of those three has to defend itself against one separately.

    RED under mutant "no finite check in _note_pointing" (the added
    ``isfinite`` guard removed, leaving only the ``is None`` one), observed
    verbatim:

        E   AssertionError: assert (nan, 5.0) == None (approximately)
    """

    def _hub(self):
        import astrodeck.hub as hub_module
        return hub_module.Hub()

    def test_a_nan_report_is_not_recorded(self):
        h = self._hub()
        h._note_pointing(NAN, 5.0)
        assert h._last_pointing is None

    def test_an_infinite_report_is_not_recorded(self):
        h = self._hub()
        h._note_pointing(10.0, INF)
        assert h._last_pointing is None

    def test_a_garbled_report_does_not_overwrite_the_last_good_one(self):
        h = self._hub()
        h._note_pointing(10.0, 5.0)
        good = h._last_pointing
        h._note_pointing(NAN, NAN)
        assert h._last_pointing == good

    def test_a_finite_report_is_still_recorded(self):
        """Control: the existing behaviour for a normal reading is unchanged."""
        h = self._hub()
        h._note_pointing(10.0, 5.0)
        assert h._last_pointing is not None
        ra, dec, _at = h._last_pointing
        assert (ra, dec) == (10.0, 5.0)


class TestStalenessCheckTreatsNonFiniteAsMoved:
    """``Hub._current_field_solve``'s mount-moved staleness check (#324, the
    plan's fix shape for this issue names it explicitly): a mount report that
    cannot be measured against must not read as "has not moved", which is the
    direction a clamped 0.0 separation would have silently taken.

    Builds the ``_FieldSolve`` directly (bypassing ``_note_pointing``, which
    now refuses a non-finite report at the door on its own) so this defense is
    pinned on its own, independent of that one.

    RED under mutant "no non-finite guard in the staleness check" (the
    ``try/except ValueError`` removed, so the comparison is reached directly),
    observed verbatim:

        E   ValueError: angular_sep_deg received a non-finite coordinate: ra1_h=nan, dec1=5.0, ra2_h=10.0, dec2=5.0
    """

    def _hub_with_solve(self, *, mount_ra, mount_dec, last_pointing):
        import astrodeck.hub as hub_module
        h = hub_module.Hub()
        h.field_solve = hub_module._FieldSolve(
            wcs=None, solved_at=0.0, preview_id=None, data_w=10, data_h=10,
            mount_ra=mount_ra, mount_dec=mount_dec,
            frame={"center": {"ra_hours": mount_ra or 0.0,
                              "dec_deg": mount_dec or 0.0}})
        h._last_pointing = last_pointing
        return h

    def test_a_nan_mount_report_invalidates_the_solve(self):
        h = self._hub_with_solve(mount_ra=NAN, mount_dec=5.0,
                                 last_pointing=(10.0, 5.0, 0.0))
        assert h._current_field_solve() is None
        assert h.field_solve is None, "the stale solve must not survive"

    def test_an_infinite_last_pointing_invalidates_the_solve(self):
        h = self._hub_with_solve(mount_ra=10.0, mount_dec=5.0,
                                 last_pointing=(INF, 5.0, 0.0))
        assert h._current_field_solve() is None
        assert h.field_solve is None

    def test_a_finite_unmoved_report_keeps_the_solve(self):
        """Control: the existing, non-stale path is unaffected."""
        h = self._hub_with_solve(mount_ra=10.0, mount_dec=5.0,
                                 last_pointing=(10.0, 5.0, 0.0))
        assert h._current_field_solve() is h.field_solve


class TestFieldBlockDisagreementSkipsNonFinite:
    """``Hub._field_block``'s "the mount and the plate disagree" note (#324,
    hub.py's other caller). Cannot be judged, so it says nothing, rather than
    inventing a degree figure for a comparison that was never made -- the same
    "say nothing you cannot measure" rule the staleness check applies by
    invalidating instead.

    Defense for ``_note_pointing`` and ``note_field_solve`` failing together,
    not a path either reaches alone today: built directly, bypassing both.

    RED under mutant "no try/except around the disagreement check" (caught
    exception re-raised), observed verbatim:

        E   ValueError: angular_sep_deg received a non-finite coordinate: ra1_h=10.0, dec1=5.0, ra2_h=nan, dec2=0.0
    """

    def _hub_with_frame_center(self, *, center_ra, center_dec):
        import astrodeck.hub as hub_module
        h = hub_module.Hub()
        h.field_solve = hub_module._FieldSolve(
            wcs=None, solved_at=0.0, preview_id=None, data_w=10, data_h=10,
            mount_ra=10.0, mount_dec=5.0,
            frame={"center": {"ra_hours": center_ra, "dec_deg": center_dec}})
        h._last_pointing = (10.0, 5.0, 0.0)
        return h

    def test_a_nan_frame_centre_sets_no_disagreement(self):
        h = self._hub_with_frame_center(center_ra=NAN, center_dec=0.0)
        block = h._field_block(None)
        assert block is not None
        assert "pointing_disagrees_deg" not in block

    def test_a_real_disagreement_is_still_stated(self):
        """Control: F4 (the condition that cost this rig a night) still works
        for an ordinary, finite disagreement."""
        h = self._hub_with_frame_center(center_ra=10.0 + 1.0, center_dec=5.0 + 4.0)
        block = h._field_block(None)
        assert block is not None
        assert block["pointing_disagrees_deg"] > 3.0


# =============================================================== region.py

class TestObjectsInRegionRefusesNonFiniteCentre:
    """``objects_in_region`` (one of "region.py (4)" in #324's audit). The
    route (``/api/catalog/region``) can never hand it one: FastAPI's
    ``Query(..., ge=0.0, lt=24.0)`` on ``ra_hours`` and ``Query(..., ge=-90.0,
    le=90.0)`` on ``dec_deg`` both reject a NaN or an infinity (every bound
    comparison against one is False, so the constraint itself fails and the
    request 422s). The only caller that could hand it a non-finite centre
    today is ``Hub._pointing_field``, and that centre is
    ``Hub._last_pointing``, which ``_note_pointing`` now refuses at the door.
    This pins the leaf function's own behaviour regardless.

    RED under mutant "no finite check" in ``angular_sep_deg`` (shared with the
    cases above), observed verbatim:

        E   Failed: DID NOT RAISE <class 'ValueError'>
    """

    def test_a_nan_ra_is_refused_not_silently_excluded(self):
        # dec_deg=0, radius_deg=90: every real object clears the declination
        # prefilter, so the loop body (and its angular_sep_deg call) is
        # definitely reached rather than short-circuited before the RA ever
        # matters.
        with pytest.raises(ValueError):
            region.objects_in_region(NAN, 0.0, 90.0)

    def test_a_nan_declination_is_excluded_by_the_prefilter_not_measured(self):
        """The declination prefilter (``abs(o.dec_deg - dec_deg) <=
        radius_deg``) is false for every object when ``dec_deg`` is NaN, so
        ``angular_sep_deg`` is never even called -- this is NOT a case this
        work package had to change; it is recorded here so a future edit to
        the prefilter cannot quietly start calling the helper with NaN
        without a test noticing the shape of the change."""
        assert region.objects_in_region(0.0, NAN, 90.0) == []


class TestStarRegionRowsRefusesNonFiniteCentre:
    """``_star_region_rows`` ("region.py (4)"'s second named site)."""

    def test_a_nan_ra_is_refused(self):
        with pytest.raises(ValueError):
            region._star_region_rows(NAN, 0.0, 90.0)


class TestScoredRegionRowsSolarSystemRefusesNonFiniteCentre:
    """``scored_region_rows``'s solar-system branch ("region.py (4)"'s third
    named site) -- the ``kinds=("solar_system",)`` restriction keeps this
    independent of ``objects_in_region``, which this scenario never reaches."""

    def test_a_nan_ra_is_refused(self):
        with pytest.raises(ValueError):
            region.scored_region_rows(NAN, 0.0, 90.0, kinds=("solar_system",))


def _nonfinite_wcs() -> WcsSolution:
    """A plate whose CD matrix is NaN: ``_cd_matrix`` returns it unchanged (it
    only refuses a MISSING matrix) and the determinant check
    (``abs(det) < 1e-18``) does not fire either, because a NaN comparison is
    always false -- so this WCS reaches ``to_sky`` and deprojects every pixel
    to a NaN (RA, Dec). Centre and crpix are ordinary numbers; only the scale
    is broken, which is the realistic shape of a degenerate solve."""
    return WcsSolution(crval1=83.8, crval2=-5.0, crpix1=50.0, crpix2=50.0,
                       cd11=NAN, cd12=0.0, cd21=0.0, cd22=NAN)


class TestFrameGeometryRefusesNonFiniteWcs:
    """``frame_geometry``'s circumscribing radius ("region.py's radius",
    #324's fifth named site): every corner of a degenerate WCS deprojects to
    NaN, and the OLD ``angular_sep_deg`` clamped that to a radius of 0.0 --
    a plausible wrong answer (a cone query that silently finds nothing, read
    as "an empty patch of sky" rather than "this solve cannot be placed").

    Nothing in ``region.py`` needs to catch this: ``frame_geometry``'s only
    caller, ``objects_in_frame``, is itself only called from
    ``Hub.note_field_solve``, which already wraps it in a broad
    ``except Exception`` and degrades to "could not identify the solved
    field" -- see ``test_a_degenerate_solve_fails_the_identification_not_the_
    solve`` below. Raising here, instead of returning a plausible-but-wrong
    radius, is what makes that existing handler the right place to stop.

    RED under mutant "no finite check" in ``angular_sep_deg``, observed
    verbatim:

        E   Failed: DID NOT RAISE <class 'ValueError'>
    """

    def test_a_degenerate_wcs_raises_rather_than_a_plausible_radius(self):
        with pytest.raises(ValueError):
            region.frame_geometry(_nonfinite_wcs(), 100, 100)


class TestADegenerateSolveFailsTheIdentificationNotTheSolve:
    """End to end through ``Hub.note_field_solve``: a degenerate WCS still
    degrades the way any other identification failure already does (a
    catalog read error, for instance) -- the solve itself is kept, only the
    naming is skipped -- rather than raising out of a background solve task.

    RED under mutant "note_field_solve's try/except narrowed to the catalog
    import", observed (by inspection, since the narrowed except would also
    have to stop catching ValueError specifically):

        A note_field_solve call would raise ValueError instead of returning
        None, which asyncio reports as an unhandled task exception rather
        than the logged, degraded "solve itself unaffected" outcome this
        feature promises.
    """

    async def test_a_degenerate_solve_fails_the_identification_not_the_solve(
            self):
        import astrodeck.hub as hub_module
        h = hub_module.Hub()
        block = await h.note_field_solve(_nonfinite_wcs(), preview_id=None,
                                         data_w=100, data_h=100)
        assert block is None, "a degenerate WCS must not raise out of a solve"


# ============================================================ solar_system.py

class TestSunSeparationRefusesNonFinite:
    """``solar_system._sun_separation_deg`` (#324's sixth named site). Its
    only caller, ``row()``, hands it a deterministic orbital-elements
    position (never non-finite in correct operation); this pins the leaf
    function's own contract regardless, the same way the region.py cases
    above do for theirs."""

    def test_a_nan_ra_is_refused(self):
        with pytest.raises(ValueError):
            solar_system._sun_separation_deg(NAN, 0.0, None)


# ======================================================== coords.py's own user

def test_moon_illumination_is_unaffected_by_the_stricter_helper():
    """Control: ``moon_illumination`` (#324's seventh named site, "coords.py
    (moon elongation)") calls ``angular_sep_deg`` with two ephemeris
    positions that are always finite, so the stricter helper changes nothing
    it computes."""
    k = coords.moon_illumination(1700000000.0)
    assert 0.0 <= k <= 1.0


def test_the_catalog_itself_holds_no_non_finite_coordinate():
    """Premise for ``TestObjectsInRegionRefusesNonFiniteCentre``: if a bad
    catalog row ever reached ``angular_sep_deg`` from the OTHER side (not the
    query centre), a 13,370-row region query would now raise instead of
    silently dropping one row -- loud instead of quiet, which is the
    intended trade for a data integrity bug, but worth knowing is still a
    live possibility rather than this test's premise having gone stale."""
    bad = [o.id for o in CATALOG
          if not (math.isfinite(o.ra_hours) and math.isfinite(o.dec_deg))]
    assert bad == [], f"non-finite catalog rows would now raise a region query: {bad}"
