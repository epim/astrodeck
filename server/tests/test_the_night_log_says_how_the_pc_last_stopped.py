# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#125: at startup the night log says how the PC's previous session ended.

The 2026-09-08 thermal resets were diagnosed by a dig through the Windows event
log after the fact, and the 2026-09-21 outage's evidence was on the machine
that was down. The System log answers the question the moment the PC is back;
`bootcause` reads it once at startup and writes one line.

The cases are built from wevtutil's own XML shape (checked against a real
System log on the development machine when written: 40 events, ids 41, 1074,
6005, 6006, 6008, and 1074's fields named param1..param7).

MUTATIONS RUN, and what each printed:

  M1, drop the lead before the marker (`boot - _KERNEL_LEAD` -> `boot`),
  which is what this module first did. 1 failed: the 41-alone case, read as
  having no record. The other unexpected cases carry a 6008 at the marker and
  stay green, which is why the 41-alone case exists.

  M2, drop the 1074-before-6006 pairing. 1 failed:
  test_a_windows_update_restart_names_who_asked, which read "clean shutdown".

  M3, print param2 (the computer name) in the planned line. 1 failed:
  test_nothing_identifying_leaves.
"""
from __future__ import annotations

import asyncio

from astrodeck import bootcause

_NS = 'xmlns="http://schemas.microsoft.com/win/2004/08/events/event"'


def _ev(event_id: int, at: str, provider: str = "EventLog", data: dict | None = None) -> str:
    fields = "".join(f"<Data Name='{k}'>{v}</Data>" for k, v in (data or {}).items())
    return (f"<Event {_NS}><System><Provider Name='{provider}'/>"
            f"<EventID>{event_id}</EventID>"
            f"<TimeCreated SystemTime='{at}'/><Computer>RIG-PC-NAME</Computer></System>"
            f"<EventData>{fields}</EventData></Event>")


def _classify(*events: str):
    return bootcause.classify(bootcause.parse_events("".join(events)))


def test_a_thermal_reset_is_called_unexpected():
    """The 2026-09-08 shape: nothing asked, and the new boot says so."""
    level, message = _classify(
        _ev(6005, "2026-09-08T20:33:05.1234567Z"),
        _ev(41, "2026-09-08T20:33:02.0000000Z", "Microsoft-Windows-Kernel-Power"),
        _ev(6008, "2026-09-08T20:33:07.7654321Z"))
    # 41 is stamped BEFORE 6005 on a real boot - measured 16.5 s on the
    # development machine's own log - because the kernel writes it before the
    # event log service reports itself started.
    assert level == "warning", message
    assert "UNEXPECTEDLY" in message and "2026-09-08 20:33 UTC" in message, message


def test_kernel_power_41_alone_is_enough_though_it_precedes_the_marker():
    """The measured ordering: 41 sixteen seconds before 6005. A window that
    only looked forward from the marker - what this module first did - read
    this boot as having no record at all."""
    level, message = _classify(
        _ev(41, "2026-08-26T19:52:35.0000000Z", "Microsoft-Windows-Kernel-Power"),
        _ev(6005, "2026-08-26T19:52:52.0000000Z"))
    assert level == "warning" and "Kernel-Power 41" in message, message


def test_6008_alone_is_enough():
    level, message = _classify(
        _ev(6006, "2026-09-07T03:00:00.0000000Z"),        # the night before: clean
        _ev(6005, "2026-09-08T20:33:05.0000000Z"),
        _ev(6008, "2026-09-08T20:33:09.0000000Z"))
    assert level == "warning" and "EventLog 6008" in message, message


def test_a_windows_update_restart_names_who_asked():
    level, message = _classify(
        _ev(1074, "2026-09-15T09:30:10.0000000Z", "User32",
            {"param1": r"C:\Windows\servicing\TrustedInstaller.exe (RIG-PC-NAME)",
             "param2": "RIG-PC-NAME", "param5": "restart", "param7": "NT AUTHORITY\\SYSTEM"}),
        _ev(6006, "2026-09-15T09:31:40.0000000Z"),
        _ev(6005, "2026-09-15T09:33:00.0000000Z"))
    assert level == "info", message
    assert "planned restart by TrustedInstaller.exe" in message, message


def test_a_clean_shutdown_with_nobody_named():
    level, message = _classify(
        _ev(6006, "2026-09-15T09:31:40.0000000Z"),
        _ev(6005, "2026-09-15T09:33:00.0000000Z"))
    assert level == "info" and "clean shutdown" in message, message


def test_nothing_identifying_leaves():
    _, message = _classify(
        _ev(1074, "2026-09-15T09:30:10.0000000Z", "User32",
            {"param1": r"C:\Users\someone\evil path\tool.exe", "param2": "RIG-PC-NAME",
             "param5": "power off", "param7": "RIG\\someone"}),
        _ev(6006, "2026-09-15T09:31:40.0000000Z"),
        _ev(6005, "2026-09-15T09:33:00.0000000Z"))
    for leak in ("RIG-PC-NAME", "someone", "Users", "evil path"):
        assert leak not in message, f"the boot line printed {leak!r}: {message}"


def test_no_boot_marker_says_so_rather_than_guessing():
    level, message = _classify(_ev(6006, "2026-09-15T09:31:40.0000000Z"))
    assert "cannot be said" in message


def test_garbage_never_raises():
    assert bootcause.parse_events("<Event><not-closed>") == []
    assert bootcause.parse_events("") == []


def test_the_startup_hook_writes_one_line_and_swallows_failure(monkeypatch):
    lines = []
    monkeypatch.setattr(bootcause, "_read_system_log", lambda: (
        _ev(6005, "2026-09-08T20:33:05.0000000Z")
        + _ev(6008, "2026-09-08T20:33:09.0000000Z")))
    asyncio.run(bootcause.log_boot_cause(lambda lvl, msg, src: lines.append((lvl, msg, src))))
    assert len(lines) == 1 and lines[0][0] == "warning" and lines[0][2] == "system", lines

    def _boom():
        raise RuntimeError("wevtutil exploded")
    monkeypatch.setattr(bootcause, "_read_system_log", _boom)
    asyncio.run(bootcause.log_boot_cause(lambda *a: lines.append(a)))   # must not raise
    assert len(lines) == 1
