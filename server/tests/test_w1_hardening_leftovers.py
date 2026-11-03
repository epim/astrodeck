# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Wave 1, WP 1.6 (#1129): hardening leftovers of #794, #1068 and #831.

* #794: ``audit.record`` takes a category, so an operator attestation reads
  ``attest trust_position ...`` and not ``auth trust_position ...`` in the sink
  a sign-in filter reads.
* #1068: a factory reset removes the ``.bak`` (and any staging leftover) that
  ``write_json_atomic`` keeps beside every setup file it removes. The test lists
  the directory after the reset.
* #831: a stored ``deadman_url`` the ping could never use is said at config
  load, without being echoed and without being rewritten. The rotator reverse
  409, the save-time check and the singular CD refusal of #831 are already
  pinned by ``test_rotator_api.py``, ``test_deadman_failures_said_and_url_
  checked_at_save.py`` and ``test_fitsio.py``.
"""
from __future__ import annotations

import json
import logging

import pytest

import astrodeck.events as events_mod
from astrodeck.auth import audit
from astrodeck.config import ConfigStore
from astrodeck.factory_reset import factory_reset
from astrodeck.persist import write_json_atomic
from test_w16_mount_trust_position_route import (  # noqa: F401
    _POLE, _post, _provider_restored, _reset_am5, rig)
from test_zwo_am5 import fixed_env  # noqa: F401

AUDIT = "astrodeck.audit"


# ------------------------------------------------------------------- #794

def test_the_default_category_is_auth(caplog):
    with caplog.at_level(logging.INFO, logger=AUDIT):
        audit.record("login", ok=True, user="alice")
    rec = [r for r in caplog.records if r.name == AUDIT][-1]
    assert rec.message.startswith("auth login ok "), rec.message


@pytest.mark.parametrize("category", ["auth", "attest", "admin"])
def test_the_category_leads_the_line(caplog, category):
    with caplog.at_level(logging.INFO, logger=AUDIT):
        audit.record("trust_position", ok=True, user="admin",
                     category=category)
    rec = [r for r in caplog.records if r.name == AUDIT][-1]
    assert rec.message.startswith(f"{category} trust_position ok "), rec.message


def test_a_denial_keeps_its_category(caplog):
    with caplog.at_level(logging.WARNING, logger=AUDIT):
        audit.record("user_admin_relay", ok=False, user="admin",
                     reason="no_step_up", category="admin")
    rec = [r for r in caplog.records if r.name == AUDIT][-1]
    assert rec.message.startswith("admin user_admin_relay deny "), rec.message
    assert "reason=no_step_up" in rec.message


def test_a_category_the_sink_does_not_know_is_refused(caplog):
    with caplog.at_level(logging.INFO, logger=AUDIT):
        with pytest.raises(ValueError):
            audit.record("login", ok=True, user="alice", category="bogus")
    assert not [r for r in caplog.records if r.name == AUDIT]


async def test_the_trust_position_route_audits_as_an_attestation(rig):
    link, tel = await _reset_am5()
    rig.use(tel)

    r = _post()

    assert r.status_code == 200, r.text
    lines = [m for (_lvl, m, src) in rig.lines
             if src == "audit" and "trust_position" in m]
    assert lines, "the attestation left no audit line"
    assert lines[0].startswith("attest trust_position ok "), lines[0]
    assert not any(m.startswith("auth trust_position") for m in lines)


# ------------------------------------------------------------------ #1068

def _seed_atomic(path, first, second):
    """Write ``path`` twice through the real writer, so the first content is
    left beside it as ``<name>.bak`` exactly as it is on an install."""
    write_json_atomic(path, first)
    write_json_atomic(path, second)


def _listing(directory):
    return sorted(p.name for p in directory.iterdir())


def test_a_reset_leaves_no_backup_of_a_file_it_removed(isolated_config):
    store, cfg_dir, cap = (isolated_config.store, isolated_config.dir,
                           isolated_config.captures)
    for name in ("locations.json", "filter_names.json", "egain.json"):
        _seed_atomic(cfg_dir / name, {"v": 1}, {"v": 2})
        assert (cfg_dir / (name + ".bak")).exists(), "fixture: no .bak made"
    (cfg_dir / ".locations.json.k3j9x.tmp").write_text("{}")
    _seed_atomic(cfg_dir / "unrelated.json", {"v": 1}, {"v": 2})

    factory_reset(store, cfg_dir, cap)

    left = _listing(cfg_dir)
    assert [n for n in left if n.endswith(".bak")] == ["unrelated.json.bak"], (
        f"a setup file's backup survived the reset: {left}")
    assert not [n for n in left if n.endswith(".tmp")], left
    assert "unrelated.json" in left


def test_a_reset_that_clears_sign_in_also_clears_the_users_backup(
        isolated_config):
    store, cfg_dir, cap = (isolated_config.store, isolated_config.dir,
                           isolated_config.captures)
    _seed_atomic(cfg_dir / "users.json", {"users": []}, {"users": []})
    assert (cfg_dir / "users.json.bak").exists(), "fixture: no .bak made"

    factory_reset(store, cfg_dir, cap, reset_auth=True)

    assert not [n for n in _listing(cfg_dir) if n.startswith("users.json")]


def test_a_reset_that_keeps_sign_in_keeps_the_users_backup(isolated_config):
    store, cfg_dir, cap = (isolated_config.store, isolated_config.dir,
                           isolated_config.captures)
    _seed_atomic(cfg_dir / "users.json", {"users": []}, {"users": []})

    factory_reset(store, cfg_dir, cap)

    assert (cfg_dir / "users.json.bak").exists()
    assert (cfg_dir / "users.json").exists()


# -------------------------------------------------------------------- #831

def _store_with_stored_deadman(tmp_path, url):
    path = tmp_path / "astrodeck.json"
    path.write_text(json.dumps({"deadman_url": url}))
    return ConfigStore(path)


@pytest.fixture
def bus_lines(monkeypatch):
    lines: list[tuple[str, str, str]] = []

    def record(level, message, source="hub", **_kw):
        lines.append((level, message, source))
    monkeypatch.setattr(events_mod.bus, "log", record)
    return lines


@pytest.mark.parametrize("url", [
    "ftp://monitor.example.invalid/ping/SECRET-TOKEN-7",
    "http:///ping/SECRET-TOKEN-7",
    "https://monitor.example.invalid:99999/ping/SECRET-TOKEN-7",
    "not a url SECRET-TOKEN-7",
])
def test_a_stored_deadman_url_the_ping_cannot_use_is_said_at_load(
        tmp_path, bus_lines, url):
    store = _store_with_stored_deadman(tmp_path, url)

    cfg = store.cfg()

    said = [m for (lvl, m, src) in bus_lines
            if lvl == "warning" and "dead-man" in m]
    assert len(said) == 1, bus_lines
    assert "SECRET-TOKEN-7" not in said[0], "the refusal echoed the url"
    assert cfg.deadman_url == url, "load must not rewrite the owner's setting"


@pytest.mark.parametrize("url", ["", "https://monitor.example.invalid/ping/ok"])
def test_a_usable_or_absent_deadman_url_is_not_said(tmp_path, bus_lines, url):
    _store_with_stored_deadman(tmp_path, url).cfg()

    assert not [m for (_l, m, _s) in bus_lines if "dead-man" in m]
