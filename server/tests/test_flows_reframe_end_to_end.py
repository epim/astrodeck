"""Re-framing through the routes: a small move keeps every compiled id, a
large one restarts them and the save's answer says so (#189 Revision 2
ruling 3, spec 3.3, 2.5; task S3-A).

The rule is ``framing.reframe_carry`` and the anchor is written by the save
(``save_rules.prepare_save``, applied by ``FlowStore.save_and_report``);
both have pure tests. This file holds the whole path an operator's raw field
edit takes: ``PUT /api/flows/{id}`` with a moved Dec, then the ids the run
would carry, read back through ``GET /api/flows/{id}/progress``, which
compiles the stored flow exactly as ``run_flow`` does (the flow's own id,
the run's arguments). An id here is a promise about the ledger: CONTINUE
counts frames by step id alone, so a kept id is a campaign that carries on
and a changed one is a campaign that restarts.

THE GEOMETRY is the spec's (A.5) and test_flows_save_rules.py's: a 3x2 of
2.0 x 1.33 deg panels at 25% overlap, laid out at PA 30 at Dec +41. Its
carry threshold is half the narrower overlap strip, 0.5 x 0.25 x 1.33 deg =
9.975', and a pure north shift moves every corner by the shift: 9.9' moves
them 9.8998' (carries) and 10.1' moves them 10.0998' (does not), both
measured with ``framing.reframe_carry`` before these numbers were written.
RA and Dec are M31's catalogue position; no observer is involved.

The 10.1' save follows the 9.9' one, so it is measured against the ANCHOR,
not the previous save: had the 9.9' save moved the anchor, the 10.1' save
would be a 0.2' nudge and carry.

MUTANTS were written over byte copies of the file named, in a private copy
of ``server/`` under the session scratchpad (``s3-a-routes-k7m2``); the
shared tree was never mutated.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.hub as hub_mod
from astrodeck.config import ConfigStore
from astrodeck.flows.store import FlowStore

RA = "00h 42m 44s"
#: The threshold and the 10.1' move, in degrees (see the module docstring).
THRESHOLD_DEG = 0.5 * 0.25 * 1.33
MOVE_101_DEG = 10.0998 / 60.0


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(app_module, "flow_store", FlowStore(tmp_path / "flows"))
    with TestClient(app_module.create_app()) as c:
        yield c


def _graph(dec: str, *, skip: str = "") -> dict:
    """The 3x2 block, moved to ``dec``, feeding one FILTER CYCLE that rotates
    the panels (the loop wire)."""
    target = {"name": "M31 3x2", "ra": RA, "dec": dec, "rotation": 30,
              "angle": "Rotate to PA", "rows": 2, "cols": 3, "overlap": 25,
              "fovX": 2.0, "fovY": 1.33, "skip": skip}
    return {"nodes": [
                {"id": "t", "type": "target", "x": 0, "y": 0,
                 "params": target},
                {"id": "c", "type": "cycle", "x": 230, "y": 0,
                 "params": {"plan": "L 60, R 60", "cycles": 2}}],
            "edges": [
                {"from": "t", "fromPort": "target", "to": "c",
                 "toPort": "run"},
                {"from": "c", "fromPort": "pass", "to": "t",
                 "toPort": "next"}]}


def _save(client, graph: dict, fid: str | None = None) -> dict:
    body = {"flow": {"name": "reframe", "graph": graph}}
    r = (client.post("/api/flows", json=body) if fid is None
         else client.put(f"/api/flows/{fid}", json=body))
    assert r.status_code == 200, r.text
    return r.json()


def _ids(client, fid: str) -> set[str]:
    """Every target and step id the run would carry, from the progress
    route's compile of the stored flow."""
    r = client.get(f"/api/flows/{fid}/progress")
    assert r.status_code == 200, r.text
    out: set[str] = set()
    for block in r.json()["blocks"]:
        for panel in block["panels"]:
            out.add(panel["target_id"])
            out.update(s["step_id"] for s in panel["steps"])
    return out


def _anchor(tmp_path, fid: str) -> str:
    raw = json.loads((tmp_path / "flows" / f"{fid}.json")
                     .read_text(encoding="utf-8"))
    return next(n["params"]["frameAnchor"] for n in raw["flow"]["graph"]
                ["nodes"] if n["id"] == "t")


class TestAMoveThroughTheRoutes:
    def test_a_move_under_the_threshold_keeps_every_id_and_one_over_restarts(
            self, client, tmp_path):
        """POST at +41 00 00, then PUT 9.9' north: the anchor is kept, the
        answer lists nothing, and every one of the 6 panels' target and
        step ids the run would carry is the same. Then PUT 10.1' north of
        where the counts started: the anchor is replaced, the answer lists
        the block with the move and the threshold, and every id changes.

        RED under mutant "the answer drops the report" (``_persist_flow``
        answers ``reanchored`` as ``[]`` whatever the save said), observed:

            AssertionError: the save's answer names the block it restarted
            assert [] == ['t']
              Right contains one more item: 't'

        RED under mutant "compare with the previous save"
        (``flows/save_rules.py``, ``_anchor_on_save``: a carry returns
        ``text`` instead of ``base``, so each save re-bases the anchor on
        itself), observed at the 9.9' save, whose carry moved the anchor:

            assert '{"cols":3,"d...00","rows":2}' ==
            '{"cols":3,"d...00","rows":2}'
              Skipping 106 identical trailing characters in diff, use -v to
              show
              - {"cols":3,"dec_deg":"41.000000","fov
              ?                         ^^^
              + {"cols":3,"dec_deg":"41.165000","fov
              ?                         ^^^

        (Without the anchor assertion the 10.1' save would then be a 0.2'
        nudge from the moved anchor, and carry.)
        """
        first = _save(client, _graph("+41 00 00"))
        fid = first["id"]
        assert first["reanchored"] == [], "a first save restarts nothing"
        before = _ids(client, fid)
        assert len(before) == 6 * (1 + 2), (
            "premise: 6 panels, each with an L and an R step")
        anchor = _anchor(tmp_path, fid)

        nudged = _save(client, _graph("+41 09 54"), fid)
        assert nudged["reanchored"] == [], "9.9' carries: nothing restarts"
        assert _anchor(tmp_path, fid) == anchor
        assert _ids(client, fid) == before, "every compiled id is kept"

        moved = _save(client, _graph("+41 10 06"), fid)
        assert [r["node_id"] for r in moved["reanchored"]] == ["t"], (
            "the save's answer names the block it restarted")
        (row,) = moved["reanchored"]
        assert row["max_move_deg"] == pytest.approx(MOVE_101_DEG, abs=1e-5)
        assert row["threshold_deg"] == pytest.approx(THRESHOLD_DEG, abs=1e-9)
        assert _anchor(tmp_path, fid) != anchor
        after = _ids(client, fid)
        assert len(after) == len(before) and not (after & before), (
            "10.1' restarts every panel's ids")

    def test_control_a_skip_is_not_a_move(self, client, tmp_path):
        """Skipping a panel is not a change of identity (2.5): the save keeps
        the anchor and lists nothing, the five live panels keep their ids,
        and a re-enabled panel gets its ids back. A control: green on the
        code and under both mutants above. (Under test_flows_save_switches_
        counts's "the store skips the rules" no anchor is written at all,
        and it fails first with ``KeyError: 'frameAnchor'``.)"""
        fid = _save(client, _graph("+41 00 00"))["id"]
        before = _ids(client, fid)
        anchor = _anchor(tmp_path, fid)
        saved = _save(client, _graph("+41 00 00", skip="2-3"), fid)
        assert saved["reanchored"] == []
        assert _anchor(tmp_path, fid) == anchor
        after = _ids(client, fid)
        assert after < before and len(before - after) == 3, (
            "one panel's target and two step ids leave the compile, and "
            "nothing else moves")
        restored = _save(client, _graph("+41 00 00"), fid)
        assert restored["reanchored"] == []
        assert _ids(client, fid) == before, "re-enabled, its ids come back"
