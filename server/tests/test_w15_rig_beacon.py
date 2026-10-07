# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#606 part A: the status beacon the dead-man ping carries is a CLOSED
vocabulary, and nothing about the site or the sky can be in it.

The beacon leaves the box on every dead-man ping, to a monitor the owner does
not run, so the property under test is not "it reads well" but "no input can
put a character of its own into it". Every case that feeds hostile text feeds
it through the REAL sources (a real EventBus, an engine-shaped state dict) and
asserts on the rendered line, because that line is what leaves.

The hostile strings are made up: a fictional label and coordinate-looking
numbers that match no real site (the privacy rule forbids a real one anywhere,
tests included).

MUTANTS RUN, each from a byte backup, restored byte-identically (sha256
compared) with the mutant text grepped out afterwards, and what each printed
(`python -m pytest -n 0` on this file, plus test_alerting.py for M7). The
counts leave out test_the_app_wires_the_beacon_to_the_dispatcher_it_builds,
which is red under every mutant and without one until api/app.py sets the
source (it was added after the runs below):

  M1, build_beacon adds a seventh key from the newest log line's text
  (`"msg": ...["data"].get("message", "")`). 2 failed, the case that matters
  being test_the_beacon_is_six_keys_and_none_of_the_hostile_text:
  `AssertionError: the beacon grew keys: ['msg']`. (The other, the broken-ring
  case, died on the mutant's own unguarded read: `RuntimeError: ring exploded`.)

  M2, drop the allowlist clamp on state (`_word(raw_state, _STATES,
  _STATE_DEFAULT)` -> `raw_state`). 7 failed, every id of
  test_an_engine_state_outside_the_vocabulary_reads_unknown:
  `assert 'lat-11.1111-lon-22.2222' == 'unknown'`.

  M3, render prints the value as it is (`_coerce` returns `str(value)` for
  every key but the version). 1 failed,
  test_render_is_the_last_gate_and_clamps_a_hostile_dict:
  `AssertionError: v=1 state=lat 11.1111 lon 22.2222 frames=Fictional Hilltop
  Observatory of Nowhere in Particular level=NGC 99999 Totally Made Up Nebula
  up=-5 boot=lat 11.1111`.

  M4, the level window opens at 24 h instead of 15 min (`LEVEL_WINDOW_S`).
  1 failed, test_level_reads_only_the_last_fifteen_minutes: `assert 'error' ==
  'none'` (an hour-old error read as this quarter hour's).

  M5, read `bus.log_history` (the full ring) instead of
  `bus.log_history_unflagged`. 1 failed,
  test_a_site_derived_line_cannot_move_the_level_word: `assert 'error' ==
  'info'` (the flagged flip line moved the word).

  M6, `make_source` binds `bootcause.BOOT_WORD` when it is built instead of
  when it is called. 1 failed, test_the_source_reads_the_boot_word_when_it_is_called:
  `assert False + where False = ...'v=1 state=idle frames=0 level=none up=2
  boot=unread'.endswith('boot=unexpected')`.

  M7, `is_closed_vocabulary` tests the alphabet (`[a-z0-9_= .-]{1,200}`)
  instead of the grammar. 2 failed, here and in test_alerting.py
  (test_text_outside_the_closed_vocabulary_is_never_sent):
  test_is_closed_vocabulary_is_the_dispatchers_gate, `AssertionError: lat
  11.1111 lon 22.2222 / assert not True`. Lower-case words and digits pass an
  alphabet check; only the grammar refuses a coordinate-looking string.

  M8, the boot word is passed through unclamped. 1 failed,
  test_the_boot_field_is_one_of_three_words: `assert 'Windows Update by
  someone' == 'unread'`.

  M9, no floor under a count (`max(0, min(n, cap))` -> `min(n, cap)`). 3
  failed: `assert (-5 == 0)` (frames), `assert -90 == 0` (uptime after a clock
  step) and the hostile-dict render `up=-5`.

  M10, no cap on a count (-> `max(0, n)`). 1 failed, frames with a 10**12
  progress: `assert (1000000000000 == 999999)`.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrodeck import bootcause, events, rig_beacon
from astrodeck.events import EventBus

#: what a beacon may be made of (the brief's own pattern).
CLOSED = re.compile(r"^[a-z0-9_= .-]+$")
SIX_KEYS = {"v", "state", "frames", "level", "up_s", "boot"}

NOW = 1_800_000_000.0

# Made up. A label, a coordinate pair and a target name, none of them real.
HOSTILE_LABEL = "Fictional Hilltop Observatory of Nowhere in Particular"
HOSTILE_COORDS = "lat 11.1111 lon 22.2222"
HOSTILE_TARGET = "NGC 99999 Totally Made Up Nebula"
HOSTILE = (HOSTILE_LABEL, HOSTILE_COORDS, HOSTILE_TARGET, "11.1111", "22.2222")


def _engine(state: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(state=state if state is not None else {"state": "idle"})


def _say(monkeypatch, bus: EventBus, ts: float, level: str, text: str, **kw) -> None:
    """Publish one log line AT ``ts``. The bus stamps an event through
    ``events._wall``, looked up at each call, so pinning it pins the moment."""
    monkeypatch.setattr(events, "_wall", lambda: ts)
    bus.log(level, text, "hub", **kw)


def test_the_beacon_is_six_keys_and_none_of_the_hostile_text(monkeypatch):
    """(1) The closed-vocabulary guarantee, fed from every source the beacon
    reads. The engine state dict and the log ring both carry the site's words;
    the beacon carries none of them, and exactly the six keys it names."""
    engine = _engine({
        "state": "running",
        "target": {"name": HOSTILE_TARGET, "label": HOSTILE_LABEL},
        "progress": {"frames_done": 42, "note": HOSTILE_COORDS},
        "mount": {"summary": HOSTILE_COORDS},
        "site": HOSTILE_LABEL,
    })
    bus = EventBus(persist=False)
    _say(monkeypatch, bus, NOW - 60, "warning", f"slewing to {HOSTILE_TARGET} at {HOSTILE_COORDS}")
    _say(monkeypatch, bus, NOW - 50, "error", f"{HOSTILE_LABEL}: {HOSTILE_COORDS}")
    _say(monkeypatch, bus, NOW - 40, "info", HOSTILE_LABEL, site_derived=True)

    beacon = rig_beacon.build_beacon(engine, bus, "normal", NOW, started=NOW - 3600)
    extra = sorted(set(beacon) - SIX_KEYS)
    assert not extra, f"the beacon grew keys: {extra}"
    assert set(beacon) == SIX_KEYS, f"the beacon lost keys: {sorted(SIX_KEYS - set(beacon))}"

    text = rig_beacon.render(beacon)
    assert CLOSED.match(text), f"the beacon left the closed alphabet: {text!r}"
    for piece in HOSTILE:
        assert piece.lower() not in text.lower(), f"{piece!r} reached the beacon: {text!r}"
    # The six keys, in the order the owner reads them, each once.
    assert re.findall(r"(\w+)=", text) == ["v", "state", "frames", "level", "up", "boot"]
    assert "\n" not in text and len(text) < 120, text


def test_render_has_the_documented_shape():
    beacon = {"v": 1, "state": "running", "frames": 42, "level": "warning",
              "up_s": 3600, "boot": "normal"}
    assert rig_beacon.render(beacon) == \
        "v=1 state=running frames=42 level=warning up=3600 boot=normal"


@pytest.mark.parametrize("state", ["idle", "running", "paused", "holding",
                                   "aborting", "aborted", "complete", "error"])
def test_every_engine_state_the_engine_sets_passes_through(state):
    beacon = rig_beacon.build_beacon(_engine({"state": state}), EventBus(persist=False),
                                     "unread", NOW, started=NOW)
    assert beacon["state"] == state


@pytest.mark.parametrize("hostile", [
    "lat-11.1111-lon-22.2222",         # a coordinate-looking word
    HOSTILE_LABEL,
    "RUNNING",                         # not the engine's spelling
    "",
    None,
    7,
    ["running"],
])
def test_an_engine_state_outside_the_vocabulary_reads_unknown(hostile):
    """(2) The clamp. A state the allowlist does not name is 'unknown', never
    the value: the engine's own state dict is not trusted to stay a word."""
    beacon = rig_beacon.build_beacon(_engine({"state": hostile}), EventBus(persist=False),
                                     "unread", NOW, started=NOW)
    assert beacon["state"] == "unknown", beacon


def test_a_state_that_is_not_a_dict_reads_unknown_and_zero_frames():
    for broken in (None, "running", 3, []):
        beacon = rig_beacon.build_beacon(SimpleNamespace(state=broken), EventBus(persist=False),
                                         "unread", NOW, started=NOW)
        assert beacon["state"] == "unknown" and beacon["frames"] == 0, (broken, beacon)
    beacon = rig_beacon.build_beacon(SimpleNamespace(), EventBus(persist=False),
                                     "unread", NOW, started=NOW)
    assert beacon["state"] == "unknown" and beacon["frames"] == 0, beacon


@pytest.mark.parametrize("progress,want", [
    ({"frames_done": 42}, 42),
    ({"frames_done": 0}, 0),
    ({"frames_done": None}, 0),
    ({"frames_done": "17"}, 17),
    ({"frames_done": "lat 11.1111"}, 0),
    ({"frames_done": -5}, 0),
    ({"frames_done": float("nan")}, 0),
    ({"frames_done": 10 ** 12}, rig_beacon.FRAMES_MAX),
    ({}, 0),
    (None, 0),
    ("a string, not a dict", 0),
])
def test_frames_is_a_small_non_negative_integer_whatever_progress_holds(progress, want):
    engine = _engine({"state": "running", "progress": progress})
    beacon = rig_beacon.build_beacon(engine, EventBus(persist=False), "unread", NOW, started=NOW)
    assert beacon["frames"] == want and isinstance(beacon["frames"], int), beacon


def test_level_reads_only_the_last_fifteen_minutes(monkeypatch):
    """(3) The word is the highest level in the WINDOW. An error from an hour
    ago is history, and must not make a healthy rig read as in trouble."""
    bus = EventBus(persist=False)
    _say(monkeypatch, bus, NOW - 3600, "error", "an hour old")
    _say(monkeypatch, bus, NOW - 901, "warning", "just outside")
    quiet = rig_beacon.build_beacon(_engine(), bus, "unread", NOW, started=NOW)
    assert quiet["level"] == "none", quiet

    _say(monkeypatch, bus, NOW - 899, "info", "just inside")
    assert rig_beacon.build_beacon(_engine(), bus, "unread", NOW, started=NOW)["level"] == "info"

    _say(monkeypatch, bus, NOW - 300, "warning", "five minutes ago")
    assert rig_beacon.build_beacon(_engine(), bus, "unread", NOW, started=NOW)["level"] == "warning"

    # The highest wins, not the newest.
    _say(monkeypatch, bus, NOW - 600, "error", "ten minutes ago")
    _say(monkeypatch, bus, NOW - 10, "info", "just now")
    assert rig_beacon.build_beacon(_engine(), bus, "unread", NOW, started=NOW)["level"] == "error"

    # The same ring, later: the error has aged out and the warning is next.
    later = NOW + 700           # error at -600 is now 1300 s old; warning at -300, 1000 s old
    assert rig_beacon.build_beacon(_engine(), bus, "unread", later, started=NOW)["level"] == "info"


def test_a_level_the_vocabulary_does_not_name_is_ignored(monkeypatch):
    bus = EventBus(persist=False)
    _say(monkeypatch, bus, NOW - 5, "lat 11.1111 lon 22.2222", "x")
    _say(monkeypatch, bus, NOW - 4, "debug", "y")
    beacon = rig_beacon.build_beacon(_engine(), bus, "unread", NOW, started=NOW)
    assert beacon["level"] == "none", beacon


def test_a_site_derived_line_cannot_move_the_level_word(monkeypatch):
    """A line flagged site_derived (spec 6.9, #166) is flagged because its
    MOMENT is set by a site computation. The beacon goes off the box with a
    time on it, so the word must come from the ring that never holds one."""
    bus = EventBus(persist=False)
    _say(monkeypatch, bus, NOW - 30, "error", "a flip crossing", site_derived=True)
    _say(monkeypatch, bus, NOW - 20, "info", "an ordinary line")
    beacon = rig_beacon.build_beacon(_engine(), bus, "unread", NOW, started=NOW)
    assert beacon["level"] == "info", beacon


def test_a_bus_that_cannot_be_read_never_stops_the_beacon():
    class Broken:
        @property
        def log_history_unflagged(self):
            raise RuntimeError("ring exploded")

    beacon = rig_beacon.build_beacon(_engine(), Broken(), "normal", NOW, started=NOW - 5)
    assert beacon["level"] == "none" and beacon["boot"] == "normal", beacon


def test_uptime_counts_from_the_process_start_and_never_goes_negative():
    bus = EventBus(persist=False)
    assert rig_beacon.build_beacon(_engine(), bus, "unread", NOW, started=NOW - 3600)["up_s"] == 3600
    # A wall clock that stepped back (NTP, a resume) must not print a minus.
    assert rig_beacon.build_beacon(_engine(), bus, "unread", NOW, started=NOW + 90)["up_s"] == 0


def test_the_boot_field_is_one_of_three_words():
    """(4a) The beacon passes the boot word through the same clamp as the rest:
    a value bootcause did not produce reads 'unread'."""
    bus = EventBus(persist=False)
    for word in ("unexpected", "normal", "unread"):
        assert rig_beacon.build_beacon(_engine(), bus, word, NOW, started=NOW)["boot"] == word
    for hostile in ("Windows Update by someone", HOSTILE_LABEL, None, 4):
        assert rig_beacon.build_beacon(_engine(), bus, hostile, NOW, started=NOW)["boot"] == "unread"


def test_render_is_the_last_gate_and_clamps_a_hostile_dict():
    """A beacon built by hand, or by a later edit that forgot a clamp, still
    renders to the closed alphabet: render() is the gate the text leaves by."""
    text = rig_beacon.render({"v": 9, "state": HOSTILE_COORDS, "frames": HOSTILE_LABEL,
                              "level": HOSTILE_TARGET, "up_s": -5, "boot": "lat 11.1111"})
    assert text == "v=1 state=unknown frames=0 level=none up=0 boot=unread", text
    assert rig_beacon.render({}) == "v=1 state=unknown frames=0 level=none up=0 boot=unread"


def test_is_closed_vocabulary_is_the_dispatchers_gate():
    ok = "v=1 state=running frames=42 level=warning up=3600 boot=normal"
    assert rig_beacon.is_closed_vocabulary(ok)
    for bad in ("", "x" * 400, "Running", "lat 11.1111, lon 22.2222", "a\nb", ok + "\n", "k=v;rm",
                "https://example.org/x", None, 5,
                # lower-case letters and digits only, so a character-class check
                # passes it; the grammar does not
                "lat 11.1111 lon 22.2222",
                ok + " extra=1", ok + " lat 11.1111", "v=2" + ok[3:],
                "state=running v=1 frames=42 level=warning up=3600 boot=normal",
                ok.replace("state=running", "state=lat-11.1111"),
                ok.replace("frames=42", "frames=4200000")):
        assert not rig_beacon.is_closed_vocabulary(bad), bad


def test_the_source_reads_the_boot_word_when_it_is_called(monkeypatch):
    """`from .bootcause import BOOT_WORD` binds the value ONCE, at import, and
    the boot read finishes seconds AFTER import: the module attribute has to be
    looked up per ping."""
    monkeypatch.setattr(bootcause, "BOOT_WORD", "unread")
    source = rig_beacon.make_source(_engine({"state": "idle"}), EventBus(persist=False))
    assert source().endswith("boot=unread")
    monkeypatch.setattr(bootcause, "BOOT_WORD", "unexpected")
    assert source().endswith("boot=unexpected")
    assert CLOSED.match(source())


def test_the_source_never_raises_on_a_broken_engine():
    class Bomb:
        @property
        def state(self):
            raise RuntimeError("engine exploded")

    source = rig_beacon.make_source(Bomb(), EventBus(persist=False))
    assert CLOSED.match(source()), source()


def test_the_module_imports_nothing_that_knows_the_site():
    """The #19 lesson: a filter cannot withhold a value its route computes, so
    the beacon's module must be unable to reach one. It imports no config, no
    locations, no hub or engine: its only inputs are the objects it is handed
    and the closed words it reads."""
    src = Path(rig_beacon.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            # `from . import x` names a sibling module; `from m import x` names m.
            imported.update(base if node.module else base + a.name for a in node.names)
    allowed = {"__future__", "re", "time", "typing", "collections.abc", ".bootcause"}
    stray = sorted(imported - allowed)
    assert not stray, (
        f"rig_beacon imports {stray}: the beacon leaves the box, so it may import "
        f"nothing that can name the site (config, locations, hub, engine)")


def test_the_real_engines_own_state_renders_to_a_closed_line():
    """The cases above feed a namespace and a dict. A double that is shaped
    like what the code reads hides a wrong guess about the shape, so this one
    drives the REAL engine's own `_set_state`, with a plan whose names are
    hostile, and reads the beacon off whatever the engine really merged."""
    from astrodeck.hub import Hub
    from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target

    eng = SequenceEngine(Hub())
    eng.plan = SequencePlan(
        name=HOSTILE_LABEL, guide=False, dither_every=0, autofocus_every=0, meridian_flip=False,
        targets=[Target(name=HOSTILE_TARGET, ra_hours=1.5, dec_deg=2.5, center=False,
                        autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=1.0, gain=100, count=9,
                                            frame_type="Light")])])
    eng._frames_done = 7
    eng._set_state(state="running", target=HOSTILE_TARGET)

    # The premise the guarantee rests on: the engine's state DOES carry the
    # words. Without this the assertions below could not fail.
    carried = json.dumps(eng.state, default=str)
    assert HOSTILE_TARGET in carried and HOSTILE_LABEL in carried, carried[:200]

    beacon = rig_beacon.build_beacon(eng, EventBus(persist=False), "normal", NOW, started=NOW - 30)
    assert beacon["state"] == "running" and beacon["frames"] == 7, beacon
    text = rig_beacon.render(beacon)
    assert text == "v=1 state=running frames=7 level=none up=30 boot=normal", text
    for piece in HOSTILE:
        assert piece.lower() not in text.lower(), (piece, text)


def test_the_app_wires_the_beacon_to_the_dispatcher_it_builds():
    """The beacon only leaves the box if something hands the dispatcher its
    source. `api/app.py` builds the dispatcher before the engine exists, so the
    line that sets it belongs right after `engine.dispatcher = dispatcher`:

        dispatcher.beacon_source = rig_beacon.make_source(engine, bus)

    A built-and-never-wired beacon is a claim nothing keeps: every test above
    passes and the owner's monitor never shows a status."""
    import astrodeck.api.app as app_module

    source = app_module.dispatcher.beacon_source
    assert source is not None, (
        "api/app.py never sets dispatcher.beacon_source, so no dead-man ping carries the "
        "status line: add `dispatcher.beacon_source = rig_beacon.make_source(engine, bus)` "
        "after `engine.dispatcher = dispatcher`")
    assert rig_beacon.is_closed_vocabulary(source()), source()


def test_the_wiring_line_the_app_needs_works_against_the_apps_own_names(monkeypatch):
    """The line the test above asks `api/app.py` for, run here against the
    names `app.py` already has (`dispatcher`, `engine`, `bus`), so that adding
    it there is known to work, and a ping through the real dispatcher sends
    the real engine's line."""
    import astrodeck.api.app as app_module

    monkeypatch.setattr(app_module.dispatcher, "beacon_source",
                        rig_beacon.make_source(app_module.engine, app_module.bus))
    text = app_module.dispatcher.beacon_source()
    assert rig_beacon.is_closed_vocabulary(text), text
    assert CLOSED.match(text) and text.startswith("v=1 state="), text
