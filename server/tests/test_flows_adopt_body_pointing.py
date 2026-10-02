# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""ADOPT maps a moving body's step only if the old session pointed at the body
when its frames were taken (H3 orchestrator ruling 7 (spec, Still waiting on
the owner, item 16), #234; spec 5.9).

H2 (#229) matched a body on its canonical name and measured nothing, because
a body leaves ADOPT's 10 arcmin bound behind in days and measured against
tonight's position the bound refused every real pre-S1 session of one. That
made the NAME the only evidence again, at the old end: #190's wizard filed
Andromeda's frames as "M16", and a session filed as "Jupiter" at M31's
coordinates was adopted onto tonight's Jupiter, 104 degrees from anything it
shot.

So the bound applies to a body too, against the body's own ephemeris at the
instants its frames were taken. A session frame records no solve position, so
the evidence is the old plan's target coordinates, compared with
``tonight.resolve_target(name, when=t)``; ``t`` is the first and the last
frame of each night the step was shot on, ``created_ts`` for a step that holds
no frames, and a step with no usable time is left unmatched. A failed check
is listed with a reason that names the body and the worst separation.

#249's flows half is here too: ``adopt_evidence`` asks every catalogue
question ADOPT has, so the route can ask them off the event loop and outside
the store's write lock, and ``adopt_matches(evidence=)`` then asks none.

Every test names the mutation of ``flows/continuation.py`` (or of
``flows/tonight.py``) it guards and quotes the failure it produced, run from a
byte backup of the file and restored byte-identical after. The real-catalogue
tests print no coordinate, only separations: the process config has no site,
so every position here is geocentric.
"""
from __future__ import annotations

import math

import pytest

import astrodeck.flows.tonight as tonight_module
from astrodeck.flows import continuation
from astrodeck.flows.continuation import (ADOPT_MAX_SEPARATION_ARCMIN,
                                          AdoptEvidence, adopt_evidence,
                                          adopt_matches, apply_adoption)
from astrodeck.flows.tonight import NameResolution
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import Session, SessionFrame

#: 2026-09-10 04:00 UTC, the instant the ephemeris tests in this suite pin.
T0 = 1_789_012_800.0
HOUR = 3_600.0
DAY = 86_400.0

#: Where the old session pointed, and where tonight's Jupiter is: 120 arcmin
#: apart, so a body step can only map through its body key, never through
#: the bound measured against tonight.
POINT_RA, POINT_DEC = 5.0, 20.0
TONIGHT_RA, TONIGHT_DEC = 5.0, 22.0

#: M31's coordinates, which #190's wizard wrote under whatever name was typed.
M31_RA_H, M31_DEC = 0.712222, 41.269167


def _step(filt="L", exposure=60.0):
    return ExposureStep(filter=filt, exposure_s=exposure, count=5)


def _session(*steps_frames, name="Jupiter", ra=POINT_RA, dec=POINT_DEC,
             created_ts=T0):
    """A pre-S1 session: one target named ``name`` at (``ra`` h, ``dec``
    deg), uuid4 ids. Each argument is ``(step, [(night, ts), ...])``: one
    frame per pair, banked on that step under that night at that time."""
    steps = [st for st, _ in steps_frames]
    t = Target(name=name, ra_hours=ra, dec_deg=dec, steps=steps)
    s = Session(status="dormant", created_ts=created_ts,
                plan=SequencePlan(name="p", targets=[t]))
    for st, frames in steps_frames:
        s.frames.extend(SessionFrame(target_id=t.id, step_id=st.id,
                                     night=night, ts=ts)
                        for night, ts in frames)
    return s


def _plan(*steps, name="Jupiter", ra=TONIGHT_RA, dec=TONIGHT_DEC):
    return SequencePlan(targets=[Target(name=name, ra_hours=ra, dec_deg=dec,
                                        steps=list(steps))])


def _catalogue(off_arcmin=lambda when: 0.0, *, unplaced=()):
    """A catalogue that knows Jupiter, a moving body, and M16, a fixed row.

    At an instant ``when`` Jupiter sits ``off_arcmin(when)`` arcmin due north
    of the old pointing; at ``when=None`` (the identity question, and
    tonight) it is where tonight's plan puts it. An instant in ``unplaced``
    is one the ephemeris cannot place it at."""
    def resolve(name, when=None):
        key = str(name).strip().lower()
        if key == "m16":
            return NameResolution(ra_hours=18.3, dec_deg=-13.8,
                                  identity="M16", moves=False)
        if key != "jupiter" or (when is not None and when in unplaced):
            return None
        if when is None:
            return NameResolution(ra_hours=TONIGHT_RA, dec_deg=TONIGHT_DEC,
                                  identity="Jupiter", moves=True)
        return NameResolution(ra_hours=POINT_RA,
                              dec_deg=POINT_DEC + off_arcmin(when) / 60.0,
                              identity="Jupiter", moves=True)
    return resolve


def _drift(arcmin_per_hour):
    """Jupiter drifting north of the old pointing from T0 at a steady rate."""
    return lambda when: arcmin_per_hour * (when - T0) / HOUR


# ------------------------------------------ the two cases the ruling names

class TestTheRulingsTwoCases:
    """Against the shipped catalogue and its ephemeris: the one resolver
    ``to_plan`` points a name with (``tonight.resolve_target``) is the one
    ADOPT asks, unless a test hands in another, and ``api/app.py`` hands in
    none."""

    def test_a_jupiter_session_at_m31_stays_unmatched_and_says_why(self):
        """A pre-S1 session named "Jupiter" whose target sits at M31's
        coordinates, three L subs shot over two hours of one night. Tonight's
        flow names Jupiter too. The names are one body; the frames are
        Andromeda's. Unmatched, with a reason that names the body and the
        worst of the separations at the two sampled instants, and nothing is
        re-keyed.

        Mutant "no position check at the old end" (``_pointing`` answers
        every body step as on the body, as H2 had it) failed:
            AssertionError: assert {'857bcb6ef71...38380923592')} == {}
              Left contains 1 more item:
              {'857bcb6ef719478daafb21c65ad638cf': (
                  '9483d1d1a1064f6ca39d18762008a5b1',
                  'bab9065f5aea4d1c954a338380923592')}
        """
        old = _step("L")
        times = [T0, T0 + HOUR, T0 + 2 * HOUR]
        s = _session((old, [("n1", t) for t in times]), name="Jupiter",
                     ra=M31_RA_H, dec=M31_DEC)
        tonight = tonight_module.resolve_target("Jupiter", T0 + DAY)
        plan = _plan(_step("L"), name="Jupiter", ra=tonight.ra_hours,
                     dec=tonight.dec_deg)
        worst = max(continuation._separation_arcmin(
            s.plan.targets[0], tonight_module.resolve_target("Jupiter", t))
            for t in (times[0], times[-1]))
        far = worst > 100 * ADOPT_MAX_SEPARATION_ARCMIN
        assert far, "premise: M31 is nowhere near Jupiter"
        before = [(f.target_id, f.step_id) for f in s.frames]

        m = adopt_matches(s, plan)

        assert m.mapping == {}
        assert m.frames_matched == 0 and m.ambiguous == []
        entry, = m.unmatched
        assert entry["step_id"] == old.id and entry["frames"] == 3
        assert entry["separation_arcmin"] == pytest.approx(worst, abs=0.001)
        assert "Jupiter" in entry["reason"], entry["reason"]
        assert f"{worst:.1f} arcmin" in entry["reason"], entry["reason"]
        assert apply_adoption(s, m) == 0
        assert [(f.target_id, f.step_id) for f in s.frames] == before

    def test_a_real_mars_session_one_day_old_maps(self):
        """A pre-S1 session of Mars, compiled at T0 and shot for three hours,
        two filters. Tonight is a day later, and Mars has moved on (37
        arcmin at this date; a median 39 a day over two years). Each step's
        frames were taken within the bound of where Mars was then, so both
        map.

        Mutant "check against tonight's ephemeris instead of capture time"
        (the old target measured against the new plan's target, tonight's
        ephemeris as compiled, in place of ``_pointing``) failed:
            AssertionError: assert {} == {'25ed75872e7...ca7c9f87101')}
              Right contains 2 more items:
              {'25ed75872e794ad8af75c57c30af504e': (
                  '267aeea8e6d746bab9e82db4b1890074',
                  'c483acc4fb1647e694241d51d254b276'),
               'ecc7313eb0144474bb5986cfcdd12320': (
                  '267aeea8e6d746bab9e82db4b1890074',
                  '48357558e10f48b896cc3ca7c9f87101')}
        and so did the variant in which ``adopt_evidence`` asks
        ``resolve(name, when=None)`` (now, 2026-09-24, 14 days on) in place
        of each capture time:
            AssertionError: assert {} == {'61841778ac9...c50d43e2308')}
              Right contains 2 more items:
        """
        mars = tonight_module.resolve_target("Mars", T0)
        lum, red = _step("L"), _step("R")
        s = _session((lum, [("n1", T0 + 600.0), ("n1", T0 + HOUR)]),
                     (red, [("n1", T0 + 2 * HOUR), ("n1", T0 + 3 * HOUR)]),
                     name="Mars", ra=mars.ra_hours, dec=mars.dec_deg)
        tonight = tonight_module.resolve_target("Mars", T0 + DAY)
        plan = _plan(_step("L"), _step("R"), name="Mars",
                     ra=tonight.ra_hours, dec=tonight.dec_deg)
        moved = continuation._separation_arcmin(s.plan.targets[0],
                                                plan.targets[0])
        gone = moved > ADOPT_MAX_SEPARATION_ARCMIN
        assert gone, f"premise: Mars moved past the bound ({moved:.1f})"
        new_t = plan.targets[0]

        m = adopt_matches(s, plan)

        assert m.mapping == {lum.id: (new_t.id, new_t.steps[0].id),
                             red.id: (new_t.id, new_t.steps[1].id)}
        assert m.frames_matched == 4 and m.rest() == []


# ------------------------------------------------------ which instants

class TestTheInstantsChecked:
    def test_each_night_is_checked_at_its_last_frame_as_well(self):
        """Jupiter drifts 2 arcmin an hour off the pointing. A night shot
        from T0 to T0+6h ends 12 arcmin away: unmatched, worst 12.0. The
        same night cut at T0+4h ends 8 arcmin away and maps.

        Mutant "first frame of each night only" (``_capture_times`` keeps
        ``min(times)`` alone) failed:
            AssertionError: assert {'7484a1ce12a...c5372508977')} == {}
              Left contains 1 more item:
              {'7484a1ce12a4427e9d8d5c229375673e': (
                  '51a21181c97248b1809b16fae922a349',
                  '4729e09d364d42f89874dc5372508977')}
        """
        cat = _catalogue(_drift(2.0))
        far, near = _step("L"), _step("L")
        s = _session((far, [("n1", T0), ("n1", T0 + 3 * HOUR),
                            ("n1", T0 + 6 * HOUR)]))
        m = adopt_matches(s, _plan(_step("L")), resolve=cat)
        assert m.mapping == {}
        entry, = m.unmatched
        assert entry["separation_arcmin"] == pytest.approx(12.0, abs=1e-6)
        s2 = _session((near, [("n1", T0), ("n1", T0 + 4 * HOUR)]))
        assert list(adopt_matches(s2, _plan(_step("L")),
                                  resolve=cat).mapping) == [near.id]

    def test_every_night_is_checked_not_only_the_steps_first_and_last(self):
        """Three nights. Jupiter is on the pointing on the first and the
        third, and 30 arcmin off it on the second (a table, so the path can
        leave and come back, which a steady drift cannot). The second night
        holds frames that were not of it: unmatched, worst 30.0.

        Mutant "the step's first and last frame only" (``_capture_times``
        files every frame under one night, so it samples ``min`` and ``max``
        over the whole step) failed:
            AssertionError: assert {'dfbe421aee0...b94b76f3183')} == {}
              Left contains 1 more item:
              {'dfbe421aee064fb0acc833907cd35aa8': (
                  '69ce94ad48214b95bb401f6050e224db',
                  'f537de91e14b42809aa66b94b76f3183')}
        """
        n1, n2, n3 = T0, T0 + DAY, T0 + 2 * DAY
        off = {n1: 0.0, n1 + HOUR: 0.0, n2: 30.0, n2 + HOUR: 30.0,
               n3: 0.0, n3 + HOUR: 0.0}
        cat = _catalogue(lambda when: off[when])
        old = _step("L")
        s = _session((old, [("a", n1), ("a", n1 + HOUR), ("b", n2),
                            ("b", n2 + HOUR), ("c", n3), ("c", n3 + HOUR)]))
        m = adopt_matches(s, _plan(_step("L")), resolve=cat)
        assert m.mapping == {}
        entry, = m.unmatched
        assert entry["separation_arcmin"] == pytest.approx(30.0, abs=1e-6)

    def test_control_the_middle_of_a_night_is_not_asked(self):
        """Only a night's two ends are asked: frames at T0, T0+1h and T0+2h
        ask two instants, not three. The catalogue raises on the middle one,
        so asking it fails the test. (The endpoints suffice because the arc
        between them is short and near-geodesic, the comment on
        ``_capture_times`` says why.)

        Mutant "every frame is sampled" (``_capture_times`` adds every
        instant of a night, not its ``min`` and ``max``) failed:
            AssertionError: the middle of the night was asked
        """
        def cat(name, when=None):
            if when == T0 + HOUR:
                raise AssertionError("the middle of the night was asked")
            return _catalogue()(name, when)
        old = _step("L")
        s = _session((old, [("n1", T0), ("n1", T0 + HOUR),
                            ("n1", T0 + 2 * HOUR)]))
        assert list(adopt_matches(s, _plan(_step("L")),
                                  resolve=cat).mapping) == [old.id]

    def test_a_step_with_no_frames_is_checked_at_created_ts(self):
        """A frameless step has nothing to carry, but it still maps, so that
        ``plan_replace_report`` reads it as kept. It is checked at the
        session's ``created_ts``: on the body then, it maps; 30 arcmin off,
        it does not, and is not listed (it holds no frames).

        Mutant "a frameless body step maps unchecked" (``_pointing`` returns
        on-the-body when the step holds no frames) failed:
            AssertionError: assert (['6046ae06701...64396131ca01'] == []
              Left contains one more item: '6046ae0670124152964464396131ca01'
        """
        cat = _catalogue(lambda when: 0.0 if when == T0 else 30.0)
        on, off = _step("L"), _step("L")
        s = _session((on, []), created_ts=T0)
        assert list(adopt_matches(s, _plan(_step("L")),
                                  resolve=cat).mapping) == [on.id]
        s2 = _session((off, []), created_ts=T0 + DAY)
        m = adopt_matches(s2, _plan(_step("L")), resolve=cat)
        assert list(m.mapping) == [] and m.rest() == []

    @pytest.mark.parametrize("stamp", [0.0, -5.0, math.nan, math.inf])
    def test_a_step_with_no_usable_time_is_unmatched(self, stamp):
        """Frames with no capture time (never stamped, or a stamp that is not
        an instant) say nothing about where the body was, so the step is
        listed, with a reason naming the body, and not mapped. The
        session's ``created_ts`` is NOT used in their place: it is when the
        session was made, not when these frames were taken. The catalogue
        here puts Jupiter on the pointing at every instant, so only the
        missing time can refuse.

        Mutant "an unstamped frame's ts used as it is" (``_usable`` returns
        any float) failed every row, the 0.0 one shown (the catalogue here
        answers any instant, NaN and infinity included):
            AssertionError: assert {'52914fbc279...45e6032cf99')} == {}
              Left contains 1 more item:
              {'52914fbc279a4bf8a72b1570c2d920f7': (
                  '343f38e280114783aef1773d04db8389',
                  '1dbd7e905ab44bac9fde245e6032cf99')}
        Mutant "frames with no time fall back to created_ts"
        (``_capture_times`` answers ``created_ts`` when no frame has a
        usable instant) failed every row, the NaN one shown:
            AssertionError: assert {'c1f49aedc7a...d6fd9dbb65b')} == {}
              Left contains 1 more item:
              {'c1f49aedc7a0460b98f88935b5746d57': (
                  'ae5fea8e5e334b67a844c69ca7806f91',
                  'e8c21a255a424ef1bc41ed6fd9dbb65b')}
        """
        old = _step("L")
        s = _session((old, [("n1", stamp), ("n1", stamp)]), created_ts=T0)
        m = adopt_matches(s, _plan(_step("L")), resolve=_catalogue())
        assert m.mapping == {}
        entry, = m.unmatched
        assert entry["frames"] == 2 and entry["separation_arcmin"] is None
        assert "Jupiter" in entry["reason"], entry["reason"]
        assert "no capture time" in entry["reason"], entry["reason"]

    def test_a_body_the_catalogue_cannot_place_then_is_unmatched(self):
        """The ephemeris cannot place Jupiter at the night's last frame. That
        instant is unproven, so the step is listed with a reason naming the
        body, and not mapped.

        Mutant "an unplaced instant is skipped" (``_pointing`` passes over a
        None answer) failed:
            AssertionError: assert {'96b47735840...6008389a677')} == {}
              Left contains 1 more item:
              {'96b4773584064df7bb8bf12fbbd47b8e': (
                  '3f552690ca74486c99258dd60f2774c9',
                  'a3b5bb437c2647579f7776008389a677')}
        """
        cat = _catalogue(unplaced={T0 + HOUR})
        old = _step("L")
        s = _session((old, [("n1", T0), ("n1", T0 + HOUR)]))
        m = adopt_matches(s, _plan(_step("L")), resolve=cat)
        assert m.mapping == {}
        entry, = m.unmatched
        assert "Jupiter" in entry["reason"], entry["reason"]
        assert "could not place" in entry["reason"], entry["reason"]
        assert entry["separation_arcmin"] is None

    def test_an_answer_that_names_another_object_proves_nothing(self):
        """The answer at a capture time must be the body itself. The shared
        resolver can name another object there: a satellite the shared
        instant cannot place is decided at the caller's instant, so a name
        that is the satellite now falls, at a capture time its element set
        cannot reach either, to the row ``IDENTITY_WHEN`` chose ("ISS" to
        the star Meissa, ``tonight._identity_row``). Here that other row
        sits exactly on the old pointing, so only its identity can refuse:
        the step is listed as unplaced, not measured against it. Control:
        the same session, answered by the body at both ends, maps.

        Mutant "no identity check" (``_pointing`` tests ``at is None``
        alone) failed:
            AssertionError: assert {'5a73ec42a49...9a630a703a8')} == {}
              Left contains 1 more item:
              {'5a73ec42a49641728a82b8dfdcce9942': (
                  '73f759e461084bcc9f7de0b832b1a82f',
                  '494064aa19f842de9ee269a630a703a8')}
        """
        def cat(name, when=None):
            hit = _catalogue()(name, when)
            if hit is not None and when == T0 + HOUR:
                return NameResolution(ra_hours=hit.ra_hours,
                                      dec_deg=hit.dec_deg,
                                      identity="Meissa", moves=False)
            return hit
        old = _step("L")
        s = _session((old, [("n1", T0), ("n1", T0 + HOUR)]))
        control = adopt_matches(s, _plan(_step("L")), resolve=_catalogue())
        assert list(control.mapping) == [old.id], control.unmatched
        m = adopt_matches(s, _plan(_step("L")), resolve=cat)
        assert m.mapping == {}
        entry, = m.unmatched
        assert "Jupiter" in entry["reason"], entry["reason"]
        assert "could not place" in entry["reason"], entry["reason"]
        assert entry["separation_arcmin"] is None


# ----------------------------------------------------------- the bound

#: A body exactly 10 arcmin due north of the pointing, on the equator: the
#: atan2 separation of the two is exactly 10.0 (``_separation_arcmin``).
EXACT_RA, EXACT_DEC = 5.0, 0.0


class TestTheBoundForABody:
    def _cat(self, dec):
        def resolve(name, when=None):
            if str(name).strip().lower() != "jupiter":
                return None
            if when is None:
                return NameResolution(ra_hours=TONIGHT_RA,
                                      dec_deg=TONIGHT_DEC,
                                      identity="Jupiter", moves=True)
            return NameResolution(ra_hours=EXACT_RA, dec_deg=dec,
                                  identity="Jupiter", moves=True)
        return resolve

    def test_a_body_exactly_ten_arcmin_off_maps(self):
        """Inclusive, as it is for a deep-sky field (spec 5.9).

        Mutant "'<=' to '<' for a body" (``worst <
        ADOPT_MAX_SEPARATION_ARCMIN`` in ``_pointing``) failed:
            AssertionError: assert [] == ['fbd428d6b8b...1b2a7cb911f6']
              Right contains one more item: 'fbd428d6b8b4424da23f1b2a7cb911f6'
        """
        old = _step("L")
        s = _session((old, [("n1", T0)]), ra=EXACT_RA, dec=EXACT_DEC)
        at = NameResolution(ra_hours=EXACT_RA, dec_deg=10.0 / 60.0,
                            identity="Jupiter", moves=True)
        apart = continuation._separation_arcmin(s.plan.targets[0], at)
        exact = apart == ADOPT_MAX_SEPARATION_ARCMIN == 10.0
        assert exact, f"premise: exactly 10.0 arcmin ({apart!r})"
        m = adopt_matches(s, _plan(_step("L")), resolve=self._cat(10 / 60.0))
        assert list(m.mapping) == [old.id]

    def test_control_a_body_a_hair_past_ten_arcmin_is_unmatched(self):
        """The first representable Dec past the exact pair is not the body.

        Mutant "no position check at the old end" failed here too:
            AssertionError: assert {'12629dfbcec...7d0682b86c9')} == {}
              Left contains 1 more item:
              {'12629dfbcec94770949e423b0e1b3fa0': (
                  'ea87533228fa46d6a7cee5c7fb69d2e8',
                  'bde1a0de5ad24a5f911537d0682b86c9')}
        """
        old = _step("L")
        s = _session((old, [("n1", T0)]), ra=EXACT_RA, dec=EXACT_DEC)
        dec, apart = 10.0 / 60.0, 10.0
        for _ in range(64):
            if apart > 10.0:
                break
            dec = math.nextafter(dec, math.inf)
            apart = continuation._separation_arcmin(
                s.plan.targets[0], NameResolution(
                    ra_hours=EXACT_RA, dec_deg=dec, identity="Jupiter",
                    moves=True))
        hair = 10.0 < apart < 10.0 + 1e-12
        assert hair, f"premise: a hair past 10.0 arcmin ({apart!r})"
        m = adopt_matches(s, _plan(_step("L")), resolve=self._cat(dec))
        assert m.mapping == {}


# ------------------------------------------- what stays as it was, and kinds

class TestControls:
    def test_control_a_deep_sky_step_needs_no_capture_time(self):
        """A deep-sky name keeps the name as typed and the 10 arcmin bound
        against tonight's target, exactly as before: frames with no capture
        time at all, 9.9 arcmin from tonight's M16, still map.

        Mutant "every step needs a capture time" (a fixed row's match
        refused too when ``_capture_times`` finds no instant) failed:
            AssertionError: assert [] == ['6f1b4a5d934...8e413f4880d1']
              Right contains one more item: '6f1b4a5d934b40dea3698e413f4880d1'
        and so did eight tests in ``test_flows_adopt_separation.py``, whose
        deep-sky frames carry no capture time.
        """
        old = _step("L")
        s = _session((old, [("", 0.0)]), name="M16", ra=18.3, dec=-13.8)
        plan = _plan(_step("L"), name="M16", ra=18.3,
                     dec=-13.8 + 9.9 / 60.0)
        m = adopt_matches(s, plan, resolve=_catalogue())
        assert list(m.mapping) == [old.id]

    def test_a_satellite_is_a_body(self, monkeypatch):
        """H3 orchestrator ruling 9 (spec, Still waiting on the owner, item
        18): a satellite is a moving body, so its step matches on its
        canonical name and is checked against its ephemeris at capture time.
        Through the real resolver over a stubbed search, so the row's KIND
        is what decides: on the satellite at T0 it maps, 120 arcmin from
        tonight's; ``MOVING_KINDS`` is unchanged.

        Mutant "satellite not a moving kind" (``MOVING_KINDS`` without
        ``"satellite"``, in ``tonight``) failed:
            AssertionError: [{'binning': 1, 'exposure_s': 60.0, 'filter': 'L',
            'frame_type': 'Light', ...}]
            assert [] == ['d683a410f74...4f83c375f33e']
              Right contains one more item: 'd683a410f7454570af624f83c375f33e'
        """
        from astrodeck.catalog import objects

        def search(query, limit=25, when=None, site_derived=True):
            dec = TONIGHT_DEC if when is None else POINT_DEC
            if str(query).strip().lower() != "iss":
                return objects.SearchResult(rows=[], notes=[])
            return objects.SearchResult(rows=[{
                "id": "ISS (ZARYA)", "kind": "satellite", "norad_id": 25544,
                "ra_hours": POINT_RA, "dec_deg": dec, "mag": None}],
                notes=[])
        monkeypatch.setattr(objects, "search", search)
        old = _step("L")
        s = _session((old, [("n1", T0)]), name="iss")
        m = adopt_matches(s, _plan(_step("L"), name="ISS"))
        assert list(m.mapping) == [old.id], m.unmatched
        assert tonight_module.MOVING_KINDS == frozenset(
            {"solar_system", "comet", "satellite"})


# ------------------------------------------------ the evidence (#249)

class TestTheEvidence:
    def _case(self):
        """A body step with frames on two nights, a frameless body step, and
        a deep-sky target, against tonight's "jupiter" (another case) and
        M16."""
        lum, red, deep = _step("L"), _step("R"), _step("Ha")
        s = _session((lum, [("n1", T0 + HOUR), ("n1", T0),
                            ("n2", T0 + DAY)]),
                     (red, []), name="Jupiter")
        m16 = Target(name="M16", ra_hours=18.3, dec_deg=-13.8, steps=[deep])
        s.plan.targets.append(m16)
        s.frames.append(SessionFrame(target_id=m16.id, step_id=deep.id,
                                     night="n1", ts=T0))
        plan = _plan(_step("L"), _step("R"), name="jupiter")
        plan.targets.append(Target(name="M16", ra_hours=18.3, dec_deg=-13.8,
                                   steps=[_step("Ha")]))
        return s, plan

    def test_the_evidence_holds_every_answer_adopt_needs(self):
        """Every distinct target name of both plans, and the body at each
        sampled instant of each body step (two per night, ``created_ts`` for
        the frameless one); nothing is sampled for a fixed row.

        Mutant "the new plan's names are not asked" (``adopt_evidence``
        resolves the old plan's names only) failed:
            AssertionError: assert ['Jupiter', 'M16'] == ['Jupiter', 'M16',
            'jupiter']
              Right contains one more item: 'jupiter'
        and four other tests, the next one and ``test_a_satellite_is_a_body``
        among them: a new name keyed as typed matches no body key.
        """
        s, plan = self._case()
        ev = adopt_evidence(s, plan, resolve=_catalogue())
        assert isinstance(ev, AdoptEvidence)
        assert sorted(ev.names) == ["Jupiter", "M16", "jupiter"]
        assert ev.names["jupiter"].identity == "Jupiter"
        assert sorted(ev.positions) == [("Jupiter", T0),
                                        ("Jupiter", T0 + HOUR),
                                        ("Jupiter", T0 + DAY)]

    def test_given_evidence_adopt_matches_asks_the_catalogue_nothing(
            self, monkeypatch):
        """With the evidence in hand, ``adopt_matches`` makes no catalogue
        call: the resolver handed in raises, and so does the module's own,
        and the answer is the one the evidence gives.

        Mutant "evidence ignored" (``adopt_matches`` builds its own evidence
        even when given one) failed:
            AssertionError: the catalogue was asked: Jupiter
        """
        s, plan = self._case()
        want = adopt_matches(s, plan, resolve=_catalogue())
        ev = adopt_evidence(s, plan, resolve=_catalogue())

        def raises(name, when=None):
            raise AssertionError(f"the catalogue was asked: {name}")
        monkeypatch.setattr(tonight_module, "resolve_target", raises)

        got = adopt_matches(s, plan, resolve=raises, evidence=ev)

        assert got == want
        assert sorted(got.mapping) == sorted([s.plan.targets[0].steps[0].id,
                                              s.plan.targets[0].steps[1].id,
                                              s.plan.targets[1].steps[0].id])

    def test_evidence_leaves_the_session_as_it_was(self):
        """``adopt_evidence`` reads the session and writes nothing to it, so
        the route can build it from the first read, outside the write lock
        and off the event loop. The frames are banked out of time order on
        purpose.

        Mutant "frames sorted in place" (``adopt_evidence`` sorts
        ``session.frames`` by ``ts`` before sampling) failed:
            assert '{"id":"6c3bb...h_resumes":0}' ==
            '{"id":"6c3bb...h_resumes":0}'
              Skipping 2492 identical leading characters in diff, use -v to
              show
        """
        s, plan = self._case()
        before = s.model_dump_json()
        plan_before = plan.model_dump_json()
        adopt_evidence(s, plan, resolve=_catalogue())
        assert s.model_dump_json() == before
        assert plan.model_dump_json() == plan_before

    def test_a_night_banked_after_the_evidence_is_unproven(self):
        """The route re-reads the session under the lock, and a run could
        have banked another night in between. That night has no answer in
        the evidence, so the step is listed as unplaced, not mapped and not
        asked of the catalogue.

        Mutant "an instant missing from the evidence is skipped"
        (``_pointing`` passes over an instant the evidence has no key for)
        failed:
            AssertionError: assert {'a2d7b176f89...897334286b6')} == {}
              Left contains 1 more item:
              {'a2d7b176f8914ab6983c95acb5edb232': (
                  '4d75c74355114b9d90d4e368f7a5aaca',
                  'a3939cd614464bb2bd209897334286b6')}
        Mutant "evidence ignored" failed here too, the raising resolver
        asked, and "an unplaced instant is skipped" mapped the step.
        """
        old = _step("L")
        s = _session((old, [("n1", T0)]))
        ev = adopt_evidence(s, _plan(_step("L")), resolve=_catalogue())
        s.frames.append(SessionFrame(target_id=s.plan.targets[0].id,
                                     step_id=old.id, night="n2",
                                     ts=T0 + DAY))

        def raises(name, when=None):
            raise AssertionError(f"the catalogue was asked: {name}")
        m = adopt_matches(s, _plan(_step("L")), resolve=raises, evidence=ev)
        assert m.mapping == {}
        entry, = m.unmatched
        assert "could not place" in entry["reason"], entry["reason"]

    def test_control_without_evidence_it_asks_the_shared_resolver(
            self, monkeypatch):
        """With no evidence and no resolver, ``adopt_matches`` asks
        ``tonight.resolve_target`` itself, as ``api/app.py`` calls it until
        the route builds the evidence off the lock (T8): the same answer as
        through the evidence.

        Mutant "no evidence means no position check" (``adopt_matches``
        called without evidence passes every body step) failed:
            AssertionError: assert {'5c039fe7ad1...37d25bbae31')} == {}
              Left contains 1 more item:
              {'5c039fe7ad144ed5a693e1206611b234': (
                  'b4b818b141a7450e9f7f8d2c86185ba2',
                  'fb0c933f24154255a916337d25bbae31')}
        """
        monkeypatch.setattr(tonight_module, "resolve_target",
                            _catalogue(_drift(20.0)))
        old = _step("L")
        s = _session((old, [("n1", T0), ("n1", T0 + HOUR)]))
        m = adopt_matches(s, _plan(_step("L")))
        assert m.mapping == {}
        entry, = m.unmatched
        assert entry["separation_arcmin"] == pytest.approx(20.0, abs=1e-6)
