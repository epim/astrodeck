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

WP-95 (#125, code part): "configured" is not "watched". A URL nothing has
answered is a rig no external service has heard from, so the line now also
reads the dispatcher's ``last_ok_age_s`` (seconds since the monitor last
ACCEPTED a ping, None if never) from ``/api/alerts/health``.

DELIBERATE PIN CHANGE (WP-95): ``test_a_rig_with_both_says_so`` used to pin
the config-only text "dead-man configured; ..." for a rig whose ping state was
never read. That text is exactly the claim nothing keeps, so with no health
block the line now says the ping state is UNKNOWN; the old wording survives
nowhere because a configured URL alone no longer earns it.

MUTATIONS RUN FOR WP-95 (each from a byte backup inside the worktree,
restored byte-identically by sha256, mutant text grepped out afterwards):

  m2, ``_watch_line`` ignores the health block and prints the config-only
  text ("dead-man configured"). 13 failed, 11 passed; first:
  test_a_rig_with_both_says_so, ``AssertionError: dead-man configured; 1
  alert channel(s), 1 verified``, and test_a_configured_url_nothing_has_answered_is_not_watched
  with the same config-only text where "NO ping has been accepted" was owed.

  m2b, ``main`` calls ``_watch_line()`` without the health block. 1 failed:
  test_main_reads_the_health_route_and_prints_what_it_says, ``assert 'NO ping
  has been accepted' in "watched: dead-man configured, ping state UNKNOWN (the
  server's health was not read); 0 alert channel(s), 0 verified"``.

  m2c, treat any non-negative age as answering (the 3-interval limit dropped).
  1 failed: test_a_stale_ping_is_not_called_answering, ``assert 'answering'
  not in 'dead-man configured and answering (last ping accepted 181 s ago); 0
  alert channel(s), 0 verified'``.

  m2d, a None age prints the answering line. 2 failed:
  test_a_configured_url_nothing_has_answered_is_not_watched and
  test_main_reads_the_health_route_and_prints_what_it_says, ``assert 'NO ping
  has been accepted' in 'dead-man configured and answering (last ping accepted
  0 s ago); 0 alert channel(s), 0 verified'``.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rig_precheck.py"
_spec = importlib.util.spec_from_file_location("astrodeck_rig_precheck_watch", _SCRIPT)
precheck = importlib.util.module_from_spec(_spec)
sys.modules["astrodeck_rig_precheck_watch"] = precheck
_spec.loader.exec_module(precheck)

PING = "https://hc-ping.example/3f9a1c2e-SECRET-PING-TOKEN"
HOOK = "https://hooks.example/T000/B000/SECRET-HOOK"


def _write(tmp_path, monkeypatch, cfg, health=None) -> str:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "astrodeck.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(precheck, "ROOT", str(tmp_path))
    return precheck._watch_line(health)


def test_the_rig_as_it_was_found_is_called_unwatched(tmp_path, monkeypatch):
    line = _write(tmp_path, monkeypatch, {"deadman_url": "", "alerts": []})
    assert line.startswith("UNWATCHED"), line
    assert "#125" in line


def test_a_rig_with_both_says_so(tmp_path, monkeypatch):
    # DELIBERATE PIN CHANGE (WP-95, #125): this used to be the config-only
    # "dead-man configured; 1 alert channel(s), 1 verified". With no health
    # block there is no evidence a ping was ever accepted, so the line says so
    # instead of claiming a watcher.
    line = _write(tmp_path, monkeypatch, {"deadman_url": PING, "alerts": [
        {"id": "a", "kind": "webhook", "url": HOOK, "enabled": True, "verified": True}]})
    assert line == ("dead-man configured, ping state UNKNOWN (the server's "
                    "health was not read); 1 alert channel(s), 1 verified"), line


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


# ---------------------------------------------------- watched = accepted (#125)

def _dm(age, **extra) -> dict:
    """The ``deadman`` block of /api/alerts/health, as the dispatcher builds it."""
    return {"configured": True, "healthy": True, "last_ping_age_s": 10.0,
            "last_ok_age_s": age, **extra}


def _cfg_with_deadman() -> dict:
    return {"deadman_url": PING, "alerts": []}


def test_a_configured_url_nothing_has_answered_is_not_watched(tmp_path, monkeypatch):
    """The state a freshly pasted URL, a typo and a dead monitor share, and the
    one `healthy` cannot see: nothing has failed yet because nothing has been
    accepted yet."""
    line = _write(tmp_path, monkeypatch, _cfg_with_deadman(), _dm(None))
    assert "NO ping has been accepted" in line, line
    assert "answering" not in line, line


def test_an_accepted_ping_is_called_answering_with_its_age(tmp_path, monkeypatch):
    line = _write(tmp_path, monkeypatch, _cfg_with_deadman(), _dm(12.4))
    assert line == ("dead-man configured and answering (last ping accepted "
                    "12 s ago); 0 alert channel(s), 0 verified"), line


def test_a_stale_ping_is_not_called_answering(tmp_path, monkeypatch):
    """Three missed intervals is where an external monitor would have paged."""
    limit = 3 * precheck.DEADMAN_INTERVAL_S
    fresh = _write(tmp_path, monkeypatch, _cfg_with_deadman(), _dm(limit - 1.0))
    stale = _write(tmp_path, monkeypatch, _cfg_with_deadman(), _dm(limit + 1.0))
    assert "answering" in fresh, fresh
    assert "answering" not in stale, stale
    assert "NO ping has been accepted" in stale, stale
    assert "since start" not in stale, "it WAS accepted once; do not say it never was"


@pytest.mark.parametrize("garbage", ["12", True, -5.0, float("nan"), float("inf"), []])
def test_an_unreadable_age_is_never_called_answering(tmp_path, monkeypatch, garbage):
    line = _write(tmp_path, monkeypatch, _cfg_with_deadman(), _dm(garbage))
    assert "answering" not in line, line
    assert "UNKNOWN" in line, line


def test_a_server_that_does_not_report_the_age_is_unknown_not_answering(
        tmp_path, monkeypatch):
    """A server older than the field: say it cannot tell."""
    block = {"configured": True, "healthy": True, "last_ping_age_s": 5.0}
    line = _write(tmp_path, monkeypatch, _cfg_with_deadman(), block)
    assert "answering" not in line and "UNKNOWN" in line, line


def test_no_url_text_can_escape_with_a_health_block(tmp_path, monkeypatch):
    for age in (None, 5.0, 9999.0):
        line = _write(tmp_path, monkeypatch, _cfg_with_deadman(),
                      _dm(age, url=PING, ping_url=PING))
        for secret in ("SECRET-PING-TOKEN", "hc-ping", "3f9a1c2e"):
            assert secret not in line, f"the watch line printed {secret!r}: {line}"


def test_the_health_block_cannot_rescue_a_rig_with_no_deadman_url(tmp_path, monkeypatch):
    """The config decides whether there IS a dead-man; the health block only
    says whether it is answering. Both existing branches stay as they were."""
    unwatched = _write(tmp_path, monkeypatch, {"deadman_url": "", "alerts": []},
                       _dm(5.0))
    assert unwatched.startswith("UNWATCHED"), unwatched
    sink_only = _write(tmp_path, monkeypatch, {"deadman_url": "", "alerts": [
        {"id": "a", "kind": "webhook", "url": HOOK, "enabled": True}]}, _dm(5.0))
    assert "NO dead-man URL" in sink_only, sink_only
    assert "answering" not in sink_only, sink_only


def test_the_precheck_interval_is_the_dispatchers():
    """The script cannot import the server (it runs under the rig's venv with
    nothing else on the path), so it carries its own copy of the interval. A
    copy nothing compares is a copy that drifts."""
    from astrodeck.alerting import DEADMAN_INTERVAL_S
    assert precheck.DEADMAN_INTERVAL_S == DEADMAN_INTERVAL_S


# --------------------------------------------------- main() carries the block

def _run_main(tmp_path, monkeypatch, capsys, health):
    """Run ``main(["--report"])`` against canned responses. ``health`` is the
    body of /api/alerts/health, or an Exception to raise from it."""
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "astrodeck.json").write_text(
        json.dumps(_cfg_with_deadman()), encoding="utf-8")
    monkeypatch.setattr(precheck, "ROOT", str(tmp_path))
    monkeypatch.setattr(precheck, "_session_cookie", lambda: "ad_session=x")
    canned = {
        "/api/status": {"mount": {}, "connected": {}},
        "/api/polar/state": {"state": "idle", "running": False},
        "/api/sequence/state": {"state": "idle", "running": False},
        "/api/alerts/health": health,
    }

    def fake_get(_cookie, path):
        body = canned[path]
        if isinstance(body, Exception):
            raise body
        return body

    monkeypatch.setattr(precheck, "_get", fake_get)
    code = precheck.main(["--report"])
    out = capsys.readouterr().out
    watched = [ln for ln in out.splitlines() if ln.startswith("watched:")]
    assert len(watched) == 1, out
    return code, watched[0], out


def test_main_reads_the_health_route_and_prints_what_it_says(
        tmp_path, monkeypatch, capsys):
    never = {"undelivered": 0, "undelivered_by_sink": {}, "deadman": _dm(None)}
    code, line, out = _run_main(tmp_path, monkeypatch, capsys, never)
    assert code == 0
    assert "NO ping has been accepted" in line, line
    answering = {"undelivered": 0, "undelivered_by_sink": {}, "deadman": _dm(7.0)}
    _code, line, out = _run_main(tmp_path, monkeypatch, capsys, answering)
    assert "answering" in line, line
    assert "SECRET-PING-TOKEN" not in out and "hc-ping" not in out


def test_an_unreadable_health_route_degrades_the_line_not_the_precheck(
        tmp_path, monkeypatch, capsys):
    """The watched line is information; the deploy gate is the BUSY check. A
    health route that errors must not turn a readable rig into exit 2, and it
    must not read as watched either."""
    import urllib.error
    boom = urllib.error.URLError("refused")
    code, line, out = _run_main(tmp_path, monkeypatch, capsys, boom)
    assert code == 0 and "IDLE" in out, out
    assert "answering" not in line and "UNKNOWN" in line, line


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
