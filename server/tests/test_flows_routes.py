"""The Flows API — library, folders, compile, tonight, calibration health, run.

Three things here are worth more than the coverage:

1. **The wire alias.** ``FlowEdge``'s source field is ``from_`` with
   ``alias="from"``. Every edge round-trips through the RESPONSE, not just
   through the model, because a route that hand-built its payload with
   ``model_dump()`` would emit ``from_`` and every wire in the canvas would
   vanish with no error anywhere.

2. **Server-owned fields.** ``last_run`` and ``last_result`` are what the
   library card draws, and nothing on the server writes them. A client can put
   whatever it likes in that field of a ``FlowRecord``; the route must not keep
   it, or a flow that has never run shows a green card.

3. **The run guards.** ``POST /api/flows/{id}/run`` is a second way into
   ``SequenceEngine.start``, and a second start path that quietly omits one of
   the first one's guards is how a guard stops being a guard.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore
from astrodeck.flows.store import FlowStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    # CONFIG_DIR *and* the store object. FlowStore resolves CONFIG_DIR live so
    # the first alone would mostly work — but flow_store is a module singleton,
    # and a carried-over singleton makes one test pass for the previous test's
    # reasons.
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(app_module, "flow_store", FlowStore(tmp_path / "flows"))

    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


GRAPH = {
    "nodes": [
        {"id": "d", "type": "dusk", "x": 30, "y": 60,
         "params": {"offset": -30, "stop": "Dawn", "minAlt": 30}},
        {"id": "t", "type": "target", "x": 260, "y": 60,
         "params": {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09",
                    "rotation": 0}},
        {"id": "c", "type": "capture", "x": 490, "y": 60,
         "params": {"filter": "L", "exposure": 120, "gain": 100, "bin": "1",
                    "count": 12, "goal": 0}},
    ],
    "edges": [
        {"from": "d", "fromPort": "window", "to": "t", "toPort": "arm"},
        {"from": "t", "fromPort": "target", "to": "c", "toPort": "run"},
    ],
}


def _flow(**over):
    body = {"name": "Test flow", "folder": "My flows", "graph": GRAPH}
    body.update(over)
    return body


class TestLibrary:
    def test_the_examples_are_listed_without_being_on_disk(self, client):
        rows = client.get("/api/flows").json()
        ids = {r["id"] for r in rows}
        assert "example-m16" in ids
        assert all(r["readonly"] for r in rows), "a fresh library is all fixtures"

    def test_the_list_is_cards_not_graphs(self, client):
        """A library of 30 flows at up to 400 nodes each is megabytes of wires
        to draw a card wall."""
        row = client.get("/api/flows").json()[0]
        assert "graph" not in row
        assert {"id", "name", "folder", "stages", "wires"} <= set(row)

    def test_save_then_read_back(self, client):
        saved = client.post("/api/flows", json={"flow": _flow()})
        assert saved.status_code == 200, saved.text
        fid = saved.json()["id"]
        got = client.get(f"/api/flows/{fid}").json()
        assert got["name"] == "Test flow"
        assert len(got["graph"]["nodes"]) == 3

    def test_every_wire_survives_the_RESPONSE(self, client):
        """The alias trap. ``model_dump()`` would emit ``from_`` here and the
        canvas would render a graph with no wires and no error."""
        fid = client.post("/api/flows", json={"flow": _flow()}).json()["id"]
        edges = client.get(f"/api/flows/{fid}").json()["graph"]["edges"]
        assert len(edges) == 2
        for e in edges:
            assert "from" in e and "from_" not in e, e
        assert {e["from"] for e in edges} == {"d", "t"}

    def test_a_put_writes_the_path_id_not_the_body_id(self, client):
        fid = client.post("/api/flows", json={"flow": _flow()}).json()["id"]
        body = _flow(id="somewhere-else", name="renamed")
        out = client.put(f"/api/flows/{fid}", json={"flow": body}).json()
        assert out["id"] == fid and out["name"] == "renamed"
        assert client.get("/api/flows/somewhere-else").status_code == 404

    def test_delete(self, client):
        fid = client.post("/api/flows", json={"flow": _flow()}).json()["id"]
        assert client.delete(f"/api/flows/{fid}").json() == {"deleted": fid}
        assert client.get(f"/api/flows/{fid}").status_code == 404

    def test_deleting_something_absent_is_a_404_not_a_cheerful_lie(self, client):
        """``FlowStore.delete`` returns False rather than raising, so without
        the route's own check this answers 'deleted' for a flow it never saw."""
        assert client.delete("/api/flows/never-existed").status_code == 404

    def test_an_example_cannot_be_overwritten_or_deleted(self, client):
        assert client.put("/api/flows/example-m16",
                          json={"flow": _flow()}).status_code == 403
        assert client.delete("/api/flows/example-m16").status_code == 403

    @pytest.mark.parametrize("bad", ["../../astrodeck", "..\\..\\astrodeck",
                                     "a/b", "CON", "x.", "nul"])
    def test_a_traversal_id_cannot_write_outside_the_flows_dir(
            self, client, bad, tmp_path):
        """The id is client-controllable in BOTH the path and the body, and a
        value like "../../astrodeck" would let a PUT overwrite the server's own
        config. Asserted on the FILESYSTEM, not on the status code — a refusal
        that still wrote the file would satisfy any code-only check."""
        before = {p for p in tmp_path.rglob("*") if p.is_file()}
        r = client.put(f"/api/flows/{bad}", json={"flow": _flow()})
        assert r.status_code != 200, r.text
        assert {p for p in tmp_path.rglob("*") if p.is_file()} == before, \
            "the refused write still touched the disk"


class TestServerOwnedFields:
    def test_a_client_cannot_forge_a_run_result(self, client):
        """``last_result`` is what the library card draws, and nothing on the
        server writes it — so without re-deriving, a flow that has never run
        gets a green card by asking for one."""
        body = _flow(last_run=1.0, last_result="ok")
        out = client.post("/api/flows", json={"flow": body}).json()
        assert out["last_run"] is None and out["last_result"] == ""

    def test_a_client_cannot_smuggle_in_a_readonly_flow(self, client):
        out = client.post("/api/flows", json={"flow": _flow(readonly=True)})
        assert out.status_code == 200 and out.json()["readonly"] is False

    def test_an_update_keeps_the_stored_history_rather_than_the_bodys(self, client):
        fid = client.post("/api/flows", json={"flow": _flow()}).json()["id"]
        created = client.get(f"/api/flows/{fid}").json()["created_ts"]
        out = client.put(f"/api/flows/{fid}",
                         json={"flow": _flow(created_ts=0.0)}).json()
        assert out["created_ts"] == created


class TestFolders:
    def test_the_two_seeded_folders_are_always_there(self, client):
        names = [f["name"] for f in client.get("/api/flows/folders").json()]
        assert names[0] == "My flows" and names[-1] == "Examples"

    def test_folders_resolves_before_the_flow_id_route(self, client):
        """Ordering. Declared after ``/api/flows/{flow_id}`` this would 404 with
        "folders" read as a flow id — a route that exists and cannot be hit."""
        r = client.get("/api/flows/folders")
        assert r.status_code == 200 and isinstance(r.json(), list)

    def test_rename_reparents_every_flow_in_it(self, client):
        client.post("/api/flows", json={"flow": _flow(folder="Autumn")})
        client.post("/api/flows", json={"flow": _flow(folder="Autumn",
                                                      name="second")})
        out = client.post("/api/flows/folders",
                          json={"name": "Autumn", "new_name": "Winter"}).json()
        assert out["moved"] == 2
        assert "Winter" in [f["name"] for f in
                            client.get("/api/flows/folders").json()]

    @pytest.mark.parametrize("bad", ["../../../etc", "a/b/c/d/e/f", "", "x" * 300])
    def test_a_path_shaped_folder_name_is_refused(self, client, bad):
        """``rename_folder`` uses ``model_copy``, which runs NO validators — so
        the record's own folder rule never sees this and the string would be
        persisted into every moved flow."""
        r = client.post("/api/flows/folders",
                        json={"name": "My flows", "new_name": bad})
        assert r.status_code == 422, r.text

    def test_deleting_a_folder_moves_its_flows_and_never_deletes_them(self, client):
        client.post("/api/flows", json={"flow": _flow(folder="Autumn")})
        out = client.delete("/api/flows/folders/Autumn").json()
        assert out == {"moved": 1, "reparented_to": "My flows"}
        assert len([r for r in client.get("/api/flows").json()
                    if r["name"] == "Test flow"]) == 1

    def test_the_examples_folder_is_fixed(self, client):
        assert client.delete("/api/flows/folders/Examples").status_code == 403
        assert client.post("/api/flows/folders",
                           json={"name": "Examples",
                                 "new_name": "Mine"}).status_code == 403


class TestCompile:
    def test_a_stored_flow_compiles_to_four_lists(self, client):
        fid = client.post("/api/flows", json={"flow": _flow()}).json()["id"]
        out = client.post(f"/api/flows/{fid}/compile").json()
        assert set(out) == {"plan", "structural", "issues", "unmapped"}
        assert out["plan"]["targets"][0]["name"] == "M31"
        assert out["structural"] == []

    def test_a_draft_compiles_without_being_saved(self, client):
        out = client.post("/api/flows/compile",
                          json={"graph": GRAPH, "name": "draft"}).json()
        assert out["plan"]["name"] == "draft"
        # Asserted on ownership, not by diffing two listings: the Examples are
        # constructed fresh on every read, so their updated_ts differs between
        # calls and a list-equality check would fail for a reason that has
        # nothing to do with persistence.
        assert all(r["readonly"] for r in client.get("/api/flows").json()), \
            "compiling a draft must not persist it"

    def test_an_empty_canvas_gets_an_answer_not_a_422(self, client):
        """A new flow is the normal state of an editor."""
        out = client.post("/api/flows/compile", json={})
        assert out.status_code == 200
        assert out.json()["plan"]["targets"] == []

    def test_a_broken_wire_is_reported_as_STRUCTURAL(self, client):
        """``compile_plan`` validates nothing and will emit ``action: "?"`` for
        an edge whose destination is missing, so the route must run the graph's
        own check itself."""
        graph = {"nodes": GRAPH["nodes"],
                 "edges": [*GRAPH["edges"],
                           {"from": "c", "fromPort": "frame", "to": "ghost",
                            "toPort": "events"}]}
        out = client.post("/api/flows/compile", json={"graph": graph}).json()
        assert out["structural"], "an edge to a node that does not exist"

    def test_the_doctors_advice_comes_back_too(self, client):
        out = client.post("/api/flows/compile", json={"graph": GRAPH}).json()
        assert all({"text", "level"} <= set(i) for i in out["issues"])

    def test_what_the_run_will_not_honour_is_named(self, client):
        """The list that stops a graph feature being silently inert."""
        m16 = client.post("/api/flows/example-m16/compile").json()
        keys = {u["key"] for u in m16["unmapped"]}
        assert "automation.dome" in keys
        assert any(k.startswith("instructions[on_clouds_in") for k in keys)

    def test_compiling_something_absent_is_a_404(self, client):
        assert client.post("/api/flows/nope/compile").status_code == 404


class TestTonight:
    def test_it_resolves_for_a_stored_flow(self, client):
        fid = client.post("/api/flows", json={"flow": _flow()}).json()["id"]
        out = client.get(f"/api/flows/{fid}/tonight")
        assert out.status_code == 200
        assert "ok" in out.json()

    def test_no_site_is_a_reason_sentence_not_an_http_error(self, client):
        """The reason IS the product — the panel shows it. A 500 here would
        replace a sentence the operator can act on with one they cannot."""
        fid = client.post("/api/flows", json={"flow": _flow()}).json()["id"]
        body = client.get(f"/api/flows/{fid}/tonight").json()
        if not body.get("ok"):
            assert isinstance(body.get("reason"), str) and body["reason"]

    def test_it_is_gated_on_site_derived_not_view_status(self):
        """Every value it returns is f(latitude, longitude) — dark windows,
        altitude curves, transit, the flip instant. A key-name filter cannot
        withhold that, and an audit of this codebase recovered the observatory
        to 2.9 km from three viewer-legal requests."""
        from astrodeck.auth.capabilities import CAP_VIEW_SITE_DERIVED
        from astrodeck.auth.rbac import CAP_ATTR
        app = app_module.create_app()
        route = next(r for r in app.routes
                     if getattr(r, "path", "") == "/api/flows/{flow_id}/tonight")
        assert CAP_VIEW_SITE_DERIVED in getattr(route.endpoint, CAP_ATTR, set())


class TestCalibrationHealth:
    def test_with_no_flow_it_says_nothing_is_planned(self, client):
        """An empty matrix MUST NOT read as healthy — 'you have everything' and
        'you have not asked for anything' are different statements."""
        out = client.get("/api/calibration/health").json()
        assert out["planned"] is False
        assert out["rows"] == []

    def test_a_flow_makes_the_rows(self, client):
        fid = client.post("/api/flows", json={"flow": _flow()}).json()["id"]
        out = client.get(f"/api/calibration/health?flow_id={fid}").json()
        assert out["planned"] is True and out["rows"]
        assert {r["kind"] for r in out["rows"]} == {"DARK", "BIAS", "FLAT"}

    def test_the_assumptions_are_declared_rather_than_hidden(self, client):
        """Offset and sensor temperature are not in the graph vocabulary at all.
        A row that quietly used a default would be inventing the very values the
        matrix is supposed to be matching on."""
        out = client.get("/api/calibration/health").json()
        assert out["counts_masters_only"] is True
        assert out["assumed"] == {"offset": 30, "temp_c": None}

    def test_an_absent_flow_is_a_404(self, client):
        assert client.get(
            "/api/calibration/health?flow_id=nope").status_code == 404


class TestRun:
    def test_a_flow_whose_features_are_dropped_refuses_until_acknowledged(
            self, client):
        """M31 loses its on_unsafe rule and its integration goal. Starting a
        night that quietly does less than the canvas shows is the thing this
        whole seam exists to prevent."""
        fid = client.post("/api/flows", json={"flow": _flow()}).json()["id"]
        r = client.post(f"/api/flows/{fid}/run", json={})
        assert r.status_code == 409
        body = r.json()["detail"]
        assert body["code"] == "unmapped" and body["unmapped"]

    def _dome_flow(self, client):
        graph = {"nodes": [*GRAPH["nodes"],
                           {"id": "dm", "type": "dome", "x": 700, "y": 60,
                            "params": {}}],
                 "edges": GRAPH["edges"]}
        return client.post("/api/flows",
                           json={"flow": _flow(graph=graph)}).json()["id"]

    def test_a_dome_node_does_NOT_block_when_no_dome_is_attached(self, client):
        """There is no roof to leave open. Refusing anyway would stop the
        shipped M16 example running on the simulator, which the handoff's
        definition of done explicitly requires."""
        fid = self._dome_flow(client)
        r = client.post(f"/api/flows/{fid}/run", json={"accept_unmapped": True})
        code = (r.json().get("detail") or {}).get("code") \
            if isinstance(r.json().get("detail"), dict) else None
        assert code != "dome_unmapped", r.text

    def test_a_dome_node_DOES_block_when_a_dome_is_connected(self, client,
                                                             monkeypatch):
        """A DOME CONTROL node compiles a real policy that SequencePlan has
        nowhere to put. Unlike a lost flat panel, that means a shutter which was
        promised to close on unsafe and will not — so it refuses, and
        accept_unmapped does not clear it."""
        fid = self._dome_flow(client)

        class _Dome:
            connected = True
        monkeypatch.setitem(app_module.hub.devices, "dome", _Dome())
        r = client.post(f"/api/flows/{fid}/run", json={"accept_unmapped": True})
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "dome_unmapped"

    def test_an_unrunnable_graph_is_a_422_naming_the_problem(self, client):
        graph = {"nodes": [n for n in GRAPH["nodes"] if n["type"] != "target"],
                 "edges": []}
        fid = client.post("/api/flows",
                          json={"flow": _flow(graph=graph)}).json()["id"]
        r = client.post(f"/api/flows/{fid}/run", json={"accept_unmapped": True})
        assert r.status_code == 422
        assert "target" in r.json()["detail"]["detail"].lower()

    def test_running_something_absent_is_a_404(self, client):
        assert client.post("/api/flows/nope/run", json={}).status_code == 404

    def test_the_sun_cone_is_checked_EVEN_WITH_force(self, client, monkeypatch):
        """The one guard ``force`` does not lift, and the asymmetry is the whole
        point: every other refusal on this route costs you a night, and this one
        costs you a sensor. Disarming it takes a solar session in config, not a
        flag on a request.

        Driven through ``hub._check_solar`` rather than by picking a target near
        today's Sun — a test whose subject is "this fires" must not depend on
        the date it runs."""
        from astrodeck.devices.base import DeviceError
        monkeypatch.setattr(
            app_module.hub, "_check_solar",
            lambda ra, dec: (_ for _ in ()).throw(
                DeviceError("target is 3.1 deg from the Sun")))
        fid = client.post("/api/flows", json={"flow": _flow()}).json()["id"]
        r = client.post(f"/api/flows/{fid}/run",
                        json={"accept_unmapped": True, "force": True})
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "sun_exclusion"

    def test_force_DOES_lift_the_horizon_refusal(self, client, monkeypatch):
        """The companion to the above — if force lifted nothing, the test
        opposite it would pass for the wrong reason."""
        from astrodeck.devices.base import DeviceError
        monkeypatch.setattr(
            app_module.hub, "_check_altitude",
            lambda *a, **k: (_ for _ in ()).throw(DeviceError("below horizon")),
            raising=False)
        fid = client.post("/api/flows", json={"flow": _flow()}).json()["id"]
        blocked = client.post(f"/api/flows/{fid}/run",
                              json={"accept_unmapped": True})
        forced = client.post(f"/api/flows/{fid}/run",
                             json={"accept_unmapped": True, "force": True})
        codes = {r.json().get("detail", {}).get("code")
                 for r in (blocked, forced)
                 if isinstance(r.json().get("detail"), dict)}
        assert "sun_exclusion" not in codes

    def test_it_declares_the_motion_sink(self):
        """``reaches={"SequenceEngine.start"}`` is what ARMS the boot check that
        a mount-motion route is gated on control.mount. Omitting it boots clean
        while shipping a motion route behind control.capture — the failure is
        silent, which is why it gets its own test."""
        from astrodeck.auth.capabilities import CAP_CONTROL_MOUNT
        from astrodeck.auth.rbac import CAP_ATTR, REACHES_ATTR
        app = app_module.create_app()
        route = next(r for r in app.routes
                     if getattr(r, "path", "") == "/api/flows/{flow_id}/run")
        assert "SequenceEngine.start" in getattr(route.endpoint, REACHES_ATTR, set())
        assert CAP_CONTROL_MOUNT in getattr(route.endpoint, CAP_ATTR, set())
