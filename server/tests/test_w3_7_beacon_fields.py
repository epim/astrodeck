# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 3.7 (#606): the beacon carries the last night-log line, the boot cause
and whether Windows has a restart pending.

``boot`` was already there (WP-106). This adds ``log`` (when the newest
unflagged night-log line was written, epoch seconds) and ``pending`` (``yes`` or
``no``), between ``up`` and ``boot``. They are OPTIONAL keys: a six-key beacon is still a valid
beacon, so a rig on the older build and a relay on this one still talk.

The beacon leaves the box, so the property under test is the same closed
vocabulary as ``test_w15_rig_beacon.py``: no input can put a character of its
own into the line. The hostile strings are made up.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from astrodeck import bootcause, events, rig_beacon
from astrodeck.events import EventBus

NOW = 1_800_000_000.0
HOSTILE_COORDS = "lat 11.1111 lon 22.2222"
HOSTILE_LABEL = "Fictional Hilltop Observatory of Nowhere in Particular"
_WU = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate"
       r"\Auto Update\RebootRequired")


def _say(monkeypatch, bus, ts, level, text, **kw):
    monkeypatch.setattr(events, "_wall", lambda: ts)
    bus.log(level, text, "hub", **kw)


def _six(**over):
    beacon = {"v": 1, "state": "running", "frames": 42, "level": "warning",
              "up_s": 3600, "boot": "normal"}
    beacon.update(over)
    return beacon


def test_the_extended_beacon_renders_the_two_optional_fields():
    beacon = _six(log=1_799_999_940, pending=True)
    assert rig_beacon.render(beacon) == (
        "v=1 state=running frames=42 level=warning up=3600 log=1799999940 "
        "pending=yes boot=normal")
    assert " up=3600 log=0 pending=no boot=normal" in rig_beacon.render(
        _six(log=0, pending=False))


def test_a_six_key_beacon_still_renders_as_it_did():
    assert rig_beacon.render(_six()) == \
        "v=1 state=running frames=42 level=warning up=3600 boot=normal"


def test_extend_adds_exactly_log_and_pending(monkeypatch):
    bus = EventBus(persist=False)
    _say(monkeypatch, bus, NOW - 60, "info", "hello")
    base = rig_beacon.build_beacon(SimpleNamespace(state={"state": "idle"}), bus,
                                   "normal", NOW, started=NOW - 10)
    extended = rig_beacon.extend(base, bus, pending=True)
    assert set(extended) - set(base) == {"log", "pending"}
    assert extended["log"] == int(NOW - 60) and extended["pending"] is True
    assert {k: extended[k] for k in base} == base


def test_log_is_the_newest_unflagged_line_and_none_is_zero(monkeypatch):
    bus = EventBus(persist=False)
    assert rig_beacon.extend(_six(), bus, pending=False)["log"] == 0
    _say(monkeypatch, bus, NOW - 300, "info", "older")
    _say(monkeypatch, bus, NOW - 100, "warning", "newer")
    _say(monkeypatch, bus, NOW - 5, "info", HOSTILE_LABEL, site_derived=True)
    assert rig_beacon.extend(_six(), bus, pending=False)["log"] == int(NOW - 100)


def test_log_reads_no_text_of_a_line(monkeypatch):
    bus = EventBus(persist=False)
    _say(monkeypatch, bus, NOW - 100, "info", f"slewing at {HOSTILE_COORDS}")
    text = rig_beacon.render(rig_beacon.extend(_six(), bus, pending=False))
    assert "11.1111" not in text and "lat" not in text.replace("level", "")


def test_a_bus_that_cannot_be_read_gives_log_zero():
    class Broken:
        @property
        def log_history_unflagged(self):
            raise RuntimeError("ring exploded")

    assert rig_beacon.extend(_six(), Broken(), pending=False)["log"] == 0


@pytest.mark.parametrize("bad", [HOSTILE_COORDS, -5, None, 10 ** 15, float("nan"),
                                 True])
def test_render_clamps_a_hostile_log_field(bad):
    text = rig_beacon.render(_six(log=bad, pending=False))
    assert rig_beacon.is_closed_vocabulary(text), text
    assert "lat" not in text.replace("level", "") and "11.1111" not in text
    assert re.search(r" log=[0-9]{1,12} pending=no boot=normal$", text), text


@pytest.mark.parametrize("bad", [HOSTILE_LABEL, "yes", 1, None])
def test_render_prints_pending_yes_only_for_a_real_true(bad):
    text = rig_beacon.render(_six(log=0, pending=bad))
    assert " pending=no boot=" in text, text


@pytest.mark.parametrize("line,ok", [
    ("v=1 state=idle frames=0 level=none up=1 boot=unread", True),
    ("v=1 state=idle frames=0 level=none up=1 log=0 pending=no boot=unread", True),
    ("v=1 state=idle frames=0 level=none up=1 log=1799999940 pending=yes boot=normal", True),
    ("v=1 state=idle frames=0 level=none up=1 pending=no boot=unread", True),
    ("v=1 state=idle frames=0 level=none up=1 log=0 pending=maybe boot=unread", False),
    ("v=1 state=idle frames=0 level=none up=1 log=-1 pending=no boot=unread", False),
    ("v=1 state=idle frames=0 level=none up=1 log=1234567890123 pending=no boot=unread", False),
    ("v=1 state=idle frames=0 level=none up=1 pending=no log=0 boot=unread", False),
    ("v=1 state=idle frames=0 level=none up=1 log=0 pending=no boot=unread lat 11.1111", False),
])
def test_the_dispatchers_gate_takes_the_extended_grammar_and_nothing_else(line, ok):
    assert rig_beacon.is_closed_vocabulary(line) is ok


def test_the_source_reads_the_pending_flag_when_it_is_called(monkeypatch):
    """A flag bound when the source was built would be whatever Windows said
    at startup for the life of the process, and an update stages hours in."""
    staged = {}
    monkeypatch.setattr(bootcause, "_registry_read",
                        lambda key, value=None: staged.get((key, value)))
    bus = EventBus(persist=False)
    source = rig_beacon.make_source(SimpleNamespace(state={"state": "idle"}), bus)
    before = source()
    staged[(_WU, None)] = True
    after = source()
    assert " pending=no boot=" in before, before
    assert " pending=yes boot=" in after, after
    assert rig_beacon.is_closed_vocabulary(after)


def test_the_source_carries_the_boot_word_the_log_and_the_flag(monkeypatch):
    monkeypatch.setattr(bootcause, "_registry_read", lambda key, value=None: None)
    monkeypatch.setattr(bootcause, "BOOT_WORD", "unexpected")
    bus = EventBus(persist=False)
    _say(monkeypatch, bus, NOW - 100, "info", "a line")
    monkeypatch.setattr(rig_beacon.time, "time", lambda: NOW)
    text = rig_beacon.make_source(SimpleNamespace(state={"state": "idle"}), bus)()
    assert re.search(r" log=%d pending=no boot=unexpected$" % int(NOW - 100), text), text


# ------------------------------------------- the relay reads the same grammar
# The relay is a separate package that cannot import ``astrodeck``, so its
# ``rig_status`` carries a copy of the grammar. This is what keeps the copy
# honest: every line the rig can render, the relay parses, and the two gates
# agree on what is not a beacon.

def _relay_rig_status():
    import importlib.util
    import sys
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "relay" / "relay" / "rig_status.py"
    spec = importlib.util.spec_from_file_location("astrodeck_relay_rig_status_w37", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_line_the_rig_can_render_the_relay_parses():
    relay = _relay_rig_status()
    for state in sorted(rig_beacon._STATES | {rig_beacon._STATE_DEFAULT}):
        for level in rig_beacon._LEVELS:
            for boot in sorted(rig_beacon._BOOT_WORDS):
                for extras in ({}, {"log": 1_800_000_000, "pending": True},
                               {"log": 0, "pending": False}):
                    beacon = _six(state=state, level=level, boot=boot, **extras)
                    text = rig_beacon.render(beacon)
                    parsed = relay.parse_beacon(text)
                    assert parsed is not None, text
                    assert (parsed["state"], parsed["level"], parsed["boot"]) == \
                        (state, level, boot)


@pytest.mark.parametrize("text", [
    "v=1 state=idle frames=0 level=none up=1 boot=unread",
    "v=1 state=idle frames=0 level=none up=1 log=0 pending=no boot=unread",
    "v=1 state=idle frames=0 level=none up=1 pending=maybe boot=unread",
    "v=1 state=idle frames=0 level=none up=1 boot=unread lat 11.1111",
    "v=1 state=idle frames=9999999 level=none up=1 boot=unread",
    "lat 11.1111 lon 22.2222",
])
def test_the_rigs_gate_and_the_relays_gate_agree(text):
    relay = _relay_rig_status()
    assert rig_beacon.is_closed_vocabulary(text) is (relay.parse_beacon(text) is not None)
