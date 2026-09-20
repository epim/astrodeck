"""Polar alignment session.

Drives NINA's Three-Point Polar Alignment (TPPA) over its ``/tppa`` websocket
on the live rig, or a simulator otherwise, and publishes a single ``polar``
event the UI's reticle consumes. Errors are normalized to arcminutes.
"""
from __future__ import annotations

import asyncio
import json
import math
import random
import time
from typing import Any

from ..config import FrameSettingsConfig, frames_payload, publish_frames, \
    set_frame_settings
from ..devices.nina import pick
from ..devices.sim import _sim_delay
from ..events import bus
from ..providers import resolve

# NINA's TPPA AzimuthError/AltitudeError/TotalError are doubles in DEGREES;
# we present arcminutes. (Single constant — easy to flip if a live TPPA run
# shows otherwise.)
_DEG_TO_MIN = 60.0

# How often a paused driver re-checks the flag. Short enough that Resume feels
# instant; it is also the ONLY await point a paused driver has, so it is the
# cancellation point ``stop()`` relies on to unwind a paused session.
_PAUSE_POLL_S = 0.1


async def wait_if_paused(session: Any) -> None:
    """Block while ``session`` is paused (the user hit Pause mid-run).

    The one pause primitive both first-party drivers poll — the native TPPA
    engine (``polar/native.py``) and the simulator below. Kept module-level and
    tolerant of a stub ``session`` (``getattr`` default) because the native
    driver is driven with fake sessions in tests. ``stop()`` cancels the driver
    task, so the sleep here is what lets a paused session still unwind.

    Arriving here is ALSO the only evidence anybody has that the rig has
    actually stopped: a driver only reaches a checkpoint between its steps, with
    no exposure open and no slew outstanding (``tel.slew`` returns when the
    mount stops reporting ``slewing``). So the first arrival for a given pause
    is what promotes the session from "pausing" to "paused" — see
    :meth:`PolarAlignSession._ack_pause_reached`. Nothing else may claim it:
    ``pause()`` itself only raises the flag, and the 12° RA rotation it lands in
    the middle of runs on for another 5-15 s.
    """
    if not getattr(session, "_native_paused", False):
        return
    ack = getattr(session, "_ack_pause_reached", None)
    if callable(ack):
        ack()
    while getattr(session, "_native_paused", False):
        await asyncio.sleep(_PAUSE_POLL_S)


class PolarAlignSession:
    def __init__(self, hub: Any):
        self.hub = hub
        self._task: asyncio.Task | None = None
        self._ws: Any = None
        # The imaging settings the NATIVE driver's solve frames use, mutable at
        # any time (a PUT mid-run applies from the next frame — that is the
        # point: when solves fail behind thin cloud, the fix is a longer
        # exposure NOW, not after abandoning the session). NINA and the
        # simulator ignore these.
        #
        # THEY NO LONGER LIVE HERE. Until 2026-08-08 this was a private dict on
        # the session: it died with the process, no client ever read it back
        # (``GET /api/polar/solve-settings`` existed and nothing called it), and
        # it was published as a field of the ``polar`` event — which ``start()``
        # resets to ``_idle()``, so beginning an alignment wiped the numbers off
        # every Align screen while the engine went on using them. It is now the
        # ``solve`` scope of the persisted frame settings (config.py), read
        # through the property below so this class still owns the vocabulary.
        # Pause flag polled by the FIRST-PARTY drivers — the native TPPA engine
        # (``polar/native.py``) and ``_run_sim`` below — via ``wait_if_paused``.
        # NINA has its own mechanism (a ws "pause-alignment" action), so this
        # stays False for it and changes nothing about its behavior.
        self._native_paused = False
        # Has a driver ARRIVED at ``wait_if_paused`` since the current pause was
        # requested? That, not the POST returning, is when the mount has stopped
        # — it is the difference between "pausing" and "paused" on the screen of
        # someone with a hand on an altitude bolt.
        self._pause_acked = False
        self.state: dict[str, Any] = self._idle()

    @staticmethod
    def _idle() -> dict[str, Any]:
        return {"state": "idle", "az_error": 0.0, "alt_error": 0.0,
                "total_error": 0.0, "progress": 0.0, "message": "", "source": None,
                # what the run is doing THIS second (exposing | solving | None).
                # Published by the native driver around each frame so the panel
                # can show that work is happening without the operator scrolling
                # to the log — a solve can take 15 s and used to look like a hang.
                "activity": None}

    #: The native solve frame's imaging defaults — the values that were
    #: hardcoded at the capture call until 2026-08-07. ``filter`` None means
    #: "leave the wheel where it is", which is what the code always did.
    #: Derived from the config model so there is ONE copy of each number
    #: (``polar/native.py`` used to keep a second one).
    SOLVE_DEFAULTS: dict[str, Any] = FrameSettingsConfig().solve.model_dump()

    @property
    def solve_settings(self) -> dict[str, Any]:
        """The effective solve-frame settings — the persisted ``solve`` scope.
        Always complete, so a reader never needs a guard."""
        return frames_payload()["solve"]

    def set_solve_settings(self, **kw: Any) -> dict[str, Any]:
        """Merge operator-set solve settings (None values clear back to the
        default) and announce them on the ``frames`` event, so every client
        renders the same numbers. Takes effect on the next frame; safe mid-run.

        The announcement is its OWN event, not a field on ``polar``: a setting
        outlives the session that reads it, and publishing it as session state
        meant ``start()`` erased it from every screen.
        """
        patch = {k: v for k, v in kw.items() if k in self.SOLVE_DEFAULTS}
        eff = set_frame_settings("solve", patch)
        publish_frames(getattr(self.hub, "guider", None) if self.hub else None)
        return eff

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _publish(self, **kw: Any) -> None:
        # An explicit Pause is the USER's state; a driver may not downgrade it.
        # The native driver publishes state:"running" on every solve it had
        # already started, which landed AFTER pause() wrote the pause state and
        # flipped the UI's Resume button back to Pause — so the run looked live
        # while it was, in fact, waiting on the flag. Drop only the "running"
        # key; every other field (progress / az_error / *_direction / message)
        # still streams.
        #
        # ONLY "running". "pausing" and "paused" are the pause's own two states
        # and have to get through — "paused" in particular is published from
        # ``_ack_pause_reached`` while the flag is up, which is the whole point
        # — and the TERMINAL states must still land or a session that finishes
        # while paused would stick there forever with nothing left to clear it.
        if self._native_paused and kw.get("state") == "running":
            kw = {k: v for k, v in kw.items() if k != "state"}
        self.state = {**self.state, **kw}
        if "az_error" in kw or "alt_error" in kw:
            self.state["reading_ts"] = time.time()
            self.state["total_error"] = round(
                math.hypot(self.state["az_error"], self.state["alt_error"]), 2)
        bus.publish("polar", **self.state)

    # ----------------------------------------------------------------- control

    async def start(self) -> None:
        if self.running:
            raise RuntimeError("polar alignment is already running")
        self.state = self._idle()
        self._native_paused = False
        self._pause_acked = False
        # Resolve the driver and record the source SYNCHRONOUSLY, before we
        # return. create_task only schedules the driver; its body (and its first
        # _publish(source=...)) hasn't run when the API handler reads
        # state["source"] for the {"started": true, "source": ...} response — so
        # without this a REST client always saw source=null and could not tell
        # whether the real NINA TPPA or the simulator was started.
        #
        # THREE-WAY provider resolution (native-parity spec §5): the capability
        # resolver decides who runs polar alignment for this rig —
        #   backend   -> NINA's TPPA plugin (existing _run_nina)
        #   astrodeck -> the Rust TPPA engine (polar/native.run_native)
        #   sim / unavailable -> the built-in _run_sim
        # Since fe9abea the resolver's kind vocabulary maps 1:1 onto that split:
        # kind "astrodeck" IS the native engine (label "AstroDeck native"), and
        # the simulator fallback — as well as an explicit sim override — now
        # resolves to kind "sim", not "astrodeck". Resolution never fatally
        # fails here (any error degrades to the simulator) — polar align always
        # has a driver.
        try:
            choice = resolve("polar_align", self.hub)
        except Exception:
            choice = None

        if self.hub.nina_client is not None and (
                choice is None or choice.kind == "backend"):
            self.state["source"] = "nina"
            self._task = asyncio.create_task(self._run_nina())
        # ``choice.label != "Simulator"`` is now redundant — kind "astrodeck"
        # is exclusively the native engine (the simulator resolves to kind
        # "sim", see above) — but it's belt-and-braces harmless, so it stays.
        elif (choice is not None and choice.kind == "astrodeck"
              and choice.label != "Simulator"):
            from .native import run_native
            self.state["source"] = "native"
            self._task = asyncio.create_task(run_native(self, self.hub))
        else:
            self.state["source"] = "sim"
            self._task = asyncio.create_task(self._run_sim())

    async def stop(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"Action": "stop-alignment"}))
            except Exception:
                pass
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._ws = None
        self._publish(state="idle", message="stopped", progress=0.0)

    def _ack_pause_reached(self) -> None:
        """A driver has ARRIVED at ``wait_if_paused``: NOW the rig has stopped.

        Called from the primitive itself (see :func:`wait_if_paused`), because
        that call site is the only place in the system that knows the driver is
        between steps — no exposure open, no slew outstanding. Publishing
        "paused" anywhere else is a claim about hardware we have not checked.

        Idempotent: a driver hits the primitive several times per loop and only
        the first arrival after a given ``pause()`` is the transition. Also a
        no-op once the user has resumed, so a straggling call cannot re-park a
        live run.
        """
        if not self._native_paused or self._pause_acked:
            return
        self._pause_acked = True
        self._publish(state="paused", message="paused — the mount has stopped")

    async def pause(self) -> None:
        # Nothing to pause: publishing a pause state with no session would
        # STRAND the UI, which derives "a run is live" from that state string
        # (PolarView.tsx) — Resume/Stop would light up over nothing and Start
        # would be disabled, with no driver left to clear it.
        if not self.running:
            return
        # Already stopping (or stopped). A second Pause — a REST client, or a
        # double tap through a slow round trip — must NOT re-arm "pausing": the
        # driver is parked inside ``wait_if_paused`` and will never re-enter it
        # to ack, so the UI would sit on "Stopping…" over a mount that has been
        # stationary for a minute.
        if self._native_paused:
            return
        # First-party drivers (native + sim): raise the flag they poll in
        # ``wait_if_paused`` so they stop capturing at the next yield point.
        self._native_paused = True
        self._pause_acked = False
        # NINA runs its own alignment loop and ignores the flag; ask it over the
        # websocket instead. Best-effort — the plugin's acceptance of the action
        # is unverified against a live TPPA (no rig has confirmed it).
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"Action": "pause-alignment"}))
            except Exception:
                pass
        if self.state.get("source") == "nina":
            # NINA never reaches ``wait_if_paused``, so nothing would promote
            # "pausing" and the UI would sit on a disabled "Stopping…" with no
            # way out. Its plugin owns the stop and tells us nothing about when
            # it lands, so this stays the old best-effort claim — the honest
            # two-step below needs a driver we can observe.
            self._pause_acked = True
            self._publish(state="paused", message="paused")
            return
        # NOT "paused" — the flag is only READ at the checkpoints, and an RA
        # rotation already in flight keeps turning for another 5-15 s. The user
        # of this screen is crouched at the mount with a hex key, so "stopped"
        # is a claim about where their hands can safely be; say what is actually
        # true and let ``_ack_pause_reached`` upgrade it.
        self._publish(state="pausing",
                      message="stopping — the mount may still be moving")

    async def resume(self) -> None:
        if not self.running:
            return
        self._native_paused = False
        self._pause_acked = False
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"Action": "resume-alignment"}))
            except Exception:
                pass
        self._publish(state="running", message="resumed")

    # -------------------------------------------------------------- NINA driver

    async def _run_nina(self) -> None:
        try:
            import websockets
        except ImportError:
            self._publish(state="error", source="nina",
                          message="websockets library unavailable")
            return
        client = self.hub.nina_client
        url = f"ws://{client.host}:{client.port}/v2/tppa"
        self._publish(state="running", source="nina", progress=0.0,
                      message="connecting to NINA TPPA…")
        got_measurement = False
        try:
            async with websockets.connect(url, open_timeout=10, ping_interval=20) as ws:
                self._ws = ws
                await ws.send(json.dumps({"Action": "start-alignment"}))
                bus.log("info", "NINA TPPA started", "polar")
                async for raw in ws:
                    try:
                        if self._handle_nina(json.loads(raw)):
                            got_measurement = True
                    except (ValueError, TypeError):
                        continue
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._publish(state="error", source="nina",
                          message=f"TPPA connection failed: {e}")
            bus.log("error", f"NINA TPPA: {e}", "polar")
            return
        finally:
            self._ws = None
        # The `async for` above ends WITHOUT an exception when NINA closes the
        # TPPA websocket cleanly (alignment finished/stopped on the NINA side,
        # plugin reload, or shutdown with a normal close frame). Nothing in the
        # loop publishes a terminal state, so the last event still says
        # "running" — the UI would stick on a blinking "running" with Start
        # disabled forever. Publish a terminal state now: "done" if TPPA
        # produced at least one measurement (it actually ran to a result),
        # otherwise "error" for a socket that closed before any alignment data.
        if got_measurement:
            self._publish(state="done", progress=1.0, message="alignment complete")
            bus.log("info", "NINA TPPA finished", "polar")
        else:
            self._publish(state="error",
                          message="NINA closed the TPPA connection")
            bus.log("warning",
                    "NINA TPPA connection closed before any measurement", "polar")

    def _handle_nina(self, msg: dict) -> bool:
        """Apply one NINA TPPA message. Returns True if it carried an alignment
        measurement (proof TPPA actually ran) — the caller uses that to decide
        the terminal state when the websocket later closes cleanly."""
        resp = msg.get("Response", msg) if isinstance(msg, dict) else {}
        az = pick(resp, "AzimuthError")
        alt = pick(resp, "AltitudeError")
        tot = pick(resp, "TotalError")
        got_measurement = az is not None or alt is not None or tot is not None
        if got_measurement:
            self._publish(
                state="running",
                az_error=round(float(az or 0) * _DEG_TO_MIN, 2),
                alt_error=round(float(alt or 0) * _DEG_TO_MIN, 2),
                message="adjust the mount",
            )
        status = pick(resp, "Status")
        progress = pick(resp, "Progress")
        kw: dict[str, Any] = {}
        if status is not None:
            kw["message"] = str(status)
        if progress is not None:
            try:
                p = float(progress)
            except (TypeError, ValueError):
                p = None
            if p is not None:
                # NINA reports Progress as -1 while a phase is indeterminate
                # (e.g. "Solving…"). Never emit a negative progress — the UI
                # hides the bar on negatives. Hold the last known progress
                # through an indeterminate phase, and clamp to [0, 1] otherwise.
                if p < 0:
                    kw["progress"] = float(self.state.get("progress", 0.0))
                else:
                    kw["progress"] = max(0.0, min(1.0, p))
        if kw:
            self._publish(**kw)
        return got_measurement

    # --------------------------------------------------------------- sim driver

    async def _run_sim(self) -> None:
        try:
            self._publish(state="running", source="sim", progress=0.05,
                          message="slewing to first point")
            await asyncio.sleep(_sim_delay(1.2))
            for i in range(1, 4):
                await wait_if_paused(self)
                self._publish(message=f"measuring point {i}/3", progress=0.1 + 0.2 * i)
                await asyncio.sleep(_sim_delay(1.0))

            az = random.uniform(4.0, 9.0) * random.choice((-1, 1))
            alt = random.uniform(3.0, 8.0) * random.choice((-1, 1))
            self._publish(state="running", progress=0.8, message="adjust the mount",
                          az_error=round(az, 2), alt_error=round(alt, 2))

            # Converge (as if the user were turning the bolts) so the whole
            # reticle/vector/auto-zoom flow is visible end to end.
            while math.hypot(az, alt) > 0.4:
                await wait_if_paused(self)
                await asyncio.sleep(_sim_delay(1.0))
                # Pause can land DURING that tick; re-check before publishing,
                # so a paused sim goes quiet at once instead of streaming one
                # more "adjust the mount" reading the user didn't ask for.
                await wait_if_paused(self)
                az = az * 0.82 + random.uniform(-0.2, 0.2)
                alt = alt * 0.82 + random.uniform(-0.2, 0.2)
                self._publish(az_error=round(az, 2), alt_error=round(alt, 2),
                              message="adjust the mount")

            self._publish(state="done", progress=1.0, message="polar aligned",
                          az_error=round(az, 2), alt_error=round(alt, 2))
            bus.log("info", "sim polar alignment complete", "polar")
        except asyncio.CancelledError:
            raise
