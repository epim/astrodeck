# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 3.7 (#1076): arming a night is refused while Windows may restart the PC
under it, the rig status says a restart is pending, and the deploy precheck
reports the flag.

The registry is replaced by a fake (``bootcause._registry_read``); no test
here reads the real one, so each case means the same thing on Linux CI.

NAMED MUTANT: ignore the pending flag in the arm gate (``not state.pending`` in
``Hub.arm_refusal`` read as always true, so the gate never refuses).
test_a_pending_restart_outside_active_hours_refuses_to_arm turns red.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

from _simhub import sim_hub  # noqa: F401
from astrodeck import bootcause
from astrodeck.config import config_store
from astrodeck.sequence.schedule import observing_night

_WU = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate"
       r"\Auto Update\RebootRequired")
_UX = r"SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"
#: A mid-day moment at the fixture site (40 N 74 W): tonight is the coming
#: night, so the whole of it lies ahead.
NOW = datetime(2026, 11, 5, 18, 0).timestamp()


def _registry(monkeypatch, *, pending: bool, hours=None) -> None:
    values = {}
    if pending:
        values[(_WU, None)] = True
    if hours is not None:
        values[(_UX, "ActiveHoursStart")], values[(_UX, "ActiveHoursEnd")] = hours
    monkeypatch.setattr(bootcause, "_registry_read",
                        lambda key, value=None: values.get((key, value)))


def _hours_covering_tonight(hub) -> tuple[int, int]:
    """Active hours that begin at dusk's hour and end an hour after dawn's,
    in this machine's own clock, so the case does not depend on its zone."""
    dusk, dawn = observing_night(hub.site, now=NOW)
    return (datetime.fromtimestamp(dusk).hour,
            (datetime.fromtimestamp(dawn).hour + 2) % 24)


def test_a_pending_restart_outside_active_hours_refuses_to_arm(sim_hub, monkeypatch):
    _registry(monkeypatch, pending=True, hours=(8, 17))
    why = sim_hub.arm_refusal(now=NOW)
    assert why is not None, "a night outside active hours armed with a restart pending"
    # It says why and what to do, in words an operator can act on.
    assert "restart pending" in why
    assert "active hours" in why
    assert "windows_update" in why
    assert "08:00" in why and "17:00" in why
    # The last sentence names the control, and says what pressing it does.
    assert why.endswith("press ACKNOWLEDGE on the Windows restart card in Rig > "
                        "Safety: nights then start despite it until Windows has "
                        "restarted."), why


def test_the_refusal_names_no_time_of_the_night(sim_hub, monkeypatch):
    """Dusk and dawn are computed from the site, and the refusal is shown to
    anyone who can arm a night: the only clock times in it are the active
    hours, which are the PC's, not the night's."""
    _registry(monkeypatch, pending=True, hours=(8, 17))
    why = sim_hub.arm_refusal(now=NOW)
    assert not re.search(r"\d", why.replace("08:00", "").replace("17:00", "")), why


def test_nothing_pending_arms(sim_hub, monkeypatch):
    _registry(monkeypatch, pending=False, hours=(8, 17))
    assert sim_hub.arm_refusal(now=NOW) is None


def test_an_acknowledged_pending_restart_arms(sim_hub, monkeypatch):
    _registry(monkeypatch, pending=True, hours=(8, 17))
    assert sim_hub.arm_refusal(now=NOW, acknowledged=True) is None


def test_active_hours_that_cover_the_night_arm_with_a_restart_pending(
        sim_hub, monkeypatch):
    _registry(monkeypatch, pending=True,
              hours=_hours_covering_tonight(sim_hub))
    assert sim_hub.arm_refusal(now=NOW) is None


def test_with_no_site_the_night_cannot_be_shown_covered_so_it_refuses(
        sim_hub, monkeypatch):
    _registry(monkeypatch, pending=True, hours=(8, 17))
    monkeypatch.setattr(config_store.cfg().site, "is_default", True)
    assert sim_hub.arm_refusal(now=NOW) is not None


def test_with_no_site_and_nothing_pending_it_still_arms(sim_hub, monkeypatch):
    _registry(monkeypatch, pending=False)
    monkeypatch.setattr(config_store.cfg().site, "is_default", True)
    assert sim_hub.arm_refusal(now=NOW) is None


async def test_the_rig_status_says_a_restart_is_pending(sim_hub, monkeypatch):
    _registry(monkeypatch, pending=True, hours=(16, 10))
    status = await sim_hub.poll_status()
    assert status["restart"] == {"pending": True, "sources": ["windows_update"],
                                 "active_hours": {"start": 16, "end": 10},
                                 "acknowledged": False,
                                 "arm_refusal": sim_hub.arm_refusal()}
    assert status["restart"]["arm_refusal"] is not None


async def test_the_rig_status_says_nothing_is_pending(sim_hub, monkeypatch):
    _registry(monkeypatch, pending=False)
    status = await sim_hub.poll_status()
    assert status["restart"]["pending"] is False
    assert status["restart"]["arm_refusal"] is None


# ------------------------------------------------------------ the preflight

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rig_precheck.py"
_spec = importlib.util.spec_from_file_location("astrodeck_rig_precheck_w37", _SCRIPT)
precheck = importlib.util.module_from_spec(_spec)
sys.modules["astrodeck_rig_precheck_w37"] = precheck
_spec.loader.exec_module(precheck)


def test_the_precheck_line_reports_a_pending_restart():
    line = precheck._restart_line({"pending": True, "sources": ["windows_update"],
                                   "active_hours": {"start": 8, "end": 17}})
    assert line.startswith("PENDING"), line
    assert "windows_update" in line
    assert "08:00" in line and "17:00" in line


def test_the_precheck_line_reports_nothing_pending():
    line = precheck._restart_line({"pending": False, "sources": [],
                                   "active_hours": {"start": 8, "end": 17}})
    assert line.startswith("none pending"), line


@pytest.mark.parametrize("block", [None, "pending", [], {}, {"pending": "yes"},
                                   {"pending": 1}])
def test_the_precheck_line_does_not_guess_from_a_block_it_cannot_read(block):
    assert precheck._restart_line(block).startswith("UNKNOWN"), block


def test_the_precheck_line_prints_only_words_it_knows():
    line = precheck._restart_line({
        "pending": True,
        "sources": ["windows_update", "Fictional Hilltop Observatory"],
        "active_hours": {"start": "lat 11.1111", "end": 99}})
    assert "Fictional" not in line and "11.1111" not in line and "99" not in line
    assert line.startswith("PENDING")


def _run_main(tmp_path, monkeypatch, capsys, restart):
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "astrodeck.json").write_text(
        json.dumps({"deadman_url": "", "alerts": []}), encoding="utf-8")
    monkeypatch.setattr(precheck, "ROOT", str(tmp_path))
    monkeypatch.setattr(precheck, "_session_cookie", lambda: "ad_session=x")
    status = {"mount": {}, "connected": {}}
    if restart is not None:
        status["restart"] = restart
    canned = {"/api/status": status,
              "/api/polar/state": {"state": "idle", "running": False},
              "/api/sequence/state": {"state": "idle", "running": False},
              "/api/alerts/health": {}}
    monkeypatch.setattr(precheck, "_get", lambda _cookie, path: canned[path])
    code = precheck.main(["--report"])
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith("restart:")]
    assert len(lines) == 1, out
    return code, lines[0], out


def test_main_prints_the_restart_line_and_a_pending_restart_is_not_busy(
        tmp_path, monkeypatch, capsys):
    """A pending Windows restart is a fact about tonight, not a reason the
    server cannot be restarted now: the deploy gate stays IDLE."""
    block = {"pending": True, "sources": ["component_servicing"],
             "active_hours": {"start": 8, "end": 17}}
    code, line, out = _run_main(tmp_path, monkeypatch, capsys, block)
    assert code == 0
    assert "PENDING" in line and "component_servicing" in line, line
    assert "IDLE" in out and "BUSY" not in out


def test_main_says_unknown_when_the_server_does_not_report_it(
        tmp_path, monkeypatch, capsys):
    _code, line, _out = _run_main(tmp_path, monkeypatch, capsys, None)
    assert "UNKNOWN" in line, line
