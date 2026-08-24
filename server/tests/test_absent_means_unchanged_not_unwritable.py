"""POST /api/config must be able to write back what GET /api/config hands out.

THE DEFECT CLASS, found by grepping for the shape that cost the cooling
setpoint. ``cooling.setpoint_c`` was unwritable through this route because
``set_cooling`` carried the stored value over UNCONDITIONALLY: the route
answered 200 and discarded the number, and no caller could tell "saved" from
"thrown away". That cost 19 light frames at +23 C against a -10 C dark library
on 2026-08-22.

The guard was there for a real reason -- the settings panel POSTs a whole block
whose client type omits the field, so a plain replace would erase the value by
omission. Both readings are failures, and the difference between them is
whether the caller actually SENT the field. ``model_fields_set`` is the only
thing that knows.

Three more fields had the identical shape, all answering 200, all measured:

  * ``site.horizon_min_deg`` -- carried over unconditionally, so unwritable.
    Worse than the cooling case: ``_require_config_field_caps`` 403s a caller
    who lacks ``config.safety`` for sending that very field, so the route
    gated a write it then dropped, and the dedicated PUT /api/site honoured it.
    Two routes, silently disagreeing about a safety floor.

  * ``AlertSink.chat_id`` -- redacted outbound and NOT restored inbound, so a
    plain GET -> POST echo of the alerts array (what a settings panel does when
    you edit any unrelated field) erased it AND tripped the identity check that
    resets ``verified``. The sink stayed in the config, could no longer deliver,
    and nothing said so. ``token`` was restored; ``chat_id`` was added to the
    redaction later and never added to the restore.

  * ``deadman_url`` -- empty means unchanged, with no way to clear it. The
    comment promised "to truly clear it the UI POSTs a dedicated clear (handled
    at the /api/config layer if needed)" and it never was, so the documented
    escape hatch did not exist.

The pattern done RIGHT, and the one the deadman fix copies, is one route over:
POST /api/config/weather pairs empty-means-unchanged with an explicit
``clear_astrospheric_key`` flag.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.hub as hub_module
from astrodeck.config import REDACTED_SINK_FIELDS, ConfigStore, redacted
from astrodeck.flows.store import FlowStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(app_module, "flow_store", FlowStore(tmp_path / "flows"))
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    with TestClient(app_module.create_app()) as c:
        c.config_store = store
        yield c


def _site(client) -> dict:
    return client.get("/api/config").json()["site"]


# ------------------------------------------------------- site.horizon_min_deg

class TestTheHorizonFloorIsWritableAndNotErasable:
    """Both halves, because fixing either one alone reopens the other."""

    def test_an_explicitly_sent_horizon_is_SAVED(self, client):
        site = _site(client)
        site.update({"name": "Probe Observatory", "horizon_min_deg": 25.0})
        r = client.post("/api/config", json={"site": site})
        assert r.status_code == 200, r.text
        got = _site(client)
        assert got["horizon_min_deg"] == 25.0, (
            "the route answered 200 and threw the horizon away")
        assert got["name"] == "Probe Observatory", "the sibling field saved"

    def test_an_OMITTED_horizon_is_left_alone(self, client):
        """The reason the carry-over existed. The TS Site type has no horizon
        field, so an ordinary site save must not reset a safety floor."""
        site = _site(client)
        site["horizon_min_deg"] = 25.0
        client.post("/api/config", json={"site": site})
        later = {k: v for k, v in site.items() if k != "horizon_min_deg"}
        later["name"] = "Later Edit"
        r = client.post("/api/config", json={"site": later})
        assert r.status_code == 200, r.text
        got = _site(client)
        assert got["horizon_min_deg"] == 25.0, (
            "a site save with no horizon field erased the safety floor")
        assert got["name"] == "Later Edit"

    def test_the_two_routes_now_AGREE(self, client):
        """PUT /api/site always honoured the horizon; POST /api/config never
        did. A field whose value depends on which route you used is a field
        nobody can reason about.

        Note the two shapes: PUT /api/site carries the horizon as a SIBLING of
        the site block (SiteSaveBody), POST /api/config carries it inside the
        Site model. Different spellings are fine; different outcomes were not.
        """
        site = _site(client)
        r = client.put("/api/site", json={"site": site, "horizon_min_deg": 22.0})
        assert r.status_code == 200, r.text
        assert _site(client)["horizon_min_deg"] == 22.0, "premise: PUT honours it"
        site = _site(client)
        site["horizon_min_deg"] = 31.0
        assert client.post("/api/config", json={"site": site}).status_code == 200
        assert _site(client)["horizon_min_deg"] == 31.0


# ---------------------------------------------------------- alert sink secrets

class TestARoundTripDoesNotDestroyAlertSinks:

    SINK = {"id": "s1", "kind": "telegram", "token": "SECRET-123",
            "chat_id": "-100123", "verified": True, "enabled": True}

    def _store(self, client):
        r = client.post("/api/config", json={"alerts": [dict(self.SINK)]})
        assert r.status_code == 200, r.text
        return client.config_store.cfg().alerts[0]

    def test_the_echo_preserves_every_redacted_field(self, client):
        """The measured failure: POST the array GET just handed back, and the
        chat_id is gone and the sink is un-verified."""
        assert self._store(client).chat_id == "-100123", "premise"
        echoed = client.get("/api/config").json()["alerts"]
        assert echoed[0]["chat_id"] == "" and echoed[0]["token"] == "", (
            "premise: both fields are redacted outbound")
        r = client.post("/api/config", json={"alerts": echoed})
        assert r.status_code == 200, r.text
        after = client.config_store.cfg().alerts[0]
        assert after.chat_id == "-100123", (
            "a plain read-modify-write erased the telegram chat_id")
        assert after.token == "SECRET-123"
        assert after.verified is True, (
            "the echoed blank tripped the identity check and un-verified a "
            "sink that had not changed")

    def test_a_REAL_change_still_lands_and_still_un_verifies(self, client):
        """The guard must not become the new eraser's opposite. Pointing a sink
        at a different chat is a real edit and has to invalidate the
        verification, or a sink reads "verified" for a destination nobody ever
        proved."""
        self._store(client)
        changed = dict(self.SINK, chat_id="-999")
        assert client.post("/api/config",
                           json={"alerts": [changed]}).status_code == 200
        after = client.config_store.cfg().alerts[0]
        assert after.chat_id == "-999", "a genuine chat_id change was swallowed"
        assert after.verified is False

    def test_the_restore_list_is_DERIVED_from_the_redaction(self, client):
        """The structural half, and the reason this bug happened at all.

        chat_id was added to redacted() and never added to the restore. As long
        as the two lists are written out separately, the next redaction can do
        the same thing again. This asserts they are one list: every field
        redacted() blanks survives an echo.
        """
        self._store(client)
        blanked = [f for f in REDACTED_SINK_FIELDS
                   if redacted(client.config_store.cfg())["alerts"][0][f] == ""]
        assert set(blanked) == set(REDACTED_SINK_FIELDS), (
            f"REDACTED_SINK_FIELDS claims fields redacted() does not blank: "
            f"{set(REDACTED_SINK_FIELDS) - set(blanked)}")
        echoed = client.get("/api/config").json()["alerts"]
        client.post("/api/config", json={"alerts": echoed})
        after = client.config_store.cfg().alerts[0]
        for field in REDACTED_SINK_FIELDS:
            assert getattr(after, field) == self.SINK[field], (
                f"{field} is blanked outbound but not restored inbound, so an "
                f"echo erases it -- the chat_id bug in a new field")


# ------------------------------------------------------------- deadman_url

class TestTheDeadmanCanBeClearedOnPurpose:

    URL = "https://d.example/ping/s3cr3t"

    def test_a_blank_echo_still_means_unchanged(self, client):
        """Unchanged, because the url is redacted outbound and the UI can only
        ever send back the blank it was given."""
        client.post("/api/config", json={"deadman_url": self.URL})
        r = client.post("/api/config", json={"deadman_url": ""})
        assert r.status_code == 200, r.text
        assert client.config_store.cfg().deadman_url == self.URL, (
            "a redacted round-trip wiped the deadman url")

    def test_the_explicit_clear_WORKS(self, client):
        """The escape hatch the comment promised and never built."""
        client.post("/api/config", json={"deadman_url": self.URL})
        r = client.post("/api/config", json={"clear_deadman_url": True})
        assert r.status_code == 200, r.text
        assert client.config_store.cfg().deadman_url == "", (
            "clear_deadman_url answered 200 and cleared nothing")

    def test_the_clear_REACHES_DISK(self, client):
        client.post("/api/config", json={"deadman_url": self.URL})
        client.post("/api/config", json={"clear_deadman_url": True})
        assert client.config_store.reload().deadman_url == ""

    def test_the_clear_WINS_over_a_url_in_the_same_body(self, client):
        """Asking to clear it and supplying one is a contradiction; the
        destructive reading is the one the operator typed on purpose, and
        picking a winner is better than depending on dict ordering."""
        client.post("/api/config", json={"deadman_url": self.URL})
        r = client.post("/api/config", json={"clear_deadman_url": True,
                                             "deadman_url": "https://x.test/y"})
        assert r.status_code == 200, r.text
        assert client.config_store.cfg().deadman_url == ""

    def test_the_clear_is_RBAC_GATED_like_the_field_it_clears(self, client):
        """The field-cap map is fail-closed, so an unmapped field 403s -- which
        would have made the clear unreachable for everyone rather than gated
        for the right people. It has to be mapped, and mapped to the same cap
        as deadman_url itself."""
        from astrodeck.api.app import CAP_CONFIG_ALERTS
        import astrodeck.api.app as m
        import inspect
        src = inspect.getsource(m.create_app)
        assert '"clear_deadman_url": CAP_CONFIG_ALERTS' in src, (
            "clear_deadman_url is not in the field-cap map; it would 403 as an "
            "unknown block for every caller")
        assert CAP_CONFIG_ALERTS  # the cap exists under that name
