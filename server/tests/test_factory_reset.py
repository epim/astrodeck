"""Factory reset (Settings -> Danger zone). Real logic with a real blast radius,
so this pins BOTH halves of the contract:

  * what it MUST clear — the whole setup surface, so the next QA tester gets a
    genuinely fresh install (site back to ``is_default``, zero profiles);
  * what it MUST NOT touch — captured frames and session reports unless the
    separate opt-in is set, sign-in accounts unless the OTHER opt-in is set, and
    the install plumbing (relay pairing, self-update signing key, the offline
    survey pack) under any combination at all.

The "must not" half is the point. A reset that over-deletes destroys a tester's
images, and no amount of confirm dialog makes that recoverable.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.hub as hub_mod
from astrodeck.config import AppConfig, ConfigStore, Site
from astrodeck.factory_reset import PRESERVED_CAPTURE_ENTRIES, factory_reset


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cap = tmp_path / "captures"
    cap.mkdir()
    store = ConfigStore(path=cfg_dir / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", cap)
    monkeypatch.setattr(app_module, "config_store", store)
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c, store, cfg_dir, cap


def _dirty(store: ConfigStore, cfg_dir, cap):
    """Bring the box to the state a tester leaves behind: a real site, a saved
    profile, a configured driver, a plan, and a folder of their frames."""
    store.set_site(Site(name="Test Field", latitude=12.5, longitude=-3.25,
                        elevation_m=100.0))
    store.add_driver("alpaca", host="10.0.0.9", port=11111)
    cfg = store.cfg()
    cfg.active_profile_id = "abc123"
    cfg.deadman_url = "https://example.invalid/ping"
    # install plumbing that must SURVIVE (tier 3)
    cfg.remote.enabled = True
    cfg.remote.relay_url = "wss://relay.example.invalid/scope"
    cfg.update.signing_pubkey = "A" * 43 + "="
    store.bump_and_save()

    (cfg_dir / "profiles").mkdir()
    (cfg_dir / "profiles" / "p1.json").write_text('{"id":"p1","name":"rig"}')
    (cfg_dir / "plans").mkdir()
    (cfg_dir / "plans" / "n1.json").write_text('{"name":"night"}')
    (cfg_dir / "guider").mkdir()
    (cfg_dir / "guider" / "p1.json").write_text("{}")
    (cfg_dir / "locations.json").write_text("[]")
    (cfg_dir / "filter_names.json").write_text("{}")
    (cfg_dir / "egain.json").write_text("{}")
    (cfg_dir / "users.json").write_text('{"users":[]}')

    # the tester's DATA
    (cap / "M31").mkdir()
    (cap / "M31" / "light_0001.fits").write_bytes(b"FITS-ish payload")
    (cap / "sessions").mkdir()
    (cap / "sessions" / "s1.json").write_text('{"id":"s1"}')
    (cap / "reports").mkdir()
    (cap / "reports" / "r1.json").write_text('{"id":"r1"}')
    # NOT data, NOT setup: install assets/caches that must survive both modes
    for name in PRESERVED_CAPTURE_ENTRIES:
        (cap / name).mkdir()
        (cap / name / "keepme").write_text("x")


# ------------------------------------------------------------------ core reset

def test_reset_restores_a_fresh_install(env):
    """The pinned acceptance criteria: default site, zero profiles, no drivers —
    i.e. exactly what the first-run wizard reads at 0/N."""
    _c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)
    assert store.cfg().site.is_default is False

    factory_reset(store, cfg_dir, cap)

    cfg = store.reload()
    fresh = AppConfig()
    assert cfg.site.is_default is True
    assert cfg.site == fresh.site
    assert cfg.optics == fresh.optics
    assert cfg.drivers == []
    assert cfg.active_profile_id is None
    assert cfg.deadman_url == ""
    assert cfg.safety == fresh.safety
    assert cfg.weather == fresh.weather
    assert not (cfg_dir / "profiles").exists()
    assert not (cfg_dir / "plans").exists()
    assert not (cfg_dir / "guider").exists()
    for f in ("locations.json", "filter_names.json", "egain.json"):
        assert not (cfg_dir / f).exists(), f
    # the recovery backup held the PREVIOUS tester's coordinates
    assert not (cfg_dir / "astrodeck.json.bak").exists()


def test_version_stays_monotonic(env):
    """Never rewind the optimistic-concurrency counter: an open client holding a
    stale token must still lose its race, not accidentally match a reset one."""
    _c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)
    before = store.cfg().version
    factory_reset(store, cfg_dir, cap)
    assert store.cfg().version > before


# -------------------------------------------------- what it must NOT destroy

def test_captures_survive_by_default(env):
    """THE invariant. A tester's images do not disappear because somebody reset
    the settings."""
    _c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)

    factory_reset(store, cfg_dir, cap)

    assert (cap / "M31" / "light_0001.fits").read_bytes() == b"FITS-ish payload"
    assert (cap / "sessions" / "s1.json").exists()
    assert (cap / "reports" / "r1.json").exists()


def test_auth_survives_by_default(env):
    """Wiping sign-in reopens a rig that may be WAN-reachable — it happens only
    when explicitly asked for."""
    _c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)
    store.cfg().auth.methods = ["local"]
    store.bump_and_save()

    factory_reset(store, cfg_dir, cap)

    assert store.cfg().auth.methods == ["local"]
    assert (cfg_dir / "users.json").exists()


@pytest.mark.parametrize("delete_captures", [False, True])
@pytest.mark.parametrize("reset_auth", [False, True])
def test_install_plumbing_survives_every_mode(env, delete_captures, reset_auth):
    """Relay pairing, the self-update signing key and the offline survey pack are
    not 'setup' — losing them costs a support call or a multi-GB re-download, and
    no combination of options may take them."""
    _c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)

    factory_reset(store, cfg_dir, cap, delete_captures=delete_captures,
                  reset_auth=reset_auth)

    cfg = store.cfg()
    assert cfg.remote.enabled is True
    assert cfg.remote.relay_url == "wss://relay.example.invalid/scope"
    assert cfg.update.signing_pubkey == "A" * 43 + "="
    for name in PRESERVED_CAPTURE_ENTRIES:
        assert (cap / name / "keepme").exists(), name


# ------------------------------------------------------- the explicit opt-ins

def test_delete_captures_opt_in_clears_data_only(env):
    _c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)

    factory_reset(store, cfg_dir, cap, delete_captures=True)

    assert not (cap / "M31").exists()
    assert not (cap / "sessions").exists()
    assert not (cap / "reports").exists()
    for name in PRESERVED_CAPTURE_ENTRIES:
        assert (cap / name / "keepme").exists(), name


def test_reset_auth_opt_in_clears_accounts_and_bumps_epoch(env):
    """Clearing accounts must also invalidate outstanding sessions, or the
    previous tester's cookie keeps working against an 'empty' user store."""
    _c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)
    store.cfg().auth.methods = ["local"]
    store.cfg().auth.session_epoch = 4
    store.bump_and_save()

    factory_reset(store, cfg_dir, cap, reset_auth=True)

    cfg = store.cfg()
    assert cfg.auth.methods == []
    assert cfg.auth.session_epoch == 5
    assert not (cfg_dir / "users.json").exists()


# ------------------------------------------------------------------- the route

def test_route_requires_the_typed_word(env):
    c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)

    for body in ({}, {"confirm": ""}, {"confirm": "yes"}, {"confirm": "RESE"},
                 {"confirm": "factory reset"}):
        r = c.post("/api/system/factory-reset", json=body)
        assert r.status_code == 400, body
        assert r.json()["detail"]["code"] == "confirm_required"
    # nothing moved
    assert store.cfg().site.is_default is False
    assert (cap / "M31" / "light_0001.fits").exists()


def test_confirm_word_tolerates_a_phone_keyboard(env):
    """Phone keyboards auto-capitalise the first letter and it is easy to trail a
    space. The interlock exists to make the act DELIBERATE, not to make it a
    typing test, so the compare is trimmed + case-folded — and the UI's own
    enable check must agree with this, or the button lights up on a word the
    server then rejects."""
    c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)

    r = c.post("/api/system/factory-reset", json={"confirm": " Reset "})
    assert r.status_code == 200, r.text
    assert store.cfg().site.is_default is True


def test_route_defaults_leave_data_alone(env):
    """A body carrying ONLY the confirm word must not delete frames or accounts:
    the safe value is the default at the API too, not just in the UI."""
    c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)

    r = c.post("/api/system/factory-reset", json={"confirm": "reset"})
    assert r.status_code == 200, r.text
    assert r.json()["captures_deleted"] is False
    assert r.json()["auth_reset"] is False
    assert store.cfg().site.is_default is True
    assert (cap / "M31" / "light_0001.fits").exists()
    assert (cfg_dir / "users.json").exists()


def test_route_refuses_while_the_rig_is_busy(env, monkeypatch):
    """Same idle gate as self-update: never reset out from under a running
    sequence."""
    c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)
    monkeypatch.setattr(type(hub_mod.hub), "restart_blocker",
                        property(lambda self: "a sequence is running"))

    r = c.post("/api/system/factory-reset", json={"confirm": "RESET"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "rig_busy"
    assert store.cfg().site.is_default is False


def test_preview_counts_what_would_go(env):
    """The panel quotes these numbers on screen, so they must be measured."""
    c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)

    snap = c.get("/api/system/factory-reset").json()
    assert snap["site_is_default"] is False
    assert snap["profiles"] == 1
    assert snap["plans"] == 1
    assert snap["drivers"] == 1
    assert snap["captures"]["frames"] == 1
    assert snap["can_reset"] is True
    # the preserved entries are reported so the UI can name them verbatim
    assert set(snap["preserved_capture_entries"]) == set(PRESERVED_CAPTURE_ENTRIES)
    # ...and are excluded from the "will be deleted" tally
    assert snap["captures"]["entries"] == 3  # M31 + sessions + reports
