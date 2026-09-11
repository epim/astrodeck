"""D-RIG-4: the relative mount nudge.

TWO LAYERS, AND THE SPLIT IS DELIBERATE.

The geometry (``astrodeck.mount_offset``) is pure and is tested directly - no
app, no hub, no device. That is where the cos(dec) rule lives and it is the
part that is wrong-but-plausible if nobody checks it.

The ROUTE tests use a THROWAWAY FastAPI app built here, not ``create_app()``.
``POST /api/mount/nudge`` itself belongs to app.py (task S7L) because it needs
``_horizon_block``, ``_solar_block`` and ``_spawn`` - all closures/module
globals of that file. ``_nudge_route`` below is a line-for-line mirror of the
handler delivered in S7g's report as the app.py patch: same order of
operations, same guards, same response shape. It exercises the real
``_spawn``, the real ``require()``/``declare`` RBAC pair and the real
``hub.from_mount_frame``; only ``_horizon_block``/``_solar_block`` are stood in
for, because those two are create_app closures over site config.

If the mirror and the patch ever drift, this file is testing a route that does
not exist - which is why the handler body is kept verbatim rather than
paraphrased.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from typing import Literal

import astrodeck.api.app as app_module
from astrodeck.auth import (CAP_CONTROL_MOUNT, Principal, principal_for_role,
                            require, reset_active_provider, set_active_provider)
from astrodeck.auth.rbac import assert_route_capabilities, declare
from astrodeck.devices.base import DeviceError
from astrodeck import mount_offset
from astrodeck.mount_offset import (MAX_NUDGE_ARCMIN, MIN_NUDGE_ARCMIN,
                                    nudge_target, parse_nudge)

# ------------------------------------------------------------------ geometry

#: hours of RA per arcminute of sky at dec 0 (15 deg/hour x 60 arcmin/deg).
_HOURS_PER_ARCMIN = 1.0 / 60.0 / 15.0


def test_a_nudge_at_dec_0_moves_ra_by_arcmin_over_900():
    ra, dec = nudge_target(6.0, 0.0, "ra", 10.0)
    assert ra == pytest.approx(6.0 + 10.0 * _HOURS_PER_ARCMIN, abs=1e-12)
    assert dec == pytest.approx(0.0, abs=1e-12)


def test_a_nudge_at_dec_60_moves_twice_the_ra():
    """cos(60) = 0.5, so 10 arcmin ON THE SKY is 20 arcmin OF RA.

    This is the assertion the whole module exists for: an implementation that
    forgets the cos(dec) division passes every equator test and fails here.
    """
    flat = nudge_target(6.0, 0.0, "ra", 10.0)[0] - 6.0
    at60 = nudge_target(6.0, 60.0, "ra", 10.0)[0] - 6.0
    assert at60 == pytest.approx(2.0 * flat, rel=1e-9)
    assert at60 == pytest.approx(20.0 * _HOURS_PER_ARCMIN, rel=1e-9)


def test_a_nudge_at_dec_89_9_is_finite():
    ra, dec = nudge_target(6.0, 89.9, "ra", 10.0)
    assert 0.0 <= ra < 24.0
    assert dec == pytest.approx(89.9)
    # cos(89.9) = 1.745e-3, so 10 arcmin of sky is ~5730 arcmin = ~6.37 h of RA.
    assert (ra - 6.0) == pytest.approx(6.3663, abs=1e-3)


def test_an_ra_nudge_at_the_pole_saturates_at_half_a_turn():
    """cos(90) is 6.12e-17, not 0 - so nothing raises and an unbounded offset
    comes out near 1e16 hours, wraps to something in [0, 24) and reaches the
    mount looking like an ordinary target. The saturation is what stops that,
    so the test pins the saturated VALUE and not merely "it is a number"."""
    ra, dec = nudge_target(6.0, 90.0, "ra", 600.0)
    assert dec == pytest.approx(90.0)
    assert ra == pytest.approx(18.0)          # 6 h + the 12 h bound
    ra_w, _ = nudge_target(6.0, -90.0, "ra", -600.0)
    assert ra_w == pytest.approx(18.0)        # 6 h - 12 h, wrapped


def test_a_dec_nudge_past_the_pole_clamps_and_leaves_ra_alone():
    ra, dec = nudge_target(6.0, 89.5, "dec", 60.0)   # +1 degree from 89.5
    assert dec == pytest.approx(90.0)
    assert ra == pytest.approx(6.0)
    # ...and the south pole is the same rule.
    ra_s, dec_s = nudge_target(6.0, -89.5, "dec", -60.0)
    assert dec_s == pytest.approx(-90.0)
    assert ra_s == pytest.approx(6.0)


def test_a_dec_nudge_inside_the_pole_is_just_arcmin_over_60():
    ra, dec = nudge_target(6.0, 40.0, "dec", -30.0)
    assert dec == pytest.approx(39.5)
    assert ra == pytest.approx(6.0)


def test_an_ra_nudge_wraps_23h59_round_to_zero():
    ra, _ = nudge_target(23.99, 0.0, "ra", 600.0)   # +0.666.. h
    assert 0.0 <= ra < 24.0
    assert ra == pytest.approx((23.99 + 600.0 * _HOURS_PER_ARCMIN) % 24.0)
    # and westward off the bottom
    ra_w, _ = nudge_target(0.01, 0.0, "ra", -600.0)
    assert 0.0 <= ra_w < 24.0
    assert ra_w > 23.0


def test_parse_nudge_rejects_half_an_arcminute_and_seven_hundred():
    with pytest.raises(ValueError):
        parse_nudge("ra", 0.5)
    with pytest.raises(ValueError):
        parse_nudge("ra", 700.0)
    # the bound is on the ABSOLUTE value: west is as big a move as east.
    with pytest.raises(ValueError):
        parse_nudge("dec", -0.5)
    with pytest.raises(ValueError):
        parse_nudge("dec", -700.0)


def test_parse_nudge_accepts_the_bounds_themselves():
    assert parse_nudge("ra", MIN_NUDGE_ARCMIN) == MIN_NUDGE_ARCMIN
    assert parse_nudge("dec", MAX_NUDGE_ARCMIN) == MAX_NUDGE_ARCMIN
    assert parse_nudge("ra", -5.0) == -5.0


def test_parse_nudge_rejects_an_unknown_axis_and_a_nan():
    with pytest.raises(ValueError):
        parse_nudge("alt", 5.0)
    with pytest.raises(ValueError):
        parse_nudge("ra", float("nan"))
    with pytest.raises(ValueError):
        parse_nudge("ra", float("inf"))


def test_nudge_target_refuses_an_unknown_axis_on_its_own():
    """The geometry does not trust its caller to have called parse_nudge: an
    axis it does not understand is a refusal, never a silent no-op that returns
    the position unchanged and reads, upstream, as a mount that did not move."""
    with pytest.raises(ValueError):
        nudge_target(6.0, 40.0, "alt", 5.0)


# ------------------------------------------------------- the route, mirrored


class NudgeBody(BaseModel):
    axis: Literal["ra", "dec"]
    #: signed; + is east / north. allow_inf_nan=False for the same reason
    #: GotoBody.rotation_deg has it: a NaN must never reach the geometry.
    arcmin: float = Field(..., allow_inf_nan=False)


class _Tel:
    """A mount that reports a fixed position and records nothing else."""

    connected = True
    backend = ""

    def __init__(self, ra: float = 6.0, dec: float = 40.0):
        self.ra, self.dec = ra, dec

    async def get_position(self) -> tuple[float, float]:
        return self.ra, self.dec


class _Rig:
    """Everything the mirrored handler touches, in one place."""

    def __init__(self):
        self.tel = _Tel()
        self.slews: list[tuple[float, float]] = []
        self.horizon: dict | None = None
        self.solar: dict | None = None


def _build_app(rig: _Rig) -> FastAPI:
    app = FastAPI()

    def _horizon_block(ra_hours: float, dec_deg: float) -> dict | None:
        return rig.horizon

    def _solar_block(ra_hours: float, dec_deg: float) -> dict | None:
        return rig.solar

    async def _plain_goto(ra_hours: float, dec_deg: float) -> None:
        rig.slews.append((ra_hours, dec_deg))

    # --- BEGIN mirror of the S7L app.py patch --------------------------------
    @app.post("/api/mount/nudge",
              dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.slew"})
    async def nudge(body: NudgeBody):
        try:
            tel = app_module.hub.require("telescope")
        except DeviceError as e:
            raise app_module._err(e)
        try:
            arcmin = parse_nudge(body.axis, body.arcmin)
        except ValueError as e:
            raise HTTPException(422, detail={"detail": str(e),
                                             "code": "out_of_range"})
        try:
            cur_ra, cur_dec = await tel.get_position()
        except DeviceError as e:
            raise app_module._err(e)
        # The mount reports its OWN frame; a real Alpaca mount reports JNOW.
        # Convert FIRST and slew J2000 - the frame every other target on this
        # server is in. Nudging in the mount's frame and slewing the answer as
        # J2000 would add a precession-sized error to EVERY tap.
        from_ra, from_dec = await app_module.hub.from_mount_frame(
            tel, cur_ra, cur_dec)
        moved = mount_offset.nudge(from_ra, from_dec, body.axis, arcmin)
        to_ra, to_dec = moved.ra_hours, moved.dec_deg
        blocked = _horizon_block(to_ra, to_dec)
        if blocked is not None:
            raise HTTPException(409, detail=blocked)
        solar = _solar_block(to_ra, to_dec)
        if solar is not None:
            raise HTTPException(409, detail=solar)
        started = app_module._spawn("goto", _plain_goto(to_ra, to_dec))
        return {**started,
                "from": {"ra_hours": from_ra, "dec_deg": from_dec},
                "to": {"ra_hours": to_ra, "dec_deg": to_dec},
                "arcmin": arcmin,
                "clamped": moved.clamped,
                "achieved_arcmin": moved.achieved_arcmin}
    # --- END mirror ----------------------------------------------------------

    return app


@pytest.fixture(autouse=True)
def _clean_auth_and_lanes():
    yield
    reset_active_provider()
    for name, task in list(app_module.hub._busy.items()):
        if task is not None and not task.done():
            task.cancel()
        app_module.hub._busy.pop(name, None)


class FakeAuthProvider:
    """A FIXED principal for every request (mirrors test_api_mount.py)."""

    name = "fake"

    def __init__(self, principal: "Principal | None"):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture
def rig():
    return _Rig()


@pytest.fixture
def client(rig, monkeypatch):
    monkeypatch.setattr(app_module.hub, "require", lambda role: rig.tel)
    with TestClient(_build_app(rig)) as c:
        yield c


def _await_goto(client) -> None:
    """Run the spawned goto lane to completion.

    ``_spawn`` returns as soon as the task is created, so the response is back
    before the mount has been touched. Without this the "did it slew" half of
    every route test would be an assertion against an empty list that happened
    to be true - the shape of a test that cannot fail.
    """
    task = app_module.hub._busy.get("goto")
    assert task is not None, "the route never spawned a goto lane"

    async def _wait():
        await asyncio.wait_for(asyncio.shield(task), 2.0)

    client.portal.call(_wait)


def test_the_mirrored_route_passes_the_rbac_boot_assertion(rig):
    """The declare/require pair on the real patch has to survive create_app's
    own boot check - a nudge REACHES Telescope.slew, so it must carry
    control.mount. Asserting it here means S7L's paste cannot boot-fail."""
    assert_route_capabilities(_build_app(rig))


def test_an_operator_nudge_slews_the_offset_target(client, rig):
    r = client.post("/api/mount/nudge", json={"axis": "dec", "arcmin": 30.0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["started"] == "goto"
    assert body["arcmin"] == 30.0
    assert body["from"]["ra_hours"] == pytest.approx(6.0)
    assert body["from"]["dec_deg"] == pytest.approx(40.0)
    assert body["to"]["dec_deg"] == pytest.approx(40.5)
    assert body["to"]["ra_hours"] == pytest.approx(6.0)
    _await_goto(client)
    assert rig.slews == [(pytest.approx(6.0), pytest.approx(40.5))]


def test_a_jnow_mount_is_nudged_in_j2000(client, rig, monkeypatch):
    """Convert, THEN offset.

    The mount here reports JNOW (an Alpaca backend), so the position the route
    reads is not in the frame the rest of the server speaks. The route has to
    hand it to ``hub.from_mount_frame`` BEFORE ``nudge_target`` touches it,
    otherwise the J2000 target it slews to is the JNOW position plus the
    offset - off by a whole epoch's precession on every single tap.

    The precession function is stubbed with a large, unmistakable shift so the
    assertion is about the ORDER OF OPERATIONS and not about astropy (and so
    the test never reaches for IERS data). The real transform is hub's, and
    hub's tests own it.
    """
    import astrodeck.hub as hub_module

    rig.tel.backend = "alpaca"          # what _mount_expects_jnow keys on
    monkeypatch.setattr(app_module.hub, "_mount_wants_jnow", True)
    monkeypatch.setattr(hub_module, "precess_jnow_to_j2000",
                        lambda ra, dec, when=None: (ra - 0.5, dec - 2.0))

    r = client.post("/api/mount/nudge", json={"axis": "dec", "arcmin": 30.0})
    assert r.status_code == 200, r.text
    body = r.json()
    # JNOW (6.0, 40.0) -> J2000 (5.5, 38.0), then +30' of dec.
    assert body["from"]["ra_hours"] == pytest.approx(5.5)
    assert body["from"]["dec_deg"] == pytest.approx(38.0)
    assert body["to"]["dec_deg"] == pytest.approx(38.5)
    # ...and the slew that actually went out carries the J2000 pair, NOT 40.5.
    _await_goto(client)
    assert rig.slews == [(pytest.approx(5.5), pytest.approx(38.5))]


def test_a_nudge_below_the_horizon_is_the_gotos_409(client, rig):
    rig.horizon = {"detail": "target is below the horizon (alt -3.2 deg)",
                   "code": "below_horizon"}
    r = client.post("/api/mount/nudge", json={"axis": "dec", "arcmin": 30.0})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "below_horizon"


def test_a_nudge_into_the_sun_cone_is_a_409(client, rig):
    rig.solar = {"detail": "target is inside the sun-exclusion cone",
                 "code": "sun_exclusion"}
    r = client.post("/api/mount/nudge", json={"axis": "ra", "arcmin": 30.0})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "sun_exclusion"


@pytest.mark.parametrize("arcmin", [0.5, 700.0, -0.5, -700.0, 0.0])
def test_an_out_of_range_nudge_is_422_and_the_mount_never_moves(
        client, rig, arcmin):
    r = client.post("/api/mount/nudge", json={"axis": "ra", "arcmin": arcmin})
    assert r.status_code == 422
    assert r.json()["detail"] == {
        "detail": "a nudge is 1 to 600 arcminutes", "code": "out_of_range"}
    assert rig.slews == []


def test_an_unknown_axis_is_stopped_by_the_body_model_itself(client, rig):
    """Two layers refuse "alt", and this pins the OUTER one.

    ``parse_nudge`` refuses an unknown axis too, so a 422 alone proves nothing
    about where it came from - drop the ``Literal`` and the test still passes.
    FastAPI's own validation failure carries a LIST of errors where ours is a
    dict, so that is what separates them.
    """
    r = client.post("/api/mount/nudge", json={"axis": "alt", "arcmin": 30.0})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list), r.text
    assert rig.slews == []


def test_a_viewer_cannot_nudge(client, rig):
    set_active_provider(FakeAuthProvider(principal_for_role("viewer")))
    r = client.post("/api/mount/nudge", json={"axis": "ra", "arcmin": 30.0})
    assert r.status_code == 403
    assert rig.slews == []


def test_an_operator_can_nudge(client, rig):
    set_active_provider(FakeAuthProvider(principal_for_role("operator")))
    r = client.post("/api/mount/nudge", json={"axis": "ra", "arcmin": 30.0})
    assert r.status_code == 200, r.text


# ============================ what the nudge ACTUALLY moved, when it is clamped
#
# Both clamps in ``mount_offset`` are silent, and both bite exactly where a
# nudge is most used: polar alignment and circumpolar targets. Near the pole the
# cos(dec) division saturates against ``_MAX_RA_OFFSET_HOURS``, so 600 arcmin
# east at dec 89.9 is 18.9 arcmin of sky; a dec tap into +90 stops at the pole.
# The response echoed the REQUESTED size either way, so the pad reported a
# correction it had not made and the operator waited for a field that was never
# going to arrive.

def test_an_unclamped_nudge_says_so_and_reports_what_it_asked_for():
    """The control. Without it every assertion below would also hold for a
    function that always answered ``clamped``."""
    out = mount_offset.nudge(6.0, 40.0, "ra", 10.0)
    assert out.clamped is False
    assert out.achieved_arcmin == pytest.approx(10.0, abs=1e-3)
    out = mount_offset.nudge(6.0, 40.0, "dec", -30.0)
    assert out.clamped is False
    assert out.achieved_arcmin == pytest.approx(-30.0, abs=1e-3)


def test_an_ra_nudge_near_the_pole_reports_the_sky_it_could_cover():
    """At dec 89.9 a 600-arcmin request is 382 HOURS of RA, saturated to 12.
    Twelve hours at that declination is 18.85 arcmin of sky, and that is the
    number the toast has to say."""
    out = mount_offset.nudge(6.0, 89.9, "ra", 600.0)
    assert out.clamped is True
    assert out.achieved_arcmin == pytest.approx(18.85, abs=0.01)
    assert abs(out.achieved_arcmin) < 600.0


def test_an_ra_nudge_at_the_pole_itself_reports_approximately_nothing():
    """And that is the honest answer: half a turn of RA is no distance at all
    when the tube is standing on the axis."""
    out = mount_offset.nudge(6.0, 90.0, "ra", 600.0)
    assert out.clamped is True
    assert out.achieved_arcmin == pytest.approx(0.0, abs=1e-3)


def test_a_dec_nudge_into_the_pole_reports_the_degrees_it_got():
    """40 arcmin north from dec 89.5 runs into +90 after 30."""
    out = mount_offset.nudge(6.0, 89.5, "dec", 40.0)
    assert out.dec_deg == pytest.approx(90.0)
    assert out.clamped is True
    assert out.achieved_arcmin == pytest.approx(30.0)


def test_a_dec_nudge_that_lands_exactly_on_the_pole_is_not_clamped():
    """The boundary: asking for exactly the distance that remains is a nudge
    that fully happened, and calling it clamped would put a warning on a move
    that did what it said."""
    out = mount_offset.nudge(6.0, 89.5, "dec", 30.0)
    assert out.dec_deg == pytest.approx(90.0)
    assert out.clamped is False
    assert out.achieved_arcmin == pytest.approx(30.0)


def test_nudge_target_is_the_same_destination_under_the_older_name():
    """The thin wrapper has to stay the same function, or a caller that only
    wants the destination starts getting a different one."""
    for axis, size, dec in (("ra", 10.0, 40.0), ("ra", 600.0, 89.9),
                            ("dec", 30.0, 89.5), ("dec", -45.0, 0.0)):
        full = mount_offset.nudge(6.0, dec, axis, size)
        assert nudge_target(6.0, dec, axis, size) == (full.ra_hours,
                                                      full.dec_deg)


def test_the_route_carries_the_clamp_to_the_client(client, rig):
    """SABOTAGE (run red, restored): echo ``arcmin`` alone again. The toast
    says the pad moved 600 arcmin east when it moved nineteen."""
    rig.tel.dec = 89.9
    r = client.post("/api/mount/nudge", json={"axis": "ra", "arcmin": 600.0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["arcmin"] == 600.0, "the REQUEST is still echoed, unchanged"
    assert body["clamped"] is True
    assert body["achieved_arcmin"] == pytest.approx(18.85, abs=0.01)
    _await_goto(client)


def test_the_route_says_nothing_was_clamped_when_nothing_was(client, rig):
    r = client.post("/api/mount/nudge", json={"axis": "dec", "arcmin": 30.0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["clamped"] is False
    assert body["achieved_arcmin"] == pytest.approx(30.0)
    _await_goto(client)


def test_the_shipped_route_carries_the_two_fields_too(monkeypatch):
    """The mirror above is a copy, and a copy cannot fail when the original
    does. This drives ``api/app.py``'s own handler."""
    from fastapi.testclient import TestClient as _TC

    tel = _Tel(ra=6.0, dec=89.9)
    app = app_module.create_app()
    monkeypatch.setattr(app_module.hub, "require", lambda role: tel)
    monkeypatch.setattr(app_module.hub, "_check_solar",
                        lambda ra, dec, **kw: None, raising=False)
    with _TC(app) as c:
        r = c.post("/api/mount/nudge", json={"axis": "ra", "arcmin": 600.0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["clamped"] is True
    assert body["achieved_arcmin"] == pytest.approx(18.85, abs=0.01)
