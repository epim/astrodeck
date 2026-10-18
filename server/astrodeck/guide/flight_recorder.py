# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The guide loop's flight recorder (#1071).

WHY. The night log keeps the guider's sentences ("5 in a row at the 1000 ms
limit, west, 42 to 145 arcsec") and none of the cycles behind them: not the
star's position, not the pulses sent, not how long a cycle took. The runaway
on pier east after a flip could not be told apart from a mount that stopped
tracking, because the evidence was gone by the time the sentence was written.

WHAT. A ring of the last ``CYCLES`` guide-loop cycles, and the calibration
walk's steps, held in memory. On the events that matter (a calibration
discarded, the star lost, a re-acquire, five pulses in a row at the limit, a
guided frame that carries the "pulses at the limit" note) the ring is written
to ``captures/guide/<night>/<HHMMSS>-<event>.json`` and ONE info line goes to
the night log naming the file and summarising it.

PRIVACY. Everything recorded is a camera-frame pixel, an error in arcsec or
px, a pulse (direction, ms) or a time. No mount coordinate, no declination, no
hour angle and nothing derived from the site is read here, and the calibration
is copied through a short whitelist (``_CAL_KEYS``) rather than whole, because
the calibration dict carries the declination it was walked at.

OFF THE EVENT LOOP. The write goes to the default executor when a loop is
running (the night log has had its own thread since #878 for the same
reason); the snapshot it writes is copied on the loop first, so the loop's
own state is never read from the thread.

CYCLE TIMES TO WALL TIME. Each cycle's ``t`` is ``time.monotonic()`` and the
file has two stamps taken together at the dump: ``written_at`` (wall) and
``mono_at_write`` (monotonic). For any cycle,

    wall = written_at - (mono_at_write - t)

so a cycle can be laid against the night log, the frame headers or the
camera-drift record. (``dt`` is the gap since the previous cycle.)

BOUNDED. At most ``MAX_FILES_PER_NIGHT`` files per night; past that one
warning, then silence. A flickering star at 3 s a cycle would otherwise write
a file per frame.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import math
import os
import time
from collections import deque
from pathlib import Path

from ..events import bus, night_key

#: Cycles kept. 60 is about three minutes at the measured 3.7 s cycle.
CYCLES = 60
#: Files written per night before the recorder goes quiet.
MAX_FILES_PER_NIGHT = 200
#: Cycles the RA-error trend in the log line is fitted over.
TREND_CYCLES = 10
#: A gap this long between two cycles is a stop and a restart, not a cycle
#: time, and is left out of the mean.
_CYCLE_GAP_S = 30.0
#: Calibration-walk steps kept.
_WALK_MAX = 400
#: The only calibration fields copied into a file. None is a position.
_CAL_KEYS = ("x_rate", "y_rate", "x_angle", "y_angle", "y_angle_error",
             "is_valid")


def _round(v, n: int = 3):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, n) if math.isfinite(f) else None


def _xy(v) -> list | None:
    try:
        return [_round(v[0], 2), _round(v[1], 2)]
    except (TypeError, IndexError, KeyError):
        return None


def captures_dir() -> Path:
    """``captures/guide``, resolved live so a CAPTURE_DIR monkeypatch wins."""
    from .. import hub as _hubmod
    return _hubmod.CAPTURE_DIR / "guide"


class FlightRecorder:
    """The ring, the walk and the writer. Every public method is total: a
    recorder that raised would take the guide loop down with it."""

    def __init__(self) -> None:
        self.ring: deque[dict] = deque(maxlen=CYCLES)
        self.walk: list[dict] = []
        #: Files known per night: counted from the directory the first time a
        #: night is written to, then by this process.
        self._files: dict[str, int] = {}
        self._warned: set[str] = set()
        self._pending: set[asyncio.Future] = set()
        self._taken: set[Path] = set()
        self._last_t: float | None = None

    # ------------------------------------------------------------ recording

    def begin_cycle(self, *, star=None, lock=None, snr=None, err_px=None,
                    scale: float = 1.0, scale_known: bool = False,
                    action: str = "", asked=None, pier: str = "unknown",
                    measured: bool = False) -> dict | None:
        """Open this cycle's record and put it in the ring. ``err_px`` is the
        engine's (ra, dec) in px for a measured frame, else None. The caller
        adds the pulses as they are sent (``add_pulse``). Returns the record
        so the caller can hand it back, or None on any failure."""
        try:
            now = time.monotonic()
            rec = {
                "t": round(now, 3),
                "dt": (round(now - self._last_t, 3)
                       if self._last_t is not None else None),
                "star_px": _xy(star) if star is not None else None,
                "lock_px": _xy(lock) if lock is not None else None,
                "measured": bool(measured),
                "ra_err_px": None, "dec_err_px": None,
                "ra_err_arcsec": None, "dec_err_arcsec": None,
                "action": str(action),
                "asked": asked or [],
                "pulses": [],
                "snr": _round(snr, 1),
                "hfr": None,            # the engine does not publish one
                "pier_side": pier,
            }
            if err_px is not None:
                rec["ra_err_px"] = _round(err_px[0])
                rec["dec_err_px"] = _round(err_px[1])
                if scale_known and scale > 0:
                    rec["ra_err_arcsec"] = _round(err_px[0] * scale)
                    rec["dec_err_arcsec"] = _round(err_px[1] * scale)
            self._last_t = now
            self.ring.append(rec)
            self._cur = rec
            return rec
        except Exception:                       # noqa: BLE001 - total
            self._cur = None
            return None

    _cur: dict | None = None

    def add_pulse(self, direction: str, asked_ms: int, sent_ms: int,
                  at_limit: bool) -> None:
        """One pulse the guider sent this cycle. ``capped`` is the mount's
        cap shortening it; ``at_limit`` is the guider's own axis limit."""
        try:
            if self._cur is None:
                return
            self._cur["pulses"].append({
                "dir": str(direction), "asked_ms": int(asked_ms),
                "sent_ms": int(sent_ms), "at_limit": bool(at_limit),
                "capped": int(sent_ms) < int(asked_ms)})
        except Exception:                       # noqa: BLE001 - total
            pass

    def begin_walk(self) -> None:
        self.walk = []

    def walk_step(self, n: int, leg, direction, ms: int, star_px) -> None:
        """A calibration step. ``star_px`` is where the star stood in the
        frame that asked for it, which is where the PREVIOUS step left it."""
        try:
            if self.walk:
                self.walk[-1]["star_after_px"] = _xy(star_px)
            self.walk.append({"step": int(n), "leg": leg, "dir": direction,
                              "ms": int(ms), "star_before_px": _xy(star_px),
                              "star_after_px": None})
            del self.walk[:-_WALK_MAX]
        except Exception:                       # noqa: BLE001 - total
            pass

    def walk_end(self, star_px) -> None:
        with contextlib.suppress(Exception):
            if self.walk and self.walk[-1]["star_after_px"] is None:
                self.walk[-1]["star_after_px"] = _xy(star_px)

    # -------------------------------------------------------------- summary

    def summary(self, unit: str = "arcsec") -> str:
        """The numbers the log line carries."""
        cycles = list(self.ring)
        n = len(cycles)
        key = "ra_err_arcsec" if unit == "arcsec" else "ra_err_px"
        errs = [c[key] for c in cycles if c.get(key) is not None]
        errs = errs[-TREND_CYCLES:]
        slope = None
        if len(errs) >= 2:
            xs = range(len(errs))
            mx = sum(xs) / len(errs)
            my = sum(errs) / len(errs)
            den = sum((x - mx) ** 2 for x in xs)
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, errs)) / den
        dts = [c["dt"] for c in cycles
               if c.get("dt") is not None and c["dt"] <= _CYCLE_GAP_S]
        mean_dt = sum(dts) / len(dts) if dts else None
        pulses = [p for c in cycles for p in c["pulses"]]
        share = (sum(1 for p in pulses if p["at_limit"]) / len(pulses)
                 if pulses else None)
        trend = (f"RA error trend {slope:+.1f} {unit}/cycle over the last "
                 f"{len(errs)}" if slope is not None
                 else "RA error trend unavailable")
        cyc = (f"mean cycle {mean_dt:.1f} s" if mean_dt is not None
               else "mean cycle unavailable")
        lim = (f"{share * 100:.0f}% of {len(pulses)} pulses at the limit"
               if share is not None else "no pulses sent")
        return f"{n} cycles, {trend}, {cyc}, {lim}"

    # ---------------------------------------------------------------- write

    def _count(self, night: str, folder: Path) -> int:
        if night not in self._files:
            n = 0
            with contextlib.suppress(OSError):
                n = sum(1 for f in os.listdir(folder) if f.endswith(".json"))
            self._files[night] = n
        return self._files[night]

    def dump(self, event: str, *, scale: float = 1.0,
             scale_known: bool = False, pier: str = "unknown",
             calibration: dict | None = None) -> str | None:
        """Write the ring and the walk to a file for ``event`` and log the one
        line. Returns the file's path relative to ``captures``, or None when
        nothing was written (cap, or a failure that was logged). Never raises.
        """
        try:
            return self._dump(event, scale, scale_known, pier, calibration)
        except Exception as e:                  # noqa: BLE001 - total
            with contextlib.suppress(Exception):
                bus.log("warning",
                        f"native guider: flight recorder could not write "
                        f"({type(e).__name__})", "guide")
            return None

    def _dump(self, event, scale, scale_known, pier, calibration):
        if not self.ring and not self.walk:
            return None
        now = time.time()
        mono = time.monotonic()         # the same instant, for ``t`` (above)
        night = night_key(now)
        folder = captures_dir() / night
        if self._count(night, folder) >= MAX_FILES_PER_NIGHT:
            if night not in self._warned:
                self._warned.add(night)
                bus.log("warning",
                        f"native guider: flight recorder has written "
                        f"{MAX_FILES_PER_NIGHT} files tonight; no more will "
                        f"be written until the next night", "guide")
            return None
        stamp = time.strftime("%H%M%S", time.localtime(now))
        safe = "".join(c if c.isalnum() or c == "-" else "-"
                       for c in str(event))[:40] or "event"
        name = f"{stamp}-{safe}.json"
        path = folder / name
        k = 2
        # A name an earlier dump has taken but whose write is still in the
        # executor does not exist on disk yet: two events in one second would
        # otherwise share a file.
        while path.exists() or path in self._taken:
            path = folder / f"{stamp}-{safe}-{k}.json"
            k += 1
        self._taken.add(path)
        unit = "arcsec" if scale_known and scale > 0 else "px"
        cal = {}
        if isinstance(calibration, dict):
            cal = {key: (_round(calibration[key], 5)
                         if key != "is_valid" else bool(calibration[key]))
                   for key in _CAL_KEYS if key in calibration}
        payload = {
            "event": str(event), "night": night,
            "written_at": round(now, 3),
            "mono_at_write": round(mono, 3),
            "unit": unit, "image_scale_arcsec_px":
                _round(scale, 4) if scale_known else None,
            "pier_side_as_guider_believes": pier,
            "calibration": cal,
            "calibration_walk": copy.deepcopy(self.walk),
            "cycles": copy.deepcopy(list(self.ring)),
        }
        line = (f"native guider flight recorder ({event}): wrote "
                f"guide/{night}/{path.name}: {self.summary(unit)}")
        self._files[night] = self._files.get(night, 0) + 1
        self._submit(path, payload)
        bus.log("info", line, "guide")
        return f"guide/{night}/{path.name}"

    def _submit(self, path: Path, payload: dict) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._write(path, payload)
            return
        fut = loop.run_in_executor(None, self._write, path, payload)
        self._pending.add(fut)

        def _done(f: asyncio.Future) -> None:
            self._pending.discard(f)
            if f.cancelled():
                return
            exc = f.exception()
            if exc is not None:
                bus.log("warning",
                        f"native guider: flight recorder write failed "
                        f"({type(exc).__name__})", "guide")
        fut.add_done_callback(_done)

    @staticmethod
    def _write(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    async def flush(self) -> None:
        """Wait for every write in flight (tests, shutdown)."""
        while self._pending:
            await asyncio.gather(*list(self._pending),
                                 return_exceptions=True)
