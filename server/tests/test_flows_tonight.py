"""Tonight, resolved — the numbers behind the Tonight panel.

The prototype's ``tonight()`` returns invented times: dusk at 20:41 every night,
moonrise at 23:37, "banked" integration hard-coded as 35% of the goal. The
README says production must compute them from ``catalog/visibility.py`` and
``sequence/schedule.py`` instead, so what these tests guard is mostly the
difference between a measurement and a plausible number:

* a night that CANNOT be resolved (no site, polar day) returns no times at all,
  because an operator who plans an evening around a fabricated dusk finds out at
  the scope;
* the two modules must describe ONE night — ``observing_night``'s dusk/dawn and
  ``compute_night``'s astronomical dark are different boundaries of the same
  darkness, and a date-derivation slip would put them 24 h apart with both
  looking entirely reasonable;
* the answer depends on the ``now`` it was given and not on when the test ran.

Site and instant are pinned: 40°N 105°W (a real mid-latitude site, NOT the
observatory's) and two fixed UTC instants. UTC, not ``time.mktime``, so the
answers do not move with the machine's timezone.
"""
from __future__ import annotations

import datetime as _dt
import re

import pytest

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.tonight import (banked_hours_from_reports, catalog_coords,
                                     resolve_tonight)

SITE = {"latitude": 40.0, "longitude": -105.0, "elevation_m": 1600.0,
        "is_default": False}
DEFAULT_SITE = {"latitude": 0.0, "longitude": 0.0, "is_default": True}
#: Far enough north that mid-June has no astronomical (or nautical) night.
POLAR_SITE = {"latitude": 78.2, "longitude": 15.6, "is_default": False}
TWILIGHT = -12.0

#: Late afternoon at the site (13:00 local solar), so "tonight" is ahead.
JUNE = _dt.datetime(2026, 6, 15, 20, 0, tzinfo=_dt.timezone.utc).timestamp()
DECEMBER = _dt.datetime(2026, 12, 15, 20, 0, tzinfo=_dt.timezone.utc).timestamp()

#: A dependable summer target from 40°N, typed the way the TARGET node types it.
M16 = {"name": "M16 — Eagle", "ra": "18h 18m 48s", "dec": "−13° 49′ 00″"}


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _flow(target=None, *, exposure=180, count=20, goal=12, filt="Ha",
          offset=-30, stop="Dawn", extra=(), edges=()):
    """A one-target night: dusk window → target → slew → capture."""
    t = dict(M16 if target is None else target)
    nodes = [_n("d", "dusk", x=0, offset=offset, stop=stop, minAlt=30),
             _n("t", "target", x=100, **t),
             _n("s", "slew", x=200),
             _n("c", "capture", x=300, filter=filt, exposure=exposure,
                gain=100, bin="1", count=count, goal=goal),
             *extra]
    return FlowGraph(nodes=nodes,
                     edges=[_e("d", "window", "t", "arm"),
                            _e("t", "target", "s", "run"),
                            _e("s", "centered", "c", "run"), *edges])


def _tonight(graph=None, site=SITE, now=JUNE, **kw):
    kw.setdefault("twilight_deg", TWILIGHT)
    return resolve_tonight(_flow() if graph is None else graph, site,
                           now=now, **kw)


def _placed(ra_h, dec_d):
    """A name resolver that always places a target, so a test does not depend
    on which objects the shipped catalogue carries."""
    return lambda name: (ra_h, dec_d)


# --------------------------------------------------------------- cannot compute

class TestWhenThereIsNoNight:
    def test_an_unset_site_gets_no_times_at_all(self):
        """A site still on its defaults means we do not know where the observer
        is. Quoting 40°N's dusk to somebody in Chile is worse than a blank
        panel, because they would plan the evening around it."""
        out = _tonight(site=DEFAULT_SITE)
        assert out["ok"] is False
        assert out["night"] is None and out["moon"] is None
        assert out["targets"] == [] and out["budget"] == []
        assert "site" in out["reason"].lower()

    def test_polar_day_says_so_rather_than_inventing_a_dusk(self):
        """78°N in June: the sun never crosses −12°, so ``observing_night``
        returns None and there is genuinely no dusk to draw."""
        out = _tonight(site=POLAR_SITE)
        assert out["ok"] is False
        assert out["night"] is None
        assert "−12" in out["reason"], out["reason"]

    def test_unreadable_coordinates_are_refused_not_rounded_to_zero(self):
        """``float("north")`` raising is the good case. The bad one is a site
        that silently reads as 0/0 — the Gulf of Guinea — and answers with a
        night that is real for somebody, just not for this rig."""
        out = _tonight(site={"latitude": "north", "longitude": None,
                             "is_default": False})
        assert out["ok"] is False and out["night"] is None

    def test_the_failure_still_gives_the_panel_a_sentence(self):
        """The STORY tab has to render something. A blank list would leave the
        operator looking at an empty panel with no idea it failed closed."""
        out = _tonight(site=DEFAULT_SITE)
        assert len(out["story"]) == 1
        assert out["story"][0]["msg"] == out["reason"]
        assert out["story"][0]["t_unix"] is None


# ----------------------------------------------------------------- the night

class TestTheNight:
    def test_dusk_opens_before_dawn(self):
        out = _tonight()
        n = out["night"]
        assert out["ok"] is True
        assert n["dusk_unix"] < n["dawn_unix"]
        assert 0 < (n["dawn_unix"] - n["dusk_unix"]) < 16 * 3600

    def test_astronomical_dark_sits_inside_the_same_night(self):
        """THE CROSS-MODULE CHECK. dusk/dawn come from ``schedule`` and the dark
        window from ``visibility``, which anchors on a DATE — and if that date
        is derived wrongly the two describe nights 24 h apart while both look
        perfectly reasonable on their own."""
        n = _tonight()["night"]
        assert n["darkness_kind"] == "astronomical"
        assert n["dusk_unix"] < n["dark_start_unix"] < n["dark_end_unix"] < n["dawn_unix"]

    def test_the_window_carries_the_flows_own_offset(self):
        """The DUSK WINDOW node's −30 min is the difference between the engine
        starting to slew at dusk and starting half an hour before it."""
        out = _tonight(_flow(offset=-30))
        n = out["night"]
        assert n["window_start_unix"] == pytest.approx(n["dusk_unix"] - 1800)

    def test_stop_none_leaves_the_window_open(self):
        """A flow that does not stop at dawn must not be drawn as if it does —
        that is the operator's own choice to run past twilight."""
        assert _tonight(_flow(stop="None"))["night"]["window_stop_unix"] is None
        assert _tonight(_flow(stop="Dawn"))["night"]["window_stop_unix"] is not None

    def test_a_deeper_twilight_shortens_the_night(self):
        """The imaging twilight is the operator's setting, not a constant: a
        narrowband rig in a city and a Bortle 2 rig do not agree about when the
        night starts, and the story quotes the angle it used."""
        shallow = _tonight(twilight_deg=-6.0)["night"]
        deep = _tonight(twilight_deg=-18.0)["night"]
        assert (deep["dawn_unix"] - deep["dusk_unix"]) < \
               (shallow["dawn_unix"] - shallow["dusk_unix"])
        assert "−18" in _tonight(twilight_deg=-18.0)["story"][0]["msg"]


class TestItDependsOnTheInstantItIsGiven:
    def test_two_calls_with_the_same_now_agree_exactly(self):
        """The README makes the timeline "a rendering of the compile, never a
        separate truth". A timeline that shifted between renders would be a
        third truth, and neither render would be wrong."""
        assert _tonight() == _tonight()

    def test_a_december_call_gets_decembers_night(self):
        """The one shape of hidden clock read this can have: ``compute_night``
        reads ``time.time()`` when it is given no date, so a resolver that
        forgot to pin the date would answer for the night it RAN on and quietly
        ignore its own ``now`` — including for anyone rendering a past night."""
        june, dec = _tonight(now=JUNE)["night"], _tonight(now=DECEMBER)["night"]
        assert june["dusk_unix"] != dec["dusk_unix"]
        # a December night at 40°N is hours longer than a June one
        assert (dec["dawn_unix"] - dec["dusk_unix"]) > \
               (june["dawn_unix"] - june["dusk_unix"]) + 2 * 3600

    def test_the_night_is_the_same_whether_asked_before_or_during_it(self):
        """``observing_night`` anchors to the current-or-imminent night, so an
        operator who opens the panel at 23:00 must see the night they are in,
        not tomorrow's."""
        before = _tonight(now=JUNE)["night"]
        during = _tonight(now=before["dark_start_unix"] + 3600)["night"]
        assert during["dusk_unix"] == pytest.approx(before["dusk_unix"], abs=1.0)


# ---------------------------------------------------------------- the targets

class TestTargets:
    def test_a_typed_position_is_used_verbatim(self):
        """Typed coordinates beat a name lookup: they are what the operator put
        in the node, and a catalogue hit for a similar name would image
        somewhere else entirely."""
        t = _tonight(_flow(), resolve_name=_placed(0.0, 0.0))["targets"][0]
        assert t["coords_from"] == "node"
        assert t["ra_hours"] == pytest.approx(18.313, abs=0.01)
        assert t["dec_deg"] == pytest.approx(-13.817, abs=0.01)

    def test_the_nodes_own_default_coordinates_parse(self):
        """REGRESSION. The TARGET node ships ``+41° 16′ 09″`` with typographic
        primes, and ``coords.parse_dec`` strips only ASCII ones — so it fell
        through to ``float()`` and raised on the vocabulary's OWN default. The
        typographic minus is the same trap with a worse ending: sniffing the
        sign with ``startswith("-")`` would have read −13° as +13°, which is a
        target 27° from where the operator typed it."""
        m31 = {"name": "M31 — Andromeda", "ra": "00h 42m 44s",
               "dec": "+41° 16′ 09″"}
        t = _tonight(_flow(m31))["targets"][0]
        assert t["coords_from"] == "node"
        assert t["dec_deg"] == pytest.approx(41.269, abs=0.01)
        neg = _tonight(_flow(M16))["targets"][0]
        assert neg["dec_deg"] < 0, "the typographic minus was read as a plus"

    def test_a_pool_member_is_placed_by_name(self):
        """A TARGET POOL compiles to names and constraints and nothing else, so
        without a lookup the best-of-four night has four candidates and not one
        altitude curve to choose between them."""
        g = FlowGraph(nodes=[_n("d", "dusk", x=0),
                             _n("p", "pool", x=100, members="M16, M17"),
                             _n("c", "capture", x=200, exposure=180, count=20)],
                      edges=[_e("d", "window", "p", "arm"),
                             _e("p", "target", "c", "run")])
        out = _tonight(g, resolve_name=_placed(18.3, -13.8))
        assert [t["label"] for t in out["targets"]] == ["M16", "M17"]
        assert all(t["coords_from"] == "catalog" for t in out["targets"])
        assert all(t["window"] for t in out["targets"])

    def test_the_shipped_catalogue_places_a_messier_number(self):
        """The default resolver, exercised once: it is local static data, so it
        stays deterministic, but a rename in ``catalog.objects.search`` would
        silently leave every pool night curveless."""
        assert catalog_coords("M16", JUNE)[0] == pytest.approx(18.31, abs=0.05)

    def test_a_target_nobody_can_place_is_listed_and_marked(self):
        """Listed, because a silently-dropped target is how a night comes up
        short; marked, because there is nothing to draw for it."""
        g = FlowGraph(nodes=[_n("d", "dusk", x=0),
                             _n("p", "pool", x=100, members="Bob's Nebula"),
                             _n("c", "capture", x=200)],
                      edges=[_e("d", "window", "p", "arm"),
                             _e("p", "target", "c", "run")])
        out = _tonight(g, resolve_name=lambda name: None)
        t = out["targets"][0]
        assert t["resolved"] is False
        assert t["ra_hours"] is None and t["curve"] == [] and t["window"] is None
        assert any("No coordinates for" in s["msg"] for s in out["story"])

    def test_the_altitude_curve_stays_inside_the_night(self):
        """The curve is what the timeline draws inside its axis. Samples from
        the daylight hours either side would run off the panel or squash the
        arc into the corner."""
        out = _tonight()
        n, curve = out["night"], out["targets"][0]["curve"]
        assert len(curve) > 10
        assert all(n["dusk_unix"] <= t <= n["dawn_unix"] for t, _alt in curve)
        assert max(a for _t, a in curve) == pytest.approx(
            out["targets"][0]["transit_alt"], abs=1.0)

    def test_a_target_that_never_clears_its_floor_gets_no_window(self):
        """From 40°N a −75° declination is never above the horizon at all. The
        honest answer is no window and a sentence saying so — not a window at
        the least-bad hour."""
        deep_south = {"name": "Nothing doing", "ra": "18h 00m 00s",
                      "dec": "−75° 00′ 00″"}
        out = _tonight(_flow(deep_south))
        t = out["targets"][0]
        assert t["window"] is None and t["never_rises"] is True
        assert any("never clears" in s["msg"] for s in out["story"])


class TestTheMeridian:
    def test_a_crossing_during_the_night_lands_on_the_transit(self):
        """The flip tick and the flip the mount performs must come from one
        piece of arithmetic — ``hours_to_meridian_flip``, which the engine and
        the hub already share. The crossing IS the transit, so a sign slip
        would put the tick on the wrong side of the night."""
        out = _tonight()
        t = out["targets"][0]
        flip = t["meridian_flip_unix"]
        assert flip is not None
        assert out["night"]["dusk_unix"] <= flip <= out["night"]["dawn_unix"]
        assert flip == pytest.approx(t["transit_unix"], abs=900)

    def test_a_target_that_already_crossed_gets_no_flip_time(self):
        """At this instant the sidereal time has passed RA 4h, so that crossing
        happened hours ago and the next one is a sidereal day away — not
        tonight. No tick beats a tick at the wrong hour."""
        already = {"name": "Past it", "ra": "04h 00m 00s", "dec": "+30° 00′ 00″"}
        assert _tonight(_flow(already))["targets"][0]["meridian_flip_unix"] is None


class TestTheMoon:
    def test_the_shared_block_carries_no_separation(self):
        """Illumination, phase and rise/set are the same for every target;
        separation is not. A separation in the shared block would silently be
        the FIRST target's, quoted for all of them."""
        out = _tonight()
        assert "separation_deg" not in out["moon"]
        assert 0.0 <= out["moon"]["illumination"] <= 1.0
        assert out["moon"]["phase_name"]

    def test_each_target_carries_its_own_separation(self):
        g = FlowGraph(nodes=[_n("d", "dusk", x=0),
                             _n("t", "target", x=100, **M16),
                             _n("u", "target", x=200, name="M31",
                                ra="00h 42m 44s", dec="+41° 16′ 09″"),
                             _n("c", "capture", x=300)],
                      edges=[_e("d", "window", "t", "arm"),
                             _e("t", "target", "u", "arm"),
                             _e("u", "target", "c", "run")])
        a, b = _tonight(g)["targets"]
        assert a["moon_sep_deg"] != b["moon_sep_deg"]


# ---------------------------------------------------------------- the ledger

class TestTheIntegrationLedger:
    def test_no_ledger_says_so_instead_of_claiming_nothing_is_banked(self):
        """The prototype fakes this as 35% of the goal. Zero would be the same
        lie with a straighter face: "0 h banked" is a claim about the archive,
        and "we did not read the archive" is not."""
        out = _tonight()
        row = out["budget"][0]
        assert row["has_ledger"] is False and row["banked_h"] is None
        assert row["goal_h"] == 12 and row["tonight_h"] == pytest.approx(1.0)
        msg = next(s["msg"] for s in out["story"] if s["label"] == "BUDGET")
        assert "No session ledger" in msg

    def test_an_injected_ledger_reaches_the_row_and_the_sentence(self):
        out = _tonight(banked=lambda: {"Ha": 4.2})
        row = out["budget"][0]
        assert row["has_ledger"] is True and row["banked_h"] == 4.2
        msg = next(s["msg"] for s in out["story"] if s["label"] == "BUDGET")
        assert "4.2 h banked / 12 h goal" in msg

    def test_a_ledger_that_cannot_be_read_degrades_to_no_ledger(self):
        """``captures/reports`` living on the same SD card as everything else,
        one bad read must not blank the whole timeline — and must not report
        0 h banked either."""
        def broken():
            raise OSError("captures/reports: I/O error")
        row = _tonight(banked=broken)["budget"][0]
        assert row["has_ledger"] is False and row["banked_h"] is None

    def test_a_pool_of_four_does_not_promise_four_nights_of_integration(self):
        """``compile_plan`` copies the capture step onto EVERY target, so four
        pool candidates carry four copies of one Ha loop — and exactly one of
        them is shot on any night, which is what "best available" means. Summing
        per target would promise 4 h for a 1 h night, which is the direction of
        error that costs a project a week."""
        g = FlowGraph(
            nodes=[_n("d", "dusk", x=0),
                   _n("p", "pool", x=100, members="M16, M17, M8, NGC 6946"),
                   _n("c", "capture", x=200, filter="Ha", exposure=180,
                      gain=100, bin="1", count=20, goal=12)],
            edges=[_e("d", "window", "p", "arm"), _e("p", "target", "c", "run")])
        budget = _tonight(g, resolve_name=_placed(18.3, -13.8))["budget"]
        assert len(budget) == 1
        assert budget[0]["tonight_h"] == pytest.approx(1.0)

    def test_a_goal_of_zero_gets_no_row_at_all(self):
        """0 means "no goal" — the EAA example sets it deliberately. A row
        reading "0 h goal" would be an unmeetable target, not an absent one."""
        assert _tonight(_flow(goal=0))["budget"] == []


class TestBankedHoursFromReports:
    def test_it_sums_one_filter_across_nights(self):
        """The whole point of the ledger: "the session ledger resumes the
        remainder next clear night" is only true if last night counted."""
        got = banked_hours_from_reports([
            {"by_filter": [{"filter": "Ha", "integration_s": 3600},
                           {"filter": "L", "integration_s": 1800}]},
            {"by_filter": [{"filter": "Ha", "integration_s": 5400}]}])
        assert got == {"Ha": 2.5, "L": 0.5}

    def test_it_reads_a_report_model_as_well_as_a_dict(self):
        """``list_reports`` hands back dicts and ``load`` hands back a model;
        the route should not have to care which it had."""
        from astrodeck.sequence.report import FilterBreakdown, SessionReport
        rep = SessionReport(id="x", by_filter=[
            FilterBreakdown(filter="Oiii", frames=10, integration_s=7200)])
        assert banked_hours_from_reports([rep]) == {"Oiii": 2.0}

    def test_an_empty_archive_is_an_empty_mapping(self):
        assert banked_hours_from_reports([]) == {}

    def test_naming_targets_counts_only_their_hours(self):
        """TODO(flows-handoff) in the source: the default counts every Ha hour
        in the archive whatever it was pointed at, which fills M31's bar with
        M16's frames for anyone running two narrowband projects. This is the
        other reading, available for when the endpoint picks one."""
        archive = [{"by_filter": [{"filter": "Ha", "integration_s": 7200}],
                    "targets": [
                        {"name": "M16", "by_filter": [
                            {"filter": "Ha", "integration_s": 3600}]},
                        {"name": "M31", "by_filter": [
                            {"filter": "Ha", "integration_s": 3600}]}]}]
        assert banked_hours_from_reports(archive) == {"Ha": 2.0}
        assert banked_hours_from_reports(archive, ["M16"]) == {"Ha": 1.0}


# ----------------------------------------------------------------- dusk flats

class TestDuskFlats:
    def test_the_window_resolves_to_real_sun_crossings(self):
        """"Sun −2° … −8°" is display text on the node, and the flats block has
        to sit at the hour the sun is actually there — before dusk, while the
        sky is still bright."""
        g = _flow(extra=[_n("f", "duskflats", x=50, window="Sun −2° … −8°",
                            adu=28500, count=15,
                            method="Translucent lens cap")],
                  edges=[_e("d", "window", "f", "run")])
        out = _tonight(g)
        flats = out["flats"]
        assert flats["start_unix"] < flats["end_unix"] < out["night"]["dusk_unix"]
        assert 10 * 60 < (flats["end_unix"] - flats["start_unix"]) < 90 * 60
        assert any("Flats window" in s["msg"] for s in out["story"])

    def test_a_window_that_names_no_angles_is_left_unresolved(self):
        """A block drawn at a guessed hour is worse than no block: the operator
        plans the evening around it. Say the window could not be read."""
        g = _flow(extra=[_n("f", "duskflats", x=50, window="After sunset",
                            adu=28500)],
                  edges=[_e("d", "window", "f", "run")])
        out = _tonight(g)
        assert out["flats"]["start_unix"] is None
        assert any("does not name two sun altitudes" in s["msg"]
                   for s in out["story"])


# ------------------------------------------------------------------ the story

class TestTheStory:
    def test_it_never_formats_a_clock(self):
        """#228: one forecast, printed in UTC beside log stamps rendered in
        local time, read as a different night. The operator's timezone is the
        browser's, so a row carries ``t_unix`` and the panel formats it — a
        server that formats has already replaced their timezone with its own."""
        for row in _tonight()["story"]:
            assert not re.search(r"\d{1,2}:\d{2}", row["msg"]), row["msg"]
            assert row["t_unix"] is not None or row["label"] in {"ANY", "BUDGET"}

    def test_dawn_closes_the_night_last(self):
        story = _tonight()["story"]
        assert story[-1]["msg"].startswith("Dawn:")
        assert story[0]["msg"].startswith("Autorun window opens")

    def test_the_rules_come_from_the_compiled_instructions_not_the_canvas(self):
        """A CLOUD WATCH sitting unwired on the canvas fires nothing, so the
        story must not promise a cloud-dodge. What runs tonight is what
        compiled — the timeline is a rendering of that, per the README."""
        unwired = _tonight(_flow(extra=[_n("cw", "cloudwatch", x=400)]))
        assert not any(r["label"] == "ANY" for r in unwired["story"])
        wired = _tonight(_flow(
            extra=[_n("cw", "cloudwatch", x=400), _n("h", "holdresume", x=500)],
            edges=[_e("cw", "in", "h", "pause")]))
        assert any("IF clouds in" in r["msg"] for r in wired["story"])

    def test_a_flow_with_no_dusk_window_says_so(self):
        """The EAA example has no dusk node — it starts when the operator
        presses RUN. Inventing a window for it would put a start time on a run
        that has none."""
        g = FlowGraph(nodes=[_n("t", "target", **M16), _n("c", "capture", x=100)],
                      edges=[_e("t", "target", "c", "run")])
        first = _tonight(g)["story"][0]
        assert "No dusk window" in first["msg"]
        assert _tonight(g)["night"]["window_start_unix"] == JUNE

    def test_the_report_clause_is_only_claimed_when_the_graph_says_so(self):
        """A SESSION REPORT sink compiles to nothing, so a plan on its own
        cannot know whether the night leaves a ledger. Omitting the clause is
        honest; asserting it is not."""
        g = _flow(extra=[_n("r", "report", x=400)],
                  edges=[_e("c", "complete", "r", "session")])
        from_graph = _tonight(g)["story"][-1]["msg"]
        from_plan = resolve_tonight(compile_plan(g, "n"), SITE, now=JUNE,
                                    twilight_deg=TWILIGHT)["story"][-1]["msg"]
        assert "session report appended" in from_graph
        assert "session report appended" not in from_plan

    def test_a_dome_closes_at_dawn_and_says_it_ignores_the_flow(self):
        """Rule 9's other half: the shutter closes on unsafe whatever the flow
        is doing, and the brief has to say so where the operator reads it."""
        g = _flow(extra=[_n("m", "dome", x=50)],
                  edges=[_e("d", "window", "m", "run")])
        story = _tonight(g)["story"]
        assert any("whatever the flow is doing" in s["msg"] for s in story)
        assert "dome closes" in story[-1]["msg"]


class TestTheGraphAndThePlanAgree:
    def test_a_graph_is_compiled_rather_than_read_directly(self):
        """The README: the timeline is a rendering of the compile. Handing this
        function a graph must therefore go through ``compile_plan`` — otherwise
        a caller could render a night the run would not run."""
        g = _flow()
        from_graph = _tonight(g)
        from_plan = resolve_tonight(compile_plan(g, "n"), SITE, now=JUNE,
                                    twilight_deg=TWILIGHT)
        assert from_graph["night"] == from_plan["night"]
        assert from_graph["targets"] == from_plan["targets"]
        assert from_graph["budget"] == from_plan["budget"]
