# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#14 (code part): the native guider clips an over-cap pulse ITSELF and says so
once per dither settle window.

The AM5 delivers at most ``Telescope.max_pulse_ms`` (1000 ms) of any one pulse
and its driver clips the rest with a rate-limited WARNING. Across three pulled
night logs every one of the 50 cap events was a dither: the engine's fast
recenter (``step_recenter``, a recovery move that bypasses the per-axis
duration clamps) asks for the whole dither offset in one pulse, the driver
clips it, and the guide loop closes the rest. So the cap fires on EVERY dither
and the driver's line cannot tell that expected clip from a genuine saturation.

``NativeGuider._pulse`` now limits the pulse to the mount's cap before the
mount sees it (the value ``ZwoAm5Telescope._capped_ms`` delivers today, so the
bytes on the wire do not change), says ONE info line per settle window for the
recenter step, and WARNS for an over-cap pulse outside a settle window, which
the engine's own clamp (``max_ra_duration_ms`` / ``max_dec_duration_ms`` lowered
to the cap) makes unreachable. After this, any source=mount "capped to" line in
a night log is an anomaly by definition. No multi-pulse delivery is built: that
is guiding behaviour on real hardware and #14 stays open for its rig night.

SETTLE WINDOW AT DISPATCH TIME. ``dither()`` opens the engine's settle window
(``engine.rs`` sets ``settle = Some(..)`` inside ``dither``) but the host's
``_settle_open`` only flips in ``_sync_settle_window``, AFTER the frame's
dispatch. The over-cap recenter step is the FIRST frame after ``dither()``, so
reading ``_settle_open`` alone classifies exactly the pulse this exists for as
"outside a settle window". The classification is therefore ``_settle_open`` or
the engine's own ``stats()["settling"]`` (``_engine_settling``), read when the
pulse is dispatched.

NAMED MUTANTS (each run from a byte backup of native.py inside the worktree,
restored byte-identically and checked by sha256; assertions observed verbatim):

 * "clip removed" (``_limit_to_mount_cap`` ends ``return ms`` instead of
   ``return cap``): the fake mount is handed the whole demand; 9 cases red,
   the first being ``test_a_dither_recenter_step_is_clipped_and_said_once``:
   ``assert [('south', 2658)] == [('south', 1000)]``.
 * "settle flag ignored" (``settle = False``): 4 red, the dither cases lose
   their info line: ``assert 0 == 1`` / ``where 0 = len([])`` on
   ``at('info', "limited to the mount's")``;
   ``test_an_over_cap_pulse_outside_a_settle_window_warns`` stays green.
 * "engine settling ignored" (``settle = self._settle_open``): 3 red, the
   first frame after a dither is called an anomaly:
   ``test_the_first_frame_after_a_dither_is_a_settle_window_pulse`` fails with
   ``assert 0 == 1`` / ``where 0 = len([])`` on the info lines.
 * "latch never reset" (``_sync_settle_window`` no longer clears
   ``_settle_clip_said``): 2 red, the second window is silent:
   ``assert 1 == 2`` on the info lines of
   ``test_each_settle_window_says_its_own_one_line``.
 * "said every pulse" (``elif not self._settle_clip_said:`` -> ``else:``): 2
   red, ``assert 3 == 1`` (one window, three info lines).
 * "start reset removed" (the two resets in ``start_guiding`` deleted): 1 red,
   ``assert {'guide': 1, 'settle': 3} == {'guide': 0, 'settle': 0}``.
 * "non-numeric cap trusted" (``_mount_pulse_cap_ms`` accepts any truthy
   value): 3 red, a cap of ``True`` or ``-5`` or an arbitrary object rewrites
   the pulses instead of passing them through.
"""
import asyncio
import os
import time

import pytest

from astrodeck.devices.backends import zwo_am5
from astrodeck.devices.sim import build_sim_rig
from astrodeck.events import bus
from astrodeck.guide.native import NativeGuider

CAP = 1000


class _Mount:
    """A mount that records the pulses it is handed, with a published cap."""

    name = "fake mount"

    def __init__(self, cap=CAP):
        self.max_pulse_ms = cap
        self.pulses: list[tuple[str, int]] = []

    async def pulse_guide(self, direction, ms):
        self.pulses.append((direction, int(ms)))


class _NoCapMount:
    """A mount that publishes no cap at all (no attribute), as an out-of-tree
    driver might."""

    name = "no-cap mount"

    def __init__(self):
        self.pulses: list[tuple[str, int]] = []

    async def pulse_guide(self, direction, ms):
        self.pulses.append((direction, int(ms)))


class _Engine:
    """Engine stand-in for the unit tests: only ``stats()["settling"]`` is read."""

    def __init__(self, settling=False):
        self.settling = settling

    def stats(self):
        return {"guiding": True, "settling": self.settling, "recent": []}


class _Logs:
    """Every ``bus.log`` line, with its level, in order."""

    def __init__(self, monkeypatch):
        self.lines: list[tuple[str, str]] = []
        real = bus.log

        def _spy(level, message, source="hub"):
            self.lines.append((str(level), str(message)))
            return real(level, message, source)

        monkeypatch.setattr(bus, "log", _spy)

    def at(self, level, needle=""):
        return [m for lv, m in self.lines if lv == level and needle in m]


def _guider(tel, engine=None, settle_open=False):
    g = NativeGuider(None, tel, config={}, profile_id=None)
    g._engine = engine
    g._settle_open = settle_open
    return g


def _pair(ra=None, dec=None):
    action = {"action": "pulse_pair"}
    if ra:
        action["ra"] = {"dir": ra[0], "ms": ra[1]}
    if dec:
        action["dec"] = {"dir": dec[0], "ms": dec[1]}
    return action


@pytest.mark.asyncio
async def test_a_dither_recenter_step_is_clipped_and_said_once(monkeypatch):
    """The brief's case: a Dec step of 2658 ms (the value the 2026-09-12 log
    shows) inside a settle window reaches the mount as 1000 ms, with one info
    line and no warning."""
    logs = _Logs(monkeypatch)
    tel = _Mount()
    g = _guider(tel, settle_open=True)
    await g._pulse(_pair(dec=("south", 2658)))
    assert tel.pulses == [("south", CAP)]
    info = logs.at("info", "limited to the mount's")
    assert len(info) == 1
    assert "2658" in info[0] and str(CAP) in info[0] and "dither recenter" in info[0]
    assert logs.at("warning") == []
    assert g._cap_clips == {"settle": 1, "guide": 0}


@pytest.mark.asyncio
async def test_an_over_cap_pulse_outside_a_settle_window_warns(monkeypatch):
    """Same pulse, no settle window (``_settle_open`` False and the engine not
    settling): still clipped to the cap, but it is a WARNING and not the
    expected-dither info line."""
    logs = _Logs(monkeypatch)
    tel = _Mount()
    g = _guider(tel, engine=_Engine(settling=False), settle_open=False)
    await g._pulse(_pair(dec=("south", 2658)))
    assert tel.pulses == [("south", CAP)]
    warn = logs.at("warning")
    assert len(warn) == 1 and "2658" in warn[0] and "outside" in warn[0]
    assert logs.at("info", "limited to the mount's") == []
    assert g._cap_clips == {"settle": 0, "guide": 1}


@pytest.mark.asyncio
async def test_the_first_frame_after_a_dither_is_a_settle_window_pulse(monkeypatch):
    """``dither()`` opens the ENGINE's window at once; the host's
    ``_settle_open`` only flips after this frame's dispatch. The recenter step
    this was built for is dispatched in exactly that gap and must not be
    called an anomaly."""
    logs = _Logs(monkeypatch)
    tel = _Mount()
    g = _guider(tel, engine=_Engine(settling=True), settle_open=False)
    await g._pulse(_pair(dec=("south", 2658)))
    assert tel.pulses == [("south", CAP)]
    assert len(logs.at("info", "limited to the mount's")) == 1
    assert logs.at("warning") == []
    assert g._cap_clips == {"settle": 1, "guide": 0}


@pytest.mark.asyncio
async def test_both_axes_and_the_single_axis_action_are_clipped(monkeypatch):
    """A pulse_pair clips each axis on its own; the single-axis ``pulse`` kind
    goes through the same limit; a pulse at or under the cap is untouched."""
    logs = _Logs(monkeypatch)
    tel = _Mount()
    g = _guider(tel, engine=_Engine(settling=False))
    await g._pulse(_pair(ra=("west", 1800), dec=("north", 500)))
    await g._pulse({"action": "pulse", "dir": "west", "ms": 3137})
    await g._pulse({"action": "pulse", "dir": "east", "ms": CAP})
    await g._pulse({"action": "pulse", "dir": "east", "ms": CAP - 1})
    assert tel.pulses == [("west", CAP), ("north", 500), ("west", CAP),
                          ("east", CAP), ("east", CAP - 1)]
    assert len(logs.at("warning")) == 2          # the RA 1800 and the 3137
    assert g._cap_clips == {"settle": 0, "guide": 2}


@pytest.mark.asyncio
async def test_each_settle_window_says_its_own_one_line(monkeypatch):
    """ONE info line per settle window, however many pulses it clips, and the
    next window gets its own. Driven through the real ``_sync_settle_window``
    so the reset is the code's, not the test's."""
    logs = _Logs(monkeypatch)
    tel = _Mount()
    engine = _Engine(settling=True)
    g = _guider(tel, engine=engine)
    # window 1: three clipped pulses, one line
    await g._pulse(_pair(ra=("west", 1400), dec=("south", 2658)))
    g._sync_settle_window({"action": "pulse_pair"})
    assert g._settle_open is True
    await g._pulse(_pair(dec=("north", 1200)))
    assert len(logs.at("info", "limited to the mount's")) == 1
    # the window closes
    engine.settling = False
    g._sync_settle_window({"action": "settle"})
    assert g._settle_open is False
    # window 2: its own line
    engine.settling = True
    await g._pulse(_pair(dec=("south", 1500)))
    g._sync_settle_window({"action": "pulse_pair"})
    assert len(logs.at("info", "limited to the mount's")) == 2
    assert logs.at("warning") == []
    assert [p for p in tel.pulses] == [("west", CAP), ("south", CAP),
                                       ("north", CAP), ("south", CAP)]
    assert g._cap_clips == {"settle": 4, "guide": 0}


@pytest.mark.parametrize("cap", ["missing", None, 0, -5, True, object(),
                                 float("nan"), float("inf"), 0.5])
@pytest.mark.asyncio
async def test_no_published_cap_is_a_no_op(monkeypatch, cap):
    """No cap (Alpaca, sim, PHD2's path): nothing is limited, said or counted,
    and a published value that is not a usable cap is no cap either: a Mock
    attribute must not become a 1 ms cap, NaN and infinity must not reach
    ``int()`` (which raises, and an error out of a pulse ends the guide loop),
    and 0.5 must not truncate to a cap of 0 ms that zeroes every pulse. The
    last three were added by the verifier of WP-93; under the mutant that
    drops the ``math.isfinite`` guard the NaN and infinity cases fail with
    ``ValueError: cannot convert float NaN to integer`` and ``OverflowError:
    cannot convert float infinity to integer``, and under the mutant that
    returns ``int(cap)`` unchecked the 0.5 case fails with every pulse
    rewritten to 0."""
    logs = _Logs(monkeypatch)
    tel = _NoCapMount() if cap == "missing" else _Mount(cap=cap)
    g = _guider(tel, settle_open=True)
    await g._pulse(_pair(ra=("west", 5000), dec=("south", 2658)))
    await g._pulse({"action": "pulse", "dir": "north", "ms": 9999})
    assert tel.pulses == [("west", 5000), ("south", 2658), ("north", 9999)]
    assert logs.lines == []
    assert g._cap_clips == {"settle": 0, "guide": 0}


@pytest.mark.asyncio
async def test_a_zero_or_negative_demand_passes_through(monkeypatch):
    """``ms <= 0`` is not an over-cap pulse and is handed over unchanged (the
    driver's own ``max(0, ms)`` decides what that means)."""
    logs = _Logs(monkeypatch)
    tel = _Mount()
    g = _guider(tel, settle_open=True)
    await g._pulse(_pair(ra=("west", 0), dec=("south", -40)))
    assert tel.pulses == [("west", 0), ("south", -40)]
    assert logs.lines == []


@pytest.mark.parametrize("ms", [0, 1, 999, 1000, 1001, 2658, 100_000])
@pytest.mark.asyncio
async def test_the_clip_is_the_value_the_driver_delivers_today(monkeypatch, ms):
    """The bytes on the wire must not change: the guider's clip equals
    ``ZwoAm5Telescope._capped_ms`` for the same demand, with the cap read from
    the driver's own published ``max_pulse_ms`` rather than a copied 1000."""
    am5 = object.__new__(zwo_am5.ZwoAm5Telescope)
    am5.name = "Synthetic AM5"
    monkeypatch.setattr(zwo_am5.bus, "log", lambda *a: None)
    tel = _Mount(cap=zwo_am5.ZwoAm5Telescope.max_pulse_ms)
    g = _guider(tel, settle_open=True)
    await g._pulse({"action": "pulse", "dir": "north", "ms": ms})
    assert tel.pulses == [("north", am5._capped_ms("north", ms))]


@pytest.mark.asyncio
async def test_a_dither_through_the_real_loop_says_one_line_per_window(monkeypatch):
    """The sequence the 2026-09 night logs show, through ``_guide_loop`` and
    ``_dispatch`` themselves: the first frame after ``dither()`` returns an
    over-cap recenter step while the engine is settling and the host flag is
    not yet open; the next frame clips again; the window then closes; a second
    dither does it once more. Two info lines, no warning, and every pulse the
    mount saw is at most the cap."""
    assert os.environ.get("ASTRODECK_FAST_TEST") == "1"
    logs = _Logs(monkeypatch)

    class _ScriptedEngine:
        def __init__(self, script):
            self.script = list(script)
            self.frames = 0
            self.settling = False

        def process(self, data, ts, exposure_s):
            self.frames += 1
            if self.script:
                action, self.settling = self.script.pop(0)
                return action
            self.settling = False
            return {"action": "idle"}

        def stats(self):
            return {"guiding": True, "settling": self.settling, "recent": []}

    rig = build_sim_rig()
    cam = rig["guide_camera"]
    await cam.connect()
    tel = _Mount()
    engine = _ScriptedEngine([
        (_pair(ra=("west", 1400), dec=("south", 2658)), True),   # first frame
        (_pair(dec=("north", 1200)), True),                      # same window
        ({"action": "idle"}, False),                             # window closes
        (_pair(dec=("south", 1500)), True),                      # second dither
        ({"action": "idle"}, False),
    ])
    g = NativeGuider(cam, tel, config={"exposure_s": 0.05}, profile_id=None)
    g._engine = engine
    g._active = True
    g._stop.clear()
    task = asyncio.create_task(g._guide_loop())
    try:
        deadline = time.monotonic() + 15
        while engine.frames < 6 and time.monotonic() < deadline:
            await asyncio.sleep(0.005)
        assert engine.frames >= 6, "the scripted frames never all ran"
    finally:
        g._stop.set()
        await asyncio.wait_for(task, 5)
    assert tel.pulses == [("west", CAP), ("south", CAP), ("north", CAP),
                          ("south", CAP)]
    assert len(logs.at("info", "limited to the mount's")) == 2
    assert logs.at("warning") == []
    assert g._cap_clips == {"settle": 4, "guide": 0}


@pytest.mark.asyncio
async def test_start_guiding_resets_the_session_counters(monkeypatch):
    """A fresh session starts its clip counts and its per-window latch at
    zero, next to the other settle bookkeeping ``start_guiding`` resets. The
    start is halted by a sentinel on its first await after those resets, so
    nothing but the resets is under test."""
    class _Halt(Exception):
        pass

    async def _halt():
        raise _Halt()

    g = _guider(_Mount())
    g._cap_clips = {"settle": 3, "guide": 1}
    g._settle_clip_said = True
    monkeypatch.setattr(g, "_read_guide_rates", _halt)
    with pytest.raises(_Halt):
        await g.start_guiding()
    assert g._cap_clips == {"settle": 0, "guide": 0}
    assert g._settle_clip_said is False
