# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``fixtures/mosaic_panels_3x2.json``: one recorded ``POST
/api/framing/mosaic`` exchange that the UI draws panels from (#189 S4 item
2; spec 2026-09-23 flows mosaic, 2.3).

WHY A RECORDED ANSWER. The Target modal labels each panel from the SERVER's
panel coordinates, never from ``FovOverlay``'s screen-space grid (a point
reflection of the server layout) or from ``panelRects`` (which mirrors the
columns). Its tests (``ui/src/components/atlas/__tests__/panelLayer.test.tsx``
and the modal's own) therefore need the server's answer, not a copy of the
arithmetic written again in TypeScript: a copy would agree with the canvas
by construction and prove nothing. They READ this file, and this test keeps
the file equal to what the route answers, so a change to ``compute_mosaic``
that moves a panel turns this red before it can turn a label wrong.

The file is ``{"about", "request", "response"}``: ``request`` is the body
posted, ``response`` the route's JSON, byte for byte. The request is the
spec's reference layout (a 3x2 of 2.0 x 1.33 deg panels at 25% overlap,
Dec 41), at angle 0 so that "1-1 sits top right on the north-up, east-left
chart" is readable straight off the numbers. It asks nothing about the
site: no ``date``, no ``transit_alt``, so the answer carries no altitude.

To rewrite the file after a deliberate change to ``compute_mosaic``:

    cd server && .venv/Scripts/python.exe tests/test_framing_panels_fixture.py

(Run as a script, ``tests/`` is first on ``sys.path``, so ``astrodeck``
comes from the editable install, which is this tree. A copy of server/
elsewhere must import its own package first, or it rewrites the file from
this tree's code.)

Every test names the mutation it guards and quotes the failure that
mutation produced. Mutations of ``catalog/framing.py`` were run in a private
copy of server/ (scratchpad s4-usky-mut), never in the shared tree (#254).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.catalog import framing

FIXTURE = Path(__file__).parent / "fixtures" / "mosaic_panels_3x2.json"

#: The posted body. M31's position (a catalogue fact, not a site one), the
#: spec's 3x2 reference grid, angle 0. Written here as well as in the file so
#: that a hand edit of the file's request cannot silently re-define the case.
REQUEST = {"ra_hours": 0.7122, "dec_deg": 41.0, "rows": 2, "cols": 3,
           "overlap": framing.DEFAULT_OVERLAP, "rotation_deg": 0.0,
           "fov_x_deg": 2.0, "fov_y_deg": 1.33}

ABOUT = ("POST /api/framing/mosaic answer computed by framing.compute_mosaic "
         "for the request beside it; test_framing_panels_fixture.py keeps it "
         "equal to the route's answer. Read, never copied, by the UI panel "
         "layer and Target modal tests (#189 S4).")


class FakeAuthProvider:
    """A fixed principal for every request (mirrors test_rbac_enforcement)."""

    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(framing.router)
    # The route needs CAP_VIEW_SITE_DERIVED; an operator holds it, so the
    # gate is not what answers here (test_framing_route_reframe owns that).
    set_active_provider(FakeAuthProvider(principal_for_role("operator")))
    with TestClient(app) as c:
        yield c


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_route_answers_exactly_the_fixture(client):
    """The recorded answer IS the route's answer for the recorded request.

    Mutant "fixture panel nudged" (the file's panel 1-1 ``ra_hours`` moved
    in its last digits, 0.578742751340055 -> 0.578742751340059) failed:
        AssertionError: the fixture is not what POST /api/framing/mosaic
        answers; rewrite it with this file's __main__ (see the docstring)
        assert {'frame_fov_x...ec': 0.0, ...} == {'frame_fov_x...ec': 0.0, ...}
          Omitting 5 identical items, use -vv to show
          Differing items:
          {'panels': [{'col': 0, 'dec_deg': 41.481379199474624,
          'ra_hours': 0.578742751340055, 'rotation_deg': 0.0, ...}, ...]}
          != {'panels': [{'col': 0, 'dec_deg': 41.481379199474624,
          'ra_hours': 0.578742751340059, 'rotation_deg': 0.0, ...}, ...]}

    Mutant "columns mirrored" in ``compute_mosaic`` (``gx = ((cols - 1) /
    2.0 - c) * step_x``) failed the same assertion, the route's panel
    (0, 0) now east of the centre:
          Differing items:
          {'panels': [{'col': 0, 'dec_deg': 41.481379199474624,
          'ra_hours': 0.8456572486599452, 'rotation_deg': 0.0, ...}, ...]}
          != {'panels': [{'col': 0, 'dec_deg': 41.481379199474624,
          'ra_hours': 0.578742751340055, 'rotation_deg': 0.0, ...}, ...]}
    """
    fx = _fixture()
    r = client.post("/api/framing/mosaic", json=fx["request"])
    assert r.status_code == 200, r.text
    assert r.json() == fx["response"], (
        "the fixture is not what POST /api/framing/mosaic answers; rewrite "
        "it with this file's __main__ (see the docstring)")


def test_the_request_is_the_reference_layout_and_asks_nothing_of_the_site():
    """The file's request is ``REQUEST``, and neither it nor the answer
    carries anything site-derived (an altitude is a function of the site's
    latitude, #19): no ``date``, no ``transit_alt`` asked, none answered.

    Mutant "request asks for tonight" (``"transit_alt": true`` added to the
    file's request) failed:
        AssertionError: the fixture's request is not the reference layout:
        {'ra_hours': 0.7122, 'dec_deg': 41.0, 'rows': 2, 'cols': 3,
        'overlap': 0.25, 'rotation_deg': 0.0, 'fov_x_deg': 2.0,
        'fov_y_deg': 1.33, 'transit_alt': True}
        assert {'cols': 3, '...g': 1.33, ...} == {'cols': 3, '...g': 1.33, ...}
          Omitting 8 identical items, use -vv to show
          Left contains 1 more item:
          {'transit_alt': True}
    """
    fx = _fixture()
    assert fx["request"] == REQUEST, (
        f"the fixture's request is not the reference layout: {fx['request']}")
    assert "date" not in fx["request"] and not fx["request"].get("transit_alt")
    for p in fx["response"]["panels"]:
        assert not any(k.startswith("transit_alt") for k in p), p


def test_panel_0_0_is_the_north_west_corner_and_the_list_is_the_snake():
    """What the canvas's label convention stands on (spec 2.3): server panel
    (0, 0) is the NORTH-WEST corner, so its label ``1-1`` sits top right on
    the north-up, east-left chart. Rows run north to south and columns west
    to east (RA grows eastward), and the list is ``compute_mosaic``'s
    boustrophedon order, which the modal's "Grid order" reads.

    Read off the recorded answer rather than recomputed, so this holds the
    FILE to the convention the UI tests assume when they read it.

    Each mutant below had the fixture rewritten FROM it, so the equality
    test above passed and this one is what failed.

    Mutant "columns mirrored" in ``compute_mosaic`` (as above) failed:
        AssertionError: panel (0, 0) is not west of the centre: RA
        0.8456572486599452 h against 0.7122 h
        assert 0.8456572486599452 < 0.7122

    Mutant "rows from the south" (``gy = (r - (rows - 1) / 2.0) *
    step_y``) failed:
        AssertionError: panel (0, 0) is not north of the centre: Dec
        40.48450399184178 against 41.0
        assert 40.48450399184178 > 41.0

    Mutant "no snake" (the odd-row ``col_order.reverse()`` deleted)
    failed:
        assert [(0, 0), (0, ...1, 1), (1, 2)] == [(0, 0), (0, ...1, 1), (1, 0)]
          At index 3 diff: (1, 0) != (1, 2)
    """
    fx = _fixture()
    req = fx["request"]
    panels = fx["response"]["panels"]
    by_rc = {(p["row"], p["col"]): p for p in panels}
    nw = by_rc[(0, 0)]
    assert nw["dec_deg"] > req["dec_deg"], (
        f"panel (0, 0) is not north of the centre: Dec {nw['dec_deg']} "
        f"against {req['dec_deg']}")
    assert nw["ra_hours"] < req["ra_hours"], (
        f"panel (0, 0) is not west of the centre: RA {nw['ra_hours']} h "
        f"against {req['ra_hours']} h")
    # Column 0 is the west edge on every row, row 0 the north edge on
    # every column.
    rows, cols = req["rows"], req["cols"]
    for r in range(rows):
        ras = [by_rc[(r, c)]["ra_hours"] for c in range(cols)]
        assert ras == sorted(ras), f"row {r} does not run west to east: {ras}"
    for c in range(cols):
        decs = [by_rc[(r, c)]["dec_deg"] for r in range(rows)]
        assert decs == sorted(decs, reverse=True), (
            f"column {c} does not run north to south: {decs}")
    assert [(p["row"], p["col"]) for p in panels] == [
        (0, 0), (0, 1), (0, 2), (1, 2), (1, 1), (1, 0)]


def test_the_recorded_panels_carry_the_per_panel_angle_in_the_rigs_sense():
    """What the UI tests read the two per-panel keys by (#175): panel
    ``convergence_deg`` is local north on the grid's plane from +eta toward
    +xi, so it is POSITIVE on the west column (north leans toward the pole,
    east of a panel west of the centre), negative on the east column and 0
    on the middle one; ``pa_deg`` is the layout angle plus it, wrapped; and
    ``rotation_deg`` is still the layout angle on every panel, which is what
    the Atlas draws the rectangles at.

    Read off the recorded answer rather than recomputed, so this holds the
    FILE to the convention. Each mutant had the fixture rewritten FROM it,
    so the equality test above passed and this one is what failed.

    Mutant "n_i sign flipped" in ``panel_convergence_deg`` failed:
        AssertionError: panel (0, 0) is not turned toward the east: -1.31...
        assert -1.3136419413493814 > 0

    Mutant "pa_deg = rotation_deg" failed:
        AssertionError: panel (0, 0): pa_deg 0.0 is not rotation_deg 0.0 +
        convergence_deg 1.3136419413493814, wrapped
    """
    fx = _fixture()
    layout = fx["request"]["rotation_deg"]
    by_rc = {(p["row"], p["col"]): p for p in fx["response"]["panels"]}
    for p in by_rc.values():
        assert p["rotation_deg"] == layout, p
        want = (layout + p["convergence_deg"]) % 360.0
        assert p["pa_deg"] == pytest.approx(want, abs=1e-9), (
            f"panel ({p['row']}, {p['col']}): pa_deg {p['pa_deg']} is not "
            f"rotation_deg {layout} + convergence_deg "
            f"{p['convergence_deg']}, wrapped")
    for r in range(fx["request"]["rows"]):
        west, mid, east = (by_rc[(r, c)]["convergence_deg"] for c in range(3))
        assert west > 0, (
            f"panel ({r}, 0) is not turned toward the east: {west}")
        assert east < 0, (
            f"panel ({r}, 2) is not turned toward the west: {east}")
        assert mid == pytest.approx(0.0, abs=1e-9), (r, mid)


def _write_fixture() -> None:
    """Rewrite the file from ``compute_mosaic`` (what the route answers for
    this request: it adds nothing without ``date``/``transit_alt``/an
    anchor). JSON floats are written with ``repr``, so they round-trip.
    LF endings on every OS, like the fixtures beside it."""
    body = {"about": ABOUT, "request": REQUEST,
            "response": framing.compute_mosaic(REQUEST)}
    FIXTURE.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8",
                       newline="\n")


if __name__ == "__main__":
    _write_fixture()
    print("wrote", FIXTURE)
