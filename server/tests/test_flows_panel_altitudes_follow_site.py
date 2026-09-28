"""A mosaic's panel altitudes in Tonight follow the site ``resolve_tonight``
is handed, not the hub's (#336; spec section 8 S3 item 5, 6.9).

``framing._stamp_transit_alt`` took no site, so it called
``visibility.transit_alt_for`` with none, and ``compute_night`` fell back to
``hub.site``: the configured observatory. ``resolve_tonight`` takes its
site explicitly and hands it to ``compute_night`` for each block's curve,
window and flip, so a caller with a site of its own got one answer built
from two sites, and a script resolving a synthetic site printed panel
altitudes at the real one. The peak altitude of a known object is a
latitude oracle (#140), and #336 records exactly that slip.

The fix: the stamp takes ``site`` (keyword-only, default None, which still
reads the hub's site for the mosaic route, whose question is the rig's own
site), and ``tonight._mosaic_night`` hands it the site the resolver was
given. So one night is resolved here under TWO synthetic sites with the hub
on a THIRD, and every panel altitude must be the argument's. No site in this
file is the observatory's, and no altitude is computed anywhere the test
did not name: the hub is pinned to site C for every test, so even a mutant
that loses the argument reads a synthetic place.

Every test names the mutation it guards and quotes the failure it produced,
each run in a private copy of ``server/`` (scratchpad ``s4-tonight-mut``,
from byte backups), never in the shared tree.
"""
from __future__ import annotations

import datetime as _dt

import pytest

from astrodeck.catalog import framing, visibility
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.tonight import _night_date, resolve_tonight
from astrodeck.hub import Hub

#: Three synthetic places, none of them the observatory. A and B are handed
#: to the resolver; C is the hub's. Their latitudes are far apart, so a
#: panel's peak altitude at one is tens of degrees from its peak at another.
SITE_A = {"latitude": 40.0, "longitude": -105.0, "elevation_m": 1600.0,
          "is_default": False}
SITE_B = {"latitude": -25.0, "longitude": 20.0, "elevation_m": 1200.0,
          "is_default": False}
SITE_C = {"latitude": 10.0, "longitude": 75.0, "elevation_m": 300.0,
          "is_default": False}
TWILIGHT = -12.0
JUNE = _dt.datetime(2026, 6, 15, 20, 0, tzinfo=_dt.timezone.utc).timestamp()
M16_RA_H, M16_DEC_D = 18.0 + 18 / 60 + 48 / 3600, -(13 + 49 / 60)
FOV = (2.0, 1.33)


@pytest.fixture(autouse=True)
def hub_on_site_c(monkeypatch):
    """The hub stands on site C for every test here, so no path the code
    takes, fixed or mutated, can read the configured site."""
    monkeypatch.setattr(Hub, "site", property(lambda self: {
        "name": "synthetic C", **SITE_C, "horizon_min_deg": 0.0}))


def _n(nid, ntype, x=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=0.0, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _flow() -> FlowGraph:
    """dusk -> TARGET M16, a rotating 2x2 at Rotate to PA 30 -> CAPTURE."""
    return FlowGraph(
        nodes=[_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
               _n("t", "target", x=100, name="M16", ra="18h 18m 48s",
                  dec="-13 49 00", rotation=30, angle="Rotate to PA",
                  rows=2, cols=2, overlap=25, fovX=FOV[0], fovY=FOV[1],
                  counts="Accepted subs"),
               _n("c", "capture", x=200, filter="Ha", exposure=300,
                  gain=100, bin="1", count=2, goal=0)],
        edges=[_e("d", "window", "t", "arm"), _e("t", "target", "c", "run"),
               _e("c", "pass", "t", "next")])


def _centres() -> list[tuple[int, int, float, float]]:
    """The 2x2's panel centres, laid out as the resolver lays them out."""
    return [(p["row"], p["col"], p["ra_hours"], p["dec_deg"])
            for p in framing.compute_mosaic({
                "ra_hours": M16_RA_H, "dec_deg": M16_DEC_D, "rows": 2,
                "cols": 2, "overlap": 0.25, "rotation_deg": 30.0,
                "fov_x_deg": FOV[0], "fov_y_deg": FOV[1]})["panels"]]


def _stamped(site: dict) -> dict[tuple[int, int], float]:
    """The panel altitudes ``resolve_tonight`` answers for ``site``."""
    out = resolve_tonight(_flow(), site, now=JUNE, twilight_deg=TWILIGHT)
    assert out["ok"], out["reason"]
    (row,) = out["targets"]
    return {(p["row"], p["col"]): p["transit_alt"]
            for p in row["mosaic"]["panels"]}


def _direct(site: dict, date: str) -> dict[tuple[int, int], float]:
    """Each panel centre's peak altitude asked of ``visibility`` directly,
    at ``site``, for ``date``."""
    return {(r, c): visibility.transit_alt_for(ra, dec, date=date, site=site)
            for r, c, ra, dec in _centres()}


class TestThePanelAltitudesFollowTheSiteArgument:
    def test_two_sites_two_answers_and_neither_is_the_hubs(self):
        """Resolved at A and at B with the hub on C: each panel's altitude is
        the one ``visibility`` gives for that panel at the site the resolver
        was handed, for the night it pinned, and at neither site is it C's.

        RED under mutant "site not forwarded" in ``tonight._mosaic_night``
        (the stamp awaited as ``_stamp_transit_alt(panels, date)``, the
        call S3 made), observed (every altitude here is at a synthetic
        site: the panels at C's, the hub's, against A's):

            AssertionError: resolved at A, the panels peak at {(0, 0): 66.1,
            (0, 1): 67.0, (1, 0): 65.3, (1, 1): 66.1}; at A they peak at
            {(0, 0): 36.2, (0, 1): 37.0, (1, 1): 36.1, (1, 0): 35.4}
            assert {(0, 0): 66.1... (1, 1): 66.1} == {(0, 0): 36.2... (1, 1):
            36.1}
              Differing items:
              {(0, 1): 67.0} != {(0, 1): 37.0}

        RED under mutant "site not forwarded" in ``framing._stamp_transit_
        alt`` (``transit_alt_for`` called without ``site=site``, the call S3
        made), observed: the same failure, word for word.
        """
        answers = {}
        for label, site in (("A", SITE_A), ("B", SITE_B)):
            date = _night_date(JUNE, site["longitude"])
            got, want = _stamped(site), _direct(site, date)
            hubs = _direct(SITE_C, date)
            assert got == want, (
                f"resolved at {label}, the panels peak at {got}; at {label} "
                f"they peak at {want}")
            assert all(abs(got[k] - hubs[k]) > 10.0 for k in got), (
                f"premise: at {label} no panel peaks within 10 deg of the "
                f"hub's site ({got} against {hubs})")
            answers[label] = got
        assert all(abs(answers["A"][k] - answers["B"][k]) > 10.0
                   for k in answers["A"]), (
            f"the two sites' answers must differ: {answers}")

    async def test_the_stamp_forwards_the_site_it_is_given(self):
        """The stamp itself, asked for panels at B, answers B's altitudes.

        RED under mutant "site not forwarded" in ``framing._stamp_transit_
        alt``, observed (C's altitudes against B's, both synthetic):

            AssertionError: the stamp at B wrote {(0, 0): 66.1, (0, 1): 67.0,
            (1, 1): 66.1, (1, 0): 65.3}; at B they peak at {(0, 0): 78.5,
            (0, 1): 77.9, (1, 1): 78.8, (1, 0): 79.4}

        Green under the tonight.py mutant, which never reaches the stamp's
        own forwarding.
        """
        date = _night_date(JUNE, SITE_B["longitude"])
        panels = [{"row": r, "col": c, "ra_hours": ra, "dec_deg": dec}
                  for r, c, ra, dec in _centres()]
        await framing._stamp_transit_alt(panels, date, site=SITE_B)
        got = {(p["row"], p["col"]): p["transit_alt"] for p in panels}
        want = _direct(SITE_B, date)
        assert got == want, f"the stamp at B wrote {got}; at B they peak at " \
                            f"{want}"


class TestTheMosaicRouteStillAsksNothing:
    async def test_control_the_route_answers_for_the_hubs_site(self):
        """Control: ``POST /api/framing/mosaic`` passes no site, because its
        question is the rig's own site, so its panels peak at the hub's (C
        here). Green on the code and under both "site not forwarded"
        mutants, which only lose a site that this route never passes."""
        date = _night_date(JUNE, SITE_C["longitude"])
        spec = framing.MosaicSpecIn(
            ra_hours=M16_RA_H, dec_deg=M16_DEC_D, rows=2, cols=2,
            overlap=0.25, rotation_deg=30.0, fov_x_deg=FOV[0],
            fov_y_deg=FOV[1], date=date)
        result = await framing.post_mosaic(spec)
        got = {(p["row"], p["col"]): p["transit_alt"]
               for p in result["panels"]}
        assert got == _direct(SITE_C, date)
