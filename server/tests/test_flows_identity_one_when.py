"""A name's IDENTITY is resolved at one shared instant; its COORDINATES at the
caller's own (#249, flows half; spec 3.3).

``catalog.objects.search`` sorts its hits by (rank, magnitude, id), and a
body's magnitude is computed for the search's ``when``. So when a body and a
fixed row share a name's best rank, which one comes first can swap between two
instants. ``to_plan._coords`` asked at the compile's ``when``,
``progress._single`` at now, and ADOPT at now, so one TARGET could be two
objects: the card read "nothing banked" against a live ledger, and a second
night's compile minted new step ids and restarted the campaign.

The fix is in the one resolver all three ask, ``tonight.resolve_target``: the
row is the first hit at ``tonight.IDENTITY_WHEN``, a named instant, and its
coordinates are that row's at the caller's ``when``. A named instant rather
than a time-free tie-break, because the search does not return its ranks; a
fixed instant must still place every kind of row, which is why
``test_a_comet_and_a_satellite_resolve_at_the_shared_instant`` checks the
shipped comet and satellite code at it, and why a moving row the instant
cannot place is decided at the caller's instant instead.

Most tests stub ``catalog.objects.search``, whose order here swaps on
purpose, because the shipped catalogue offers no name whose tie swaps on a
known date. Every test names the mutation of ``flows/tonight.py`` it guards
and quotes the failure it produced, run from a byte backup of the file and
restored byte-identical after.
"""
from __future__ import annotations

import pytest

import astrodeck.flows.tonight as tonight_module
from astrodeck.catalog import objects
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.continuation import adopt_evidence
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.progress import flow_progress
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.tonight import IDENTITY_WHEN
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import Session, SessionFrame

FLOW = "flow-one-when"
#: Two compile instants a day apart, neither of them the shared one.
DAY_ONE = 1_790_000_000.0
DAY_TWO = DAY_ONE + 86_400.0

#: The two rows the name "Tie" finds at one rank: a comet whose place and
#: magnitude move, and a fixed deep-sky row.
COMET, FIXED = "C/2026 T1 (Tie)", "NGC 9999"

#: Which of the two comes first at each instant the stub is asked at: the
#: comet at the shared instant and on day one, the fixed row on day two and
#: at now (``when=None``). Any other instant is a KeyError, so a caller that
#: asks somewhere unexpected fails loudly.
FIRST = {IDENTITY_WHEN: COMET, DAY_ONE: COMET, DAY_TWO: FIXED, None: FIXED}


def _comet_at(when) -> tuple[float, float]:
    """Where the comet is: it moves 0.1 h of RA and half a degree of Dec a
    day from (10 h, +12) on day one, so each instant has its own place."""
    days = ((DAY_TWO + 5 * 86_400.0 if when is None else when)
            - DAY_ONE) / 86_400.0
    return 10.0 + 0.1 * days, 12.0 - 0.5 * days


FIXED_RA, FIXED_DEC = 3.0, 30.0


def _tie_rows(when) -> list[dict]:
    ra, dec = _comet_at(when)
    comet = {"id": COMET, "kind": "comet", "ra_hours": ra, "dec_deg": dec,
             "mag": 9.0}
    fixed = {"id": FIXED, "kind": "dso", "ra_hours": FIXED_RA,
             "dec_deg": FIXED_DEC, "mag": 9.0}
    return [comet, fixed] if FIRST[when] == COMET else [fixed, comet]


@pytest.fixture
def tie(monkeypatch):
    """``catalog.objects.search`` answering "Tie" with the swapping pair,
    and nothing for any other name."""
    def search(query, limit=25, when=None, site_derived=True):
        rows = _tie_rows(when) if str(query).strip() == "Tie" else []
        return objects.SearchResult(rows=rows[:limit], notes=[])
    monkeypatch.setattr(objects, "search", search)


def _n(nid, ntype, x=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=0.0, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _graph(name="Tie"):
    """dusk -> TARGET with a name and no typed coordinates -> CAPTURE L.
    ``ra`` and ``dec`` are "" rather than absent, because an absent param is
    filled with the palette default (M31, #190), a typed coordinate."""
    return FlowGraph(
        nodes=[_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
               _n("t", "target", x=100, name=name, ra="", dec="",
                  rotation=-1),
               _n("c", "capture", x=200, filter="L", exposure=60, gain=100,
                  bin="1", count=10, goal=0)],
        edges=[_e("d", "window", "t", "arm"), _e("t", "target", "c", "run")])


def _compile(when):
    compiled = compile_plan(_graph(), "n")
    plan, _unmapped = to_sequence_plan(compiled, _graph(), flow_id=FLOW,
                                       when=when)
    return compiled, plan


def _ids(plan):
    return [(t.id, [s.id for s in t.steps]) for t in plan.targets]


@pytest.mark.usefixtures("tie")
class TestOneIdentity:
    def test_the_resolver_names_one_object_at_every_instant(self):
        """The first hit for "Tie" is the comet on day one and the fixed row
        on day two (the premise, checked first). Asked on day one, day two
        and now, the resolver names the comet, the first hit at the shared
        instant.

        Mutant "identity resolved at now" (``resolve_target`` decides the row
        from the caller's own search: ``_identity_row(at_when, at_when)``)
        failed:
            AssertionError: assert ['C/2026 T1 (...', 'NGC 9999'] ==
            ['C/2026 T1 (...026 T1 (Tie)']
              At index 1 diff: 'NGC 9999' != 'C/2026 T1 (Tie)'
        Mutant "identity asked at when=None" (the second search made at
        ``when=None``, literally now) failed:
            AssertionError: assert ['NGC 9999', ...', 'NGC 9999'] ==
            ['C/2026 T1 (...026 T1 (Tie)']
              At index 0 diff: 'NGC 9999' != 'C/2026 T1 (Tie)'
        Neither turned any test in ``test_flows_name_keyed_target.py`` or
        ``test_flows_progress.py`` red: those replace the resolver whole.
        """
        firsts = [objects.search("Tie", 1, w).rows[0]["id"]
                  for w in (DAY_ONE, DAY_TWO)]
        assert firsts == [COMET, FIXED], "premise: the first hit swaps"
        hits = [tonight_module.resolve_target("Tie", w)
                for w in (DAY_ONE, DAY_TWO, None)]
        assert [h.identity for h in hits] == [COMET] * 3
        assert all(h.moves for h in hits)

    def test_two_compiles_and_the_card_agree(self):
        """``to_plan`` keys the TARGET on the same identity on both nights,
        so every id is kept, and ``progress._single``, resolving at now,
        finds the block the day-one compile filed its frames under.

        Mutant "identity resolved at now" failed:
            AssertionError: day two keyed the TARGET apart from day one
            assert False
        """
        compiled, one = _compile(DAY_ONE)
        _c, two = _compile(DAY_TWO)
        same = _ids(two) == _ids(one)
        assert same, "day two keyed the TARGET apart from day one"
        lum = one.targets[0].steps[0]
        session = Session(id="s1", status="dormant", nights=["n1"],
                          plan=one, origin="flow", origin_id=FLOW,
                          frames=[SessionFrame(step_id=lum.id)
                                  for _ in range(3)])
        got = flow_progress(compiled, one, session, flow_id=FLOW)
        panel, = got["blocks"][0]["panels"]
        assert panel["target_id"] == one.targets[0].id
        assert panel["banked"] == 3

    def test_adopt_asks_the_same_identity(self):
        """ADOPT keys an old "Tie" step on the identity the compile keyed
        tonight's on: the comet, a body, whatever instant it asks at.

        Mutant "identity resolved at now" failed, and so did "identity asked
        at when=None":
            AssertionError: assert ('NGC 9999', False) ==
            ('C/2026 T1 (Tie)', True)
              At index 0 diff: 'NGC 9999' != 'C/2026 T1 (Tie)'
        """
        step = ExposureStep(filter="L", exposure_s=60.0, count=5)
        old = Session(status="dormant", created_ts=DAY_ONE,
                      plan=SequencePlan(name="p", targets=[Target(
                          name="Tie", ra_hours=1.0, dec_deg=1.0,
                          steps=[step])]))
        _c, plan = _compile(DAY_ONE)
        ev = adopt_evidence(old, plan)
        hit = ev.names["Tie"]
        assert (hit.identity, hit.moves) == (COMET, True)

    def test_control_the_coordinates_are_the_compiles_own(self):
        """The row is chosen at the shared instant, and pointed where it is
        at the compile: on day one and on day two the TARGET sits on the
        comet as it is that day, and on day two that is NOT the first hit.

        Mutant "coordinates at the shared instant" (``_identity_row``
        returns the chosen row from ``at_identity``) failed on day one, the
        comet placed 20.6 days before it:
            assert (7.9407407407...6296296296298) == approx((10.0 ....0 ±
            1.2e-05))
              comparison failed. Mismatched elements: 2 / 2:
              Index | Obtained           | Expected
              0     | 7.940740740740741  | 10.0 ± 1.0e-05
              1     | 22.296296296296298 | 12.0 ± 1.2e-05
        and failed the real Mars session in
        ``test_flows_adopt_body_pointing.py`` too. Mutant "coordinates of
        the first hit at the caller's instant" (``resolve_target`` keeps the
        chosen row's identity but reads the place of ``at_when[0]``) failed
        on day two:
            assert (3.0, 30.0) == approx((10.1 ....5 ± 1.2e-05))
              comparison failed. Mismatched elements: 2 / 2:
              Index | Obtained | Expected
              0     | 3.0      | 10.1 ± 1.0e-05
              1     | 30.0     | 11.5 ± 1.2e-05
        """
        _c, one = _compile(DAY_ONE)
        _c, two = _compile(DAY_TWO)
        at = [(p.targets[0].ra_hours, p.targets[0].dec_deg)
              for p in (one, two)]
        assert at[0] == pytest.approx(_comet_at(DAY_ONE))
        assert at[1] == pytest.approx(_comet_at(DAY_TWO))


# --------------------------------------- where the shared instant stops

class TestTheTwoExceptions:
    def _stub(self, monkeypatch, table):
        def search(query, limit=25, when=None, site_derived=True):
            return objects.SearchResult(rows=list(table[when])[:limit],
                                        notes=[])
        monkeypatch.setattr(objects, "search", search)

    SAT = {"id": "ISS (ZARYA)", "kind": "satellite", "norad_id": 25544,
           "ra_hours": 7.0, "dec_deg": -3.0, "mag": None}
    STAR = {"id": "Meissa", "kind": "star", "ra_hours": 5.585,
            "dec_deg": 9.934, "mag": 3.4}

    def test_a_satellite_the_shared_instant_cannot_place_is_decided_later(
            self, monkeypatch):
        """SGP4 refuses an element set far from its epoch, so a satellite
        can be missing at the shared instant while the name still matches
        something fixed there: at J2000 the shipped catalogue answers "ISS"
        with the star Meissa. A moving row the shared instant cannot place
        is decided at the caller's instant, so "ISS" is still the ISS.

        Mutant "no fallback" (the branch for a moving row missing from
        ``at_identity`` removed) failed, and so did "satellite not a moving
        kind" (``MOVING_KINDS`` without ``"satellite"``):
            AssertionError: assert ('Meissa', 5.585) == ('ISS (ZARYA)', 7.0)
              At index 0 diff: 'Meissa' != 'ISS (ZARYA)'
        """
        self._stub(monkeypatch, {IDENTITY_WHEN: [self.STAR],
                                 DAY_ONE: [self.SAT, self.STAR]})
        hit = tonight_module.resolve_target("ISS", DAY_ONE)
        assert (hit.identity, hit.ra_hours) == ("ISS (ZARYA)", 7.0)
        assert hit.moves

    def test_a_row_the_callers_instant_cannot_place_is_no_answer(
            self, monkeypatch):
        """The shared instant names the comet, and the ephemeris cannot place
        it at the caller's instant: no answer, rather than the other row the
        name matches then. Pointing at that row would be the slide this is
        for.

        Mutant "fall back to the first hit at the caller's instant"
        (``_identity_row`` returns ``first`` when the chosen row is missing)
        failed:
            AssertionError: assert NameResolution(ra_hours=3.0, dec_deg=30.0,
            identity='NGC 9999', moves=False) is None
             +  where NameResolution(ra_hours=3.0, dec_deg=30.0,
             identity='NGC 9999', moves=False) = <function resolve_target at
             0x000001A6F63AE660>('Tie', 1790000000.0)
        """
        comet = {"id": COMET, "kind": "comet", "ra_hours": 10.0,
                 "dec_deg": 12.0, "mag": 9.0}
        fixed = {"id": FIXED, "kind": "dso", "ra_hours": FIXED_RA,
                 "dec_deg": FIXED_DEC, "mag": 9.0}
        self._stub(monkeypatch, {IDENTITY_WHEN: [comet, fixed],
                                 DAY_ONE: [fixed]})
        assert tonight_module.resolve_target("Tie", DAY_ONE) is None

    def test_two_satellites_of_one_name_are_two_objects(self, monkeypatch):
        """A satellite row's id is its name, and debris pieces share one:
        only the catalogue number tells two pieces apart (``_row_key``).
        The shared instant names piece 1. At the caller's instant piece 1 is
        placed where piece 1 is, whatever order the pieces come in, and
        where only piece 2 can be placed there is no answer, rather than
        piece 2's position under the name piece 1 was chosen by.

        Mutant "the row key drops the catalogue number" (``_row_key``
        returns kind and id only) failed:
            AssertionError: assert 2.0 == 1.0
             +  where 2.0 = NameResolution(ra_hours=2.0, dec_deg=2.0,
             identity='DEB', moves=True).ra_hours
             +    where NameResolution(ra_hours=2.0, dec_deg=2.0,
             identity='DEB', moves=True) = <function resolve_target at
             0x00000236C58CE660>('DEB', 1790000000.0)
        """
        one = {"id": "DEB", "kind": "satellite", "norad_id": 1,
               "ra_hours": 1.0, "dec_deg": 1.0, "mag": None}
        two = {"id": "DEB", "kind": "satellite", "norad_id": 2,
               "ra_hours": 2.0, "dec_deg": 2.0, "mag": None}
        self._stub(monkeypatch, {IDENTITY_WHEN: [one, two],
                                 DAY_ONE: [two, one], DAY_TWO: [two]})
        assert tonight_module.resolve_target("DEB", DAY_ONE).ra_hours == 1.0
        assert tonight_module.resolve_target("DEB", DAY_TWO) is None

    def test_control_asked_at_the_shared_instant_it_searches_once(
            self, monkeypatch):
        """Asked AT the shared instant, the two searches are one.

        Mutant "always two searches" (the second search made whatever
        ``when`` is) failed:
            assert [1788220800.0, 1788220800.0] == [1788220800.0]
              Left contains one more item: 1788220800.0
        """
        calls = []

        def search(query, limit=25, when=None, site_derived=True):
            calls.append(when)
            return objects.SearchResult(rows=[self.STAR], notes=[])
        monkeypatch.setattr(objects, "search", search)
        assert tonight_module.resolve_target("x", IDENTITY_WHEN).identity \
            == "Meissa"
        assert calls == [IDENTITY_WHEN]


# ------------------------- the shipped comet and satellite at the instant

@pytest.fixture
def shipped_elements(tmp_path, monkeypatch):
    """A real, non-default site (Greenwich, as the ephemeris tests use: the
    one meridian no rig is at), because a satellite has no position without
    one, and the ISS and 2P/Encke element sets those tests pin (epochs
    2026-09-10), written where the catalogue reads them."""
    import astrodeck.config as config_mod
    from astrodeck.catalog.ephemeris import comets
    from astrodeck.catalog.ephemeris import elements as el
    from astrodeck.config import ConfigStore
    from test_comet_ephemeris import ENCKE_LINE
    from test_satellite_ephemeris import ISS_ROW, WHEN

    s = ConfigStore(path=tmp_path / "astrodeck.json")
    cfg = s.cfg()
    cfg.site = cfg.site.model_copy(update={
        "name": "Greenwich", "latitude": 51.4778, "longitude": -0.0015,
        "elevation_m": 46.0, "is_default": False})
    monkeypatch.setattr(config_mod, "config_store", s)
    monkeypatch.setattr(el, "ELEMENTS_DIR", tmp_path / "ephemeris")
    monkeypatch.setattr(el, "SATELLITE_FILE",
                        tmp_path / "ephemeris" / "satellites.json")
    monkeypatch.setattr(el, "COMET_FILE", tmp_path / "ephemeris" / "comets.json")
    el.write_envelope(el.SATELLITE_FILE, "test", [ISS_ROW], WHEN)
    el.write_envelope(el.COMET_FILE, "mpc-cometels",
                      [comets.parse_comet_line(ENCKE_LINE)], WHEN)
    return WHEN


def test_a_comet_and_a_satellite_resolve_at_the_shared_instant(
        shipped_elements):
    """The check the choice of a named instant rests on: the shipped comet
    and satellite code places 2P/Encke and the ISS at ``IDENTITY_WHEN`` from
    the element sets pinned in their own tests, so both names are
    themselves there, and at another instant too. No position is printed.

    Mutant "IDENTITY_WHEN at J2000" (``946_728_000.0``) failed, the ISS
    element set refused by SGP4 that far back and the name sliding onto a
    star:
        AssertionError: at the shared instant: [('ISS', 'Meissa', 'star'),
        ('Encke', '2P', 'comet')]
        assert [('ISS', 'Mei...2P', 'comet')] == [('ISS', 'ISS...2P',
        'comet')]
          At index 0 diff: ('ISS', 'Meissa', 'star') != ('ISS', 'ISS
          (ZARYA)', 'satellite')
    Mutant "satellite not a moving kind" failed the second half.
    """
    at_instant = []
    for name in ("ISS", "Encke"):
        row = objects.search(name, 1, IDENTITY_WHEN).rows[0]
        at_instant.append((name, row["id"], row["kind"]))
    want = [("ISS", "ISS (ZARYA)", "satellite"), ("Encke", "2P", "comet")]
    assert at_instant == want, f"at the shared instant: {at_instant}"
    for name, ident in (("ISS", "ISS (ZARYA)"), ("Encke", "2P")):
        for when in (IDENTITY_WHEN, shipped_elements):
            hit = tonight_module.resolve_target(name, when)
            assert (hit.identity, hit.moves) == (ident, True), (name, when)
