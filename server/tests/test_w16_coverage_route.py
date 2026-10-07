# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""GET /api/sequence/coverage and GET /api/flows/{id}/coverage: whether the
banked lights cover their panels, readable by a viewer (#177, the route half;
WP-123). ``sequence/coverage.py`` is the verifier and
``test_w16_sequence_coverage.py`` grades its arithmetic. This file grades what
only the routes can get wrong, in the shape ``test_flows_progress_route.py``
grades the progress card's:

* WHO MAY READ IT. ``CAP_VIEW_STATUS``: a viewer's session gets 200 and a
  request with no credential gets 401, on both routes.
* WHAT IT MAY SAY. A viewer can read it, so it carries nothing derived from
  the site and no sky coordinate of a frame (spec 6.9; a key-name filter
  cannot withhold a value a route computes and names itself, #19). Graded
  twice: the body is byte-identical after the configured site moves, and
  every key in it is on an allow-list, level by level.
* WHICH SESSION. The live route reads the running session; the flow route the
  session ``run_flow`` would pick (``current_for_flow``: the flow's newest by
  ``created_ts``, none when that newest was abandoned), so the map and the
  chip cannot name different ledgers.
* THAT IT STAYS A READ. It runs off the event loop (a header read per frame),
  and it writes nothing: not the session file, not the store.

THE HARNESS is the progress route's: the real app over
``httpx.ASGITransport``, a throwaway config store swept into every module that
holds one, a throwaway flow library and captures directory, sessions seeded as
the files ``SessionStore`` writes. The lights are synthetic header-only FITS
files (``test_w16_sequence_coverage``'s), CRVAL on a made-up panel centre.

MUTATIONS. Each test that guards a branch names the mutant it kills and quotes
the failure. They were run from byte backups of ``api/app.py`` and
``sequence/coverage.py`` inside this worktree, restored byte-identically
(SHA-256 compared) and grepped clean.
"""
from __future__ import annotations

import json
import threading

import httpx
import pytest

import astrodeck.api.app as app_module
import astrodeck.auth.users as users_mod
import astrodeck.config as config_mod
import astrodeck.flows.store as flow_store_module
import astrodeck.hub as hub_module
from astrodeck.auth import (UserStore, configure_provider_from_auth,
                            principal_for_role, reset_active_provider,
                            set_active_provider, sign_session)
from astrodeck.auth.capabilities import CAP_CONTROL_CAPTURE, CAP_VIEW_STATUS
from astrodeck.config import AuthConfig, ConfigStore, Site
from astrodeck.flows.store import FlowStore
from astrodeck.persist import write_json_atomic
from astrodeck.sequence import coverage
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.session import Session, SessionFrame, session_store
from conftest import _sweep_config_store
from test_w16_sequence_coverage import (FOV_X, _frame, _header, _on_tile_light,
                                        _plan, _write)

SESSION_COOKIE = "ad_session"       # providers.SessionCookieProvider.COOKIE_NAME
NAME = "cover me"

# Two SYNTHETIC sites, far from 0 and from each other and not round (the pair
# test_flows_progress_route.py and test_no_route_leaks_the_site_coordinates.py
# use): a value computed from either cannot survive the move by coincidence.
SITE_A = (41.2345678, -73.9876543)
SITE_B = (12.3456789, -45.6789012)
HARNESS_SITE = (33.9988776, -84.1122334)

#: The answer's keys, level by level: held at the wire, so a key added later
#: has to be added here in a diff somebody reads (spec 6.9, #19).
ALLOWED = {
    "top": {"session", "stamping", "note", "groups"},
    "session": {"id", "status"},
    "group": {"group_id", "name", "threshold", "panels"},
    "panel": {"target_id", "name", "row", "col", "frames", "stamped",
              "unstamped", "flagged", "worst_overlap_frac",
              "worst_offset_arcmin", "worst_angle_off_deg"},
}

#: A TARGET with one capture, the smallest graph the flow library takes.
GRAPH = {
    "nodes": [
        {"id": "t", "type": "target", "x": 0, "y": 0,
         "params": {"name": "M42", "ra": "05h 35m 17s", "dec": "-05 23 28"}},
        {"id": "c", "type": "capture", "x": 0, "y": 0,
         "params": {"filter": "L", "exposure": 60.0, "gain": 100, "bin": "1",
                    "count": 3, "goal": 0}}],
    "edges": [{"from": "t", "fromPort": "target", "to": "c", "toPort": "run"}]}


# ------------------------------------------------------------------ harness

def _isolate(tmp_path, monkeypatch) -> ConfigStore:
    """A throwaway config store (``conftest._sweep_config_store``), flow
    library and captures directory, under a made-up SAVED site."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    _sweep_config_store(monkeypatch, store)
    store.set_site(Site(name="harness site", latitude=HARNESS_SITE[0],
                        longitude=HARNESS_SITE[1], elevation_m=250.0))
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    flows = FlowStore(tmp_path / "flows")
    monkeypatch.setattr(app_module, "flow_store", flows)
    monkeypatch.setattr(flow_store_module, "flow_store", flows)
    reset_active_provider()
    coverage.clear_memo()
    return store


@pytest.fixture(autouse=True)
def _restore_provider():
    """The provider and the session-secret interlock are process globals."""
    yield
    reset_active_provider()
    coverage.clear_memo()


class _Fixed:
    """Every request resolves to one principal (the RBAC suite's pattern)."""

    name = "fake"

    def __init__(self, principal) -> None:
        self._principal = principal

    async def resolve(self, request):
        return self._principal


class Api:
    def __init__(self, client: httpx.AsyncClient, store: ConfigStore,
                 tmp_path) -> None:
        self.client = client
        self.store = store
        self.tmp = tmp_path

    async def save_flow(self) -> str:
        r = await self.client.post("/api/flows", json={
            "flow": {"name": NAME, "graph": GRAPH}})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    async def flow(self, fid: str) -> httpx.Response:
        return await self.client.get(f"/api/flows/{fid}/coverage")

    async def live(self) -> httpx.Response:
        return await self.client.get("/api/sequence/coverage")


@pytest.fixture
async def api(tmp_path, monkeypatch):
    store = _isolate(tmp_path, monkeypatch)
    app = app_module.create_app()
    # Again after the app is built: a module first imported by create_app
    # would otherwise keep the real store.
    _sweep_config_store(monkeypatch, store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://testserver") as client:
        yield Api(client, store, tmp_path)


def _seed(plan: SequencePlan, frames: list[SessionFrame], *, status: str,
          created: float, flow_id: str | None = None) -> Session:
    """A session written as the file ``SessionStore.save`` writes, with the
    timestamps the test names (``save`` stamps ``updated_ts`` from the
    clock, and a test about which session wins must not depend on how finely
    it ticks)."""
    s = Session(name=f"s@{created}", created_ts=created, updated_ts=created,
                status=status, plan=plan, nights=["night-1"], frames=frames,
                origin="flow" if flow_id else "plan", origin_id=flow_id or "")
    path = session_store._path(s.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, s.model_dump(), backup=False)
    return s


def _scene(tmp_path, *, rows: int = 1, cols: int = 1):
    """A panel with one light on its tile and one half a tile off: the
    answer has a flagged frame in it, so the body is worth comparing."""
    on = _on_tile_light(tmp_path, "on.fits")
    off = _on_tile_light(tmp_path, "off.fits", dx=FOV_X / 2.0)
    bare = _write(tmp_path / "bare.fits",
                  _header(ra_hours=5.4321, dec_deg=23.4567, stamped=False))
    plan = _plan(pa=0.0, rows=rows, cols=cols)
    return plan, [_frame(on), _frame(off), _frame(bare)]


# ============================================================== who may read

class TestWhoMayReadIt:
    async def test_a_viewer_reads_both_and_no_credential_is_401(
            self, api, monkeypatch, tmp_path):
        """A real local-login viewer (a signed session cookie, resolved by
        the real cookie provider) gets 200 on both routes; the same requests
        with no cookie get 401. The viewer is shown to BE a viewer by a
        control route refusing it, so the 200 cannot come from an admin in
        disguise.

        RED under mutation "gated on control.capture" (``CAP_VIEW_STATUS`` ->
        ``CAP_CONTROL_CAPTURE`` in a route's dependency and declaration):

            AssertionError: {"detail":"capability not held"}
            assert 403 == 200
        """
        fid = await api.save_flow()
        plan, frames = _scene(tmp_path)
        _seed(plan, frames, status="dormant", created=100.0, flow_id=fid)
        users = UserStore(path=api.tmp / "users.json")
        monkeypatch.setattr(users_mod, "user_store", users)
        api.store.cfg().auth = AuthConfig(methods=["local"],
                                          local_enabled_first_run=False)
        configure_provider_from_auth(api.store.cfg().auth)
        viewer = users.create(username="viewer@example.com",
                              password="viewer-password", role="viewer")
        cookie = sign_session(viewer.role, email=viewer.email,
                              jti=f"j-{viewer.id}", authn="local",
                              subject=viewer.id,
                              account_epoch=viewer.session_epoch)

        assert (await api.flow(fid)).status_code == 401
        assert (await api.live()).status_code == 401

        api.client.cookies.set(SESSION_COOKIE, cookie)
        refused = await api.client.post("/api/flows", json={
            "flow": {"name": "x", "graph": GRAPH}})
        assert refused.status_code == 403, (
            "premise: the cookie resolves to a viewer, who may not save a flow")
        assert principal_for_role("viewer").has(CAP_VIEW_STATUS)
        assert not principal_for_role("viewer").has(CAP_CONTROL_CAPTURE)

        for r in (await api.flow(fid), await api.live()):
            assert r.status_code == 200, r.text
        assert (await api.flow(fid)).json()["session"]["status"] == "dormant"

    def test_both_are_declared_before_their_parameterised_routes(self):
        """Declaration order is match order: ``/api/flows/{flow_id}/coverage``
        up with the other static-before-parameterised flow routes (it stays
        there for the day ``{flow_id}`` widens to a path), as the progress
        route does.

        RED under mutation "declared after get_flow" (the route moved to just
        after ``GET /api/flows/{flow_id}``):

            AssertionError: GET /api/flows/{flow_id}/coverage is declared
            after GET /api/flows/{flow_id}
            assert 50 < 49
        """
        app = app_module.create_app()
        order = [getattr(r, "path", "") for r in app.routes
                 if "GET" in (getattr(r, "methods", None) or ())]
        assert "/api/sequence/coverage" in order
        assert order.index("/api/flows/{flow_id}/coverage") < order.index(
            "/api/flows/{flow_id}"), (
            "GET /api/flows/{flow_id}/coverage is declared after "
            "GET /api/flows/{flow_id}")


# ============================================================ what it may say

class TestWhatItMaySay:
    @pytest.mark.parametrize("role", ["viewer", "admin"])
    async def test_the_body_does_not_move_with_the_site(
            self, api, tmp_path, role):
        """With a synthetic site configured, the body is byte-identical after
        the site moves, on both routes, for a viewer and for a caller who
        may see the site. Valued (a flagged frame, a worst offset), so the
        equality is over real numbers: a value computed from the site would
        not survive the move.

        RED under mutation "a site-derived key on the panel"
        (``_coverage_payload`` adding ``alt = 90 - abs(site.latitude - 20)``
        to every panel row), for both roles, and the allow-list test below:

            AssertionError: the flow body moved with the site
            assert b'{"session":...19999999}]}]}' ==
            b'{"session":....3456789}]}]}'
              At index 416 diff: b'6' != b'8'
        """
        fid = await api.save_flow()
        plan, frames = _scene(tmp_path)
        _seed(plan, frames, status="active", created=100.0, flow_id=fid)
        set_active_provider(_Fixed(principal_for_role(role)))
        bodies: dict[str, list[bytes]] = {"flow": [], "live": []}
        for lat, lon in (SITE_A, SITE_B):
            api.store.set_site(Site(name="fixture", latitude=lat,
                                    longitude=lon, elevation_m=10.0,
                                    is_default=False))
            assert app_module.hub.site["latitude"] == lat, (
                "premise: the hub reads the site this test configured")
            for kind, r in (("flow", await api.flow(fid)),
                            ("live", await api.live())):
                assert r.status_code == 200, r.text
                bodies[kind].append(r.content)
        for kind, pair in bodies.items():
            assert pair[0] == pair[1], f"the {kind} body moved with the site"
            got = json.loads(pair[0])
            panel = got["groups"][0]["panels"][0]
            assert (panel["frames"], panel["stamped"], panel["flagged"]) == (
                3, 2, 1), "premise: the lights are read, so there is data"
            assert panel["worst_offset_arcmin"] > 1.0, (
                "premise: a real offset is in the body")

    @pytest.mark.parametrize("which", ["flow", "live"])
    async def test_every_key_is_on_the_allow_list(self, api, tmp_path, which):
        """Every level of a populated answer, walked at the wire, for a
        viewer. No key carries a frame's sky position or anything computed
        from the site.

        RED under mutation "a sky coordinate on the panel" (``_panel_row``
        adding ``"ra_hours": member.ra_hours``), for both routes:

            AssertionError: panel carries keys outside the allow-list
            assert {'col', 'flag...', 'row', ...} <= {'col', 'flag...stamped',
            ...}
              Extra items in the left set: 'ra_hours'
        """
        fid = await api.save_flow()
        plan, frames = _scene(tmp_path, rows=1, cols=2)
        _seed(plan, frames, status="active", created=100.0, flow_id=fid)
        set_active_provider(_Fixed(principal_for_role("viewer")))
        r = await (api.flow(fid) if which == "flow" else api.live())
        assert r.status_code == 200, r.text
        got = r.json()
        seen: dict[str, set] = {level: set() for level in ALLOWED}
        seen["top"] |= set(got)
        seen["session"] |= set(got["session"])
        for group in got["groups"]:
            seen["group"] |= set(group)
            for panel in group["panels"]:
                seen["panel"] |= set(panel)
        assert all(seen.values()), "premise: every level was walked"
        for level, keys in seen.items():
            assert keys <= ALLOWED[level], (
                f"{level} carries keys outside the allow-list")
        assert seen["panel"] == ALLOWED["panel"], (
            "the allow-list holds no key the answer does not carry")


# ============================================================ which session

class TestWhichSession:
    async def test_the_live_route_reads_the_running_session(self, api, tmp_path):
        """The ACTIVE session: another, dormant, session in the store is not
        what the run is writing."""
        plan, frames = _scene(tmp_path)
        live = _seed(plan, frames, status="active", created=200.0)
        _seed(plan, [], status="dormant", created=300.0)
        got = (await api.live()).json()
        assert got["session"] == {"id": live.id, "status": "active"}
        assert got["groups"][0]["panels"][0]["frames"] == 3

    async def test_no_run_is_an_empty_answer_not_an_error(self, api):
        """Nothing running is a state, not a fault: 200, no session, no groups
        and no note (a poll that only runs while the modal is open in run
        mode may catch the gap between two runs)."""
        r = await api.live()
        assert r.status_code == 200, r.text
        assert r.json() == {"session": None, "stamping": False, "note": None,
                            "groups": []}

    async def test_the_flow_route_reads_the_newest_session(self, api, tmp_path):
        """The session ``run_flow`` would pick (``current_for_flow``): the
        flow's newest by ``created_ts``, whatever its status. An older
        session of the same flow, and a newer session of another flow, are
        not it.

        RED under mutation "the oldest session" (``_flow_coverage_payload``
        reading the flow's oldest session by ``created_ts`` rather than
        ``current_for_flow``); the next test fails too, with the older
        session answered where there should be none:

            AssertionError: assert '4889e23219cc...ef3997ee2be5b' ==
            '603df36007ea...c6382e559c27d'
        """
        fid = await api.save_flow()
        plan, frames = _scene(tmp_path)
        _seed(plan, [], status="dormant", created=100.0, flow_id=fid)
        newer = _seed(plan, frames, status="dormant", created=200.0,
                      flow_id=fid)
        _seed(plan, [], status="dormant", created=900.0, flow_id="elsewhere")
        got = (await api.flow(fid)).json()
        assert got["session"]["id"] == newer.id
        assert got["groups"][0]["panels"][0]["frames"] == 3

    async def test_an_abandoned_newest_session_is_no_session(self, api,
                                                             tmp_path):
        """The operator closed it (``current_for_flow``): nothing to show, and
        the older session is not reopened."""
        fid = await api.save_flow()
        plan, frames = _scene(tmp_path)
        _seed(plan, frames, status="dormant", created=100.0, flow_id=fid)
        _seed(plan, frames, status="abandoned", created=200.0, flow_id=fid)
        got = (await api.flow(fid)).json()
        assert got["session"] is None
        assert got["groups"] == []

    async def test_a_flow_with_no_session_answers_empty(self, api):
        fid = await api.save_flow()
        r = await api.flow(fid)
        assert r.status_code == 200, r.text
        assert r.json()["session"] is None

    async def test_an_unknown_flow_is_404(self, api):
        """As the progress route answers it, so a client handles both alike.

        RED under mutation "KeyError unmapped" (the ``except KeyError`` around
        ``flow_store.get`` removed); the transport re-raises the handler's
        exception:

            KeyError: 'no-such-flow'
        """
        r = await api.flow("no-such-flow")
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == {"code": "not_found"}


# ============================================================ what it reports

class TestWhatItReports:
    async def test_the_answer_is_the_verifiers(self, api, tmp_path):
        """The flagged and unstamped counts reach the wire as the verifier
        counted them: three banked, two solved, one of those half a tile
        off."""
        fid = await api.save_flow()
        plan, frames = _scene(tmp_path)
        _seed(plan, frames, status="dormant", created=100.0, flow_id=fid)
        got = (await api.flow(fid)).json()
        group = got["groups"][0]
        assert group["threshold"] == pytest.approx(0.9), (
            "a 1 x 1 block is a lone panel")
        panel = group["panels"][0]
        assert (panel["frames"], panel["stamped"], panel["unstamped"],
                panel["flagged"]) == (3, 2, 1, 1)
        assert panel["worst_overlap_frac"] == pytest.approx(0.5, abs=0.01)

    async def test_a_mosaic_with_solving_off_says_it_cannot_be_checked(
            self, api, tmp_path):
        """``solve_saved_lights`` off: the answer says frames are not being
        plate-solved instead of leaving an empty report to read as covered,
        and says what the switch is. On, there is no note. Stamping is never
        turned on from here."""
        fid = await api.save_flow()
        plan, frames = _scene(tmp_path)
        _seed(plan, frames, status="dormant", created=100.0, flow_id=fid)
        off = (await api.flow(fid)).json()
        assert off["stamping"] is False
        assert off["note"] == (
            "coverage cannot be checked: frames are not being plate-solved")
        api.store.cfg().solve_saved_lights = True
        on = (await api.flow(fid)).json()
        assert on["stamping"] is True
        assert on["note"] is None
        assert api.store.cfg().solve_saved_lights is True


# ===================================================== it stays a cheap read

class TestItStaysARead:
    @pytest.mark.parametrize("which", ["flow", "live"])
    async def test_the_work_runs_off_the_event_loop(self, api, tmp_path,
                                                    monkeypatch, which):
        """A header read per frame is blocking work: the verifier runs on a
        worker thread, never on the loop's.

        RED under mutation "on the event loop" (the route calling
        ``coverage_report`` inline rather than through ``asyncio.to_thread``):

            AssertionError: the report ran on the event loop's thread
        """
        fid = await api.save_flow()
        plan, frames = _scene(tmp_path)
        _seed(plan, frames, status="active", created=100.0, flow_id=fid)
        seen: list[int] = []
        real = coverage.coverage_report

        def spy(session):
            seen.append(threading.get_ident())
            return real(session)

        monkeypatch.setattr(coverage, "coverage_report", spy)
        r = await (api.flow(fid) if which == "flow" else api.live())
        assert r.status_code == 200, r.text
        assert seen, "premise: the verifier was asked"
        assert seen[0] != threading.get_ident(), (
            "the report ran on the event loop's thread")

    @pytest.mark.parametrize("which", ["flow", "live"])
    async def test_it_writes_nothing(self, api, tmp_path, monkeypatch, which):
        """The session lock and the background stamping task make a write
        from here a race, so the answer is derived on demand: neither store
        write is called, and the session's file is byte-identical after.

        RED under mutation "the report saved to the ledger" (the route
        calling ``session_store.save(session)``):

            AssertionError: a coverage read wrote the session store
        """
        fid = await api.save_flow()
        plan, frames = _scene(tmp_path)
        s = _seed(plan, frames, status="active", created=100.0, flow_id=fid)
        path = session_store._path(s.id)
        before = path.read_bytes()

        def refuse(*a, **kw):
            raise AssertionError("a coverage read wrote the session store")

        monkeypatch.setattr(session_store, "save", refuse)
        monkeypatch.setattr(session_store, "save_run_state", refuse)
        r = await (api.flow(fid) if which == "flow" else api.live())
        assert r.status_code == 200, r.text
        assert path.read_bytes() == before
