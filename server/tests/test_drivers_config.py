"""DriverEntry config CRUD (equipment-drivers spec 2026-07-08 §3.1).

Configured backend drivers are GLOBAL config (not per-profile): declared once,
referenced by id from profiles. These tests pin the CRUD contract Tasks 2-5
build on: server-minted unique ids, per-type default ports, validation errors
as ValueError (-> 422 at the API), unknown ids as KeyError (-> 404), and
additive back-compat (old config files without a ``drivers`` key load fine).
"""
import pytest

from astrodeck.config import ConfigStore


@pytest.fixture()
def store(tmp_path):
    return ConfigStore(tmp_path / "astrodeck.json")


def test_add_driver_mints_unique_id_and_persists(store, tmp_path):
    d = store.add_driver("nina", "astrotown.lan", 1888, label="NINA on astrotown")
    assert d.id.startswith("nina-") and len(d.id) == len("nina-") + 4
    assert d.enabled is True and d.label == "NINA on astrotown"
    # round-trip: a FRESH store on the same file reads the entry back
    again = ConfigStore(tmp_path / "astrodeck.json").cfg()
    assert [e.id for e in again.drivers] == [d.id]


def test_add_driver_defaults_port_per_type(store):
    assert store.add_driver("nina", "h1").port == 1888
    assert store.add_driver("alpaca", "h2").port == 11111
    assert store.add_driver("phd2", "h3").port == 4400


def test_add_driver_default_label_names_type_and_host(store):
    d = store.add_driver("alpaca", "192.168.1.50")
    assert d.label == "ALPACA @ 192.168.1.50"


def test_add_driver_rejects_unknown_type_and_blank_host(store):
    with pytest.raises(ValueError):
        store.add_driver("asiair", "h")
    with pytest.raises(ValueError):
        store.add_driver("nina", "   ")


def test_update_driver_patches_and_revalidates(store):
    d = store.add_driver("alpaca", "192.168.1.50")
    u = store.update_driver(d.id, {"port": 11112, "enabled": False})
    assert (u.port, u.enabled) == (11112, False)
    with pytest.raises(ValueError):
        store.update_driver(d.id, {"port": 99999})      # out of range
    with pytest.raises(ValueError):
        store.update_driver(d.id, {"id": "evil"})       # id is immutable
    with pytest.raises(ValueError):
        store.update_driver(d.id, {"host": "  "})       # blank host


def test_update_delete_unknown_id_raise_keyerror(store):
    with pytest.raises(KeyError):
        store.update_driver("nope", {"port": 1})
    with pytest.raises(KeyError):
        store.delete_driver("nope")


def test_delete_driver_removes_entry(store):
    d = store.add_driver("phd2", "127.0.0.1")
    store.delete_driver(d.id)
    assert store.cfg().drivers == []


def test_old_config_without_drivers_key_loads(tmp_path):
    p = tmp_path / "astrodeck.json"
    p.write_text('{"version": 3}', encoding="utf-8")
    cfg = ConfigStore(p).cfg()
    assert cfg.drivers == []
