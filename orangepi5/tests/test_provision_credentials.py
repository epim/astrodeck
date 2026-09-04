"""Credential and physical-recovery state-machine tests."""

from __future__ import annotations

import json
import re

import pytest


@pytest.fixture
def prov(broker):
    """Credential state belongs exclusively to the privileged broker."""
    return broker


def test_generated_credentials_are_independent_and_label_safe(prov):
    values = {prov.generate_setup_password() for _ in range(256)}
    assert len(values) == 256
    assert all(re.fullmatch(r"[a-z0-9]{4}(?:-[a-z0-9]{4}){2}", value)
               for value in values)


def test_commission_is_exclusive_and_rotation_does_not_arm_ap(prov, tmp_path):
    path = tmp_path / "state" / "setup-identity.json"
    initial = prov.commission_setup_identity(path, ssid="AstroDeck-CAFE", now=10.0)
    assert prov.consume_ap_authorization(path, now=11.0) is True

    replacement = prov.rotate_setup_password(path, now=12.0)
    rotated = prov.load_setup_identity(path)
    assert replacement != initial["factory_psk"]
    assert rotated["factory_psk"] == initial["factory_psk"]
    assert rotated["active_psk"] == replacement
    assert rotated["generation"] == 2
    assert rotated["ap_authorization"]["armed"] is False
    assert prov.consume_ap_authorization(path, now=13.0) is False

    with pytest.raises(FileExistsError):
        prov.commission_setup_identity(path, ssid="AstroDeck-CAFE", now=14.0)


def test_rotation_never_reuses_the_printed_recovery_secret(prov, tmp_path, monkeypatch):
    path = tmp_path / "setup-identity.json"
    initial = prov.commission_setup_identity(path, ssid="AstroDeck-CAFE", now=1.0)
    candidates = iter([initial["factory_psk"], "abcd-1234-efgh"])
    monkeypatch.setattr(prov, "generate_setup_password", lambda: next(candidates))
    assert prov.rotate_setup_password(path, now=2.0) == "abcd-1234-efgh"


def test_only_three_distinct_current_boots_rearm_factory_secret(prov, tmp_path):
    path = tmp_path / "setup-identity.json"
    initial = prov.commission_setup_identity(path, ssid="AstroDeck-BEEF", now=1.0)
    prov.consume_ap_authorization(path, now=2.0)
    prov.rotate_setup_password(path, now=3.0)

    assert prov.record_short_boot(path, boot_id="same", now=20.0) is False
    assert prov.record_short_boot(path, boot_id="same", now=21.0) is False
    assert prov.record_short_boot(path, boot_id="second", now=22.0) is False
    assert prov.record_short_boot(path, boot_id="third", now=23.0) is True

    recovered = prov.load_setup_identity(path)
    assert recovered["active_psk"] == initial["factory_psk"]
    assert recovered["generation"] == 3
    assert recovered["ap_authorization"] == {
        "armed": True,
        "reason": "recovery",
        "armed_at": 23.0,
    }
    assert prov.consume_ap_authorization(path, now=24.0) is True
    assert prov.consume_ap_authorization(path, now=25.0) is False


def test_stale_or_malformed_recovery_progress_restarts_at_one(prov, tmp_path):
    path = tmp_path / "setup-identity.json"
    prov.commission_setup_identity(path, ssid="AstroDeck-BEEF", now=1.0)
    prov.consume_ap_authorization(path, now=2.0)

    assert prov.record_short_boot(path, boot_id="old", now=10.0) is False
    assert prov.record_short_boot(path, boot_id="fresh", now=191.0) is False
    state = prov.load_setup_identity(path)
    assert state["short_boots"] == {"boot_ids": ["fresh"], "first_at": 191.0}

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["short_boots"] = {"boot_ids": ["duplicate", "duplicate"], "first_at": 192.0}
    prov._atomic_write_private(path, prov._identity_json(raw))
    assert prov.record_short_boot(path, boot_id="repair", now=193.0) is False
    assert prov.load_setup_identity(path)["short_boots"] == {
        "boot_ids": ["repair"],
        "first_at": 193.0,
    }


def test_loader_rejects_conspicuously_weak_persisted_setup_secret(prov, tmp_path):
    path = tmp_path / "setup-identity.json"
    state = prov.commission_setup_identity(path, ssid="AstroDeck-BEEF", now=1.0)
    state["factory_psk"] = "aaaa-aaaa-aaaa"
    state["active_psk"] = "aaaa-aaaa-aaaa"
    prov._atomic_write_private(path, prov._identity_json(state))
    with pytest.raises(ValueError, match="factory_psk"):
        prov.load_setup_identity(path)


def test_loader_rejects_duplicate_fields_and_oversized_state(prov, tmp_path):
    path = tmp_path / "setup-identity.json"
    state = prov.commission_setup_identity(path, ssid="AstroDeck-BEEF", now=1.0)
    encoded = prov._identity_json(state)
    duplicate = encoded.replace('"schema": 1,', '"schema": 1,\n  "schema": 1,', 1)
    prov._atomic_write_private(path, duplicate)
    with pytest.raises(ValueError, match="duplicate"):
        prov.load_setup_identity(path)

    prov._atomic_write_private(path, " " * (prov.MAX_IDENTITY_BYTES + 1))
    with pytest.raises(ValueError, match="too large"):
        prov.load_setup_identity(path)


@pytest.mark.parametrize("boot_id", ["", "bad\nboot", "x" * 129, None, 12])
def test_invalid_boot_ids_fail_without_mutating_state(prov, tmp_path, boot_id):
    path = tmp_path / "setup-identity.json"
    prov.commission_setup_identity(path, ssid="AstroDeck-BEEF", now=1.0)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        prov.record_short_boot(path, boot_id=boot_id, now=2.0)
    assert path.read_bytes() == before


def test_healthy_boot_clear_does_not_change_credentials_or_authorization(prov, tmp_path):
    path = tmp_path / "setup-identity.json"
    initial = prov.commission_setup_identity(path, ssid="AstroDeck-BEEF", now=1.0)
    prov.record_short_boot(path, boot_id="one", now=2.0)
    prov.clear_short_boot_counter(path)
    cleared = prov.load_setup_identity(path)
    for key in ("factory_psk", "active_psk", "generation", "ap_authorization"):
        assert cleared[key] == initial[key]
    assert cleared["short_boots"] == {"boot_ids": [], "first_at": None}
