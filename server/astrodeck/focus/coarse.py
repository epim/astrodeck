"""Coarse focus: get close enough that autofocus can take over.

Autofocus fits a V-curve of star HFR, so it needs STARS at the start. A badly
defocused rig has none — on 2026-07-31 this one showed ~880px annuli with the
secondary obstruction and four spider vanes plainly visible, and both star
detectors failed on them in opposite directions (one reported 1393 ring
fragments as stars, the other correctly reported 0).

So this routine does not count stars. It measures how BIG the blob is, which
has an answer everywhere from the end of the travel to perfect focus.

Three things it deliberately does NOT do:

* it does not fit a curve — the V-curve autofocus does that far better, and this
  hands over the moment the blob is small enough to be a star;
* it does not trust ``max_position`` — the EAF here reports 600000 while the
  usable travel is a small fraction of it, so the search LEARNS the real limits
  from moves that fail and narrows its range;
* it does not walk a fixed grid — a defocus annulus grows linearly with distance
  from focus, so two measurements extrapolate straight to the answer.
"""
from __future__ import annotations

import asyncio
import contextlib

from ..devices.base import Camera, DeviceError, Focuser
from ..events import bus
from ..imaging.defocus import HANDOVER_R80_PX, measure_blob
from .autofocus import AutofocusResult
from .search import Probe, decide

#: Wall-clock cap. A search that cannot finish is worse than one that gives up:
#: the mount is tracking and the night is running.
DEFAULT_TIMEOUT_S = 900.0

#: Most probes worth taking before admitting defeat.
DEFAULT_MAX_PROBES = 12


async def run_coarse_focus(camera: Camera, focuser: Focuser, *,
                           exposure_s: float = 6.0, gain: int = 300,
                           binning: int = 2, span: int | None = None,
                           stops: int = DEFAULT_MAX_PROBES,
                           timeout_s: float = DEFAULT_TIMEOUT_S,
                           expose_guard=None,
                           hfr_method: str | None = None) -> AutofocusResult:
    """Shrink the defocus blob until autofocus can take over."""
    start_pos = await focuser.get_position()
    lo, hi = 0, int(getattr(focuser, "max_position", 0) or 0)
    if hi <= 0:
        raise DeviceError("focuser reports no usable travel")
    if span and span > 0:
        lo = max(lo, start_pos - span // 2)
        hi = min(hi, start_pos + span // 2)

    async def _expose():
        guard = expose_guard("coarse-focus") if expose_guard is not None \
            else contextlib.nullcontext()
        async with guard:
            return await camera.expose(exposure_s, gain, 30, binning=binning)

    async def _probe_here(pos: int) -> Probe | None:
        frame = await _expose()
        m = await asyncio.to_thread(measure_blob, frame.data)
        if m is None:
            bus.log("warning", f"coarse focus: nothing measurable at {pos}", "focus")
            return None
        bus.log("info",
                f"coarse focus: blob {m.r80:.0f}px (peak SNR {m.snr:.0f}) at {pos}",
                "focus")
        return Probe(pos, m.r80)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, float(timeout_s))
    history: list[Probe] = []

    bus.publish("focus", state="running", points=[], best=None,
                message="coarse focus: measuring how far out we are")
    bus.log("info",
            f"coarse focus: searching {lo}..{hi} from {start_pos}, "
            f"{exposure_s:g}s at gain {gain} bin {binning}, "
            f"handing over below {HANDOVER_R80_PX:g}px", "focus")

    def _publish() -> None:
        if not history:
            return
        bus.publish("focus", state="running", best=None,
                    points=[{"position": p.position, "hfr": p.r80, "sigma": 0.0}
                            for p in history],
                    message=(f"coarse focus: blob {history[-1].r80:.0f}px at "
                             f"{history[-1].position}"))

    try:
        first = await _probe_here(start_pos)
        if first is None:
            msg = ("nothing bright enough to measure anywhere in the frame — "
                   "check the sky, the cover, and that the camera is exposing.")
            bus.publish("focus", state="failed", points=[], best=None, message=msg)
            bus.log("warning", f"coarse focus: {msg}", "focus")
            return AutofocusResult(False, start_pos, None, [], msg)
        history.append(first)
        _publish()

        while True:
            if loop.time() > deadline:
                best = min(history, key=lambda p: p.r80)
                with contextlib.suppress(Exception):
                    await focuser.move_to(best.position)
                msg = (f"coarse focus ran out of time; best blob was "
                       f"{best.r80:.0f}px at {best.position}")
                bus.publish("focus", state="failed", points=[], best=None,
                            message=msg)
                bus.log("warning", msg, "focus")
                return AutofocusResult(False, best.position, None, [], msg)

            d = decide(history, lo, hi, max_probes=stops)

            if d.done:
                pos = history[-1].position
                msg = (f"blob is down to {history[-1].r80:.0f}px at {pos} — "
                       "small enough to autofocus from. Run autofocus now.")
                bus.publish("focus", state="idle", points=[], best=pos, message=msg)
                bus.log("info", f"coarse focus: {msg}", "focus")
                return AutofocusResult(True, pos, None, [], msg)

            if d.give_up:
                if d.move_to is not None:
                    with contextlib.suppress(Exception):
                        await focuser.move_to(d.move_to)
                tally = ", ".join(f"{p.position}:{p.r80:.0f}px" for p in history)
                msg = (f"{d.give_up}. Blob by position — {tally}. The focuser may "
                       "not reach focus over its usable travel.")
                bus.publish("focus", state="failed", points=[], best=None,
                            message=msg)
                bus.log("warning", f"coarse focus: {msg}", "focus")
                return AutofocusResult(False, d.move_to or history[-1].position,
                                       None, [], msg)

            target = d.move_to
            if target is None:                    # cannot happen; be explicit
                raise DeviceError("coarse focus: no next position decided")
            bus.log("info", f"coarse focus: {d.why} -> {target}", "focus")
            try:
                await focuser.move_to(target)
            except DeviceError as e:
                # The focuser could not get there. That is INFORMATION: it found
                # a real limit that max_position did not describe. Narrow the
                # searchable range to what is actually reachable and carry on —
                # learning the travel is the point, and aborting would throw the
                # discovery away.
                here = await focuser.get_position()
                if target > here:
                    hi = min(hi, here)
                else:
                    lo = max(lo, here)
                bus.log("warning",
                        f"coarse focus: cannot reach {target} ({e}); "
                        f"searchable range narrowed to {lo}..{hi}", "focus")
                if hi - lo < 2:
                    msg = (f"the focuser can only reach {lo}..{hi} — not enough "
                           "travel to find focus. It is probably at a mechanical "
                           "limit, or the drawtube or focus lock is jammed.")
                    bus.publish("focus", state="failed", points=[], best=None,
                                message=msg)
                    bus.log("error", f"coarse focus: {msg}", "focus")
                    return AutofocusResult(False, here, None, [], msg)
                # Record where we actually are so the next decision is not made
                # against a position the focuser never reached.
                history.append(Probe(here, history[-1].r80))
                continue

            p = await _probe_here(target)
            # Measurable-nowhere is different from measurable-and-large: carry
            # the previous size rather than feeding a fabricated number into the
            # extrapolation, which would send the search somewhere invented.
            history.append(p if p is not None else Probe(target, history[-1].r80))
            _publish()

    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await focuser.move_to(start_pos)
        bus.publish("focus", state="idle", points=[], best=None,
                    message="coarse focus cancelled")
        raise
    except Exception as e:                      # noqa: BLE001
        with contextlib.suppress(Exception):
            await focuser.move_to(start_pos)
        bus.publish("focus", state="failed", points=[], best=None, message=str(e))
        raise
