# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#125: the precheck says whether anything outside the rig is watching it.

The rig went offline on 2026-09-21 and nothing reported it. The product
already has the watcher - `alerting.py` pings a dead-man URL every minute from a
wall-clock timer, and its absence is what pages - but checked on 2026-09-22 the
rig had NO dead-man URL and NO alert channel. The outage was unannounced by
configuration, not by a missing feature, and nothing surfaced that.

`rig_precheck --report` runs at every deploy, so that is where the state now
appears. The dead-man URL carries a per-ping secret and a sink carries a token
or webhook, so the line is booleans and counts only - most of these cases are
about what it must NOT be able to say.

MUTATIONS RUN, and what each printed:

  M1, append the dead-man URL to the configured line. 2 failed:
  test_no_secret_can_escape, naming the ping token it found, and the
  exact-line case beside it.

  M2, count DISABLED sinks as channels. 1 failed:
  test_a_disabled_channel_does_not_count: a rig whose only sink is switched
  off was reported as having a channel instead of as UNWATCHED.

  M3, report a sink-only rig as fully watched (drop the NO dead-man branch).
  1 failed: test_a_channel_cannot_report_its_own_pc_dying.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rig_precheck.py"
_spec = importlib.util.spec_from_file_location("astrodeck_rig_precheck_watch", _SCRIPT)
precheck = importlib.util.module_from_spec(_spec)
sys.modules["astrodeck_rig_precheck_watch"] = precheck
_spec.loader.exec_module(precheck)

PING = "https://hc-ping.example/3f9a1c2e-SECRET-PING-TOKEN"
HOOK = "https://hooks.example/T000/B000/SECRET-HOOK"


def _write(tmp_path, monkeypatch, cfg) -> str:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "astrodeck.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(precheck, "ROOT", str(tmp_path))
    return precheck._watch_line()


def test_the_rig_as_it_was_found_is_called_unwatched(tmp_path, monkeypatch):
    line = _write(tmp_path, monkeypatch, {"deadman_url": "", "alerts": []})
    assert line.startswith("UNWATCHED"), line
    assert "#125" in line


def test_a_rig_with_both_says_so(tmp_path, monkeypatch):
    line = _write(tmp_path, monkeypatch, {"deadman_url": PING, "alerts": [
        {"id": "a", "kind": "webhook", "url": HOOK, "enabled": True, "verified": True}]})
    assert line == "dead-man configured; 1 alert channel(s), 1 verified", line


def test_a_channel_cannot_report_its_own_pc_dying(tmp_path, monkeypatch):
    """A sink is sent BY the rig, so it goes quiet exactly when the rig does.
    Only the dead-man, whose silence is the page, covers the outage #125 is
    about - a sink-only rig must not read as watched."""
    line = _write(tmp_path, monkeypatch, {"deadman_url": "", "alerts": [
        {"id": "a", "kind": "webhook", "url": HOOK, "enabled": True}]})
    assert "NO dead-man URL" in line, line


def test_a_disabled_channel_does_not_count(tmp_path, monkeypatch):
    line = _write(tmp_path, monkeypatch, {"deadman_url": "", "alerts": [
        {"id": "a", "kind": "webhook", "url": HOOK, "enabled": False}]})
    assert line.startswith("UNWATCHED"), line


def test_no_secret_can_escape(tmp_path, monkeypatch):
    line = _write(tmp_path, monkeypatch, {"deadman_url": PING, "alerts": [
        {"id": "a", "kind": "telegram", "token": "123:SECRET-BOT", "chat_id": "42",
         "url": HOOK, "enabled": True}]})
    for secret in ("SECRET-PING-TOKEN", "SECRET-HOOK", "SECRET-BOT", "hc-ping", "hooks.example"):
        assert secret not in line, f"the watch line printed {secret!r}: {line}"


def test_an_unreadable_config_is_not_reported_as_watched(tmp_path, monkeypatch):
    monkeypatch.setattr(precheck, "ROOT", str(tmp_path))
    assert "treat as unwatched" in precheck._watch_line()


# ------------------------------------------------------------ recovery (#16)
# Mutation run: `if esc.get("reconnect_resume") is True` -> `if True`.
# Observed red: 2 failed, test_recovery_off_is_said and the absent-setting case,
# both reading "reconnect-and-resume ON" for a rig that has it off.

def _recovery(tmp_path, monkeypatch, cfg) -> str:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "astrodeck.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(precheck, "ROOT", str(tmp_path))
    return precheck._recovery_line()


def test_recovery_off_is_said(tmp_path, monkeypatch):
    """The rig as it was found on 2026-09-22."""
    line = _recovery(tmp_path, monkeypatch, {"escalation": {"reconnect_resume": False}})
    assert line.startswith("reconnect-and-resume OFF") and "#16" in line, line


def test_an_absent_setting_is_the_default_and_the_default_is_off(tmp_path, monkeypatch):
    assert _recovery(tmp_path, monkeypatch, {}).startswith("reconnect-and-resume OFF")


def test_recovery_on_is_said(tmp_path, monkeypatch):
    line = _recovery(tmp_path, monkeypatch, {"escalation": {"reconnect_resume": True}})
    assert line == "reconnect-and-resume ON", line
