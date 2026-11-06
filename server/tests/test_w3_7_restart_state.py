# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 3.7 (#1076): can Windows restart this PC under a running night?

Windows Update restarted the rig PC at 08:43 under TrustedInstaller and nothing
warned beforehand. Two facts decide whether it can happen mid-night: whether a
restart is PENDING (an update is staged and waiting) and Windows Update's
ACTIVE HOURS (it will not restart inside them). ``bootcause`` reads both, and
every read goes through ``bootcause._registry_read``: the one function these
tests replace with a fake. Nothing here touches a real registry, so it runs the
same on Linux CI, where the real function answers "absent".
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from astrodeck import bootcause

_WU = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate"
       r"\Auto Update\RebootRequired")
_CBS = (r"SOFTWARE\Microsoft\Windows\CurrentVersion"
        r"\Component Based Servicing\RebootPending")
_UX = r"SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"
_POLICY = r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate"


def _fake_registry(monkeypatch, values: dict) -> list:
    """Replace the registry read with a dict lookup. Keys are
    ``(key_path, value_name)``; a key-existence check is ``(key_path, None)``.
    Returns the list of reads made, so a test can see what was asked."""
    asked: list = []

    def fake(key, value=None):
        asked.append((key, value))
        return values.get((key, value))

    monkeypatch.setattr(bootcause, "_registry_read", fake)
    return asked


def test_nothing_staged_is_not_pending(monkeypatch):
    _fake_registry(monkeypatch, {})
    state = bootcause.restart_state()
    assert state.pending is False
    assert state.sources == ()


@pytest.mark.parametrize("key,word", [(_WU, "windows_update"),
                                      (_CBS, "component_servicing")])
def test_each_staged_restart_marker_is_pending_and_named(monkeypatch, key, word):
    _fake_registry(monkeypatch, {(key, None): True})
    state = bootcause.restart_state()
    assert state.pending is True
    assert state.sources == (word,)


def test_both_markers_are_both_named(monkeypatch):
    _fake_registry(monkeypatch, {(_WU, None): True, (_CBS, None): True})
    assert bootcause.restart_state().sources == ("windows_update",
                                                 "component_servicing")


def test_the_real_read_answers_absent_off_windows(monkeypatch):
    """The Linux CI case: no registry exists, so nothing is pending and the
    active hours are Windows' own default."""
    monkeypatch.setattr(bootcause.sys, "platform", "linux")
    assert bootcause._registry_read(_WU) is None
    state = bootcause.restart_state()
    assert state.pending is False
    assert (state.active_start, state.active_end) == (8, 17)


def test_active_hours_default_to_windows_own_default(monkeypatch):
    _fake_registry(monkeypatch, {})
    assert bootcause.active_hours() == (8, 17)


def test_active_hours_come_from_the_user_setting(monkeypatch):
    _fake_registry(monkeypatch, {(_UX, "ActiveHoursStart"): 16,
                                 (_UX, "ActiveHoursEnd"): 10})
    assert bootcause.active_hours() == (16, 10)


def test_a_policy_that_sets_active_hours_beats_the_user_setting(monkeypatch):
    _fake_registry(monkeypatch, {(_UX, "ActiveHoursStart"): 16,
                                 (_UX, "ActiveHoursEnd"): 10,
                                 (_POLICY, "SetActiveHours"): 1,
                                 (_POLICY, "ActiveHoursStart"): 18,
                                 (_POLICY, "ActiveHoursEnd"): 6})
    assert bootcause.active_hours() == (18, 6)


def test_a_policy_that_does_not_set_active_hours_is_ignored(monkeypatch):
    _fake_registry(monkeypatch, {(_UX, "ActiveHoursStart"): 16,
                                 (_UX, "ActiveHoursEnd"): 10,
                                 (_POLICY, "SetActiveHours"): 0,
                                 (_POLICY, "ActiveHoursStart"): 18,
                                 (_POLICY, "ActiveHoursEnd"): 6})
    assert bootcause.active_hours() == (16, 10)


@pytest.mark.parametrize("start,end", [("16", 10), (16, None), (24, 10),
                                       (-1, 10), (16, 99), (True, 10),
                                       (16.5, 10)])
def test_an_unreadable_hour_falls_back_to_the_default_pair(monkeypatch, start, end):
    """Half a pair is not a window: one bad hour reads the whole pair as unset."""
    _fake_registry(monkeypatch, {(_UX, "ActiveHoursStart"): start,
                                 (_UX, "ActiveHoursEnd"): end})
    assert bootcause.active_hours() == (8, 17)


def test_a_registry_read_that_raises_never_raises_out(monkeypatch):
    def boom(key, value=None):
        raise OSError("hive unreadable")

    monkeypatch.setattr(bootcause, "_registry_read", boom)
    state = bootcause.restart_state()
    assert state.pending is False
    assert (state.active_start, state.active_end) == (8, 17)


def test_the_status_block_is_words_and_numbers_only(monkeypatch):
    _fake_registry(monkeypatch, {(_WU, None): True,
                                 (_UX, "ActiveHoursStart"): 16,
                                 (_UX, "ActiveHoursEnd"): 10})
    assert bootcause.restart_state().as_status() == {
        "pending": True, "sources": ["windows_update"],
        "active_hours": {"start": 16, "end": 10}}


def _utc(hour: int, day: int = 5) -> float:
    return datetime(2026, 11, day, hour, 0, tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize("dusk,dawn,hours,outside", [
    # 22:00 to 06:00 against the default 08:00-17:00 day: the whole night is
    # outside active hours, which is exactly when Windows may restart.
    (_utc(22), _utc(6, 6), (8, 17), True),
    # Active hours 16:00 to 10:00 (a window that wraps midnight) cover it.
    (_utc(22), _utc(6, 6), (16, 10), False),
    # They cover it until 10:00; a night that runs on to 11:00 leaks out.
    (_utc(22), _utc(11, 6), (16, 10), True),
    # A short winter dusk inside the day window.
    (_utc(9), _utc(15), (8, 17), False),
    # The night starts inside the window and ends past it, by an hour.
    (_utc(9), _utc(18), (8, 17), True),
])
def test_a_night_is_outside_active_hours_when_any_of_it_is(dusk, dawn, hours, outside):
    assert bootcause.night_outside_active_hours(
        dusk, dawn, *hours, tz=timezone.utc) is outside
