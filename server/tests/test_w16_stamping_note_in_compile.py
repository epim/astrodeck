# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A mosaic that runs with plate solving off says so in the compile's issues
(#177, backlog WP-123; wave 16 integration).

THE GAP. WP-123 built ``sequence.coverage.stamping_note`` and made both coverage
routes answer it as ``note``, but the check that reads a WCS out of each saved
light has nothing to read when ``solve_saved_lights`` is off (the default), and
nobody planning a mosaic would find that out from the coverage route: they
never call it. An empty report reads as a covered mosaic. The compile is where
the operator reads what the night will and will not do, so its ``issues`` carry
one ``note`` row, worded by ``stamping_note`` itself (one sentence, one rule).

Not turned on for them: stamping costs an ASTAP run per frame and on the Pi
that is the operator's call. A note, not a warning: nothing is wrong with the
night.

NAMED MUTANTS. Each was run from a byte backup of ``api/app.py`` inside the
integration worktree, restored byte-identically (sha256 compared) and the
mutant text grepped out, under one worker. The assertion each broke is recorded
verbatim on the test that caught it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore
from astrodeck.flows.store import FlowStore
from astrodeck.sequence.coverage import STAMPING_OFF_NOTE


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(app_module, "flow_store", FlowStore(tmp_path / "flows"))
    with TestClient(app_module.create_app()) as c:
        c.store = store
        yield c


def _graph(*, cols: int) -> dict:
    """One TARGET block, ``cols`` panels wide, one capture stage."""
    return {
        "nodes": [
            {"id": "n2", "type": "target", "x": 30, "y": 60,
             "params": {"name": "NGC 7000", "ra": "20h 59m 17s",
                        "dec": "+44 31 44", "rows": 1, "cols": cols,
                        "overlap": 10, "fovX": 3.0, "fovY": 2.0,
                        "angle": "Rotate to PA", "rotation": 20}},
            {"id": "n7", "type": "capture", "x": 270, "y": 60,
             "params": {"filter": "L", "exposure": 60, "gain": 100,
                        "bin": "1", "count": 4, "goal": 0}}],
        "edges": [{"id": "e1", "from": "n2", "fromPort": "target",
                   "to": "n7", "toPort": "run"}],
        "settings": {}}


def _stamping_rows(client, graph: dict) -> list[dict]:
    out = client.post("/api/flows/compile", json={"graph": graph, "name": "x"})
    assert out.status_code == 200, out.text
    return [i for i in out.json()["issues"] if STAMPING_OFF_NOTE in i["text"]]


def _turn_stamping(client, on: bool) -> None:
    cfg = client.store.cfg()
    client.store.set_wcs_stamp(on, cfg.wcs_stamp)
    assert client.store.cfg().solve_saved_lights is on, "premise: the switch moved"


class TestTheCompileSaysCoverageCannotBeChecked:
    def test_a_mosaic_with_solving_off_gets_one_note_row(self, client):
        """The row carries the sentence ``stamping_note`` words, as a ``note``.

        RED under mutant "no stamping row" (the ``if stamping_note:`` append in
        ``_compile_payload`` made ``if False:``), observed:

            E   AssertionError: []
            E   assert 0 == 1
            E    +  where 0 = len([])
        """
        _turn_stamping(client, False)
        rows = _stamping_rows(client, _graph(cols=4))
        assert len(rows) == 1, rows
        assert rows[0]["level"] == "note", rows[0]
        assert rows[0]["text"].startswith("▸ MOSAIC - "), rows[0]

    def test_with_solving_on_there_is_no_row(self, client):
        """CONTROL: stamping on, the coverage check has lights to read.

        RED under mutant "solving always read as off" (``bool(config_store.
        cfg().solve_saved_lights)`` in that call made ``False``), observed:

            E   assert [{'level': 'n... the night.'}] == []
            E     Left contains one more item: {'level': 'note', 'text': '> MOSAIC - coverage cannot be checked: ...
        """
        _turn_stamping(client, True)
        assert _stamping_rows(client, _graph(cols=4)) == []

    def test_a_flow_with_no_mosaic_has_nothing_to_check_and_no_row(self, client):
        """CONTROL: one panel is no mosaic (``stamping_note`` answers None), so
        a single-target flow's issues are the list they always were.

        RED under mutant "the row for every flow" (``stamping_note(...)``
        replaced by the constant sentence), observed here and on the
        solving-on control above:

            E   assert [{'level': 'n... the night.'}] == []
        """
        _turn_stamping(client, False)
        assert _stamping_rows(client, _graph(cols=1)) == []
