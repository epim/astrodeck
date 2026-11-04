# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Angle-keyed flats, the caller side (#1110 C-04a: #784 #825 #785 #827, and
the callers half of #176).

WP-122 keyed a flat by the rotator's MECHANICAL angle in the frame record, the
``CalKey`` and the matcher. No caller passed an angle, so ``best_master``
returned the same flat for a light at any angle. What this file pins:

* the stacking bundle reads each light's ROTMECH, groups lights by angle
  (a stack never spans two flats' worth of dust shadow), and asks the library
  for the flat at the group's own angle (#784, #825);
* a light whose angle is not recorded gets no flat from a library whose flats
  are keyed by angle, and the bundle says why (fail closed);
* the health matrix names the angle "rotator angle", with its degree value,
  not "PA" (#785);
* ``GET /api/calibration/health`` computes the flow's demand once per flow
  revision (#827).

NAMED MUTANT: ``CalibrationLibraryAdapter.match`` builds its ``LightNeed``
without ``rotator_mech_deg``. Reds ``test_each_light_gets_the_flat_at_its_own_angle``.
"""
from __future__ import annotations

import math
import threading
from types import SimpleNamespace

import pytest
from astropy.io import fits
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.calibration.keys import ROTATOR_BIN_DEG, circular_mean_angle
from astrodeck.calibration.matcher import (LightNeed, MasterRecord,
                                           MatchTolerance, best_master,
                                           unknown_angle_refusal)
from astrodeck.config import ConfigStore
from astrodeck.flows.calibration_health import (CalNeed, DemandCache,
                                                health_matrix)
from astrodeck.flows.store import FlowStore
from astrodeck.sequence.bundle import (CalibrationLibraryAdapter,
                                       NullMasterLibrary, build_bundle,
                                       bundle_summary, externalize_bundle,
                                       manifest_json)
from astrodeck.sequence.report import FrameRecord, SessionReport

TOL = MatchTolerance()


# ----------------------------------------------------------------- fixtures

def _flat(mid: str, angle: float | None, *, count: int = 20,
          filt: str = "Ha") -> MasterRecord:
    return MasterRecord(id=mid, frame_type="FLAT", exposure_s=0.0, gain=100,
                        offset=30, temp_c=None, binning=1, filter=filt,
                        frame_count=count, path=f"/lib/{mid}.fits",
                        built_ts=1.0, rotator_mech_deg=angle)


class _Lib:
    """A library that can list masters, which is all the adapter needs."""

    def __init__(self, *masters: MasterRecord) -> None:
        self._masters = list(masters)

    def list_masters(self) -> list[MasterRecord]:
        return list(self._masters)


def _light(name: str, ts: float = 1.0, filt: str = "Ha") -> FrameRecord:
    return FrameRecord(ts=ts, target="M31", filter=filt, frame_type="Light",
                       exposure_s=300.0, gain=100, offset=30, binning=1,
                       hfr=2.0, saved_path=f"/cap/{name}.fits")


def _bundle(frames, library, angles: dict | None = None, **kw):
    rep = SessionReport(id="r1", plan_name="P", frames=frames)
    reader = None if angles is None else (lambda p: angles.get(p))
    return build_bundle(rep, library, is_local=lambda p: True,
                        light_angle=reader, **kw)


def _two_angle_frames():
    """Three lights at mechanical 10, two at 100; the 100 deg flat is the
    deeper stack, so a match that ignores the angle prefers it for both."""
    frames = [_light(f"a{i}", ts=i) for i in range(3)]
    frames += [_light(f"b{i}", ts=10 + i) for i in range(2)]
    angles = {f"/cap/a{i}.fits": 10.0 + 0.1 * i for i in range(3)}
    angles.update({f"/cap/b{i}.fits": 100.0 for i in range(2)})
    return frames, angles


# --------------------------------------------------------- the bundle pairs

class TestTheBundlePairsEachLightWithItsAngle:
    def test_each_light_gets_the_flat_at_its_own_angle(self):
        """A bundle with lights at two rotator angles and flats at two angles
        pairs each group with the flat shot at its angle.

        RED under the named mutant (``rotator_mech_deg=key.rotator_mech_deg``
        removed from the ``LightNeed`` in ``CalibrationLibraryAdapter._need``):
        the need has no angle, the angle-keyed library refuses it, and no
        group is paired. (With the refusal also off, the matcher has no angle
        to rank by and pairs the deeper 100 degree flat with both groups.)
        """
        frames, angles = _two_angle_frames()
        lib = CalibrationLibraryAdapter(
            _Lib(_flat("near10", 10.1, count=20),
                 _flat("deep100", 100.0, count=60)))
        b = _bundle(frames, lib, angles)
        assert len(b.groups) == 2
        paired = {round(g.rotator_mech_deg): g.master_sources.get("flat")
                  for g in b.groups}
        assert paired == {10: "/lib/near10.fits", 100: "/lib/deep100.fits"}
        assert [len(g.lights) for g in b.groups] == [3, 2]

    def test_a_flat_at_a_third_angle_serves_neither_group(self):
        """The matcher's tolerance still rules: a flat 40 degrees from both
        groups is a gap for both, not the nearest of two wrong ones."""
        frames, angles = _two_angle_frames()
        b = _bundle(frames, CalibrationLibraryAdapter(
            _Lib(_flat("far", 55.0))), angles)
        assert [g.missing_masters for g in b.groups] == [
            ("Dark", "Flat", "Bias")] * 2

    def test_the_group_carries_the_mean_of_its_lights_angles(self):
        frames, angles = _two_angle_frames()
        b = _bundle(frames, NullMasterLibrary(), angles)
        assert b.groups[0].rotator_mech_deg == pytest.approx(10.1, abs=1e-3)
        assert b.groups[1].rotator_mech_deg == pytest.approx(100.0, abs=1e-3)

    def test_lights_a_degree_apart_are_one_stack(self):
        """One panel's repeats land within a bin, so they stay one stack."""
        frames = [_light("a0"), _light("a1", ts=2)]
        angles = {"/cap/a0.fits": 10.0, "/cap/a1.fits": 10.4}
        b = _bundle(frames, NullMasterLibrary(), angles)
        assert [len(g.lights) for g in b.groups] == [2]

    def test_the_bin_wraps_with_the_turn(self):
        """359.8 and 0.3 are half a degree apart, and are one stack whose
        angle is 0, not 180."""
        frames = [_light("a0"), _light("a1", ts=2)]
        angles = {"/cap/a0.fits": 359.8, "/cap/a1.fits": 0.3}
        b = _bundle(frames, NullMasterLibrary(), angles)
        assert len(b.groups) == 1
        got = b.groups[0].rotator_mech_deg
        assert min(got, 360.0 - got) < 0.1

    def test_groups_of_one_target_at_two_angles_get_two_folders(self):
        """Two stacks must not share a folder: the build script copies a
        group's lights into its ``lights/`` and the stacker opens that."""
        frames, angles = _two_angle_frames()
        b = _bundle(frames, NullMasterLibrary(), angles)
        dirs = [g.dir for g in b.groups]
        assert len(set(dirs)) == 2
        assert dirs == ["M31/Ha/300s_g100_bin1_r5", "M31/Ha/300s_g100_bin1_r50"]

    def test_a_single_angle_leaves_the_folder_as_it_was(self):
        """The suffix exists to tell stacks apart; one stack needs none, so a
        bundle with one angle (or none) is laid out as it always was."""
        frames = [_light("a0"), _light("a1", ts=2)]
        angles = {"/cap/a0.fits": 10.0, "/cap/a1.fits": 10.0}
        for given in (angles, None):
            b = _bundle(frames, NullMasterLibrary(), given)
            assert [g.dir for g in b.groups] == ["M31/Ha/300s_g100_bin1"]

    def test_without_a_reader_nothing_changes(self):
        """No ``light_angle``: the groups, folders and the angle are what they
        were before the angle existed."""
        frames, _angles = _two_angle_frames()
        b = _bundle(frames, NullMasterLibrary())
        assert len(b.groups) == 1
        assert b.groups[0].rotator_mech_deg is None

    def test_a_reader_that_raises_reads_as_unknown(self):
        def boom(_path):
            raise OSError("unreadable")
        rep = SessionReport(id="r1", plan_name="P",
                            frames=[_light("a0"), _light("a1", ts=2)])
        b = build_bundle(rep, NullMasterLibrary(), is_local=lambda p: True,
                         light_angle=boom)
        assert len(b.groups) == 1 and b.groups[0].rotator_mech_deg is None


class TestAnUnknownAngleFailsClosed:
    def test_an_unrecorded_angle_gets_no_flat_from_angle_keyed_flats(self):
        """The library holds flats at two angles; the light's angle is not
        recorded. Any of them could be the wrong one, so none is applied."""
        frames = [_light("a0")]
        lib = CalibrationLibraryAdapter(
            _Lib(_flat("f10", 10.0), _flat("f100", 100.0)))
        b = _bundle(frames, lib, {})
        assert "Flat" in b.groups[0].missing_masters
        assert any("rotator angle" in w for w in b.warnings), b.warnings

    def test_legacy_flats_with_no_angle_still_serve_it(self):
        """A rig with no rotator writes no ROTMECH on lights or flats; that
        library keeps working."""
        lib = CalibrationLibraryAdapter(_Lib(_flat("old", None)))
        b = _bundle([_light("a0")], lib, {})
        assert b.groups[0].master_sources["flat"] == "/lib/old.fits"
        assert not any("rotator angle" in w for w in b.warnings)

    def test_the_refusal_is_the_matchers_and_names_the_cause(self):
        need = LightNeed(300.0, 100, 30, None, 1, "Ha")
        why = unknown_angle_refusal(need, [_flat("f10", 10.0)], TOL)
        assert why and "rotator angle" in why

    def test_a_known_angle_is_never_refused(self):
        need = LightNeed(300.0, 100, 30, None, 1, "Ha", rotator_mech_deg=10.0)
        assert unknown_angle_refusal(need, [_flat("f10", 10.0)], TOL) is None

    def test_no_angle_keyed_flat_means_nothing_to_refuse(self):
        need = LightNeed(300.0, 100, 30, None, 1, "Ha")
        assert unknown_angle_refusal(need, [_flat("old", None)], TOL) is None
        assert unknown_angle_refusal(need, [], TOL) is None

    def test_another_filter_s_angled_flat_does_not_count(self):
        need = LightNeed(300.0, 100, 30, None, 1, "Ha")
        masters = [_flat("oiii", 10.0, filt="OIII")]
        assert unknown_angle_refusal(need, masters, TOL) is None

    def test_best_master_itself_is_unchanged(self):
        """The refusal is the stacking path's, where applying a wrong flat
        corrupts data; the matcher's own ``best_master`` keeps its reading of
        an unknown angle as no constraint, which the health matrix relies on
        while the rotator is disconnected."""
        need = LightNeed(300.0, 100, 30, None, 1, "Ha")
        got = best_master(need, [_flat("f10", 10.0)], TOL, "FLAT")
        assert got is not None and got.id == "f10"


class TestTheAngleSurvivesTheBundleSerialisers:
    def _two(self):
        frames, angles = _two_angle_frames()
        return _bundle(frames, NullMasterLibrary(), angles)

    def test_externalizing_keeps_the_angle(self):
        b = externalize_bundle(self._two(), lambda p: p.lstrip("/"))
        assert [round(g.rotator_mech_deg) for g in b.groups] == [10, 100]

    def test_the_manifest_and_the_summary_state_it(self):
        b = self._two()
        assert [round(g["rotator_mech_deg"])
                for g in manifest_json(b)["groups"]] == [10, 100]
        assert [round(g["rotator_mech_deg"])
                for g in bundle_summary(b)["groups"]] == [10, 100]

    def test_an_unknown_angle_is_null_not_zero(self):
        b = _bundle([_light("a0")], NullMasterLibrary())
        assert manifest_json(b)["groups"][0]["rotator_mech_deg"] is None


# ------------------------------------------------------- reading the angle

def _fits_with(path, **cards) -> str:
    hdu = fits.PrimaryHDU()
    for k, v in cards.items():
        hdu.header[k] = v
    hdu.writeto(path)
    return str(path)


class TestReadingALightsAngle:
    def test_it_reads_rotmech_and_never_rotatang(self, tmp_path):
        """ROTATANG is the SKY angle and moves when the rotator is re-synced;
        reading it as the metal's angle would pair a flat on a number that is
        not the dust's."""
        p = _fits_with(tmp_path / "a.fits", ROTMECH=23.4, ROTATANG=99.0)
        assert app_module.light_rotator_angle(p) == pytest.approx(23.4)

    def test_a_frame_with_only_the_sky_angle_is_unknown(self, tmp_path):
        p = _fits_with(tmp_path / "old.fits", ROTATANG=99.0)
        assert app_module.light_rotator_angle(p) is None

    def test_a_missing_or_unreadable_file_is_unknown(self, tmp_path):
        assert app_module.light_rotator_angle(str(tmp_path / "nope.fits")) is None
        junk = tmp_path / "junk.fits"
        junk.write_bytes(b"not a fits file")
        assert app_module.light_rotator_angle(str(junk)) is None

    def test_a_rewritten_file_is_read_again(self, tmp_path):
        """The read is cached per file and modification time, so a frame
        replaced at the same path is not served stale."""
        path = tmp_path / "a.fits"
        _fits_with(path, ROTMECH=10.0)
        assert app_module.light_rotator_angle(str(path)) == pytest.approx(10.0)
        path.unlink()
        _fits_with(path, ROTMECH=200.0)
        import os
        os.utime(path, ns=(2_000_000_000_000_000_000,) * 2)
        assert app_module.light_rotator_angle(str(path)) == pytest.approx(200.0)

    def test_the_circular_mean_wraps(self):
        assert circular_mean_angle([359.6, 0.4]) == pytest.approx(0.0, abs=1e-3)
        assert circular_mean_angle([10.0, 12.0]) == pytest.approx(11.0)
        assert circular_mean_angle([]) is None

    def test_the_group_width_is_the_librarys_default(self):
        assert ROTATOR_BIN_DEG == 2.0


# ------------------------------------------------------------ the bundle route

@pytest.fixture
def client(tmp_path, monkeypatch):
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(app_module, "flow_store", FlowStore(tmp_path / "flows"))
    monkeypatch.delitem(app_module.hub.devices, "rotator", raising=False)
    with TestClient(app_module.create_app()) as c:
        yield c


class TestTheBundleRoutesReadTheFrames:
    def _report(self, tmp_path) -> SessionReport:
        a = _fits_with(tmp_path / "a.fits", ROTMECH=10.0)
        b = _fits_with(tmp_path / "b.fits", ROTMECH=100.0)
        frames = [FrameRecord(ts=1.0, target="M31", filter="Ha",
                              frame_type="Light", exposure_s=300.0, gain=100,
                              offset=30, binning=1, hfr=2.0, saved_path=a),
                  FrameRecord(ts=2.0, target="M31", filter="Ha",
                              frame_type="Light", exposure_s=300.0, gain=100,
                              offset=30, binning=1, hfr=2.0, saved_path=b)]
        return SessionReport(id="r1", plan_name="P", frames=frames)

    def test_the_preview_pairs_each_angle_with_its_flat(self, client, tmp_path,
                                                         monkeypatch):
        report = self._report(tmp_path)
        monkeypatch.setattr(app_module.SessionReporter, "load",
                            staticmethod(lambda rid: report))
        monkeypatch.setattr(app_module.hub, "_is_local_save", lambda p: True)
        monkeypatch.setattr(app_module, "_get_master_library",
                            lambda: CalibrationLibraryAdapter(_Lib(
                                _flat("near10", 10.0, count=20),
                                _flat("deep100", 100.0, count=60))))
        out = client.get("/api/reports/r1/bundle")
        assert out.status_code == 200, out.text
        groups = out.json()["groups"]
        assert [round(g["rotator_mech_deg"]) for g in groups] == [10, 100]
        assert all(g["masters"]["flat"] for g in groups)

    def test_the_zip_names_the_angle_in_its_manifest(self, client, tmp_path,
                                                      monkeypatch):
        import io
        import json
        import zipfile
        report = self._report(tmp_path)
        monkeypatch.setattr(app_module.SessionReporter, "load",
                            staticmethod(lambda rid: report))
        monkeypatch.setattr(app_module.hub, "_is_local_save", lambda p: True)
        monkeypatch.setattr(app_module, "_get_master_library",
                            lambda: NullMasterLibrary())
        out = client.get("/api/reports/r1/bundle.zip")
        assert out.status_code == 200, out.text
        with zipfile.ZipFile(io.BytesIO(out.content)) as z:
            manifest = json.loads(z.read("manifest.json"))
        assert [round(g["rotator_mech_deg"]) for g in manifest["groups"]] == [
            10, 100]


# ---------------------------------------------------------------- the copy

def _need(rot: float) -> CalNeed:
    return CalNeed(LightNeed(300.0, 100, 30, None, 1, "Ha"), rotation_deg=rot)


def _flat_frames(angle: float, n: int = 20):
    from astrodeck.calibration.keys import CalKey
    from astrodeck.flows.calibration_health import CalFrame
    key = CalKey(frame_type="FLAT", exposure_s=0.0, gain=100, offset=30,
                 binning=1, filter="Ha", temp_c=None, rotator_mech_deg=angle)
    return [CalFrame(key=key, ts=1.0, rotation_deg=angle) for _ in range(n)]


class TestTheMatrixSaysRotatorAngle:
    def test_the_row_summary_names_the_rotator_angle(self):
        rows = health_matrix([_need(23.4)], _flat_frames(23.4), kinds=["FLAT"],
                             now=10.0)
        summary = next(r.summary for r in rows if r.kind == "FLAT")
        assert "rotator 23.4" in summary and "PA" not in summary

    def test_the_drift_reason_names_both_angles(self):
        rows = health_matrix([_need(23.4)], _flat_frames(90.0), kinds=["FLAT"],
                             now=10.0)
        row = next(r for r in rows if r.kind == "FLAT")
        text = next(r.text for r in row.reasons if r.code == "drift")
        assert "rotator angle 90" in text and "rotator angle 23.4" in text
        assert "PA" not in text


# ----------------------------------------------------------- the demand cache

class TestTheDemandCache:
    def test_one_computation_serves_two_reads(self):
        cache = DemandCache()
        calls = []

        def compute():
            calls.append(1)
            return "demand"
        assert cache.get(("f", 1.0), compute) == "demand"
        assert cache.get(("f", 1.0), compute) == "demand"
        assert len(calls) == 1

    def test_a_new_revision_is_computed_again(self):
        cache = DemandCache()
        calls = []

        def compute():
            calls.append(1)
            return len(calls)
        assert cache.get(("f", 1.0), compute) == 1
        assert cache.get(("f", 2.0), compute) == 2
        assert len(calls) == 2

    def test_it_holds_a_bounded_number_of_flows(self):
        cache = DemandCache(capacity=2)
        calls = []

        def compute():
            calls.append(1)
            return 0
        for key in ("a", "b", "c", "a"):
            cache.get(key, compute)
        assert len(calls) == 4, "a was evicted by c, so its fourth read recomputes"

    def test_a_computation_that_raises_is_not_cached(self):
        cache = DemandCache()

        def boom():
            raise RuntimeError("compile failed")
        with pytest.raises(RuntimeError):
            cache.get("k", boom)
        assert cache.get("k", lambda: "ok") == "ok"

    def test_concurrent_reads_compute_once(self):
        cache = DemandCache()
        calls = []
        gate = threading.Event()

        def compute():
            calls.append(1)
            gate.wait(0.2)
            return "demand"
        out = []
        threads = [threading.Thread(target=lambda: out.append(
            cache.get("k", compute))) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert out == ["demand"] * 4 and len(calls) == 1


def _graph() -> dict:
    return {"nodes": [
        {"id": "d", "type": "dusk", "x": 30, "y": 60,
         "params": {"offset": -30, "stop": "Dawn", "minAlt": 30}},
        {"id": "t", "type": "target", "x": 260, "y": 60,
         "params": {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09",
                    "rotation": 30}},
        {"id": "c", "type": "capture", "x": 490, "y": 60,
         "params": {"filter": "L", "exposure": 120, "gain": 100, "bin": "1",
                    "count": 12, "goal": 0}}],
        "edges": [{"from": "d", "fromPort": "window", "to": "t",
                   "toPort": "arm"},
                  {"from": "t", "fromPort": "target", "to": "c",
                   "toPort": "run"}]}


class TestTheHealthRouteComputesOncePerRevision:
    def _spy(self, monkeypatch):
        real = app_module.to_sequence_plan
        calls = []

        def spy(*a, **kw):
            calls.append(1)
            return real(*a, **kw)
        monkeypatch.setattr(app_module, "to_sequence_plan", spy)
        return calls

    def _save(self, client) -> str:
        body = {"flow": {"name": "One", "folder": "My flows",
                         "graph": _graph()}}
        saved = client.post("/api/flows", json=body)
        assert saved.status_code == 200, saved.text
        return saved.json()["id"]

    def test_two_reads_of_an_unchanged_flow_compile_the_plan_once(
            self, client, monkeypatch):
        """RED when the route compiles per request (the cache bypassed):
        ``assert 2 == 1``."""
        fid = self._save(client)
        calls = self._spy(monkeypatch)
        first = client.get(f"/api/calibration/health?flow_id={fid}")
        second = client.get(f"/api/calibration/health?flow_id={fid}")
        assert first.status_code == second.status_code == 200
        assert first.json()["planned"] is True
        assert first.json() == second.json()
        assert len(calls) == 1

    def test_an_edit_makes_a_new_revision(self, client, monkeypatch):
        fid = self._save(client)
        calls = self._spy(monkeypatch)
        client.get(f"/api/calibration/health?flow_id={fid}")
        body = {"flow": {"name": "One", "folder": "My flows",
                         "graph": _graph()}}
        edited = client.put(f"/api/flows/{fid}", json=body)
        assert edited.status_code == 200, edited.text
        client.get(f"/api/calibration/health?flow_id={fid}")
        assert len(calls) == 2

    def test_the_compile_runs_off_the_event_loop(self, client, monkeypatch):
        """The plan's catalogue searches cost 10 to 35 ms and ran on the loop
        for every request."""
        fid = self._save(client)
        real = app_module.to_sequence_plan
        threads = []

        def spy(*a, **kw):
            threads.append(threading.current_thread())
            return real(*a, **kw)
        monkeypatch.setattr(app_module, "to_sequence_plan", spy)
        client.get(f"/api/calibration/health?flow_id={fid}")
        assert threads and all(t is not threading.main_thread() for t in threads)

    def test_the_angle_is_still_read_live_from_a_cached_plan(
            self, client, monkeypatch):
        """The cache holds the plan's SKY angles, which are the flow's. The
        mechanical angle depends on the rotator's calibration, which changes
        between two reads of one flow revision."""
        from astrodeck.rotation import sky_to_mechanical
        fid = self._save(client)
        hub = app_module.hub

        def calibrate(offset: float) -> None:
            rot = SimpleNamespace(connected=True, synced=True,
                                  sync_offset_deg=offset)
            monkeypatch.setitem(hub.devices, "rotator", rot)
            monkeypatch.setattr(hub, "last_sky_angle", {
                "pa_deg": 90.0, "exposed_at": 1.0, "solved_at": 1.0,
                "source": "plate solve + sync", "pier_side": None,
                "camera": "Sim Camera", "calibrated": True, "reason": None,
                "mechanical_deg": 100.0, "rotator_before_deg": None,
                "offset_deg": offset}, raising=False)
            monkeypatch.setattr(hub, "_rotator_sky_sign", 1, raising=False)

        def flat_angle() -> float:
            rows = client.get(f"/api/calibration/health?flow_id={fid}").json()
            return next(r["rotation_deg"] for r in rows["rows"]
                        if r["kind"] == "FLAT")
        calibrate(10.0)
        first = flat_angle()
        assert first == pytest.approx(sky_to_mechanical(30.0, 100.0, 10.0, 1))
        calibrate(25.0)
        second = flat_angle()
        assert second == pytest.approx(sky_to_mechanical(30.0, 100.0, 25.0, 1))
        assert not math.isclose(first, second)
