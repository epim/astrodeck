"""API tests for POST /api/profiles/{id}/set-providers (#132).

The sibling file ``test_clear_profile_overrides`` covers the UNDO. This covers
the EDIT, which is the half that was missing and the reason the disclosure felt
like being ignored.

The failure being fixed is not a crash and does not look like anything from the
outside. ``POST /api/config/providers`` writes the GLOBAL block; the ACTIVE
PROFILE beats it inside ``providers.override_with_layer``. So a user who read
"Running the built-in simulator — pinned by profile Rig1", opened the dropdown,
chose AstroDeck native and saved got a 200, a green toast, and a rig still
running the simulator. Every assertion below is therefore about what the NEXT
read of ``/api/config`` reports, not about the request succeeding — a route that
returns 200 and changes nothing that runs is the precise thing under test.

The active-profile CACHE is the other half. ``override_with_layer`` reads a
cached profile; a write that did not invalidate it would leave the OLD pin
resolving while the console, having re-fetched, displayed the new one. Same
console-disagrees-with-rig shape as the original bug, inside its fix.

Repo convention: TestClient against a temp ConfigStore + tmp profiles dir, no
real rig, boot auto-connect OFF.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore, ProvidersConfig
from astrodeck.profiles import Profile, ProfileDevice


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.profiles import profiles as profile_lib
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(profile_lib, "_dir", tmp_path / "profiles")

    app = app_module.create_app()
    with TestClient(app) as c:
        yield c, temp_store, profile_lib


def _active_profile(profile_lib, store, **kw) -> str:
    prof = Profile(name="Rig1", primary_backend="sim", **kw)
    profile_lib.save(prof)
    store.set_active_profile(prof.id)
    app_module.hub.invalidate_profile_cache()
    return prof.id


def _effective(c, key: str) -> dict:
    return c.get("/api/config").json()["effective"][key]


def test_editing_a_pinned_capability_changes_what_the_rig_runs(client):
    """The whole point. Before this route existed the same user action wrote
    global config, which the profile then shadowed: 200, green toast, unchanged
    rig."""
    c, store, lib = client
    pid = _active_profile(lib, store, providers={"polar_align": "sim"})

    before = _effective(c, "providers.polar_align")
    assert before["layer"] == "profile" and before["value"] == "sim"

    r = c.post(f"/api/profiles/{pid}/set-providers",
               json={"providers": {"polar_align": "astrodeck"}})
    assert r.status_code == 200, r.text

    after = _effective(c, "providers.polar_align")
    assert after["layer"] == "profile", "the pin is edited, not bypassed"
    assert after["value"] == "astrodeck", "and the NEW value is what resolves"


def test_the_global_block_is_left_alone(client):
    """Writing the profile must not also mutate global. Global is what the rig
    falls back to when the pin is later cleared, so a silent edit here would
    show up as a value nobody chose, weeks later, with no trail."""
    c, store, lib = client
    # ``backend``, not ``astap``: polar align's resolver has no ASTAP branch
    # (ASTAP is the solver polar align USES, not a polar-align provider), and
    # since finding O the write layer refuses a family nothing resolves.
    store.set_providers(ProvidersConfig(polar_align="backend"))
    pid = _active_profile(lib, store, providers={"polar_align": "sim"})

    c.post(f"/api/profiles/{pid}/set-providers",
           json={"providers": {"polar_align": "astrodeck"}})

    assert store.cfg().providers.polar_align == "backend"
    assert _effective(c, "providers.polar_align")["config"] == "backend"


def test_a_capability_not_named_in_the_body_is_untouched(client):
    """Overwrite is per-capability. A whole-block body would let a client holding
    a stale config re-pin capabilities the user never touched."""
    c, store, lib = client
    pid = _active_profile(lib, store,
                          providers={"polar_align": "sim", "solve": "astap"})

    c.post(f"/api/profiles/{pid}/set-providers",
           json={"providers": {"polar_align": "auto"}})

    solve = _effective(c, "providers.solve")
    assert solve["layer"] == "profile" and solve["value"] == "astap"


def test_pinning_a_capability_the_profile_never_held(client):
    """A profile with no ``providers`` dict at all must gain one rather than
    422 — the Guide row on a freshly-captured profile is exactly this case."""
    c, store, lib = client
    pid = _active_profile(lib, store)
    assert lib.get(pid).providers is None

    r = c.post(f"/api/profiles/{pid}/set-providers",
               json={"providers": {"guide": "astrodeck"}})
    assert r.status_code == 200, r.text
    assert _effective(c, "providers.guide")["layer"] == "profile"


def test_pinning_auto_is_a_real_override_and_is_reported_as_one(client):
    """``auto`` looks like "no override" and is not: a profile pinned to auto
    beats a global ``astap`` and changes which solver runs. Reporting it as
    unpinned would recreate the invisible override one layer up."""
    c, store, lib = client
    store.set_providers(ProvidersConfig(solve="astap"))
    pid = _active_profile(lib, store)

    c.post(f"/api/profiles/{pid}/set-providers",
           json={"providers": {"solve": "auto"}})

    entry = _effective(c, "providers.solve")
    assert entry["layer"] == "profile" and entry["value"] == "auto"


def test_an_unknown_capability_is_422_not_a_silent_no_op(client):
    """Asymmetric with clear-overrides on purpose: ignoring an unknown key on a
    CLEAR still leaves the user with fewer pins, but ignoring one on a WRITE is
    a save that reports success and changes nothing — the exact failure this
    route exists to end."""
    c, store, lib = client
    pid = _active_profile(lib, store)
    r = c.post(f"/api/profiles/{pid}/set-providers",
               json={"providers": {"site_name": "auto"}})
    assert r.status_code == 422, r.text


def test_an_unknown_provider_value_is_422(client):
    """Same live vocabulary ``ConfigStore.set_providers`` validates against, so a
    typo'd or deleted driver id cannot be parked in a profile where it would be
    silently discarded at resolve time with no signal anywhere."""
    c, store, lib = client
    pid = _active_profile(lib, store)
    r = c.post(f"/api/profiles/{pid}/set-providers",
               json={"providers": {"solve": "nina-deleted"}})
    assert r.status_code == 422, r.text


def test_missing_profile_is_404_not_500(client):
    """``profiles.set_providers`` resolves through safe_id_path, which raises
    KeyError on a refused id; the route maps it like every sibling rather than
    leaking a traceback with absolute paths in it."""
    c, _, _ = client
    for bad in ("does-not-exist", "..%5Cx", "%2E%2E%5C%2E%2E%5Cpwned"):
        r = c.post(f"/api/profiles/{bad}/set-providers",
                   json={"providers": {"solve": "auto"}})
        assert r.status_code == 404, (bad, r.status_code, r.text)


def test_an_empty_body_changes_nothing(client):
    c, store, lib = client
    pid = _active_profile(lib, store, providers={"guide": "astrodeck"})
    r = c.post(f"/api/profiles/{pid}/set-providers", json={})
    assert r.status_code == 200, r.text
    assert _effective(c, "providers.guide")["value"] == "astrodeck"


def test_device_secrets_survive_the_write(client):
    """The reason this is a server route rather than a client read-modify-write:
    ``GET /api/profiles/{id}`` is wire-redacted, so a fetch-edit-POST round trip
    would persist the blanks and cost the user their stored credentials."""
    c, store, lib = client
    pid = _active_profile(
        lib, store,
        devices=[ProfileDevice(role="camera",
                               extra={"password": "hunter2", "host": "10.0.0.5"})])
    c.post(f"/api/profiles/{pid}/set-providers",
           json={"providers": {"guide": "astrodeck"}})
    # at rest, not over the wire — the wire copy is redacted by design
    assert lib.get(pid).devices[0].extra["password"] == "hunter2"


def test_the_row_returned_is_the_fresh_one(client):
    """The client re-renders from this row; returning the pre-write state would
    show the old pin until something else refetched."""
    c, store, lib = client
    pid = _active_profile(lib, store, providers={"solve": "astap"})
    row = c.post(f"/api/profiles/{pid}/set-providers",
                 json={"providers": {"solve": "sim"}}).json()
    assert row["providers"]["solve"] == "sim"


def test_a_refused_profile_id_is_404_even_with_an_invalid_body(tmp_path, monkeypatch):
    """The docstring promised 404 for a refused id and delivered 422, because the
    body was judged before anyone asked whether the profile exists — so a caller
    with BOTH problems learned about the wrong one. The existing 404 test only
    ever sent valid bodies, so nothing caught it."""
    from astrodeck.profiles import ProfileLibrary
    lib = ProfileLibrary(directory=tmp_path / "profiles")
    # KeyError is what the route maps to 404; ValueError is what it maps to 422.
    # BOTH problems are present here, so this pins the ORDER they are judged in.
    with pytest.raises(KeyError):
        lib.set_providers("does-not-exist", {"site_name": "auto"})
