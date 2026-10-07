# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The flat's rotator angle reaches the config, the build and the matrix
(#176, backlog WP-122; wave 16 integration).

WP-122 made the calibration library key and match a flat by the rotator's
MECHANICAL angle and left three wires to the integrator, each one a place the
feature was built and unreachable:

1. ``CalibrationConfig.rotator_bin_deg``: the width of one rotator-angle bin.
   Without the field the library ran at ``keys.ROTATOR_BIN_DEG`` whatever the
   operator wanted, and ``POST /api/calibration/build`` never passed one.
2. ``POST /api/calibration/build`` hands that width to ``cal_library.build``.
3. ``GET /api/calibration/health`` gives each light's ``CalNeed`` the angle of
   the PANEL it serves, converted from the plan's SKY position angle to the
   mechanical one with ``rotation.sky_to_mechanical`` (anchored on the
   rotator's last trusted calibration, with the learned sign), and gives None,
   "any flat serves", when the rotator is not calibrated.

NAMED MUTANTS. Each was run from a byte backup of the one source file inside
the integration worktree, restored byte-identically (sha256 compared) and the
mutant text grepped out, under the command this file normally runs under (one
worker). The assertion each broke is recorded verbatim on the test that
caught it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from types import SimpleNamespace

import astrodeck.api.app as app_module
from astrodeck.config import CalibrationConfig, ConfigStore
from astrodeck.flows.store import FlowStore
from astrodeck.rotation import sky_to_mechanical

ANCHOR_MECH = 100.0
ANCHOR_OFFSET = 10.0


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The routes over a config and a flow library of this test's own, and a
    rotator the test can calibrate or not (``rotator``)."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(app_module, "flow_store", FlowStore(tmp_path / "flows"))
    monkeypatch.setattr(app_module.hub, "last_sky_angle", None, raising=False)
    monkeypatch.delitem(app_module.hub.devices, "rotator", raising=False)
    with TestClient(app_module.create_app()) as c:
        c.store = temp_store
        yield c


def _calibrate(monkeypatch, *, sign: int | None = 1, connected: bool = True,
               calibrated: bool = True, synced: bool = True,
               rec_offset: float = ANCHOR_OFFSET) -> None:
    """A rotator in the hub's device map whose last sky-angle record is a
    calibration at ``ANCHOR_MECH``/``ANCHOR_OFFSET``, or one that is not."""
    hub = app_module.hub
    rot = SimpleNamespace(connected=connected, synced=synced,
                          sync_offset_deg=ANCHOR_OFFSET)
    monkeypatch.setitem(hub.devices, "rotator", rot)
    monkeypatch.setattr(hub, "last_sky_angle", {
        "pa_deg": 90.0, "exposed_at": 1.0, "solved_at": 1.0,
        "source": "plate solve + sync", "pier_side": None,
        "camera": "Sim Camera", "calibrated": calibrated, "reason": None,
        "mechanical_deg": ANCHOR_MECH, "rotator_before_deg": None,
        "offset_deg": rec_offset}, raising=False)
    monkeypatch.setattr(hub, "_rotator_sky_sign", sign, raising=False)


def _graph(*sky_angles: float) -> dict:
    """A flow with one target block per angle, each with its own capture."""
    nodes = [{"id": "d", "type": "dusk", "x": 30, "y": 60,
              "params": {"offset": -30, "stop": "Dawn", "minAlt": 30}}]
    edges = []
    for i, angle in enumerate(sky_angles):
        t, c = f"t{i}", f"c{i}"
        nodes += [
            {"id": t, "type": "target", "x": 260, "y": 60 + 160 * i,
             "params": {"name": f"M{31 + i}", "ra": "00h 42m 44s",
                        "dec": "+41 16 09", "rotation": angle}},
            {"id": c, "type": "capture", "x": 490, "y": 60 + 160 * i,
             "params": {"filter": "L", "exposure": 120, "gain": 100,
                        "bin": "1", "count": 12, "goal": 0}}]
        edges += [{"from": "d", "fromPort": "window", "to": t, "toPort": "arm"},
                  {"from": t, "fromPort": "target", "to": c, "toPort": "run"}]
    return {"nodes": nodes, "edges": edges}


def _flat_angles(client, *sky_angles: float) -> list:
    """The FLAT rows' ``rotation_deg`` for a flow of these sky angles."""
    body = {"flow": {"name": "Angles", "folder": "My flows",
                     "graph": _graph(*sky_angles)}}
    saved = client.post("/api/flows", json=body)
    assert saved.status_code == 200, saved.text
    out = client.get(f"/api/calibration/health?flow_id={saved.json()['id']}")
    assert out.status_code == 200, out.text
    rows = out.json()["rows"]
    assert {r["kind"] for r in rows} == {"DARK", "BIAS", "FLAT"}, (
        "premise: the matrix has its three kinds")
    for r in rows:
        if r["kind"] != "FLAT":
            assert r["rotation_deg"] is None, (
                f"a {r['kind']} depends on no angle: {r}")
    return [r["rotation_deg"] for r in rows if r["kind"] == "FLAT"]


# ----------------------------------------------------------- 1. the config

class TestTheBinIsConfig:
    def test_it_defaults_to_the_librarys_own_default(self):
        """The field's default is the library's (``keys.ROTATOR_BIN_DEG``), so
        a config written before this field loads and builds as it did."""
        from astrodeck.calibration.keys import ROTATOR_BIN_DEG
        assert CalibrationConfig().rotator_bin_deg == ROTATOR_BIN_DEG == 2.0

    @pytest.mark.parametrize("value", [-0.1, 30.1, 360.0])
    def test_the_bounds_live_on_the_model(self, value):
        """Rejected by the model, so the route 422s before the stacker."""
        with pytest.raises(ValidationError):
            CalibrationConfig(rotator_bin_deg=value)

    @pytest.mark.parametrize("value", [0.0, 1.0, 2.0, 30.0])
    def test_the_edges_are_allowed(self, value, tmp_path):
        """0 is "no binning" (``keys.rotator_bin``), 1.0 is the matcher's own
        tolerance, 30 the widest."""
        store = ConfigStore(path=tmp_path / "astrodeck.json")
        cfg = store.set_calibration(CalibrationConfig(rotator_bin_deg=value))
        assert cfg.calibration.rotator_bin_deg == value

    def test_a_bin_narrower_than_the_match_tolerance_is_rejected(self, tmp_path):
        """The temperature rule for the rotator: a bin narrower than the
        matcher's angle tolerance (1 degree) splits flats the matcher would
        accept for one light into separate masters, which silently halves the
        depth of every flat master.

        RED under mutant "no relational rule" (the ``if 0 < calibration.
        rotator_bin_deg < ROTATION_TOL_DEG:`` test in ``ConfigStore.
        set_calibration`` made ``if False:``), observed:

            E   Failed: DID NOT RAISE <class 'ValueError'>
        """
        store = ConfigStore(path=tmp_path / "astrodeck.json")
        with pytest.raises(ValueError, match="rotator-angle bin"):
            store.set_calibration(CalibrationConfig(rotator_bin_deg=0.5))

    def test_the_route_422s_on_it_and_round_trips_a_good_value(self, client):
        bad = client.post("/api/config/calibration",
                          json={"rotator_bin_deg": 0.5})
        assert bad.status_code == 422 and "rotator-angle bin" in bad.text
        good = client.post("/api/config/calibration",
                           json={"rotator_bin_deg": 5})
        assert good.status_code == 200, good.text
        assert good.json()["calibration"]["rotator_bin_deg"] == 5


# ------------------------------------------------------------- 2. the build

class TestTheBuildPassesIt:
    def test_the_build_route_hands_the_configured_bin_to_the_library(
            self, client, monkeypatch):
        """RED under mutant "the build forgets the bin" (``rotator_bin_deg=
        c.rotator_bin_deg`` removed from the ``cal_library.build`` call in
        ``build_masters``), observed:

            E   AssertionError: the build was not told the configured rotator bin: {'sigma': 3.0, 'temp_bin_width': 5.0, 'max_frames': 100}
        """
        seen: dict = {}

        def fake_build(**kw):
            seen.update(kw)
            return SimpleNamespace(built=[], skipped=[], rejected=[])

        monkeypatch.setattr(app_module.cal_library, "build", fake_build)
        assert client.post("/api/config/calibration",
                           json={"rotator_bin_deg": 6.5}).status_code == 200
        assert client.post("/api/calibration/build").status_code == 200
        assert seen.get("rotator_bin_deg") == 6.5, (
            f"the build was not told the configured rotator bin: {seen}")
        assert seen.get("temp_bin_width") == 5.0, (
            "premise: the temperature bin still goes through")


# ------------------------------------------------------------ 3. the matrix

class TestTheMatrixKnowsTheAngle:
    def test_a_calibrated_rotator_gives_each_flat_row_its_mechanical_angle(
            self, client, monkeypatch):
        """The plan's angle is the SKY PA; the row is for the metal's angle.
        With the rotator calibrated at mechanical 100 / offset 10 (so sky 90
        there), sign +1, a block planned at sky 30 is shot at mechanical 40.

        RED under mutant "the sky angle is used as it stands" (``mod360(
        sky_to_mechanical(...))`` in ``mechanical_angle_of`` replaced by
        ``float(sky_deg)``), observed:

            E   assert [30.0] == [40.0 +- 4.0e-05]
        """
        _calibrate(monkeypatch, sign=1)
        want = sky_to_mechanical(30.0, ANCHOR_MECH, ANCHOR_OFFSET, 1)
        assert want == pytest.approx(40.0), "premise: the hand arithmetic"
        assert _flat_angles(client, 30.0) == [pytest.approx(want)]

    def test_the_learned_sign_is_applied(self, client, monkeypatch):
        """Sign -1 (the rig's CAA, pier west, #145) runs the sky angle against
        the mechanical one: sky 30 from the anchor (sky 90) is mechanical
        100 - (30 - 90) = 160.

        RED under mutant "the sign is ignored" (``hub._effective_rotator_
        sign()`` in the route replaced by ``1``), observed:

            E   assert [40.0] == [160.0 +- 1.6e-04]
        """
        _calibrate(monkeypatch, sign=-1)
        want = sky_to_mechanical(30.0, ANCHOR_MECH, ANCHOR_OFFSET, -1)
        assert want == pytest.approx(160.0), "premise: the hand arithmetic"
        assert _flat_angles(client, 30.0) == [pytest.approx(want)]

    def test_two_panels_at_two_angles_are_two_flat_rows(
            self, client, monkeypatch):
        """A rotating mosaic's panels each have their own angle, and a flat
        serves only its own: one row per angle.

        RED under mutant "the angle is left out of the dedupe key" (the
        ``None if mech is None else round(mech, 3)`` element removed from the
        ``key`` tuple in the route), observed:

            E   assert [40.0] == [40.0 +- 4.0e-05, 100.0 +- 1.0e-04]
            E     Right contains one more item: 100.0 +- 1.0e-04
        """
        _calibrate(monkeypatch, sign=1)
        got = _flat_angles(client, 30.0, 90.0)
        assert sorted(got) == [pytest.approx(40.0), pytest.approx(100.0)]

    def test_the_same_angle_twice_is_one_row(self, client, monkeypatch):
        """CONTROL for the above: two blocks at one angle need one flat."""
        _calibrate(monkeypatch, sign=1)
        assert len(_flat_angles(client, 30.0, 30.0)) == 1

    def test_a_rotating_mosaic_gives_each_panel_its_own_flat_row(
            self, client, monkeypatch):
        """The compile has the BLOCK; the plan has its PANELS, and a rotating
        block commands each panel its own angle (its ``pa_deg``, #175): a 1x4
        of 3 x 2 degree frames at dec +44 turns the camera from 23.7 to 16.2
        degrees across the strip. Four panels, four angles, past the matcher's
        one-degree tolerance, so four flats.

        RED under mutant "the block, not its panels" (the plan refused, so
        the ``lights`` list falls back to the compile's block targets with no
        angle: ``raise GraphNotRunnable("mutant")`` ahead of the
        ``to_sequence_plan`` call in ``calibration_health``), observed:

            E   assert [None] == [26.170832488... +- 2.6e-05, ...]
            E     Right contains 3 more items, first extra item: 28.74233768504206 +- 2.9e-05
        """
        from astrodeck.flows.compile import compile_plan
        from astrodeck.flows.models import FlowGraph
        from astrodeck.flows.to_plan import to_sequence_plan
        _calibrate(monkeypatch, sign=1)
        graph = {
            "nodes": [
                {"id": "n2", "type": "target", "x": 30, "y": 60,
                 "params": {"name": "NGC 7000", "ra": "20h 59m 17s",
                            "dec": "+44 31 44", "rows": 1, "cols": 4,
                            "overlap": 10, "fovX": 3.0, "fovY": 2.0,
                            "angle": "Rotate to PA", "rotation": 20}},
                {"id": "n7", "type": "capture", "x": 270, "y": 60,
                 "params": {"filter": "L", "exposure": 60, "gain": 100,
                            "bin": "1", "count": 4, "goal": 0}}],
            "edges": [{"id": "e1", "from": "n2", "fromPort": "target",
                       "to": "n7", "toPort": "run"}],
            "settings": {}}
        fg = FlowGraph.model_validate(graph)
        plan, _ = to_sequence_plan(compile_plan(fg, "x"), fg, flow_id="f")
        skies = [t.rotation_deg for t in plan.targets]
        assert len(skies) == 4 and len(set(skies)) == 4, (
            "premise: four panels at four sky angles")
        want = sorted(sky_to_mechanical(a, ANCHOR_MECH, ANCHOR_OFFSET, 1) % 360
                      for a in skies)
        saved = client.post("/api/flows", json={"flow": {
            "name": "Strip", "folder": "My flows", "graph": graph}})
        assert saved.status_code == 200, saved.text
        out = client.get(f"/api/calibration/health?flow_id={saved.json()['id']}")
        got = sorted(r["rotation_deg"] for r in out.json()["rows"]
                     if r["kind"] == "FLAT")
        assert got == [pytest.approx(w) for w in want]

    @pytest.mark.parametrize("what,kw", [
        ("no rotator connected", None),
        ("a rotator not calibrated", {"calibrated": False}),
        ("a rotator not synced", {"synced": False}),
        ("a rotator re-synced since the calibration", {"rec_offset": 55.0}),
        ("a rotator that is not connected", {"connected": False}),
    ])
    def test_without_a_trusted_calibration_the_row_has_no_angle(
            self, client, monkeypatch, what, kw):
        """None, "any flat serves": a number built on a bare live reading
        would invent an angle the night does not have, and the matrix would
        then call a flat at the wrong angle missing.

        RED under mutant "the fallback anchor is trusted" (the ``if math.
        isnan(anchor_mech): return None`` test removed from
        ``mechanical_angle_of``), observed on every case but the first (the
        route builds a NaN angle, and the JSON answer cannot carry it):

            E   ValueError: Out of range float values are not JSON compliant: nan
        """
        if kw is not None:
            _calibrate(monkeypatch, **kw)
        assert _flat_angles(client, 30.0) == [None], what

    def test_a_block_with_no_angle_has_no_constraint_even_when_calibrated(
            self, client, monkeypatch):
        """CONTROL: a target planned at "any angle" (-1) commands no angle, so
        its flats are not tied to one."""
        _calibrate(monkeypatch, sign=1)
        assert _flat_angles(client, -1) == [None]
