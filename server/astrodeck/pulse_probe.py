# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The pulse probe (#1071): does a guide pulse leave the RA drive tracking?

The leading hypothesis for the runaway on pier east after a meridian flip is
that the AM5's emulated pulses (west is ``R2`` + ``:Mw#`` then ``:Qw#``, east
is ``:Td#`` then ``:Te#``) can leave the RA drive slow or stopped while
``:GAT#`` still reads tracking on. The night log cannot say, and the guider
needs a star to show it. This runs the pulses through the driver's real
``pulse_guide`` and measures the mount's own reported RA rate after each
stage with ``Telescope.probe_ra_drift``, so it works in daylight with no
camera:

  1.  rest                              probe
  2.  one west pulse of 1000 ms         probe
  3.  one east pulse of 1000 ms         probe
  4.  ten west pulses at 3.7 s cadence  probe   (3.7 s is the measured cycle)
  5.  ten east pulses, the same         probe
  6.  one north pulse                   probe
  7.  one south pulse                   probe
  8.  ten north pulses, the same        probe
  9.  ten south pulses, the same        probe
  10. rest                              probe

The Dec stages are there because the 2026-10-16 02:34 event hit the pulse
limit in Dec as well ("north 25, east 25"), and the 2026-08-21 walk was in Dec.
``expected_seconds`` computes the total from the constants below (about four
minutes); the route's note and the docs quote it.

A STAGE THAT SHOWS A STOPPED OR SLOW DRIVE NAMES THE PULSE THAT DID IT.
Stage 1 is the baseline, the last stage whether the mount recovers by itself.

EACH PULSE STAGE ALSO MEASURES THE STEP IT MADE. The emulated guide rate is 1x
sidereal, so a 1000 ms pulse should move the mount's model about one ``:GR#``
count and a ten-pulse train about ten. When the driver has
``measure_model_step`` the stage's pulses run through it and the stage gains
``step`` (the RA and Dec change across the pulses) and ``pulse_total_ms``:
whether the model moved by what was commanded, not only the rate afterwards.

WHAT THE PROBE READS IS THE MOUNT'S MODEL, NOT THE SKY. The AM5's reported
coordinates are its own bookkeeping of where it points. A probe that reads
about 1 sidereal is strong evidence that the firmware has stopped driving RA.
A probe that reads 0 does not show the axis is turning: compare it with the
camera drift in the guide flight recorder (disagreement points to the
mechanics, agreement to the firmware).

THE EAST STAGES TEST THE TRACKING PROBE ITSELF. East is a tracking suspend, and
#851 suggests the AM5's model does not count one. An east train whose step is
near 0, against about 150 arcsec commanded for ten one-second pulses at 1x
sidereal, means a tracking-probe reading of 0 says nothing about the drive.
This is also #851's pending bench walk.

THE OPERATOR'S TOOL, NOT THE NIGHT'S. It is refused while a run is going or
armed to start within ten minutes, while the guider is guiding, and while the
mount is parked or slewing: any of those would put motion inside a measuring
window. It runs as a background job in the ``pulse_probe`` lane and
``POST /api/mount/stop`` cancels it; the pulse in flight then stops the mount
itself (``pulse_guide``'s cancel path), and the partial result is still
written, with the stage that was running when it ended.

NO COORDINATE. Rates, steps, flags, pulse counts and the pier side. The RA and
Dec readings never leave the driver (``probe_ra_drift``,
``measure_model_step``).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from .events import bus, night_key
from .guide.flight_recorder import captures_dir
from .tracking_probe import PROBE_SECONDS, probe_line

#: Pulse length, count and cadence of the protocol.
PULSE_MS = 1000
TRAIN_PULSES = 10
#: Start to start, seconds: the measured guide cycle on this rig.
CADENCE_S = 3.7
#: A run armed to start inside this window is a reason to refuse.
ARMED_WITHIN_S = 600.0


def stage_plan(train: int) -> list[tuple[str, str | None, int]]:
    """``(name, direction, pulses)`` for each stage, in order. The RA stages
    first, then Dec, then a rest to see whether the mount recovers (#1071)."""
    return [("rest", None, 0),
            ("west x1", "west", 1), ("east x1", "east", 1),
            (f"west x{train}", "west", train),
            (f"east x{train}", "east", train),
            ("north x1", "north", 1), ("south x1", "south", 1),
            (f"north x{train}", "north", train),
            (f"south x{train}", "south", train),
            ("rest again", None, 0)]


def expected_seconds(probe_s: float | None = None, pulse_ms: int | None = None,
                     train: int | None = None,
                     cadence_s: float | None = None) -> float:
    """The protocol's duration in seconds, computed from its constants: one
    probe per stage, and per pulse stage ``n - 1`` cadences plus the last
    pulse. Excludes the few reads around each probe (milliseconds on the
    wire). The route's note and the docs quote this."""
    probe_s = PROBE_SECONDS if probe_s is None else probe_s
    pulse_ms = PULSE_MS if pulse_ms is None else pulse_ms
    train = TRAIN_PULSES if train is None else train
    cadence_s = CADENCE_S if cadence_s is None else cadence_s
    total = 0.0
    for _name, _direction, n in stage_plan(train):
        total += probe_s
        if n:
            total += (n - 1) * max(cadence_s, pulse_ms / 1000.0) \
                + pulse_ms / 1000.0
    return total


def expected_minutes_words() -> str:
    """``about four minutes``, from ``expected_seconds`` (#1071)."""
    m = max(1, round(expected_seconds() / 60.0))
    names = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
             6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
    return f"about {names.get(m, str(m))} minute{'s' if m != 1 else ''}"


class PulseProbeAborted(Exception):
    """The mount parked or slewed under the protocol; the reason is words."""


async def refusal(hub, engine, tel, *,
                  recovering: bool = False) -> str | None:
    """Why the pulse probe cannot run now, in a plain sentence, or None.
    Checked before anything is spawned, so a refusal costs the rig nothing.
    ``recovering`` is the resume service's own flag (its ladder slews)."""
    if not getattr(tel, "can_probe_ra_drift", False):
        return ("this mount's driver cannot measure its own RA rate, so the "
                "pulse probe has nothing to read")
    if engine.running:
        return ("a sequence is running; the pulse probe would put pulses "
                "inside its exposures")
    try:
        from .sequence import resume_arm
        from .sequence.session import session_store
        if recovering:
            return ("a run is being recovered and will slew the mount; "
                    "wait for it to finish or abort it")
        armed = await asyncio.to_thread(session_store.armed)
        if armed is not None:
            from .config import config_store
            from .site_gate import site_is_set
            site = getattr(hub, "site", None) or {}
            cfg = config_store.cfg()
            twilight = cfg.safety.twilight_deg if cfg else -12.0
            now = time.time()
            soon = (not site_is_set(site)
                    or resume_arm.window_open(armed, site, twilight, now)
                    or resume_arm.window_open(armed, site, twilight,
                                              now + ARMED_WITHIN_S))
            if soon:
                return ("a session is armed and may start within ten "
                        "minutes; the pulse probe would move the mount "
                        "under it")
    except Exception:                           # noqa: BLE001 - fail closed
        return ("could not tell whether a run is armed to start; the pulse "
                "probe will not run until that can be read")
    g = getattr(hub, "guider", None)
    try:
        if g is not None and getattr(g, "connected", False) \
                and await g.is_active():
            return "the guider is guiding; stop it before the pulse probe"
    except Exception:                           # noqa: BLE001 - fail closed
        return "the guider's state could not be read; stop it first"
    try:
        if await tel.is_parked():
            return "the mount is parked; unpark it before the pulse probe"
        if await tel.is_slewing() or _lane_live(hub, "goto"):
            return "the mount is slewing; wait for it to settle"
    except Exception as e:                      # noqa: BLE001 - fail closed
        return (f"the mount's state could not be read ({type(e).__name__}); "
                f"the pulse probe will not run")
    if _lane_live(hub, "pulse_probe"):
        return "a pulse probe is already running"
    return None


def _lane_live(hub, name: str) -> bool:
    t = getattr(hub, "_busy", {}).get(name)
    return t is not None and not t.done()


async def _ensure_ok(tel, parked_too: bool) -> None:
    if await tel.is_slewing():
        raise PulseProbeAborted("the mount started slewing")
    if parked_too and await tel.is_parked():
        raise PulseProbeAborted("the mount was parked")


async def _probe(tel, name: str, seconds: float) -> dict:
    try:
        r = await tel.probe_ra_drift(seconds)
    except asyncio.CancelledError:
        raise
    except Exception as e:                      # noqa: BLE001 - recorded
        bus.log("warning", f"pulse probe ({name}): the probe failed: "
                           f"{type(e).__name__}", "mount")
        return {"error": type(e).__name__}
    if not isinstance(r, dict):
        return {"error": "no result"}
    bus.log("info", probe_line(f"pulse probe: {name}", r, None), "mount")
    return r


async def _pulses(tel, direction: str, n: int, pulse_ms: int,
                  cadence_s: float, progress: dict | None = None) -> int:
    """``n`` pulses, one every ``cadence_s`` from start to start. ``progress``
    (``{"sent": n}``) is updated after each pulse for the caller."""
    sent = 0
    t_next = time.monotonic()
    for i in range(n):
        await _ensure_ok(tel, parked_too=False)
        await tel.pulse_guide(direction, pulse_ms)
        sent += 1
        if progress is not None:
            progress["sent"] = sent
        t_next += cadence_s
        wait = t_next - time.monotonic()
        if i < n - 1 and wait > 0:
            await asyncio.sleep(wait)
    return sent


async def _run_stage(tel, direction: str, n: int, pulse_ms: int,
                     cadence_s: float, progress: dict
                     ) -> tuple[int, dict | None]:
    """The stage's pulses, through ``measure_model_step`` when the driver has
    it. Returns ``(pulses sent, step or None)``. ``progress["sent"]`` is kept
    current so a stop mid-stage can say how far it got (#1071)."""
    async def action() -> None:
        await _pulses(tel, direction, n, pulse_ms, cadence_s, progress)

    if getattr(tel, "can_measure_model_step", False):
        step = await tel.measure_model_step(action)
        return progress["sent"], (step if isinstance(step, dict) else None)
    await action()
    return progress["sent"], None


def write_result(event: str, payload: dict) -> str:
    """``captures/guide/<night>/<HHMMSS>-<event>.json``, written now, on the
    caller's thread. Returns the path relative to ``captures``."""
    now = time.time()
    night = night_key(now)
    folder = captures_dir() / night
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H%M%S", time.localtime(now))
    path = folder / f"{stamp}-{event}.json"
    k = 2
    while path.exists():
        path = folder / f"{stamp}-{event}-{k}.json"
        k += 1
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return f"guide/{night}/{path.name}"


def _summary(steps: list[dict]) -> str:
    parts = []
    for st in steps:
        p = st.get("probe") or {}
        if "sidereal_fraction" in p:
            parts.append(f"{st['name']} {p['sidereal_fraction']:+.2f}")
        else:
            parts.append(f"{st['name']} no reading")
    return "; ".join(parts) + " (sidereal fractions)"


async def run_protocol(tel, *, probe_s: float | None = None,
                       pulse_ms: int | None = None, train: int | None = None,
                       cadence_s: float | None = None) -> dict:
    """Run the six stages on ``tel`` and return the result dict (also written
    to the night log and to ``captures/guide/<night>/<HHMMSS>-pulse-probe.json``).
    A cancel (``POST /api/mount/stop``) or a parked or slewing mount ends it
    early; what was measured is still written, with ``aborted`` saying why.
    """
    # Read at call time, so a test shrinks the protocol by patching the module.
    probe_s = PROBE_SECONDS if probe_s is None else probe_s
    pulse_ms = PULSE_MS if pulse_ms is None else pulse_ms
    train = TRAIN_PULSES if train is None else train
    cadence_s = CADENCE_S if cadence_s is None else cadence_s
    result: dict = {"started_at": round(time.time(), 3), "steps": [],
                    "pier_side": "unknown", "completed": False,
                    "aborted": None, "tracking_at_start": None,
                    "probe_s": probe_s, "pulse_ms": pulse_ms,
                    "train_pulses": train, "cadence_s": cadence_s,
                    "interrupted": None}
    plan = stage_plan(train)
    result["expected_seconds"] = round(expected_seconds(
        probe_s, pulse_ms, train, cadence_s), 1)
    bus.log("info", f"pulse probe started: {len(plan)} stages, a "
                    f"{probe_s:.0f} s probe after each, pulses of {pulse_ms} "
                    f"ms at a {cadence_s:.1f} s cadence in the trains",
            "mount")
    cancelled = False
    progress = {"sent": 0}
    try:
        for name, direction, n in plan:
            await _ensure_ok(tel, parked_too=True)
            sent = 0
            step = None
            progress["sent"] = 0
            # Recorded before the stage runs, so a stop in the middle of it
            # still says which stage and how many pulses went out.
            result["interrupted"] = {"name": name, "pulses": 0}
            if direction is not None:
                try:
                    sent, step = await _run_stage(
                        tel, direction, n, pulse_ms, cadence_s, progress)
                finally:
                    result["interrupted"]["pulses"] = progress["sent"]
            probe = await _probe(tel, name, probe_s)
            result["steps"].append({"name": name, "direction": direction,
                                    "pulses": sent,
                                    "pulse_total_ms": sent * pulse_ms,
                                    "step": step, "probe": probe})
            result["interrupted"] = None
            if result["tracking_at_start"] is None and probe:
                result["tracking_at_start"] = probe.get("tracking_flag_before")
                if probe.get("tracking_flag_before") is False:
                    bus.log("warning", "pulse probe: tracking is off at the "
                            "start; running anyway, because that is data too",
                            "mount")
            if probe.get("pier_side") in ("east", "west"):
                result["pier_side"] = probe["pier_side"]
        result["completed"] = True
    except asyncio.CancelledError:
        cancelled = True
        result["aborted"] = "stopped by request"
        raise
    except PulseProbeAborted as e:
        result["aborted"] = str(e)
        bus.log("warning", f"pulse probe ended early: {e}", "mount")
    except Exception as e:                      # noqa: BLE001 - recorded
        result["aborted"] = f"{type(e).__name__}"
        bus.log("error", f"pulse probe failed: {e}", "mount")
    finally:
        try:
            where = write_result("pulse-probe", result)
            bus.log("warning" if cancelled else "info",
                    f"pulse probe {'stopped' if cancelled else 'finished'} "
                    f"(pier side {result['pier_side']}): "
                    f"{_summary(result['steps'])}; wrote {where}", "mount")
        except Exception as e:                  # noqa: BLE001
            bus.log("warning", f"pulse probe result could not be written: "
                               f"{type(e).__name__}", "mount")
    return result
