# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Stamping defaults ON whenever a plate solver is configured (#826, owner
ruling D-I, backlog consolidation WP 3.9).

``solve_saved_lights`` defaulted off, so every mosaic compile carried one
``note`` row saying coverage cannot be checked, on a rig that has ASTAP and
would have stamped every frame had anyone turned the option on. The ruling:
the option follows the solver until the operator chooses, so the note shows
only with no solver or with the option turned off by hand.

THE FILE CANNOT TELL A DEFAULT FROM A CHOICE (``_save`` dumps every field), so
a config written before ``solve_saved_lights_by_hand`` is read by its stored
value: True was a choice, False was the default written out. These tests write
real files and load them through ``ConfigStore``, the path a rig takes.

NAMED MUTANT "follow ignores the choice" (``_follow_the_solver`` made to set
the option whatever ``solve_saved_lights_by_hand`` says): the test that writes
a by-hand False beside a configured solver is RED, observed:

    assert cfg.solve_saved_lights is False
    E   assert True is False
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.config import AppConfig, ConfigStore
from astrodeck.flows.store import FlowStore
from astrodeck.sequence.coverage import STAMPING_OFF_NOTE


def _solver(monkeypatch, present: bool) -> None:
    monkeypatch.setattr(config_mod, "_solver_configured", lambda: present)


def _write(path, body: dict) -> None:
    path.write_text(json.dumps(body), encoding="utf-8")


def _read(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestANewConfig:
    def test_with_a_solver_stamping_is_on_and_not_chosen(self, tmp_path, monkeypatch):
        _solver(monkeypatch, True)
        cfg = ConfigStore(path=tmp_path / "astrodeck.json").cfg()
        assert cfg.solve_saved_lights is True
        assert cfg.solve_saved_lights_by_hand is False

    def test_without_a_solver_stamping_is_off(self, tmp_path, monkeypatch):
        _solver(monkeypatch, False)
        cfg = ConfigStore(path=tmp_path / "astrodeck.json").cfg()
        assert cfg.solve_saved_lights is False
        assert cfg.solve_saved_lights_by_hand is False

    def test_the_bare_model_does_not_look_at_the_machine(self, monkeypatch):
        """CONTROL: ``AppConfig()`` stays the same on every host (a test or a
        client building one must not depend on where ASTAP is installed)."""
        _solver(monkeypatch, True)
        assert AppConfig().solve_saved_lights is False


class TestAnOldConfig:
    def test_a_stored_false_is_the_old_default_and_follows_the_solver(
            self, tmp_path, monkeypatch):
        """The file every existing rig has: ``solve_saved_lights: false``
        written out beside a rig that has ASTAP."""
        _solver(monkeypatch, True)
        path = tmp_path / "astrodeck.json"
        _write(path, {"version": 7, "solve_saved_lights": False})
        cfg = ConfigStore(path=path).cfg()
        assert cfg.solve_saved_lights is True
        assert cfg.solve_saved_lights_by_hand is False

    def test_a_stored_true_is_a_choice_and_stays_without_a_solver(
            self, tmp_path, monkeypatch):
        _solver(monkeypatch, False)
        path = tmp_path / "astrodeck.json"
        _write(path, {"version": 7, "solve_saved_lights": True})
        cfg = ConfigStore(path=path).cfg()
        assert cfg.solve_saved_lights is True
        assert cfg.solve_saved_lights_by_hand is True

    def test_a_by_hand_false_beside_a_solver_stays_off(self, tmp_path, monkeypatch):
        _solver(monkeypatch, True)
        path = tmp_path / "astrodeck.json"
        _write(path, {"version": 7, "solve_saved_lights": False,
                      "solve_saved_lights_by_hand": True})
        store = ConfigStore(path=path)
        cfg = store.cfg()
        assert cfg.solve_saved_lights is False
        assert store.reload().solve_saved_lights is False

    def test_the_backup_path_follows_the_solver_too(self, tmp_path, monkeypatch):
        """A config rebuilt from its ``.bak`` is a load like any other."""
        _solver(monkeypatch, True)
        path = tmp_path / "astrodeck.json"
        path.write_text("{ not json", encoding="utf-8")
        _write(tmp_path / "astrodeck.json.bak",
               {"version": 7, "solve_saved_lights": False})
        cfg = ConfigStore(path=path).cfg()
        assert cfg.solve_saved_lights is True


class TestTheOperatorsChoice:
    def test_turning_it_off_by_hand_survives_a_restart_with_a_solver(
            self, tmp_path, monkeypatch):
        _solver(monkeypatch, True)
        path = tmp_path / "astrodeck.json"
        store = ConfigStore(path=path)
        assert store.cfg().solve_saved_lights is True
        store.set_wcs_stamp(False, store.cfg().wcs_stamp)
        assert _read(path)["solve_saved_lights_by_hand"] is True
        assert ConfigStore(path=path).cfg().solve_saved_lights is False

    def test_saving_another_knob_with_the_option_as_shown_is_not_a_choice(
            self, tmp_path, monkeypatch):
        """The panel posts the option with every knob it saves. Saving the
        downsample while the box reads on must leave the option following the
        solver, not freeze it."""
        _solver(monkeypatch, True)
        path = tmp_path / "astrodeck.json"
        store = ConfigStore(path=path)
        knobs = store.cfg().wcs_stamp.model_copy(update={"downsample": 2})
        store.set_wcs_stamp(True, knobs)
        assert store.cfg().wcs_stamp.downsample == 2
        assert store.cfg().solve_saved_lights_by_hand is False
        _solver(monkeypatch, False)
        assert ConfigStore(path=path).cfg().solve_saved_lights is False

    def test_turning_it_on_by_hand_without_a_solver_stays_on(
            self, tmp_path, monkeypatch):
        _solver(monkeypatch, False)
        path = tmp_path / "astrodeck.json"
        store = ConfigStore(path=path)
        store.set_wcs_stamp(True, store.cfg().wcs_stamp)
        assert store.cfg().solve_saved_lights_by_hand is True
        assert ConfigStore(path=path).cfg().solve_saved_lights is True


class TestGoingBackToFollowing:
    def test_follow_solver_clears_the_choice_and_sets_the_option_as_a_load_would(
            self, tmp_path, monkeypatch):
        _solver(monkeypatch, True)
        path = tmp_path / "astrodeck.json"
        store = ConfigStore(path=path)
        store.set_wcs_stamp(False, store.cfg().wcs_stamp)
        assert store.cfg().solve_saved_lights_by_hand is True
        assert store.cfg().solve_saved_lights is False
        # The posted bool is not read: following puts it where the solver says.
        store.set_wcs_stamp(False, store.cfg().wcs_stamp, follow_solver=True)
        assert store.cfg().solve_saved_lights_by_hand is False
        assert store.cfg().solve_saved_lights is True
        on_disk = _read(path)
        assert on_disk["solve_saved_lights_by_hand"] is False
        assert on_disk["solve_saved_lights"] is True

    def test_following_with_no_solver_turns_it_off_and_it_stays_following(
            self, tmp_path, monkeypatch):
        _solver(monkeypatch, False)
        path = tmp_path / "astrodeck.json"
        store = ConfigStore(path=path)
        store.set_wcs_stamp(True, store.cfg().wcs_stamp)
        assert store.cfg().solve_saved_lights is True
        store.set_wcs_stamp(True, store.cfg().wcs_stamp, follow_solver=True)
        assert store.cfg().solve_saved_lights is False
        _solver(monkeypatch, True)
        assert ConfigStore(path=path).cfg().solve_saved_lights is True


def _graph() -> dict:
    """One TARGET block, four panels wide, one capture stage."""
    return {
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


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """The app over a real ``ConfigStore`` in ``tmp_path``, loaded under the
    test's say-so about whether a solver is configured."""
    import astrodeck.hub as hub_mod

    def _build(solver_present: bool) -> TestClient:
        # The store loads inside the patch and the patch ends there: the
        # process-wide ``config_store`` may load cold while this test runs, and
        # it must load as it would anywhere else.
        with monkeypatch.context() as scoped:
            scoped.setattr(config_mod, "_solver_configured",
                           lambda: solver_present)
            store = ConfigStore(path=tmp_path / "astrodeck.json")
            store.cfg()
        monkeypatch.setattr(config_mod, "config_store", store)
        monkeypatch.setattr(hub_mod, "config_store", store)
        monkeypatch.setattr(app_module, "config_store", store)
        monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path)
        monkeypatch.setattr(app_module, "flow_store",
                            FlowStore(tmp_path / "flows"))
        client = TestClient(app_module.create_app())
        client.store = store
        return client
    return _build


def _stamping_rows(client: TestClient) -> list[dict]:
    out = client.post("/api/flows/compile", json={"graph": _graph(), "name": "x"})
    assert out.status_code == 200, out.text
    return [i for i in out.json()["issues"] if STAMPING_OFF_NOTE in i["text"]]


class TestTheStampingNoteOnACompile:
    """The compile route feeds ``stamping_note`` the config's own value."""

    def test_a_mosaic_with_a_solver_and_the_option_untouched_has_no_note(
            self, wired):
        with wired(True) as client:
            assert _stamping_rows(client) == []

    def test_a_mosaic_with_no_solver_still_has_the_note(self, wired):
        with wired(False) as client:
            assert len(_stamping_rows(client)) == 1

    def test_a_mosaic_with_a_solver_and_the_option_turned_off_by_hand_has_it(
            self, wired):
        with wired(True) as client:
            client.store.set_wcs_stamp(False, client.store.cfg().wcs_stamp)
            assert len(_stamping_rows(client)) == 1


class _Fake:
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture
def _provider_reset():
    yield
    reset_active_provider()


def _post_wcs(client: TestClient, **body):
    stamp = client.store.cfg().wcs_stamp.model_dump()
    return client.post("/api/config/wcs", json={"wcs_stamp": stamp, **body})


class TestTheRouteHandsTheOptionBack:
    """``POST /api/config/wcs`` carries ``follow_solver`` to ``set_wcs_stamp``.

    NAMED MUTANT "the route drops the field" (``set_wcs_stamp_config`` made to
    call ``set_wcs_stamp(body.solve_saved_lights, body.wcs_stamp)``): the first
    test is RED, observed:

        assert client.store.cfg().solve_saved_lights_by_hand is False
        E   assert True is False
    """

    def test_follow_solver_after_a_by_hand_change_clears_the_choice(
            self, wired, monkeypatch):
        with wired(True) as client:
            assert _post_wcs(client, solve_saved_lights=False).status_code == 200
            assert client.store.cfg().solve_saved_lights_by_hand is True
            assert client.store.cfg().solve_saved_lights is False
            _solver(monkeypatch, True)
            r = _post_wcs(client, solve_saved_lights=False, follow_solver=True)
            assert r.status_code == 200, r.text
            assert client.store.cfg().solve_saved_lights_by_hand is False
            # What a load would set: the solver is configured, so it is on,
            # though the body said False.
            assert client.store.cfg().solve_saved_lights is True
            assert r.json()["solve_saved_lights_by_hand"] is False
            assert r.json()["solve_saved_lights"] is True

    def test_without_follow_solver_a_change_is_still_a_choice(self, wired):
        with wired(True) as client:
            r = _post_wcs(client, solve_saved_lights=False)
            assert r.status_code == 200, r.text
            assert client.store.cfg().solve_saved_lights_by_hand is True

    def test_a_viewer_is_refused_as_today_and_the_choice_stays(
            self, wired, _provider_reset):
        with wired(True) as client:
            assert _post_wcs(client, solve_saved_lights=False).status_code == 200
            set_active_provider(_Fake(principal_for_role("viewer")))
            r = _post_wcs(client, solve_saved_lights=False, follow_solver=True)
            assert r.status_code == 403, r.text
            assert client.store.cfg().solve_saved_lights_by_hand is True
