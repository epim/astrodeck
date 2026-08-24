"""The GOES cloud model had no writer, so it could never be switched on.

Stage 6a shipped 4,357 lines across six modules, seven test files, a poller and
three read routes -- and CloudmapConfig.enabled defaults to False on the
deliberate reasoning that a feature pulling 26 MB an hour should be opt-in.
Nothing anywhere could set it. ConfigPatchBody is ``extra="forbid"`` and had no
cloudmap field, so POST /api/config {"cloudmap": {...}} 422'd at binding, and
there is no /api/config/cloudmap route. Confirmed against the rig on
2026-08-24: /api/cloudmap answered ``enabled: false`` on 0.3.9 and the model had
never fetched a granule in its life.

Same defect class as cooling.setpoint_c, one step earlier: there the route
answered 200 and discarded the value, here there was no route to discard it.
Both are "a capability the product does not actually expose".

These tests are deliberately about REACHABILITY, not about the model. The model
has its own six test files; what none of them could tell you is that no
operator could ever run it.
"""
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import CloudmapConfig, config_store


def test_the_config_body_carries_a_cloudmap_block():
    """Fail-closed binding means the field's ABSENCE was the bug."""
    assert "cloudmap" in app_module.ConfigPatchBody.model_fields, (
        "without this field POST /api/config 422s on a cloudmap block and the "
        "feature is unreachable")


def test_the_store_can_persist_it():
    before = config_store.cfg().cloudmap
    try:
        cfg = config_store.set_cloudmap(
            CloudmapConfig(enabled=True, platform="G19", poll_minutes=15))
        assert cfg.cloudmap.enabled is True
        assert cfg.cloudmap.platform == "G19"
        assert cfg.cloudmap.poll_minutes == 15
        assert config_store.cfg().cloudmap.enabled is True, "not persisted"
    finally:
        config_store.set_cloudmap(before)


def test_post_api_config_turns_it_on_end_to_end():
    """The whole point: an operator can actually enable it.

    Capability gating for the block is covered by the route's own map and
    tests/test_rbac_enforcement.py. There WAS a third test here asserting the
    block was "gated and mapped"; it contained no assertion at all, which is
    the exact shape this file exists to complain about, so it is gone rather
    than padded out.
    """
    before = config_store.cfg().cloudmap
    try:
        with TestClient(app_module.create_app()) as c:
            r = c.post("/api/config", json={"cloudmap": {"enabled": True}})
            assert r.status_code == 200, (
                f"the switch is still unreachable: {r.status_code} {r.text[:200]}")
        assert config_store.cfg().cloudmap.enabled is True, (
            "route answered 200 and the value did not stick -- the "
            "cooling.setpoint_c shape all over again")
    finally:
        config_store.set_cloudmap(before)


def test_poll_minutes_below_the_satellite_cadence_is_refused():
    """Documented contract: below five is REFUSED rather than clamped, because
    it cannot produce fresher data than the satellite publishes."""
    with pytest.raises(Exception):
        CloudmapConfig(poll_minutes=1)
