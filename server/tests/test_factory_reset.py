# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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
def env(isolated_config, monkeypatch):
    """conftest's ``isolated_config`` (#341), whose ``config/`` and
    ``captures/`` are the two directories a reset works on. It was a store
    patched into three modules, which left the profile library on the
    developer's real ``profiles/`` while ``_dirty`` names an active profile
    ("abc123") that the hub looks up there."""
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    app = app_module.create_app()
    isolated_config.sweep()
    with TestClient(app) as c:
        yield (c, isolated_config.store, isolated_config.dir,
               isolated_config.captures)


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
    # NOT data, NOT setup: install assets/caches that must survive both modes.
    # exist_ok (#201): ``logs`` is the night log's own folder, and since S3's
    # capture-root isolation (conftest ``_point_the_capture_root_at``) every
    # test has a fresh ``NightLogWriter`` whose first line creates ``logs/``
    # under whatever root is current. A client's lifespan log line can reach
    # this test's root before this line runs: measured on this file alone,
    # -n0, a bare ``mkdir()`` failed 6 of 11 runs with FileExistsError
    # [WinError 183] on ...\captures\logs, and 0 of 8 with ``exist_ok``
    # (S3-X's verifier); measured again in the integration of S3, in a
    # private copy: 5 of 8 runs red bare, and 0 of 5 with ``exist_ok``.
    for name in PRESERVED_CAPTURE_ENTRIES:
        (cap / name).mkdir(exist_ok=True)
        (cap / name / "keepme").write_text("x")


def test_dirty_runs_after_the_night_log_made_logs(env, monkeypatch):
    """#201 with the race taken out: the night-log writer creates
    ``captures/logs`` FIRST, every time, and ``_dirty`` runs after it.

    The rates quoted in ``_dirty`` prove little on their own, because the
    race is intermittent: a green run may simply have lost it. This forces
    the losing order. A fresh ``NightLogWriter`` (the one conftest installs
    for every capture root) rolls the night on its first line and makes
    ``logs/`` under the root current then, which is this test's ``cap``; the
    line is published through ``bus.log``, the path a lifespan's boot line
    takes. A writer of the test's own, so the premise holds even where the
    run turned persistence off (``ASTRODECK_LOG_PERSIST=0``).

    RED under mutant "bare mkdir in _dirty" (``(cap / name).mkdir()`` for
    the preserved entries, the code before 812fcf9e), run in a private
    copy of ``server/`` (scratchpad s4-testhyg-mut2), 5 runs of 5, never
    the coin toss the unforced race is, observed verbatim (the pytest
    temporary root elided):

        tests\\test_factory_reset.py:129:
        tests\\test_factory_reset.py:85: in _dirty
            (cap / name).mkdir()
        ...
        E           FileExistsError: [WinError 183] Cannot create a file when
        that file already exists: '...\\\\test_dirty_runs_after_the_nigh0
        \\\\captures\\\\logs'
        FAILED tests/test_factory_reset.py::
        test_dirty_runs_after_the_night_log_made_logs

    Green on the fix: the file's 22 tests, 3 runs of 3 (``-p no:randomly
    -n0``).
    """
    _c, store, cfg_dir, cap = env
    from astrodeck import events
    monkeypatch.setattr(events.bus, "night_log", events.NightLogWriter())
    events.bus.log("info", "test_factory_reset: a line before _dirty (#201)",
                   "test")
    events.flush_night_logs()                   # logs/ is made off-thread now
    assert (cap / "logs").is_dir(), (
        "premise: the night log made logs/ under this test's captures root "
        "before _dirty ran")
    _dirty(store, cfg_dir, cap)
    for name in PRESERVED_CAPTURE_ENTRIES:
        assert (cap / name / "keepme").read_text() == "x", name


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


def test_managed_reset_cannot_reopen_listener(env, monkeypatch):
    c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)
    before = store.cfg().model_dump()
    monkeypatch.setenv(config_mod.REQUIRE_AUTH_ENV, "true")
    monkeypatch.delenv(config_mod.DIRECT_TOKEN_ENV, raising=False)

    r = c.post("/api/system/factory-reset",
               json={"confirm": "RESET", "reset_auth": True})

    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "authentication_required"
    assert store.cfg().model_dump() == before
    assert (cfg_dir / "users.json").exists()
    assert (cap / "M31" / "light_0001.fits").exists()


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


# ------------------------------------------------ OPEN-004: ownership transfer

def test_transfer_reset_clears_remote_pairing_and_credentials(env):
    """The transfer opt-in removes relay pairing (incl. the device token) and the
    stored update credential, so a previous operator keeps NO path to future
    traffic. The non-secret signing key stays so updates still verify."""
    _c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)
    cfg = store.cfg()
    cfg.remote.device_token = "x" * 40
    cfg.remote.home_id = "home-1"
    cfg.update.github_token = "ghp_previous_owner_secret"
    store.bump_and_save()

    factory_reset(store, cfg_dir, cap, reset_remote=True)

    cfg = store.reload()
    assert cfg.remote.enabled is False
    assert cfg.remote.relay_url == ""
    assert cfg.remote.device_token == ""
    assert cfg.remote.home_id == ""
    assert cfg.update.github_token == ""
    assert cfg.update.signing_pubkey == "A" * 43 + "="


def test_remote_pairing_survives_without_the_transfer_opt_in(env):
    """Every non-transfer mode keeps relay pairing: a QA handoff within one org
    must not orphan a remotely-managed box."""
    _c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)
    cfg = store.cfg()
    cfg.remote.device_token = "x" * 40
    cfg.update.github_token = "ghp_keep_me"
    store.bump_and_save()

    factory_reset(store, cfg_dir, cap)  # reset_remote defaults False

    cfg = store.reload()
    assert cfg.remote.enabled is True
    assert cfg.remote.device_token == "x" * 40
    assert cfg.update.github_token == "ghp_keep_me"


def test_preview_reports_remote_pairing_present(env):
    from astrodeck.factory_reset import preview
    _c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)
    snap = preview(store, cfg_dir, cap)
    assert snap["remote_paired"] is True


def test_route_transfer_reset_clears_pairing(env):
    c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)
    cfg = store.cfg()
    cfg.remote.device_token = "x" * 40
    store.bump_and_save()

    r = c.post("/api/system/factory-reset",
               json={"confirm": "RESET", "reset_remote": True})
    assert r.status_code == 200, r.text
    assert r.json()["remote_reset"] is True
    assert store.reload().remote.device_token == ""


def test_preview_reports_an_update_credential_on_its_own(env):
    """The transfer opt-in also scrubs the update credential, so the panel must
    be able to offer it when only that exists (no relay pairing)."""
    from astrodeck.factory_reset import preview
    _c, store, cfg_dir, cap = env
    _dirty(store, cfg_dir, cap)
    cfg = store.cfg()
    cfg.remote.enabled = False
    cfg.remote.relay_url = ""
    cfg.update.github_token = "ghp_only_this"
    store.bump_and_save()
    snap = preview(store, cfg_dir, cap)
    assert snap["remote_paired"] is False
    assert snap["update_credential"] is True
