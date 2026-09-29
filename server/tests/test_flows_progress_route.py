"""GET /api/flows/{id}/progress: what a flow has banked, readable by a viewer
(#189 S1 item 9, the route half; spec 8 S1 and 6.9; task S1-16).

``flows.progress.flow_progress`` is the pure half and ``test_flows_progress.py``
grades its arithmetic. This file grades what only the route can get wrong:

* WHO MAY READ IT. ``CAP_VIEW_STATUS``: the card chip ("212/315 subs") sits
  on the flow library, which a viewer can open. A viewer's own session gets
  200, and a request carrying no credential gets 401.
* WHAT IT MAY SAY. Because a viewer can read it, it carries nothing derived
  from the site (spec 6.9). A key-name filter cannot withhold a value a route
  computes and names itself (#19), so the answer is graded twice: with a
  synthetic site configured, the body is byte-identical after the site moves,
  and every key in it is on an allow-list.
* WHICH LEDGER, WHICH IDS. The stored graph is compiled with the flow's id,
  the path ``/run`` takes, so the step ids are the ones the ledger counts by.
  The session is the one ``/run`` would pick (``session_store.
  current_for_flow``): the flow's newest by ``created_ts``, whatever became of
  it, and none when that newest one was abandoned. That the chip and Run agree
  in every case is test_flow_session_selection.py. After a CONTINUE (S1-13)
  the same step ids are reported, and they are the ids the engine is
  counting.

THE HARNESS. The real app over ``httpx.ASGITransport`` on the test's own event
loop (no lifespan, so no background service ticks), a throwaway config store
swept into every astrodeck module that holds one, a throwaway flow library and
captures directory. Sessions are seeded as files, the shape ``SessionStore``
writes, except where a test needs the engine's own: the CONTINUE test runs the
real ``SequenceEngine.start``, ledger write and finalize, with only ``_run``
(the imaging loop) replaced.

MUTATIONS. Every test that guards a branch names the mutation of
``api/app.py`` it kills and quotes the failure that mutation produced. They
were run in a copy of ``server/``, so no other suite on the shared tree could
import a mutant: each was written over a byte backup of the copy's ``app.py``,
only this file was run, and the copy was restored and SHA-256 compared after
every mutant. The real ``app.py`` was hashed before and after and never
written.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx
import pytest

import astrodeck.api.app as app_module
import astrodeck.auth.users as users_mod
import astrodeck.config as config_mod
import astrodeck.flows.store as flow_store_module
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_module
from astrodeck.auth import (UserStore, configure_provider_from_auth,
                            principal_for_role, reset_active_provider,
                            set_active_provider, sign_session)
from astrodeck.auth.capabilities import (CAP_CONTROL_CAPTURE,
                                         CAP_VIEW_SITE_DERIVED,
                                         CAP_VIEW_STATUS)
from astrodeck.config import AuthConfig, ConfigStore, Site
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.progress import flow_progress
from astrodeck.flows.store import FlowStore
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.persist import write_json_atomic
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.session import (Session, SessionFrame, SessionStore,
                                        session_store)

NAME = "progress me"
SESSION_COOKIE = "ad_session"       # providers.SessionCookieProvider.COOKIE_NAME

# Two SYNTHETIC sites, the pair tests/test_no_route_leaks_the_site_coordinates.py
# uses: far from 0 and from each other, not round, and no rounding of one is a
# rounding of the other, so a value computed from either cannot survive the move
# by coincidence.
SITE_A = (41.2345678, -73.9876543)
SITE_B = (12.3456789, -45.6789012)


# ------------------------------------------------------------------ graphs

def _capture(node_id: str, filt: str, *, exposure: float = 60.0,
             count: int = 3) -> dict:
    """A CAPTURE LOOP with no integration goal, so the compile reports no
    loss and ``/run`` needs no ``accept_unmapped``."""
    return {"id": node_id, "type": "capture", "x": 0, "y": 0,
            "params": {"filter": filt, "exposure": exposure, "gain": 100,
                       "bin": "1", "count": count, "goal": 0}}


def _graph(*captures: dict) -> dict:
    """One TARGET (M42) feeding the captures in a chain."""
    nodes = [{"id": "t", "type": "target", "x": 0, "y": 0,
              "params": {"name": "M42", "ra": "05h 35m 17s",
                         "dec": "-05 23 28"}}, *captures]
    edges, prev, port = [], "t", "target"
    for c in captures:
        edges.append({"from": prev, "fromPort": port, "to": c["id"],
                      "toPort": "run"})
        prev, port = c["id"], "complete"
    return {"nodes": nodes, "edges": edges}


#: L (3) then R (2) on one target.
LR = _graph(_capture("c1", "L"), _capture("c2", "R", count=2))
#: The same flow with R's exposure changed: a new recipe, so a new step id
#: for R and for R only (#77, spec 3.3).
LR_R90 = _graph(_capture("c1", "L"), _capture("c2", "R", exposure=90.0,
                                              count=2))
#: A TARGET and a POOL in one flow, so every level of the payload is
#: populated: a block of each kind, grid panels and pool panels.
#: test_progress_locked_angle.py imports it, and relies on every target
#: having a step.
TARGET_AND_POOL = {
    "nodes": [
        {"id": "t", "type": "target", "x": 0, "y": 0,
         "params": {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09",
                    "rotation": -1}},
        {"id": "p", "type": "pool", "x": 50, "y": 0,
         "params": {"members": "M42, M31", "minAlt": 0, "moonSep": 0,
                    "maxHA": 0}},
        _capture("c", "L", count=5)],
    "edges": [{"from": "t", "fromPort": "target", "to": "p", "toPort": "arm"},
              {"from": "p", "fromPort": "target", "to": "c", "toPort": "run"}]}
#: ``TARGET_AND_POOL`` with a 2x2 mosaic after it, one panel skipped, so the
#: site and allow-list tests below walk a mosaic's own keys as well
#: (``grid``, ``skipped`` and, since S5, ``group_id``). With a stage after
#: the pool's, the first TARGET's lane is the pool alone, so it owns no step
#: (spec 1.5).
TARGET_POOL_AND_MOSAIC = {
    "nodes": [
        *TARGET_AND_POOL["nodes"],
        {"id": "m", "type": "target", "x": 150, "y": 0,
         "params": {"name": "M16", "ra": "18h 18m 48s", "dec": "-13 49 00",
                    "rotation": 30, "angle": "Rotate to PA", "rows": 2,
                    "cols": 2, "overlap": 25, "fovX": 2.0, "fovY": 1.33,
                    "skip": "2-2"}},
        _capture("k", "Ha", exposure=300.0, count=2)],
    "edges": [*TARGET_AND_POOL["edges"],
              {"from": "c", "fromPort": "complete", "to": "m",
               "toPort": "arm"},
              {"from": "m", "fromPort": "target", "to": "k", "toPort": "run"}]}


def _compiled(graph: dict, flow_id: str) -> tuple[dict, SequencePlan]:
    """What ``/run`` compiles for this graph: the same compile, the same name
    and the same ``flow_id``, so the same ids."""
    g = FlowGraph.model_validate(graph)
    compiled = compile_plan(g, NAME)
    plan, _ = to_sequence_plan(compiled, g, flow_id=flow_id)
    return compiled, plan


# ------------------------------------------------------------------ sessions

def _frames(target_id: str, step_id: str, n: int, *,
            accepted: bool = True) -> list[SessionFrame]:
    return [SessionFrame(target_id=target_id, step_id=step_id,
                         auto_accepted=accepted) for _ in range(n)]


def _seed(flow_id: str, plan: SequencePlan, frames: list[SessionFrame], *,
          created: float, updated: float | None = None,
          status: str = "dormant", count_mode: str | None = None,
          nights: tuple[str, ...] = ("night-1",),
          auto_resume: bool = False,
          plan_saved_ts: float | None = None) -> Session:
    """A session this flow started, written as the file ``SessionStore.save``
    writes but with the timestamps the test names: ``save`` stamps
    ``updated_ts`` from the clock, and a test about which timestamp decides
    must not depend on how finely the clock ticks."""
    if count_mode is not None:
        plan = plan.model_copy(update={"count_mode": count_mode})
    s = Session(name=f"{flow_id}@{created}", created_ts=created,
                updated_ts=created if updated is None else updated,
                status=status, plan=plan, nights=list(nights),
                frames=frames, origin="flow", origin_id=flow_id,
                auto_resume=auto_resume, plan_saved_ts=plan_saved_ts)
    path = session_store._path(s.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, s.model_dump(), backup=False)
    return s


# ------------------------------------------------------------------ harness

def _sweep_config_store(monkeypatch, store: ConfigStore) -> None:
    """Point every imported astrodeck module's ``config_store`` at ``store``.

    ``from .config import config_store`` binds the singleton into each
    importing module, so patching three modules by name leaves thirty-odd
    reading the real store, and a site change the test makes would reach none
    of them: a byte-identical answer would then prove nothing (the #19 scan's
    own vacuum, see test_no_route_leaks_the_site_coordinates.py)."""
    for name, mod in list(sys.modules.items()):
        if (name.startswith("astrodeck") and mod is not None
                and getattr(mod, "config_store", None) is not None):
            monkeypatch.setattr(mod, "config_store", store, raising=False)


def _isolate(tmp_path, monkeypatch) -> ConfigStore:
    """A throwaway config store, flow library and captures directory; the
    camera and the Sun check stubbed on the app's hub so ``/run`` can start
    (the CONTINUE test). The default site never blocks the horizon check."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    _sweep_config_store(monkeypatch, store)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    flows = FlowStore(tmp_path / "flows")
    # BOTH names: the routes read app_module's, and the engine's finalize
    # imports the module's at call time to write the card's last result.
    monkeypatch.setattr(app_module, "flow_store", flows)
    monkeypatch.setattr(flow_store_module, "flow_store", flows)
    monkeypatch.setattr(app_module.hub, "_check_solar", lambda *a, **kw: None)
    monkeypatch.setattr(app_module.hub, "require", lambda role: object())
    monkeypatch.setattr(app_module.hub, "last_frame", None, raising=False)
    reset_active_provider()
    return store


@pytest.fixture(autouse=True)
def _restore_provider():
    """The provider and the session-secret interlock are process globals."""
    yield
    reset_active_provider()


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

    async def save_flow(self, graph: dict) -> str:
        r = await self.client.post("/api/flows", json={
            "flow": {"name": NAME, "graph": graph}})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    async def put_flow(self, fid: str, graph: dict) -> None:
        r = await self.client.put(f"/api/flows/{fid}", json={
            "flow": {"name": NAME, "graph": graph}})
        assert r.status_code == 200, r.text

    async def progress(self, fid: str) -> httpx.Response:
        return await self.client.get(f"/api/flows/{fid}/progress")

    async def ok(self, fid: str) -> dict:
        r = await self.progress(fid)
        assert r.status_code == 200, r.text
        return r.json()


def _app_client(monkeypatch, store: ConfigStore) -> httpx.AsyncClient:
    app = app_module.create_app()
    # Again after the app is built: a module first imported by create_app
    # would otherwise keep the real store.
    _sweep_config_store(monkeypatch, store)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://testserver")


@pytest.fixture
async def api(tmp_path, monkeypatch):
    store = _isolate(tmp_path, monkeypatch)
    async with _app_client(monkeypatch, store) as client:
        yield Api(client, store, tmp_path)


def _steps(payload: dict) -> list[tuple[str, int, int]]:
    """``(step_id, banked, owed)`` for every step, in payload order."""
    return [(s["step_id"], s["banked"], s["owed"])
            for b in payload["blocks"] for p in b["panels"]
            for s in p["steps"]]


# ============================================================== who may read

class TestWhoMayReadIt:
    async def test_a_viewers_session_reads_it_and_no_credential_is_401(
            self, api, monkeypatch):
        """A real local-login viewer (a signed session cookie, resolved by the
        real cookie provider) gets 200; the same request with no cookie gets
        401. The viewer is shown to BE a viewer by a control route refusing
        it, so the 200 cannot come from an admin in disguise.

        RED under mutation "gated on control.capture" (``CAP_VIEW_STATUS`` ->
        ``CAP_CONTROL_CAPTURE`` in the route's dependency and declaration);
        the viewer case of the site test below fails the same way:

            AssertionError: {"detail":"capability not held"}
            assert 403 == 200
             +  where 403 = <Response [403 Forbidden]>.status_code

        RED under mutation "no gate, no declaration" (the route's
        ``dependencies=[...]`` and ``@declare`` both removed). The boot
        assertion grades only MUTATING routes for a gate (its invariant (1)),
        so an ungated GET builds, and it is this assertion that sees it:

            AssertionError: {"flow_id":"fb1068950e0942858fee08b4d835704e",
            "session":null,"blocks":[{"node_id":"t","name":"M42", ...}],
            "orphaned":{"frames":0,"steps":0}}
            assert 200 == 401
             +  where 200 = <Response [200 OK]>.status_code

        Mutation "no gate" (the dependency removed, ``@declare`` kept) never
        reaches this test: ``create_app`` refuses to build, and every test
        here errors at setup with

            astrodeck.auth.rbac.RouteCapabilityError: route
            /api/flows/{flow_id}/progress (['GET']) DECLARES capability
            ['view.status'] that no require() dependency enforces (actually
            enforced: []) -- a @declare marker is a label, not a gate: add
            Depends(require(...)) or drop the claim
        """
        fid = await api.save_flow(LR)          # as the open default
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

        anon = await api.progress(fid)
        assert anon.status_code == 401, anon.text

        api.client.cookies.set(SESSION_COOKIE, cookie)
        refused = await api.client.post("/api/flows", json={
            "flow": {"name": "x", "graph": LR}})
        assert refused.status_code == 403, (
            "premise: the cookie resolves to a viewer, who may not save a flow")
        assert not principal_for_role("viewer").has(CAP_VIEW_SITE_DERIVED), (
            "premise: a viewer is a caller the site must be withheld from")
        assert principal_for_role("viewer").has(CAP_VIEW_STATUS)
        assert not principal_for_role("viewer").has(CAP_CONTROL_CAPTURE)

        r = await api.progress(fid)
        assert r.status_code == 200, r.text
        assert r.json()["flow_id"] == fid

    def test_it_is_declared_before_the_flow_id_route(self):
        """The acceptance's ordering, pinned. ``{flow_id}`` matches one path
        segment, so today this is belt and braces rather than the "folders"
        collision; the comment above the route says why it is kept anyway.

        RED under mutation "declared after get_flow" (the route moved to
        just after ``GET /api/flows/{flow_id}``):

            AssertionError: GET /api/flows/{flow_id}/progress is declared
            after GET /api/flows/{flow_id}
            assert 49 < 48
        """
        app = app_module.create_app()
        order = [getattr(r, "path", "") for r in app.routes
                 if "GET" in (getattr(r, "methods", None) or ())]
        progress = order.index("/api/flows/{flow_id}/progress")
        flow = order.index("/api/flows/{flow_id}")
        assert progress < flow, (
            "GET /api/flows/{flow_id}/progress is declared after "
            "GET /api/flows/{flow_id}")


# ============================================================ what it may say

#: The payload's keys, level by level: the same list the pure half is held to
#: in test_flows_progress.py, held again at the wire. A key added later has to
#: be added here, in a diff somebody reads (spec 6.9, #19).
#:
#: DELIBERATE PIN CHANGE (S7, #473, S7 orchestrator ruling 1): the session
#: gains ``armed`` and ``plan_saved_ts``, which the ROUTE adds
#: (``progress.replay_facts``), so the pure half's list keeps four keys and
#: this one has six. Neither is derived from the site: a status and a flag,
#: and the moment an operator pressed Save. The old list against the new
#: route, observed:
#:
#:     AssertionError: session carries keys outside the allow-list
#:     assert {'armed', 'co...ts', 'status'} <= {'count_mode'...ts',
#:     'status'}
#:       Extra items in the left set:
#:       'plan_saved_ts'
#:       'armed'
ALLOWED = {
    "top": {"flow_id", "session", "blocks", "orphaned"},
    "session": {"id", "status", "nights", "count_mode", "armed",
                "plan_saved_ts"},
    "block": {"node_id", "name", "kind", "banked", "owed", "total", "panels",
              "grid", "skipped", "group_id"},
    "grid": {"rows", "cols"},
    "panel": {"target_id", "name", "row", "col", "banked", "owed", "total",
              "steps"},
    "skipped": {"target_id", "name", "row", "col", "banked"},
    "step": {"step_id", "filter", "frame_type", "exposure_s", "count",
             "banked", "owed"},
    "orphaned": {"frames", "steps"},
}


#: The seeded session's frozen version's saved time: not round, so the byte
#: test below reads a real number in the key and not a null (S7, #473).
SAVED_TS = 1790012345.678


async def _seeded_target_and_pool(api) -> str:
    """The TARGET + POOL + mosaic flow with a session that holds a frame on
    the first step of every target that has one (five: the two pool members
    and the mosaic's three live panels) and two frames on a step the flow no
    longer has. Since S7 it is armed and carries ``plan_saved_ts`` (#473), so
    the site and allow-list tests walk both keys with values, not nulls."""
    fid = await api.save_flow(TARGET_POOL_AND_MOSAIC)
    _c, plan = _compiled(TARGET_POOL_AND_MOSAIC, fid)
    frames = [f for t in plan.targets if t.steps
              for f in _frames(t.id, t.steps[0].id, 1)]
    _seed(fid, plan, frames + _frames(plan.targets[0].id, "gone", 2),
          created=100.0, auto_resume=True, plan_saved_ts=SAVED_TS)
    return fid


class TestItCarriesNoSiteData:
    @pytest.mark.parametrize("role", ["viewer", "admin"])
    async def test_the_body_does_not_move_when_the_site_does(self, api, role):
        """The same flow and the same ledger read under two synthetic sites:
        the bytes are identical. A viewer is the caller spec 6.9 is about;
        an admin is asked too, because the spec says the route carries no
        site data at all, not that it withholds it from some callers.

        The site change is shown to reach the hub, which is where a route
        would read it (``hub.site``), so the equality is not the vacuum of a
        route that never saw the site move.

        RED under mutation "add a transit altitude" (each panel of a target
        the plan holds gains ``transit_alt_deg = 90 - |latitude - dec|``, from
        ``hub.site``), for both roles:

            AssertionError: the body moved with the site
            assert b'{"flow_id":...2,"steps":1}}' ==
            b'{"flow_id":...2,"steps":1}}'
              At index 510 diff: b'8' != b'6'

        Mutations "no session" and "keyed on the flow's name" (see
        TestTheCounts) fail the premise, for both roles:

            AssertionError: premise: the ledger is read, so there is
            something to leak into
            assert 0 == 3

        (the count was 3 before S5 put the mosaic in this payload; it is 5
        now). RED under mutation "a transit altitude on the mosaic block"
        (S5: ``flows/progress.py``'s mosaic block gains ``transit_alt_deg``,
        ``90 - |latitude - dec|`` from the configured site, beside
        ``group_id``), run in scratchpad ``s5-feed-mut``, for both roles, and
        the allow-list test below on its key:

            AssertionError: the body moved with the site
            assert b'{"flow_id":...2,"steps":1}}' ==
            b'{"flow_id":...2,"steps":1}}'
              At index 1898 diff: b'3' != b'6'

        SINCE S7 the session carries ``armed`` and ``plan_saved_ts``
        (#473), seeded valued here so the equality is over real values of
        both. The premise that says so is what S7's mutants reach, run in
        scratchpad ``s7-session-mut``: mutant "plan_saved_ts rewritten on
        every save" (the route answering the flow record's ``updated_ts``),
        for both roles, observed:

            AssertionError: premise: S7's two keys are in the body, valued
            assert (True, 1790646706.1331909) == (True, 1790012345.678)

        and mutant "no replay facts on the route", for both roles, observed:

            KeyError: 'armed'
        """
        fid = await _seeded_target_and_pool(api)
        set_active_provider(_Fixed(principal_for_role(role)))
        bodies = []
        for lat, lon in (SITE_A, SITE_B):
            api.store.set_site(Site(name="fixture", latitude=lat,
                                    longitude=lon, elevation_m=10.0,
                                    is_default=False))
            assert app_module.hub.site["latitude"] == lat, (
                "premise: the hub reads the site this test configured")
            r = await api.progress(fid)
            assert r.status_code == 200, r.text
            bodies.append(r.content)
        got = json.loads(bodies[0])
        assert sum(b["banked"] for b in got["blocks"]) == 5, (
            "premise: the ledger is read, so there is something to leak into")
        assert got["blocks"][2].get("group_id"), (
            "premise: the mosaic's group id is in the body")
        assert (got["session"]["armed"], got["session"]["plan_saved_ts"]) == (
            True, SAVED_TS), "premise: S7's two keys are in the body, valued"
        assert bodies[0] == bodies[1], "the body moved with the site"

    async def test_every_key_is_on_the_allow_list(self, api):
        """Every level of a populated answer, walked at the wire.

        RED under mutation "add a transit altitude":

            AssertionError: panel carries keys outside the allow-list
            assert {'banked', 'c... 'steps', ...} <= {'banked', 'c...
            'steps', ...}
              Extra items in the left set:
              'transit_alt_deg'

        RED under mutation "a transit altitude on the mosaic block" (see
        the site test above), observed:

            AssertionError: block carries keys outside the allow-list
            assert {'banked', 'g...node_id', ...} <= {'banked',
            'g...node_id', ...}
              Extra items in the left set:
              'transit_alt_deg'
        """
        fid = await _seeded_target_and_pool(api)
        api.store.set_site(Site(name="fixture", latitude=SITE_A[0],
                                longitude=SITE_A[1], elevation_m=10.0,
                                is_default=False))
        got = await api.ok(fid)
        assert [b["kind"] for b in got["blocks"]] == [
            "target", "pool", "target"], (
            "premise: both kinds of block are present")
        assert "grid" in got["blocks"][2], "premise: the third is a mosaic"
        seen: dict[str, set] = {level: set() for level in ALLOWED}
        seen["top"] |= set(got)
        seen["session"] |= set(got["session"])
        seen["orphaned"] |= set(got["orphaned"])
        for block in got["blocks"]:
            seen["block"] |= set(block)
            seen["grid"] |= set(block.get("grid") or {})
            for s in block.get("skipped") or []:
                seen["skipped"] |= set(s)
            for panel in block["panels"]:
                seen["panel"] |= set(panel)
                for step in panel["steps"]:
                    seen["step"] |= set(step)
        assert all(seen.values()), "premise: every level was walked"
        for level, keys in seen.items():
            assert keys <= ALLOWED[level], \
                f"{level} carries keys outside the allow-list"


# ================================================================ not found

class TestNotFound:
    async def test_an_unknown_id_is_404(self, api):
        """RED under mutation "KeyError unmapped" (the ``except KeyError``
        around ``flow_store.get`` removed); the transport re-raises the
        handler's exception:

            KeyError: 'no-such-flow'
        """
        r = await api.progress("no-such-flow")
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == {"code": "not_found"}

    async def test_an_unreadable_rows_id_is_404(self, api):
        """A file this build cannot open is LISTED (#153, a read-only row
        carrying ``unreadable``) and every other route answers 404 for it.
        This one does too: there is no graph to compile.

        RED under mutation "KeyError unmapped":

            KeyError: 'damaged-flow'
        """
        path = api.tmp / "flows" / "damaged-flow.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        rows = (await api.client.get("/api/flows")).json()
        assert any(row.get("id") == "damaged-flow" and row.get("unreadable")
                   for row in rows), "premise: the library lists the row"
        r = await api.progress("damaged-flow")
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == {"code": "not_found"}

    async def test_control_a_shipped_example_is_read(self, api):
        """An example id is a flow ``flow_store.get`` answers, so its card has
        a chip too: 200, no session, the whole plan owed. Mutation "keyed on
        the flow's name" (see TestTheCounts) fails here:

            AssertionError: assert 'M31 - LRGB two-night' == 'example-m31'
        """
        got = await api.ok("example-m31")
        assert got["flow_id"] == "example-m31"
        assert got["session"] is None
        assert got["orphaned"] == {"frames": 0, "steps": 0}
        assert all(b["banked"] == 0 for b in got["blocks"])
        assert sum(b["owed"] for b in got["blocks"]) > 0


# ================================================================ the counts

class TestTheCounts:
    async def test_counts_match_the_module_for_a_seeded_session(self, api):
        """The route answers exactly what ``flow_progress`` says for the
        stored graph compiled with the flow's id and the flow's session. The
        session counts in ``accepted`` mode, over-fills L, holds a rejected R
        and two frames on a step the flow does not have, so the equality is
        about real numbers and not two zeros agreeing.

        RED under mutation "no session" (``flow_progress(..., None, ...)``):

            AssertionError: assert {'blocks': [{...ession': None} ==
            {'blocks': [{...': 'dormant'}}
              Omitting 1 identical items, use -vv to show
              Differing items:
              {'orphaned': {'frames': 0, 'steps': 0}} != {'orphaned':
              {'frames': 2, 'steps': 1}}
              {'blocks': [{'banked': 0, 'kind': 'target', 'name': 'M42',
              'node_id': 't', ...}]} != {'blocks': [{'banked': 4, 'kind':
              'target', 'name': 'M42', 'node_id': 't', ...}]}
              {'session': None} != {'session': {'count_mode': 'accepted',
              'id': '9df6752ebba6467f8b945fa9889a3fa8', 'nights': 1,
              'status': 'dormant'}}

        RED under mutation "keyed on the flow's name" (``flow_id=rec.name``
        in both the compile and the count): the ids are deterministic but
        not the ledger's, so nothing is banked and every frame is orphaned:

            AssertionError: assert {'blocks': [{...': 'dormant'}} ==
            {'blocks': [{...': 'dormant'}}
              Omitting 1 identical items, use -vv to show
              Differing items:
              {'orphaned': {'frames': 8, 'steps': 3}} != {'orphaned':
              {'frames': 2, 'steps': 1}}
              {'flow_id': 'progress me'} != {'flow_id':
              '2dbb816e3e6449ca90fcd2b3b9161b41'}
              {'blocks': [{'banked': 0, 'kind': 'target', 'name': 'M42',
              'node_id': 't', ...}]} != {'blocks': [{'banked': 4, 'kind':
              'target', 'name': 'M42', 'node_id': 't', ...}]}

        Mutation "add a transit altitude" fails here too, the route's panel
        carrying a key the module's does not:

            AssertionError: assert {'blocks': [{...': 'dormant'}} ==
            {'blocks': [{...': 'dormant'}}
              Omitting 3 identical items, use -vv to show
              Differing items:
              {'blocks': [{'banked': 4, 'kind': 'target', 'name': 'M42',
              'node_id': 't', ...}]} != {'blocks': [{'banked': 4, 'kind':
              'target', 'name': 'M42', 'node_id': 't', ...}]}
        """
        fid = await api.save_flow(LR)
        compiled, plan = _compiled(LR, fid)
        t = plan.targets[0]
        lum, red = t.steps
        frames = (_frames(t.id, lum.id, 4)
                  + _frames(t.id, red.id, 1, accepted=False)
                  + _frames(t.id, red.id, 1)
                  + _frames(t.id, "gone", 2))
        s = _seed(fid, plan, frames, created=100.0, count_mode="accepted")

        got = await api.ok(fid)

        expected = json.loads(json.dumps(flow_progress(
            compiled, plan, session_store.load(s.id), flow_id=fid)))
        assert expected["session"]["id"] == s.id
        # DELIBERATE PIN CHANGE (S7, #473): the route's session carries
        # ``armed`` and ``plan_saved_ts`` beside the module's four keys, as
        # literals here (a dormant, unarmed session that never froze a
        # version), not re-derived by the code under test. The module's
        # answer alone against the new route, observed:
        #
        #     AssertionError: assert {'blocks': [{...hts': 1, ...}} ==
        #     {'blocks': [{...': 'dormant'}}
        #       Differing items:
        #       {'session': {'armed': False, 'count_mode': 'accepted', 'id':
        #       '50136158bc2648b2911dc35f94ee7c77', 'nights': 1, ...}} !=
        #       {'session': {'count_mode': 'accepted', 'id':
        #       '50136158bc2648b2911dc35f94ee7c77', 'nights': 1, 'status':
        #       'dormant'}}
        expected["session"].update({"armed": False, "plan_saved_ts": None})
        assert _steps(expected) == [(lum.id, 3, 0), (red.id, 1, 1)], (
            "premise: L is capped at its count and the rejected R is not "
            "banked")
        assert expected["orphaned"] == {"frames": 2, "steps": 1}
        assert got == expected

    async def test_the_newest_session_is_read_and_none_once_abandoned(
            self, api):
        """The newest session is read whatever became of it, never an older
        one: here a complete session over an older dormant one, the ledger a
        START OVER leaves behind. Once a newer session is abandoned, the
        operator has closed the flow's work, and the answer is no session at
        all, not a fall back to either older ledger (#189 hardening A2: the
        rule ``/run`` picks by, from ``current_for_flow``).

        This test used to read "the newest session that was not abandoned",
        which under the second half named the complete session here, while
        Run started fresh.

        RED under mutation "progress keeps its non-abandoned filter" (the
        lookup replaced by ``session_store.newest_for_flow(flow_id,
        ("active", "dormant", "complete"))``, the rule this test used to
        pin), at the second read, observed:

            AssertionError: assert {'count_mode': 'attempts', 'id':
            'ae666feb05974543a224c3b0116a2e58', 'nights': 1, 'status':
            'complete'} is None

        RED under mutation "abandoned included" (``session_store.
        newest_for_flow(flow_id, ("active", "dormant", "complete",
        "abandoned"))``, no rule for an abandoned newest), at the second
        read, observed:

            AssertionError: assert {'count_mode': 'attempts', 'id':
            '9a2f3be446404189a2610aad87f04850', 'nights': 1, 'status':
            'abandoned'} is None

        RED under mutation "dormant only" (``session_store.newest_for_flow(
        flow_id, ("dormant",))``), at the first read, observed:

            AssertionError: assert {'count_mode'...s': 'dormant'} ==
            {'count_mode'...': 'complete'}
              Omitting 2 identical items, use -vv to show
              Differing items:
              {'id': '737ec13465a24bfe86356b770ff68657'} != {'id':
              '351bf704541c4e05b0944d8c07fc42be'}
              {'status': 'dormant'} != {'status': 'complete'}
        """
        fid = await api.save_flow(LR)
        _c, plan = _compiled(LR, fid)
        t = plan.targets[0]
        _seed(fid, plan, _frames(t.id, t.steps[0].id, 1), created=100.0)
        done = _seed(fid, plan, _frames(t.id, t.steps[0].id, 2),
                     created=150.0, status="complete")
        got = await api.ok(fid)
        # DELIBERATE PIN CHANGE (S7, #473): the session's two new keys. The
        # four-key dict against the new route, observed:
        #
        #     AssertionError: assert {'armed': Fal...ghts': 1, ...} ==
        #     {'count_mode'...': 'complete'}
        #       Left contains 2 more items:
        #       {'armed': False, 'plan_saved_ts': None}
        assert got["session"] == {"id": done.id, "status": "complete",
                                  "nights": 1, "count_mode": "attempts",
                                  "armed": False, "plan_saved_ts": None}
        assert _steps(got)[0][1] == 2

        _seed(fid, plan, _frames(t.id, t.steps[0].id, 3), created=200.0,
              status="abandoned")
        got = await api.ok(fid)
        assert got["session"] is None
        assert [banked for _id, banked, _owed in _steps(got)] == [0, 0]
        assert got["orphaned"] == {"frames": 0, "steps": 0}

    async def test_the_newest_by_creation_not_by_update(self, api):
        """``engine.start``'s singleton disarm re-saves the flow's older
        sessions, and a save stamps ``updated_ts``, so the older ledger is
        routinely the fresher file. ``created_ts`` is written once.

        RED under mutation "newest by updated_ts" (the lookup replaced by
        the flow's sessions from ``load_all()``, the one with the largest
        ``updated_ts`` taken, and None if that one was abandoned: the
        ``current_for_flow`` rule ordered by the wrong timestamp), observed
        after the lookup became ``current_for_flow`` (#189 hardening A2):

            AssertionError: assert '76fd2dc74940...0246e4b2289ad' ==
            '3a04db342daf...f441da0babf30'
              - 3a04db342daf4a6d814f441da0babf30
              + 76fd2dc749404f21aed0246e4b2289ad
        """
        fid = await api.save_flow(LR)
        _c, plan = _compiled(LR, fid)
        t = plan.targets[0]
        _seed(fid, plan, _frames(t.id, t.steps[0].id, 1), created=100.0,
              updated=900.0, status="complete")
        newer = _seed(fid, plan, _frames(t.id, t.steps[0].id, 2),
                      created=200.0, updated=300.0)
        _seed("another-flow", plan, _frames(t.id, t.steps[0].id, 3),
              created=500.0)
        got = await api.ok(fid)
        assert got["session"]["id"] == newer.id
        assert _steps(got)[0][1] == 2

    async def test_the_stored_graph_is_compiled_not_the_sessions_plan(
            self, api):
        """Night one shot R at 60 s; the flow now says 90 s. The card shows
        what the flow owes NOW: the new R step with nothing banked, and the
        60 s frames orphaned, the numbers a CONTINUE's dropped-steps question
        will quote.

        RED under mutation "the session's frozen plan" (the plan read from
        the session when there is one, instead of from the compile):

            AssertionError: assert [('460520dc15...f3', 60.0, 2)] ==
            [('460520dc15...c4', 90.0, 0)]
              At index 1 diff: ('bbe430ba24d352499b0d51c1a30ffaf3', 60.0, 2)
              != ('897fc40d3c845fd5a6e7136ad9cb26c4', 90.0, 0)
        """
        fid = await api.save_flow(LR)
        _c, night_one = _compiled(LR, fid)
        t = night_one.targets[0]
        lum, red60 = t.steps
        _seed(fid, night_one, _frames(t.id, lum.id, 2)
              + _frames(t.id, red60.id, 2), created=100.0)
        await api.put_flow(fid, LR_R90)
        _c, tonight = _compiled(LR_R90, fid)
        red90 = tonight.targets[0].steps[1]
        assert tonight.targets[0].steps[0].id == lum.id and \
            red90.id != red60.id, \
            "premise: an exposure change moves that step's id and no other"

        got = await api.ok(fid)

        steps = got["blocks"][0]["panels"][0]["steps"]
        assert [(s["step_id"], s["exposure_s"], s["banked"])
                for s in steps] == [(lum.id, 60.0, 2), (red90.id, 90.0, 0)]
        assert got["orphaned"] == {"frames": 2, "steps": 1}

    async def test_control_a_flow_that_never_ran(self, api):
        """No session: nothing banked, the whole plan owed, and a session of
        another flow is not borrowed. A control, and not one that cannot
        fail: mutation "keyed on the flow's name" names other steps here too:

            AssertionError: assert [('dd425dd66d...4ea4c', 0, 2)] ==
            [('e7e95d0ae2...c29bc', 0, 2)]
              At index 0 diff: ('dd425dd66da85fd1b8509641a62810c3', 0, 3)
              != ('e7e95d0ae2b45d18b56c2a83c2e20a62', 0, 3)
        """
        fid = await api.save_flow(LR)
        _c, plan = _compiled(LR, fid)
        t = plan.targets[0]
        _seed("another-flow", plan, _frames(t.id, t.steps[0].id, 3),
              created=100.0)
        got = await api.ok(fid)
        assert got["session"] is None
        assert _steps(got) == [(t.steps[0].id, 0, 3), (t.steps[1].id, 0, 2)]
        assert got["orphaned"] == {"frames": 0, "steps": 0}


# ======================================================== the route's plumbing

class TestPlumbing:
    async def test_a_graph_that_cannot_become_a_plan_is_422_invalid_graph(
            self, api):
        """A saved CAPTURE with no exposure (a half-filled node, which the
        editor saves) cannot compile to a plan. The chip is told so in the
        words ``/run`` would use, not with a server error.

        RED under mutation "GraphNotRunnable unmapped" (the ``except
        GraphNotRunnable`` removed); the transport re-raises it:

            astrodeck.flows.to_plan.GraphNotRunnable: M42: a CAPTURE step has
            no exposure time - set one on the capture node
        """
        fid = await api.save_flow(_graph(_capture("c1", "L", exposure=0)))
        run = await api.client.post(f"/api/flows/{fid}/run", json={})
        assert run.status_code == 422 and \
            run.json()["detail"]["code"] == "invalid_graph", (
                f"premise: /run refuses this graph the same way: {run.text}")
        r = await api.progress(fid)
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "invalid_graph"
        assert r.json()["detail"]["detail"] == run.json()["detail"]["detail"]

    async def test_the_compile_the_lookup_and_the_count_run_off_the_loop(
            self, api, monkeypatch):
        """The flow read, a compile, a scan of every session file and the
        count are CPU and disk work; on the event loop they would stall every
        other request and the engine's own ticks while a library of cards
        asks for its chips.

        The lookup spied on is ``current_for_flow``, the one call the route
        makes to pick its session (#189 hardening A2). It used to be
        ``newest_for_flow``, which ``current_for_flow`` still calls; spying on
        the route's own call means a lookup that stopped delegating could not
        slip past this test.

        RED under mutation "on the loop" (the helper called directly instead
        of through ``asyncio.to_thread``), observed again after the spy moved
        to ``current_for_flow``:

            AssertionError: assert {'current_for...e_plan': True} ==
            {'current_for..._plan': False}
              Omitting 1 identical items, use -vv to show
              Differing items:
              {'flow_progress': True} != {'flow_progress': False}
              {'to_sequence_plan': True} != {'to_sequence_plan': False}
              {'current_for_flow': True} != {'current_for_flow': False}

        RED under mutation "read on the loop" (``flow_store.get(flow_id)``
        called directly instead of through ``asyncio.to_thread``; the
        helper's own three spies cannot see this one, so the read has its
        own; added by the S1-16 verifier, whose run of it survived before
        the spy existed):

            AssertionError: assert {'flow_progre..._plan': False} ==
            {'flow_progre..._plan': False}
              Omitting 3 identical items, use -vv to show
              Differing items:
              {'flow_store.get': True} != {'flow_store.get': False}

        Mutation "newest by updated_ts" (see TestTheCounts) fails here as
        well, because it never calls the lookup this spies on, observed (and
        mutation "dormant only" the same way):

            AssertionError: assert {'flow_progre..._plan': False} ==
            {'current_for..._plan': False}
              Omitting 3 identical items, use -vv to show
              Right contains 1 more item:
              {'current_for_flow': False}
        """
        fid = await api.save_flow(LR)
        on_loop: dict[str, bool] = {}

        def loop_running() -> bool:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return False
            return True

        real_get = app_module.flow_store.get
        real_plan = app_module.to_sequence_plan
        real_count = app_module.flow_progress
        real_lookup = SessionStore.current_for_flow

        def get_spy(*a, **kw):
            on_loop["flow_store.get"] = loop_running()
            return real_get(*a, **kw)

        def plan_spy(*a, **kw):
            on_loop["to_sequence_plan"] = loop_running()
            return real_plan(*a, **kw)

        def count_spy(*a, **kw):
            on_loop["flow_progress"] = loop_running()
            return real_count(*a, **kw)

        def lookup_spy(self, *a, **kw):
            on_loop["current_for_flow"] = loop_running()
            return real_lookup(self, *a, **kw)

        # The instance the route reads (``_isolate`` put it on app_module),
        # so the spy sees the route's own call and nothing earlier: the save
        # above ran before it was installed.
        monkeypatch.setattr(app_module.flow_store, "get", get_spy)
        monkeypatch.setattr(app_module, "to_sequence_plan", plan_spy)
        monkeypatch.setattr(app_module, "flow_progress", count_spy)
        monkeypatch.setattr(SessionStore, "current_for_flow", lookup_spy)
        await api.ok(fid)
        assert on_loop == {"flow_store.get": False,
                           "to_sequence_plan": False,
                           "current_for_flow": False,
                           "flow_progress": False}


# ================================================================ CONTINUE

class _Night:
    """Stands in for ``SequenceEngine._run``, the imaging loop, and for
    nothing else. A night lasts until the test ends it, and ends through the
    engine's own ``_finalize_report``, which sets the session dormant or
    complete and saves it."""

    def __init__(self, engine: SequenceEngine) -> None:
        self.engine = engine
        self._release: asyncio.Event | None = None
        self._reason = "incomplete"

    def __call__(self):
        self._release = asyncio.Event()
        return self._body(self._release)

    async def _body(self, release: asyncio.Event) -> None:
        await release.wait()
        self.engine._finalize_report(self._reason)

    def end(self, reason: str) -> None:
        self._reason = reason
        assert self._release is not None, "no night is running"
        self._release.set()


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    store = _isolate(tmp_path, monkeypatch)
    engine = SequenceEngine(app_module.hub)
    night = _Night(engine)
    monkeypatch.setattr(engine, "_run", night)
    monkeypatch.setattr(app_module, "engine", engine)
    async with _app_client(monkeypatch, store) as client:
        yield Api(client, store, tmp_path), engine, night
    if engine.running:
        night.end("aborted")
        await engine._task


def _bank(engine: SequenceEngine, steps) -> None:
    """One frame per entry of ``steps`` (a step index on the live run's first
    target), through the engine's own ledger write."""
    t = engine.plan.targets[0]
    for i in steps:
        assert engine._record_session_frame(t, t.steps[i], {},
                                            auto_accepted=True) is not None


def _local(y: int, mo: int, d: int, h: int, mi: int = 0) -> float:
    """The epoch second at that LOCAL wall-clock time on this machine: the
    zone is the machine's (Pacific on the dev box, UTC in CI), and the
    wall-clock time, which is what the night key reads, is the same in
    both."""
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


class _Clock:
    """``time`` as the engine and the app read it, with ``time()`` pinned to
    ``t`` and every other name the real module's."""

    def __init__(self, t: float) -> None:
        self.t = t

    def time(self) -> float:
        return self.t

    def __getattr__(self, name: str):
        return getattr(time, name)


class TestContinue:
    async def test_after_a_continue_the_same_step_ids_are_reported(
            self, rig, monkeypatch):
        """Night one through ``/run``, read live and then dormant; night two
        through ``/run`` again, which CONTINUES the same session (S1-13). At
        every read the step ids are the ones the ENGINE is counting by, the
        session is the one it is writing, and night two's frame lands on
        night one's count.

        RED under mutation "keyed on the flow's name" (``flow_id=rec.name``
        in both the compile and the count), at the first read:

            AssertionError: not the ids the run is counting by
            assert ['dd425dd66da...26ebd8c4ea4c'] ==
            ['7bfd2b5e843...baca3a2d328a']
              At index 0 diff: 'dd425dd66da85fd1b8509641a62810c3' !=
              '7bfd2b5e84305e2f9b96a51aaed05662'

        RED under mutation "dormant only" (the lookup replaced by
        ``session_store.newest_for_flow(flow_id, ("dormant",))``), at the
        first read, while night one is live; observed again after the lookup
        became ``current_for_flow`` (#189 hardening A2):

            assert None is not None

        Mutation "no session" fails at the same line.

        RE-PINNED IN THE INTEGRATION OF S3: both session reads say
        ``count_mode`` "accepted" where they said "attempts". The flow is
        saved through the route, and since S3 every save writes "Accepted
        subs" into its TARGETs and POOLs (Revision 2 ruling 2), so the run it
        starts counts accepted subs. Every frame here is accepted, so the
        counts themselves do not move.

        DELIBERATE PIN CHANGES IN S7 (#430, #473; S7 orchestrator rulings 7
        and 1). The CONTINUE here is a second run on the SAME night, and it
        always was: the two runs are seconds apart. This test pinned
        ``nights: 2`` after it, which was #430's defect written down (the
        card counted runs). ``nights`` now counts observing nights, so the
        read after the CONTINUE says 1 while the session holds two report
        ids. The old pin against the new code, observed:

            AssertionError: assert {'armed': Fal...ghts': 1, ...} ==
            {'armed': Fal...ghts': 2, ...}
              Differing items:
              {'nights': 1} != {'nights': 2}

        The pin holds only inside one night, so the engine's and the app's
        clock are pinned to 21:00 and 21:30 local on one date (the machine's
        zone, whichever it is): at real time a run straddling local noon
        would read two nights. Both dormant and live reads also carry the
        session's ``armed`` (true once dormant, false while live) and
        ``plan_saved_ts`` (the flow's saved time, which the CONTINUE keeps
        because the flow was not saved again). The four-key dict against
        the new route, observed at the dormant read:

            AssertionError: assert {'armed': Tru...ghts': 1, ...} ==
            {'count_mode'...s': 'dormant'}
              Left contains 2 more items:
              {'armed': True, 'plan_saved_ts': 1790645522.92159}

        Run in scratchpad ``s7-session-mut`` (see
        test_s7_session_nights.py), mutants "night is the run count"
        (``session.py``) and "the card counts runs" (``progress.py``) each
        turn the read after the CONTINUE red, observed:

            AssertionError: assert {'armed': Fal...ghts': 2, ...} ==
            {'armed': Fal...ghts': 1, ...}
              Differing items:
              {'nights': 2} != {'nights': 1}

        mutant "armed ignores status" the same read, observed:

              Differing items:
              {'armed': True} != {'armed': False}

        and mutant "never frozen on a fresh run" the dormant read, observed:

              Differing items:
              {'plan_saved_ts': None} != {'plan_saved_ts': 1790646749.0710003}
        """
        api, engine, night = rig
        fid = await api.save_flow(LR)
        saved = (await api.client.get(f"/api/flows/{fid}")).json()[
            "updated_ts"]
        clock = _Clock(_local(2026, 9, 20, 21, 0))
        monkeypatch.setattr(engine_module, "time", clock)
        monkeypatch.setattr(app_module, "time", clock)
        r = await api.client.post(f"/api/flows/{fid}/run", json={})
        assert r.status_code == 200, r.text
        assert r.json()["session"]["continued"] is False
        counted = [s.id for t in engine.plan.targets for s in t.steps]
        sid = engine._session.id
        _bank(engine, [0, 0, 1])

        live = await api.ok(fid)
        assert live["session"] is not None
        assert [s[0] for s in _steps(live)] == counted, \
            "not the ids the run is counting by"
        assert (live["session"]["id"], live["session"]["status"]) == \
            (sid, "active")
        assert _steps(live) == [(counted[0], 2, 1), (counted[1], 1, 1)]

        night.end("incomplete")
        await engine._task
        one = await api.ok(fid)
        assert one["session"] == {"id": sid, "status": "dormant",
                                  "nights": 1, "count_mode": "accepted",
                                  "armed": True, "plan_saved_ts": saved}
        assert _steps(one) == _steps(live)

        clock.t = _local(2026, 9, 20, 21, 30)
        r = await api.client.post(f"/api/flows/{fid}/run", json={})
        assert r.status_code == 200, r.text
        assert r.json()["session"]["continued"] is True
        assert engine._session.id == sid, "premise: night two continued"
        assert [s.id for t in engine.plan.targets for s in t.steps] == \
            counted, "premise: night two compiled the same ids"

        two = await api.ok(fid)
        assert len(engine._session.nights) == 2, "premise: two runs"
        assert two["session"] == {"id": sid, "status": "active",
                                  "nights": 1, "count_mode": "accepted",
                                  "armed": False, "plan_saved_ts": saved}
        assert _steps(two) == _steps(one)

        _bank(engine, [0])
        three = await api.ok(fid)
        assert _steps(three) == [(counted[0], 3, 0), (counted[1], 1, 1)]
        assert three["orphaned"] == {"frames": 0, "steps": 0}
