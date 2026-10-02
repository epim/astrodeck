# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``POST /api/framing/mosaic`` answers ``reframe`` when it is given an anchor
(#189 Revision 2 ruling 3; spec 2.5 and 3.3).

The Target modal shows, live, how far the new layout moves against the
block's anchor and whether the counts carry ("moved 4.2' of the 10.0' this
grid allows: counts carry over"). Those numbers come from the server, from
the same ``reframe_carry`` the save uses, so the modal cannot promise a carry
the save then refuses. The route takes the anchor in either of two forms:

* the block's stored ``frameAnchor`` text, exactly as the server wrote it,
  which is what the save compares against; a blank one (a block saved before
  S3) is no anchor, and gets no ``reframe``;
* a geometry in ``MosaicSpecIn``'s own field names.

Without an anchor the answer is exactly what it was, and the gate is exactly
what it was: ``CAP_VIEW_SITE_DERIVED``, because ``transit_alt`` makes this
route's answer a function of the site.

Every test names the mutation of ``catalog/framing.py`` it guards and quotes
the failure that mutation produced, each run in a private copy of server/
(scratchpad s3-G-mut), never in the shared tree.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrodeck.auth import (CAP_VIEW_SITE_DERIVED, principal_for_role,
                            reset_active_provider, set_active_provider)
from astrodeck.auth import rbac
from astrodeck.catalog import framing
from astrodeck.flows import identity

ARCMIN = 1.0 / 60.0

#: The task's 3x2 of 2.0 x 1.33 deg at 25%, Dec 41, angle 30, as the route
#: takes it. No date and no transit_alt: nothing here asks about the site.
SPEC = {"ra_hours": 0.7122, "dec_deg": 41.0, "rows": 2, "cols": 3,
        "overlap": 0.25, "rotation_deg": 30.0,
        "fov_x_deg": 2.0, "fov_y_deg": 1.33}

#: The same geometry as the server stores it on the block.
ANCHOR = identity.canonical_geometry(0.7122, 41.0, 30.0, rows=2, cols=3,
                                     overlap=0.25, fov_x=2.0, fov_y=1.33)


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
    with TestClient(app) as c:
        yield c


def _moved(arcmin: float) -> dict:
    return {**SPEC, "dec_deg": SPEC["dec_deg"] + arcmin * ARCMIN}


def test_without_an_anchor_the_answer_is_unchanged(client):
    """Control: no ``reframe`` key, and every other key exactly as
    ``compute_mosaic`` gives it. The Atlas "Send to Plan" path posts this.

    Mutant "reframe always answered" (the route computes it against the
    spec itself when no anchor is given) failed:
        Left contains 1 more item:
        {'reframe': {'carry': True,
                     'max_move_deg': 0.0,
                     'reason': 'unchanged',
                     'threshold_deg': 0.16625}}
    """
    r = client.post("/api/framing/mosaic", json=_moved(10.1))
    assert r.status_code == 200, r.text
    assert r.json() == framing.compute_mosaic(_moved(10.1))
    assert "reframe" not in r.json()


def test_the_stored_anchor_text_gets_the_saves_verdict(client):
    """The pinned cases through the route: 9.9' north carries, 10.1'
    re-anchors, each exactly as ``reframe_carry`` answers.

    Mutant "anchor ignored" (the route never adds ``reframe``) failed:
        KeyError: 'reframe'
    """
    for arcmin, carry in ((9.9, True), (10.1, False)):
        r = client.post("/api/framing/mosaic",
                        json={**_moved(arcmin), "anchor": ANCHOR})
        assert r.status_code == 200, r.text
        body = r.json()
        want = framing.reframe_carry(
            ANCHOR, framing.MosaicSpecIn(**_moved(arcmin)))
        assert body["reframe"] == want
        assert body["reframe"]["carry"] is carry
        assert body["reframe"]["threshold_deg"] / ARCMIN == \
            pytest.approx(9.975)
        # The panels are still the NEW layout's, not the anchor's.
        assert body["panels"] == framing.compute_mosaic(_moved(arcmin))[
            "panels"]


def test_the_anchor_as_a_spec_answers_as_its_text(client):
    """The anchor in ``MosaicSpecIn``'s own words is the same anchor.

    Mutant "spec fields swapped" (``fov_x`` read from ``fov_y_deg`` and the
    reverse) failed:
        Differing items:
        {'carry': True} != {'carry': False}
        {'max_move_deg': 0.16499753730442338} != {'max_move_deg':
        1.1692341338909875}
    """
    as_spec = {k: SPEC[k] for k in ("ra_hours", "dec_deg", "rows", "cols",
                                    "overlap", "rotation_deg", "fov_x_deg",
                                    "fov_y_deg")}
    for arcmin in (9.9, 10.1):
        by_text = client.post("/api/framing/mosaic",
                              json={**_moved(arcmin), "anchor": ANCHOR})
        by_spec = client.post("/api/framing/mosaic",
                              json={**_moved(arcmin), "anchor": as_spec})
        assert by_spec.status_code == 200, by_spec.text
        assert by_spec.json()["reframe"] == by_text.json()["reframe"]


def test_an_anchor_with_no_camera_field_is_accepted(client):
    """A block framed before MATCH CAMERA holds no field, which the spec
    form must accept (``fov`` 0, where the layout itself needs a field).

    MATCH CAMERA FOLLOWS RULING 4 (#351, spec 2.5). Framed with the camera's
    field at the same centre and angle, the anchor is COMPLETED, as the save
    completes it: one geometry with the layout, it carries, with the
    threshold of the field it now records (0.5 x 0.25 x 1.33 deg). Framed
    with a field AND moved 1', it does not complete (the edit may not set
    its own allowance), so the move is judged against no field, threshold
    0, and re-anchors. Both forms of the anchor answer alike.

    Mutant "the anchor needs a field" (``MosaicAnchorIn``'s fields ``gt=0``)
    failed:
        {"type":"greater_than","loc":["body","anchor","MosaicAnchorIn",
        "fov_x_deg"],"msg":"Input should be greater than 0",...}
        assert 422 == 200

    Mutant "the route does not complete" (``_reframe_answer``'s
    ``identity.completes`` branch removed), the modal would promise a
    restart the save never makes:
        AssertionError: assert (False, 0.0, 'move') == (True, 0.16625, 'unchanged')
          At index 0 diff: False != True

    Mutant "complete whatever else changed" (``identity.completes`` without
    its key comparison) failed the 1' case, which then completed and
    carried on the allowance the same edit records:
        AssertionError: assert (True, 0.16625, 'move') == (False, 0.0, 'move')
          At index 0 diff: True != False
    """
    old = {"ra_hours": 0.7122, "dec_deg": 41.0, "rows": 1, "cols": 1,
           "overlap": 0.25, "rotation_deg": 30.0, "fov_x_deg": 0.0,
           "fov_y_deg": 0.0}
    text = identity.canonical_geometry(0.7122, 41.0, 30.0)
    single = {**SPEC, "rows": 1, "cols": 1}
    for anchor in (old, text):
        r = client.post("/api/framing/mosaic",
                        json={**single, "anchor": anchor})
        assert r.status_code == 200, r.text
        got = r.json()["reframe"]
        assert (got["carry"], got["threshold_deg"], got["reason"]) == \
            (True, 0.5 * 0.25 * 1.33, "unchanged")
        r = client.post("/api/framing/mosaic",
                        json={**single, "dec_deg": 41.0 + ARCMIN,
                              "anchor": anchor})
        got = r.json()["reframe"]
        assert (got["carry"], got["threshold_deg"], got["reason"]) == \
            (False, 0.0, "move")


def test_a_completed_anchor_is_laid_out_with_the_field_it_records(client):
    """The stored text of a completed anchor (#351, ruling 4) keys no field
    and records the camera's. The route lays it out with the recorded field
    and takes the threshold from it, as the save does: a 3' nudge of a
    1.3 x 0.9 deg panel carries under 0.1125 deg, and a 10' one does not.

    Mutant "a recorded field is not laid out" (``framing._laid_out``
    returns every frame as it is) failed:
        assert (False, 0.0) == (True, 0.1125)
          At index 0 diff: False != True
    The route hands ``reframe_carry`` the anchor already laid out, as a
    mapping, so the save-side mutant "threshold from the anchor's zero field
    after completion", which reads the keyed field from the stored TEXT,
    cannot reach it here: this case stays green under it, and the save's
    cases (test_flows_anchor_completion.py) and reframe_carry's own
    (test_framing_reframe_carry.py) are the ones that go red.
    """
    blank = identity.canonical_geometry(0.7122, 41.0, 30.0)
    completed = identity.complete_anchor(blank, identity.canonical_geometry(
        0.7122, 41.0, 30.0, fov_x=1.3, fov_y=0.9))
    assert completed is not None, "premise: the field completes the anchor"
    single = {**SPEC, "rows": 1, "cols": 1, "fov_x_deg": 1.3,
              "fov_y_deg": 0.9}
    got = []
    for arcmin in (3.0, 10.0):
        r = client.post("/api/framing/mosaic",
                        json={**single, "dec_deg": 41.0 + arcmin * ARCMIN,
                              "anchor": completed})
        assert r.status_code == 200, r.text
        got.append((r.json()["reframe"]["carry"],
                    r.json()["reframe"]["threshold_deg"]))
    assert got[0] == (True, 0.5 * 0.25 * 0.9)
    assert got[1] == (False, 0.5 * 0.25 * 0.9)


def test_a_named_anchor_is_measured_at_the_specs_position(client):
    """A name-keyed block's anchor holds no coordinates (its position is the
    catalogue's answer, and a planet's moves). The modal frames it where the
    catalogue puts it now, so the change of SHAPE is measured there; whether
    the name is still that object is the save's to decide, since it is the
    save that resolves names.

    Mutant "the anchor's name not carried" failed, the spec read as another
    kind of key:
        Differing items:
        {'reason': 'identity'} != {'reason': 'move'}
        {'max_move_deg': None} != {'max_move_deg': 0.1698074654842341}
    """
    named = identity.canonical_name("Jupiter", 30.0, rows=2, cols=3,
                                    overlap=0.25, fov_x=2.0, fov_y=1.33)
    turned = {**SPEC, "rotation_deg": 33.5}
    r = client.post("/api/framing/mosaic", json={**turned, "anchor": named})
    assert r.status_code == 200, r.text
    typed = client.post("/api/framing/mosaic",
                        json={**turned, "anchor": ANCHOR})
    assert r.json()["reframe"] == typed.json()["reframe"]
    assert r.json()["reframe"]["carry"] is False


def test_a_blank_anchor_is_no_anchor(client):
    """A block saved before S3 has ``frameAnchor = ""``, and the modal may
    send it verbatim. There is nothing to compare against, so there is no
    ``reframe``, and the request is not refused for it.

    Mutant "reframe always answered" failed:
        AssertionError: assert 'reframe' not in {'frame_fov_x_deg': 2.0, ...}
    """
    r = client.post("/api/framing/mosaic", json={**SPEC, "anchor": ""})
    assert r.status_code == 200, r.text
    assert "reframe" not in r.json()


@pytest.mark.parametrize("bad", ["not json", '{"rows": 2}', "[]",
                                 ANCHOR.replace('"rows":2', '"rows":0')])
def test_a_malformed_anchor_is_a_422_not_a_500(client, bad):
    """An anchor that is not one is the caller's error, said as such.

    Mutant "no validator" (anchor text checked only inside the answer)
    failed every row, the error escaping the route, for example:
        ValueError: an anchor is canonical JSON, and this is not JSON
        (Expecting value: line 1 column 1 (char 0)): 'not json'
        ValueError: anchor rows must be a whole number of at least 1, not 0
    """
    r = client.post("/api/framing/mosaic", json={**SPEC, "anchor": bad})
    assert r.status_code == 422, r.text


def test_the_gate_is_unchanged(client):
    """The route still needs ``CAP_VIEW_SITE_DERIVED``, anchor or not: a
    viewer is refused, an operator answered. The anchor adds nothing
    site-derived (a move and a threshold, from the geometry the caller sent),
    and it must not become a reason to open the route.

    Mutant "gate dropped" (the ``require`` dependency removed from the
    route) failed, the viewer answered in full:
        AssertionError: {"panels":[...],...,"reframe":{"carry":false,
        "max_move_deg":0.16832971593624388,"threshold_deg":0.16625,
        "reason":"move"}}
        assert 200 == 403
    """
    body = {**_moved(10.1), "anchor": ANCHOR}
    set_active_provider(FakeAuthProvider(principal_for_role("viewer")))
    refused = client.post("/api/framing/mosaic", json=body)
    assert refused.status_code == 403, refused.text
    set_active_provider(FakeAuthProvider(principal_for_role("operator")))
    ok = client.post("/api/framing/mosaic", json=body)
    assert ok.status_code == 200, ok.text
    assert ok.json()["reframe"]["carry"] is False
    # What it enforces and what it declares, read as the boot assertion
    # reads them: one capability, the same one as before the anchor.
    route = next(r for r in framing.router.routes
                 if getattr(r, "path", "") == "/api/framing/mosaic")
    assert rbac._dependency_caps(route) == {CAP_VIEW_SITE_DERIVED}
    assert rbac._marker_caps(route) == {CAP_VIEW_SITE_DERIVED}


@pytest.fixture
def real_client():
    """The shipped app, for its 422 handler: a NaN in a refused request is
    echoed in the error's ``input``, and FastAPI's own handler cannot write
    it (``app._request_validation_error`` names it instead). No lifespan, so
    nothing starts; an operator, so the gate is not what answers."""
    import astrodeck.api.app as app_module
    set_active_provider(FakeAuthProvider(principal_for_role("operator")))
    return TestClient(app_module.create_app())


def _raw(anchor=None, *, spec_field=None, anchor_field=None,
         token="NaN") -> str:
    """The pinned request with one number spelled as a JSON ``NaN`` or
    ``Infinity`` token, which Python's json (and so the route) accepts."""
    import json
    body = dict(SPEC)
    if spec_field:
        body[spec_field] = "@@"
    if anchor is not None:
        body["anchor"] = dict(anchor)
        if anchor_field:
            body["anchor"][anchor_field] = "@@"
    return json.dumps(body).replace('"@@"', token)


AS_SPEC = {k: SPEC[k] for k in ("ra_hours", "dec_deg", "rows", "cols",
                                "overlap", "rotation_deg", "fov_x_deg",
                                "fov_y_deg")}


@pytest.mark.parametrize("spec_field, anchor_field, token", [
    (None, "rotation_deg", "NaN"), (None, "fov_x_deg", "Infinity"),
    ("rotation_deg", None, "NaN"), ("fov_y_deg", None, "Infinity")],
    ids=["anchor rotation NaN", "anchor fov inf", "spec rotation NaN",
         "spec fov inf"])
def test_a_non_finite_layout_with_an_anchor_is_a_422_not_a_carry(
        real_client, spec_field, anchor_field, token):
    """Found by the S3-G verifier (#324): before the fix each of these
    answered 200 with ``reframe: {"carry": true, "max_move_deg": 0.0,
    ...}``. A NaN frame's corners are all NaN, ``coords.angular_sep_deg``
    clamps each cosine to 1.0 (``min(1.0, nan)`` is 1.0), so every corner
    "moved" 0.0. A layout nobody can lay out is the caller's error, a 422,
    as a malformed anchor text is.

    Mutant "the anchor form takes NaN" (``MosaicAnchorIn`` without
    ``allow_inf_nan=False``) failed both anchor rows, ``_frame``'s refusal
    escaping the route instead, for example:
        [anchor rotation NaN] ValueError: frame field 'rotation_deg' is not
        finite: nan
    Mutant "no layout check when an anchor is given" (``MosaicSpecIn``'s
    ``_a_reframe_needs_a_finite_layout`` returns at once) failed both spec
    rows the same way, for example:
        [spec fov inf] ValueError: frame field 'fov_y' is not finite: inf

    CONFIRMED BY S4-SAVE (#324), in a private copy of server/. Mutant
    "MosaicAnchorIn allows inf/nan" failed both anchor rows again, the
    refusal escaping the route from ``_frame``:
        [anchor rotation NaN] E   ValueError: frame field 'rotation_deg' is not finite: nan
        [anchor fov inf]      E   ValueError: frame field 'fov_x' is not finite: inf
    Mutant "the frame skips _finite" left all four rows GREEN, by design:
    through this route a non-finite number is refused before ``_frame`` is
    reached, by ``MosaicAnchorIn``'s config for the anchor and by
    ``_a_reframe_needs_a_finite_layout`` for the spec, so ``_finite`` is the
    guard of a direct caller, and the frame cases in
    test_framing_reframe_carry.py are what grade it. No route input reaches
    ``_finite`` alone, so no route case can go red under that mutant; the
    two mutants together would give back #324's 200 carry.
    """
    r = real_client.post("/api/framing/mosaic",
                         content=_raw(AS_SPEC, spec_field=spec_field,
                                      anchor_field=anchor_field, token=token),
                         headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "invalid_request"


def test_without_an_anchor_a_non_finite_spec_is_answered_as_before(
        real_client):
    """Control: the layout check is the anchor's. A spec with no anchor is
    answered as it was before the anchor existed, so no caller that sends no
    anchor sees a new refusal.

    Mutant "the layout check runs with no anchor" (the validator ignores
    whether an anchor was given) failed:
        assert 422 == 200
    """
    r = real_client.post("/api/framing/mosaic",
                         content=_raw(spec_field="rotation_deg"),
                         headers={"content-type": "application/json"})
    assert r.status_code == 200, r.text
    assert "reframe" not in r.json()
