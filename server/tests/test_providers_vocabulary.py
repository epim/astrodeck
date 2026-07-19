"""Registry-driven provider-override vocabulary (equipment-drivers spec §3.4).

Write-time rule (review finding 2): a value is valid iff it is ``auto``, the
legacy alias ``backend``, an implicit driver id (sim/astrodeck/astap), or a
currently-configured driver id. Unknown ids are rejected at write time (422 on
the route, ValueError at the store); resolve-time degrade lives in
test_providers.py. ``solve`` is the third capability slot — old configs and
old client bodies without it must keep loading/POSTing fine.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from astrodeck.config import (
    IMPLICIT_DRIVER_IDS,
    ConfigStore,
    ProvidersConfig,
)


@pytest.fixture()
def store(tmp_path):
    return ConfigStore(path=tmp_path / "astrodeck.json")


# ------------------------------------------------------------- store level

def test_implicit_ids_match_platform_vocabulary():
    # sim/astrodeck/astap are the cross-platform triple; ascom-local (COM-T6) is
    # a Windows-only addition to the implicit vocabulary (the bundled COM host).
    import sys
    expected = {"sim", "astrodeck", "astap"}
    if sys.platform == "win32":
        expected |= {"ascom-local"}
    assert set(IMPLICIT_DRIVER_IDS) == expected


def test_valid_values_include_legacy_implicit_and_configured(store):
    d = store.add_driver("nina", "astrotown.lan")
    v = store.valid_override_values()
    assert {"auto", "backend", "sim", "astrodeck", "astap", d.id} <= v


def test_set_providers_accepts_new_vocabulary(store):
    d = store.add_driver("nina", "astrotown.lan")
    cfg = store.set_providers(ProvidersConfig(
        autofocus=d.id, polar_align="backend", solve="astap"))
    assert cfg.providers.autofocus == d.id
    assert cfg.providers.solve == "astap"


def test_set_providers_rejects_unknown_id(store):
    with pytest.raises(ValueError, match="nina-dead"):
        store.set_providers(ProvidersConfig(autofocus="nina-dead"))


def test_deleting_a_driver_invalidates_its_id_for_new_writes(store):
    d = store.add_driver("nina", "astrotown.lan")
    store.set_providers(ProvidersConfig(autofocus=d.id))
    store.delete_driver(d.id)
    # the STORED value is untouched (resolve-time degrade handles it) …
    assert store.cfg().providers.autofocus == d.id
    # … but a NEW write of the dead id is rejected.
    with pytest.raises(ValueError):
        store.set_providers(ProvidersConfig(polar_align=d.id))


def test_solve_defaults_auto_for_old_configs():
    # An old ProvidersConfig payload without the key parses with the default.
    assert ProvidersConfig(autofocus="auto", polar_align="auto").solve == "auto"


# ------------------------------------------------------------- route level
# TestClient fixture per repo convention (see test_drivers_api.py).

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    import astrodeck.drivers as drv
    drv.invalidate()
    from astrodeck.api import app as app_module
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def test_providers_route_accepts_vocabulary_and_rejects_unknown(client):
    ok = client.post("/api/config/providers",
                     json={"autofocus": "astrodeck", "polar_align": "sim",
                           "solve": "astap"})
    assert ok.status_code == 200
    bad = client.post("/api/config/providers",
                      json={"autofocus": "nina-dead", "polar_align": "auto",
                            "solve": "auto"})
    assert bad.status_code == 422
    assert "nina-dead" in bad.text


def test_providers_route_backcompat_body_without_solve(client):
    # An old client POSTing only the two original caps must still succeed.
    r = client.post("/api/config/providers",
                    json={"autofocus": "auto", "polar_align": "backend"})
    assert r.status_code == 200
    assert r.json()["providers"]["solve"] == "auto"
