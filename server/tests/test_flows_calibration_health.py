"""The LIBRARY HEALTH matrix — the thing that decides "if library stale".

Two consumers read this and neither of them is a page of text: the inspector
panel shows it to a human at 21:00, and the calibration queue acts on it at
23:40 with the mount parked at a black slot. So the properties under test are
the ones that cost a night when they are wrong:

* a library the scanner could not find must read MISSING, never OK — the
  quietest possible failure here is a green matrix over an empty disk;
* a set that exists but does not fit tonight must be distinguishable from one
  that was never shot, because "refresh these" and "you have never had these"
  are different jobs;
* and counting must never be mistaken for evidence. On 2026-08-11 this rig shot
  50 bias and 44 darks at gain 125 / offset 30 / bin 1 / −5 °C and they were
  later found contaminated. A row that graded on counts alone said OK about
  them.

Nothing here touches the filesystem: the matrix takes its supply as rows, which
is the seam that makes it testable at all.
"""
from __future__ import annotations

import time

import pytest

from astrodeck.calibration.keys import CalKey
from astrodeck.calibration.matcher import LightNeed, MasterRecord, MatchTolerance
from astrodeck.flows.calibration_health import (KIND_ORDER, ROTATION_TOL_DEG,
                                                STALE_AFTER_DAYS, CalFrame,
                                                CalNeed, HealthRow,
                                                bias_matches, frame_from_header,
                                                health_matrix)
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowGraph, FlowNode

NOW = 1_754_000_000.0                      # a fixed clock: age is under test
DAY = 86400.0


def need(exp=300.0, gain=100, offset=30, temp=-5.0, binning=1, filt="Ha",
         rot=23.4) -> CalNeed:
    return CalNeed(LightNeed(exp, gain, offset, temp, binning, filt),
                   rotation_deg=rot)


def frames(kind, n, *, exp=300.0, gain=100, offset=30, temp=-5.0, binning=1,
           filt="", rot=23.4, ts=NOW, dark_ok=None, why=""):
    """``n`` identical calibration frames of ``kind`` on disk."""
    exposure = 0.0 if kind == "BIAS" else exp
    return [CalFrame(key=CalKey(kind, exposure, gain, offset, temp, binning, filt),
                     ts=ts, rotation_deg=rot, dark_ok=dark_ok, why=why,
                     path=f"cal/{kind.lower()}_{i}.fits") for i in range(n)]


def master(kind="DARK", *, exp=300.0, gain=100, offset=30, temp=-5.0, binning=1,
           filt="", count=40, built=NOW, mid="m1") -> MasterRecord:
    return MasterRecord(mid, kind, exp, gain, offset, temp, binning, filt, count,
                        f"/masters/{mid}.fits", built)


def row_of(rows, kind) -> HealthRow:
    return next(r for r in rows if r.kind == kind)


def codes(row: HealthRow) -> list[str]:
    return [r.code for r in row.reasons]


class TestFailingHonestly:
    def test_an_empty_library_is_MISSING_in_every_row(self):
        """The quietest way for this feature to fail is a green matrix over a
        disk the scanner never found. Every kind must say MISSING, and every row
        must still be PRESENT — a matrix that returned nothing would be rendered
        as a panel with no complaints in it."""
        rows = health_matrix([need()], [], now=NOW)
        assert [r.kind for r in rows] == list(KIND_ORDER)
        assert {r.verdict for r in rows} == {"MISSING"}
        assert {r.have for r in rows} == {0}

    def test_no_lights_planned_yields_no_rows_which_is_not_the_same_as_OK(self):
        """A flow with nothing to shoot needs no calibration, so there is
        nothing to grade. The caller must say 'no lights planned' rather than
        paint an empty matrix green — asserted here so the emptiness is a
        decision somebody made and not an accident."""
        assert health_matrix([], frames("DARK", 50), now=NOW) == []

    def test_frames_nothing_asked_for_do_not_become_rows(self):
        """The rows are the DEMAND. Last season's 60s darks are not a row; if
        they were, a matrix could be full of green while the one exposure
        tonight needs has no line at all."""
        rows = health_matrix([need(exp=300.0)], frames("DARK", 50, exp=60.0),
                             now=NOW)
        assert [r.exposure_s for r in rows if r.kind == "DARK"] == [300.0]


class TestTheVerdicts:
    def test_a_full_quota_at_tonights_settings_is_OK(self):
        rows = health_matrix([need()], frames("DARK", 20), kinds=["DARK"], now=NOW)
        assert row_of(rows, "DARK").verdict == "OK"

    def test_short_of_the_quota_is_STALE_and_says_by_how_much(self):
        """The prototype's own row: 14/20 STALE. A verdict with no number in it
        cannot tell the operator whether the queue needs six frames or sixty."""
        row = row_of(health_matrix([need()], frames("DARK", 14), kinds=["DARK"],
                                   now=NOW), "DARK")
        assert (row.verdict, row.quantity) == ("STALE", "14/20")
        assert codes(row) == ["short"]
        assert "6 short" in row.reasons[0].text

    def test_a_set_shot_at_another_temperature_is_STALE_not_MISSING(self):
        """The prototype's worked example: 'darks STALE (sensor −10→−5°C)'. You
        own these frames — the setpoint moved under them. Reading MISSING would
        send the operator looking for a library that is right there."""
        row = row_of(health_matrix([need(temp=-5.0)],
                                   frames("DARK", 20, temp=-10.0),
                                   kinds=["DARK"], now=NOW), "DARK")
        assert row.verdict == "STALE"
        assert row.family == 20 and row.have == 0
        assert "drift" in codes(row)

    def test_the_drift_reason_names_the_tolerance_that_decided_it(self):
        """Derived, not decreed: the number in the sentence is the matcher's own
        ``temp_tol_c``, which is the same number the pipeline will use when it
        goes looking for a master. Hard-coding a second one here is how a row
        says OK about a set the pipeline then refuses to apply."""
        tol = MatchTolerance(temp_tol_c=7.0)
        row = row_of(health_matrix([need(temp=-5.0)],
                                   frames("DARK", 20, temp=-10.0),
                                   kinds=["DARK"], tol=tol, now=NOW), "DARK")
        assert row.verdict == "OK"          # −10 is inside ±7
        loose = row_of(health_matrix([need(temp=-5.0)],
                                     frames("DARK", 20, temp=-20.0),
                                     kinds=["DARK"], tol=tol, now=NOW), "DARK")
        text = next(r.text for r in loose.reasons if r.code == "drift")
        assert "±7" in text and "−20" in text and "−5" in text

    def test_a_different_exposure_is_MISSING_not_STALE(self):
        """A 60s dark is not a drifted 300s dark, it is a different thing you
        have never shot. The split is what makes STALE mean 'refresh' and
        MISSING mean 'shoot'."""
        row = row_of(health_matrix([need(exp=300.0)],
                                   frames("DARK", 40, exp=60.0),
                                   kinds=["DARK"], now=NOW), "DARK")
        assert row.verdict == "MISSING" and row.family == 0

    def test_a_different_gain_is_a_different_row_entirely(self):
        """Gain is EXACT in ``dark_matches``. Pooling two gains would add frames
        the pipeline keeps apart and report a quota that cannot be met."""
        row = row_of(health_matrix([need(gain=125)], frames("DARK", 44, gain=100),
                                   kinds=["DARK"], now=NOW), "DARK")
        assert (row.verdict, row.have, row.family) == ("MISSING", 0, 0)

    def test_binning_is_exact_too(self):
        row = row_of(health_matrix([need(binning=2)], frames("DARK", 20, binning=1),
                                   kinds=["DARK"], now=NOW), "DARK")
        assert row.verdict == "MISSING"


class TestCountingIsNotEvidence:
    def test_frames_the_dark_check_contradicted_are_not_counted(self):
        """``library.build`` will not stack a DARKOK=False frame. A row that
        counted one would promise a master that cannot be built out of it —
        which is 2026-08-11 exactly: 44 darks on disk, none of them usable."""
        row = row_of(health_matrix(
            [need()], frames("DARK", 44, dark_ok=False,
                             why="median pinned at the sensor ceiling"),
            kinds=["DARK"], now=NOW), "DARK")
        assert (row.have, row.contradicted, row.family) == (0, 44, 44)
        assert row.verdict == "STALE"       # you HAVE them; they are refused

    def test_the_row_carries_the_frames_own_evidence_sentence(self):
        """'3 frames skipped' tells an operator nothing they can act on. The
        sentence off the frame's own header is what identifies an empty slot
        ticked opaque — the same reasoning ``library.build`` logs under."""
        row = row_of(health_matrix(
            [need()],
            frames("DARK", 20, dark_ok=False, why="the frame is full of stars"),
            kinds=["DARK"], now=NOW), "DARK")
        text = next(r.text for r in row.reasons if r.code == "contradicted")
        assert "the frame is full of stars" in text and "20" in text

    def test_a_contradicted_frame_still_makes_the_row_STALE_not_MISSING(self):
        """MISSING means 'nothing at these settings has ever been shot'. Saying
        that about a folder holding 44 darks would make the operator think the
        scan was broken, and hide the reason they are useless."""
        row = row_of(health_matrix([need()], frames("DARK", 44, dark_ok=False),
                                   kinds=["DARK"], now=NOW), "DARK")
        assert row.verdict == "STALE" and "contradicted" in codes(row)

    def test_an_OK_row_reports_how_many_of_its_frames_were_ever_MEASURED(self):
        """``imaging.darks`` is explicit that a positive verdict is only the
        absence of a negative one, and names the leak it cannot see. So OK means
        'nothing contradicts these'. 20/20 with 0 measured and 20/20 with 20
        measured are worth different amounts, and the row has to be able to say
        which one it is."""
        unjudged = row_of(health_matrix([need()], frames("DARK", 20, dark_ok=None),
                                        kinds=["DARK"], now=NOW), "DARK")
        judged = row_of(health_matrix([need()], frames("DARK", 20, dark_ok=True),
                                      kinds=["DARK"], now=NOW), "DARK")
        assert unjudged.verdict == judged.verdict == "OK"
        assert (unjudged.measured, judged.measured) == (0, 20)

    def test_an_unjudged_frame_is_usable_because_every_old_frame_is_unjudged(self):
        """``library.DARK_OK_CARD``: absent means unjudged, and MUST read as
        usable. Defaulting the other way empties a working library on the day
        the check ships."""
        row = row_of(health_matrix([need()], frames("DARK", 20, dark_ok=None),
                                   kinds=["DARK"], now=NOW), "DARK")
        assert row.have == 20 and row.contradicted == 0


class TestFlatsAndRotation:
    def test_a_flat_from_another_camera_angle_is_STALE(self):
        """Dust shadows travel with the rotator. A flat shot at PA 90 divides
        out a mote that is not where it was, which puts a dark ring beside a
        bright one in every frame of the night."""
        rows = health_matrix([need(rot=23.4)],
                             frames("FLAT", 20, filt="Ha", rot=90.0),
                             kinds=["FLAT"], now=NOW)
        row = row_of(rows, "FLAT")
        assert row.verdict == "STALE"
        assert "PA 90" in next(r.text for r in row.reasons if r.code == "drift")

    def test_the_rotation_check_wraps(self):
        """359.5° and 0.5° are one degree apart. A naive subtraction calls that
        359 and reshoots a perfectly good set of flats."""
        rows = health_matrix([need(rot=0.5)], frames("FLAT", 20, filt="Ha", rot=359.6),
                             kinds=["FLAT"], rotation_tol_deg=1.0, now=NOW)
        assert row_of(rows, "FLAT").verdict == "OK"

    def test_an_unknown_angle_is_not_a_constraint_on_either_side(self):
        """A rig with no rotator writes no ROTATANG, and a target with no
        rotation asks for none. Mirrors ``matcher._temp_ok``: unknown is not a
        mismatch, or every fixed-camera rig reads STALE forever."""
        no_card = health_matrix([need(rot=23.4)],
                                frames("FLAT", 20, filt="Ha", rot=None),
                                kinds=["FLAT"], now=NOW)
        no_target_pa = health_matrix([need(rot=None)],
                                     frames("FLAT", 20, filt="Ha", rot=90.0),
                                     kinds=["FLAT"], now=NOW)
        assert row_of(no_card, "FLAT").verdict == "OK"
        assert row_of(no_target_pa, "FLAT").verdict == "OK"

    def test_flat_exposure_is_not_an_identity(self):
        """``flat_matches`` does not test exposure — a flat is solved to an ADU
        target, so its exposure is an outcome, not a key. A row that split on it
        would demand a fresh set every time the sky brightness changed, and two
        sub lengths through one filter would print two identical flat lines."""
        row = row_of(health_matrix([need(exp=300.0)],
                                   frames("FLAT", 20, filt="Ha", exp=1.7),
                                   kinds=["FLAT"], now=NOW), "FLAT")
        assert row.verdict == "OK" and row.exposure_s is None
        two_sub_lengths = health_matrix([need(exp=300.0), need(exp=60.0)],
                                        frames("FLAT", 20, filt="Ha"),
                                        kinds=["FLAT"], now=NOW)
        assert len(two_sub_lengths) == 1

    def test_flats_are_per_filter(self):
        """The prototype's matrix has two flat rows for two filters, one OK and
        one MISSING. Ha frames cannot cover an OIII need."""
        rows = health_matrix([need(filt="Ha"), need(filt="OIII")],
                             frames("FLAT", 20, filt="OIII"), kinds=["FLAT"], now=NOW)
        by_filter = {r.filter: r.verdict for r in rows}
        assert by_filter == {"Ha": "MISSING", "OIII": "OK"}


class TestAge:
    def test_a_stale_dated_set_is_STALE_and_the_reason_names_the_horizon(self):
        """The horizon is a POLICY standing in for an event nothing here can
        observe — the train came apart, the camera turned in its ring. A bare
        STALE would hide that; the number has to be in the sentence so the panel
        can explain itself and the operator can disagree with it."""
        old = NOW - 30 * DAY
        row = row_of(health_matrix([need()], frames("FLAT", 20, filt="Ha", ts=old),
                                   kinds=["FLAT"], now=NOW), "FLAT")
        assert row.verdict == "STALE" and codes(row) == ["age"]
        assert "30 days" in row.reasons[0].text and "14-day" in row.reasons[0].text

    def test_the_horizon_is_shorter_for_flats_than_for_darks(self):
        """Different things invalidate them. A dark's enemies — gain, offset,
        binning, setpoint — are in the row key already, so a change there makes
        a different row rather than an old one; a flat's enemy is a fingerprint
        on the corrector, which leaves no trace at all."""
        assert STALE_AFTER_DAYS["FLAT"] < STALE_AFTER_DAYS["DARK"]
        old = NOW - 30 * DAY
        supply = frames("DARK", 20, ts=old) + frames("FLAT", 20, filt="Ha", ts=old)
        rows = health_matrix([need()], supply, kinds=["DARK", "FLAT"], now=NOW)
        assert row_of(rows, "DARK").verdict == "OK"
        assert row_of(rows, "FLAT").verdict == "STALE"

    def test_the_horizon_is_a_parameter_a_site_can_replace(self):
        rows = health_matrix([need()], frames("FLAT", 20, filt="Ha", ts=NOW - 30 * DAY),
                             kinds=["FLAT"], stale_after_days={"FLAT": 90.0}, now=NOW)
        assert row_of(rows, "FLAT").verdict == "OK"

    def test_age_is_measured_from_the_NEWEST_usable_frame(self):
        """A set topped up last night is not stale because it also contains
        frames from March."""
        supply = (frames("FLAT", 10, filt="Ha", ts=NOW - 300 * DAY)
                  + frames("FLAT", 10, filt="Ha", ts=NOW - DAY))
        row = row_of(health_matrix([need()], supply, kinds=["FLAT"], now=NOW), "FLAT")
        assert row.verdict == "OK" and row.age_days == pytest.approx(1.0)

    def test_an_empty_row_is_not_also_reported_as_old(self):
        """MISSING plus 'newest is None days old' is noise stacked on a verdict
        that already said everything."""
        row = row_of(health_matrix([need()], [], kinds=["FLAT"], now=NOW), "FLAT")
        assert "age" not in codes(row) and row.age_days is None


class TestMasters:
    def test_a_built_master_counts_as_the_frames_it_was_built_from(self):
        """``frame_count`` is the builder's own record of the stack's depth,
        written so it survives the raws being pruned. Without this a library
        that has been built and tidied reads MISSING and the queue reshoots a
        night of calibration it already owns."""
        row = row_of(health_matrix([need()], [], masters=[master(count=40)],
                                   kinds=["DARK"], now=NOW), "DARK")
        assert (row.verdict, row.have, row.from_master) == ("OK", 40, 40)
        assert row.master_id == "m1"

    def test_the_master_a_row_credits_is_the_one_the_pipeline_would_apply(self):
        """``best_master`` ranks by |Δexposure|, then |Δtemp|, then depth. The
        row must name that one, or the panel is describing a stack the
        calibration step will not use."""
        rows = health_matrix([need(exp=300.0)], [],
                             masters=[master(exp=310.0, count=99, mid="far"),
                                      master(exp=301.0, count=20, mid="near")],
                             kinds=["DARK"], now=NOW)
        assert row_of(rows, "DARK").master_id == "near"

    def test_a_master_the_pipeline_would_refuse_is_not_counted_at_all(self):
        """A ``MasterRecord`` carries no evidence beyond its key. Crediting a
        stack that ``dark_matches`` rejects is the 'library looks healthy'
        failure this whole matrix exists to prevent."""
        row = row_of(health_matrix([need(temp=-5.0)], [],
                                   masters=[master(temp=-25.0, count=99)],
                                   kinds=["DARK"], now=NOW), "DARK")
        assert (row.verdict, row.have, row.master_id) == ("MISSING", 0, None)

    def test_a_bias_master_is_picked_by_depth(self):
        """``best_master`` has no BIAS branch (matcher: 'Bias is optional in
        v1'), so this module picks one — deepest stack wins, since bias has no
        exposure to rank on."""
        rows = health_matrix([need()], [],
                             masters=[master("BIAS", exp=0.0, count=20, mid="thin"),
                                      master("BIAS", exp=0.0, count=60, mid="deep")],
                             kinds=["BIAS"], now=NOW)
        assert row_of(rows, "BIAS").master_id == "deep"


class TestBias:
    def test_a_bias_row_ignores_exposure(self):
        """``key_from_header`` folds a bias exposure to 0.0, so it is not an
        identity. A bias row keyed on the LIGHT's exposure would need a separate
        bias set per sub length, which is not a thing anyone shoots."""
        rows = health_matrix([need(exp=300.0), need(exp=60.0, filt="Ha")],
                             frames("BIAS", 40), kinds=["BIAS"], now=NOW)
        assert len(rows) == 1 and rows[0].exposure_s is None
        assert rows[0].verdict == "OK"

    @pytest.mark.parametrize("axis,value", [("gain", 125), ("offset", 60),
                                            ("binning", 2)])
    def test_bias_is_still_bound_to_gain_offset_and_binning(self, axis, value):
        """The three axes ``dark_matches`` compares exactly, compared exactly
        here too. A bias read out at bin 2 has a different pedestal AND a
        different shape; pooling it in would report a quota that cannot be
        met by anything the pipeline will apply."""
        rows = health_matrix([need(**{axis: value})], frames("BIAS", 50),
                             kinds=["BIAS"], now=NOW)
        assert row_of(rows, "BIAS").verdict == "MISSING"

    def test_bias_temperature_is_toleranced_like_a_darks(self):
        """A bias is the offset pedestal plus read noise, and the pedestal moves
        with the sensor — so a bias from another setpoint is the same kind of
        wrong as a dark from one, and it drifts rather than disappearing."""
        assert bias_matches(LightNeed(0.0, 100, 30, -5.0, 1, ""),
                            master("BIAS", exp=0.0, temp=-6.0), MatchTolerance())
        assert not bias_matches(LightNeed(0.0, 100, 30, -5.0, 1, ""),
                                master("BIAS", exp=0.0, temp=-15.0), MatchTolerance())
        row = row_of(health_matrix([need()], frames("BIAS", 50, temp=-15.0),
                                   kinds=["BIAS"], now=NOW), "BIAS")
        assert row.verdict == "STALE" and "drift" in codes(row)


class TestTheShapeTheUIConsumes:
    def test_rows_come_out_in_the_queues_shooting_order(self):
        """``compile_plan`` writes ``order: [dark, bias, flat]`` into every plan.
        The matrix reads top-to-bottom as the night's work, and the two orders
        living in two files is exactly the drift this pins."""
        graph = FlowGraph(nodes=[FlowNode(id="q", type="calib", x=0, y=0, params={})])
        compiled = compile_plan(graph, "n")["automation"]["calibration_queue"]["order"]
        assert [k.upper() for k in compiled] == list(KIND_ORDER)
        rows = health_matrix([need()], [], now=NOW)
        assert [r.kind for r in rows] == list(KIND_ORDER)

    def test_the_row_json_carries_the_four_columns_the_panel_draws(self):
        """The prototype's matrix row is k / v / q / s. All four have to arrive
        from the server, or the panel invents its own wording for a verdict."""
        row = row_of(health_matrix([need()], frames("DARK", 14), kinds=["DARK"],
                                   now=NOW), "DARK")
        js = row.to_json()
        assert js["label"] == "DARKS"
        assert js["summary"] == "300s g100 · −5°C"
        assert js["quantity"] == "14/20"
        assert js["verdict"] == "STALE"
        assert js["reasons"][0]["code"] == "short"

    def test_binning_shows_up_only_when_it_is_not_one(self):
        """Two rows differing only in binning would print the same line twice,
        and the ordinary row should not carry a word about the unusual case."""
        def dark_row(b):
            return row_of(health_matrix([need(binning=b)], [], kinds=["DARK"],
                                        now=NOW), "DARK")
        one, two = dark_row(1), dark_row(2)
        assert "bin" not in one.summary and "bin 2" in two.summary

    def test_a_dark_row_carries_no_filter_and_a_flat_row_no_temperature(self):
        """The row key is the matcher's predicate made visible: DARK drops the
        filter (shutter closed), FLAT drops exposure and temperature (neither is
        tested by ``flat_matches``). A field the predicate ignores would split
        one row into two with identical counts."""
        rows = health_matrix([need()], [], now=NOW)
        dark, flat = row_of(rows, "DARK"), row_of(rows, "FLAT")
        assert dark.filter == "" and dark.rotation_deg is None
        assert flat.temp_c is None and flat.exposure_s is None

    def test_two_targets_at_the_same_settings_make_one_row(self):
        """A four-target pool night shot the same way needs one dark set, not
        four identical rows the operator has to read past."""
        rows = health_matrix([need(), need(), need()], [], now=NOW)
        assert len(rows) == len(KIND_ORDER)

    def test_the_matrix_is_deterministic(self):
        """The panel and the queue read the same endpoint; two answers would let
        them disagree with neither being wrong."""
        supply = frames("DARK", 14) + frames("FLAT", 20, filt="Ha")
        args = ([need()], supply)
        assert (health_matrix(*args, now=NOW) == health_matrix(*args, now=NOW))


class TestQuota:
    def test_the_quota_can_differ_per_kind(self):
        """The prototype's matrix banks bias 40 deep against 20 for darks — a
        zero-second frame is nearly free, so operators shoot more of them."""
        supply = frames("DARK", 20) + frames("BIAS", 20)
        rows = health_matrix([need()], supply, quota={"DARK": 20, "BIAS": 40},
                             kinds=["DARK", "BIAS"], now=NOW)
        assert row_of(rows, "DARK").verdict == "OK"
        assert row_of(rows, "BIAS").verdict == "STALE"

    def test_a_quota_of_zero_cannot_make_a_row_that_is_never_short(self):
        """'Skip' is expressed by dropping the KIND. A row that needs nothing
        would read OK forever, which is the same lie by another route."""
        row = row_of(health_matrix([need()], [], quota=0, kinds=["DARK"], now=NOW),
                     "DARK")
        assert row.need >= 1

    def test_skipping_a_kind_leaves_it_out_of_the_matrix(self):
        rows = health_matrix([need()], [], kinds=["DARK", "BIAS"], now=NOW)
        assert [r.kind for r in rows] == ["DARK", "BIAS"]


class TestTheScannerSeam:
    def test_frames_may_arrive_as_a_scanner_callable(self):
        """The route hands in a scanner so the capture-root walk happens once,
        inside, on the worker thread — and tests hand in a list, which is what
        makes this function testable without a filesystem at all."""
        calls: list[int] = []

        def scan():
            calls.append(1)
            return frames("DARK", 20)

        rows = health_matrix([need()], scan, kinds=["DARK"], now=NOW)
        assert row_of(rows, "DARK").verdict == "OK"
        assert len(calls) == 1              # walked once, not once per row

    def test_now_defaults_to_the_wall_clock(self):
        """The parameter exists for tests; the route does not have to pass one,
        and if the default were wrong every age verdict in production would be."""
        row = row_of(health_matrix([need()], frames("DARK", 20, ts=time.time()),
                                   kinds=["DARK"]), "DARK")
        assert row.age_days == pytest.approx(0.0, abs=0.01)


class TestHeaderBridge:
    def test_a_header_becomes_a_row_through_the_libraryS_own_key(self):
        """Identity comes from ``keys.key_from_header`` unchanged — the same
        call the master builder makes — so a frame lands in the row it will
        eventually be stacked into."""
        f = frame_from_header({"IMAGETYP": "Dark Frame", "EXPTIME": 300.0,
                               "GAIN": 100, "OFFSET": 30, "CCD-TEMP": -5.0,
                               "XBINNING": 1}, ts=NOW, path="a.fits")
        assert f.key == CalKey("DARK", 300.0, 100, 30, -5.0, 1, "")
        assert f.dark_ok is None and f.rotation_deg is None

    def test_a_light_frame_is_not_a_supply_row(self):
        light = {"IMAGETYP": "LIGHT", "EXPTIME": 300.0}
        assert frame_from_header(light, ts=NOW) is None
        assert frame_from_header({}, ts=NOW) is None

    def test_the_rotator_angle_comes_off_ROTATANG(self):
        """The card ``imaging.fitsio`` writes as 'Rotator sky PA (deg)'. It is
        the axis ``CalKey`` does not carry and the flats row turns on."""
        f = frame_from_header({"IMAGETYP": "FLAT", "FILTER": "Ha", "ROTATANG": 23.4},
                              ts=NOW)
        assert f.rotation_deg == pytest.approx(23.4)

    def test_an_unreadable_angle_is_unknown_rather_than_zero(self):
        """Zero is a real camera angle. Coercing a junk card to 0.0 would claim
        a rotation the frame never had and quietly match the wrong flats."""
        f = frame_from_header({"IMAGETYP": "FLAT", "ROTATANG": "n/a"}, ts=NOW)
        assert f.rotation_deg is None

    @pytest.mark.parametrize("card", [False, 0, "F", "false", "NO"])
    def test_the_dark_checks_refusal_is_read_as_the_builder_reads_it(self, card):
        """One reading of DARKOK in this codebase. A header hand-written by
        another tool carries 0 or 'F' rather than a FITS logical, and a second
        copy of this rule is how a contradicted frame starts counting as
        healthy."""
        f = frame_from_header({"IMAGETYP": "DARK", "DARKOK": card,
                               "DARKWHY": "median pinned at the ceiling"}, ts=NOW)
        assert f.dark_ok is False and f.why == "median pinned at the ceiling"

    def test_a_passing_verdict_is_distinguishable_from_no_verdict(self):
        judged = frame_from_header({"IMAGETYP": "DARK", "DARKOK": True}, ts=NOW)
        unjudged = frame_from_header({"IMAGETYP": "DARK"}, ts=NOW)
        assert judged.dark_ok is True and unjudged.dark_ok is None
