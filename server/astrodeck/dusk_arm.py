"""Opt-in preparation of an idle rig at dusk (#28).

This service connects and cools; it never commands mount motion or starts a
sequence. Each night's attempt is recorded BEFORE touching hardware. A restart,
manual disconnect or warm-up therefore cannot silently start another attempt.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path

from . import config
from .events import bus
from .persist import write_json_atomic
from .profiles import profiles
from .sequence.engine import COOLER_AT_TARGET_C, COOLER_STABLE_S
from .sequence.schedule import observing_night

CHECK_INTERVAL_S = 30.0
CONNECT_TIMEOUT_S = 120.0
DEVICE_TIMEOUT_S = 15.0


class DuskStateError(Exception):
    """The durable attempt record cannot safely be used."""


class DuskArm:
    def __init__(self, hub, engine, *, weather=None, connection_busy=None,
                 clock=None, state_path: Path | None = None):
        self.hub, self.engine = hub, engine
        self.weather = weather
        self.connection_busy = connection_busy or (lambda: False)
        self._clock = clock or (lambda: time.time())
        self._state_path = state_path
        self._task = None
        self._attempt = None
        self._camera = None
        self._deadline = 0.0
        self._in_band_since = None
        self._target = None
        self._signature = None
        self._warm_marker = None
        self.connecting = False
        self.status = {"state": "disabled", "detail": "Dusk preparation is off."}

    @property
    def path(self):
        return self._state_path or config.CONFIG_DIR / "dusk-arm.json"

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.connecting = False

    async def _run(self):
        # Give boot-time connection and the safety services their first turn.
        while True:
            await asyncio.sleep(CHECK_INTERVAL_S)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except DuskStateError:
                self._set("failed", "The dusk preparation record could not be read or saved. Check the server's storage.")
            except Exception:
                # Driver errors may include private backend addresses. Keep the
                # public status/log bounded and leave details to driver logs.
                self._set("failed", "Dusk preparation failed. Check the equipment connection.")

    def _set(self, state, detail, **extra):
        if (state, detail) != (self.status["state"], self.status["detail"]):
            bus.log("warning" if state == "failed" else "info", detail, "dusk")
        self.status = {"state": state, "detail": detail, **extra}

    def _busy(self):
        boot = getattr(self.hub, "_boot_connect_task", None)
        guider = getattr(self.hub, "guider", None)
        warm = getattr(self.hub, "_warm_task", None)
        return (self.engine.running or bool(self.hub.busy_lanes())
                or bool(getattr(guider, "running", False))
                or bool(boot is not None and not boot.done())
                or bool(warm is not None and not warm.done())
                or any(lock is not None and lock.locked() for lock in (
                    getattr(self.hub, "_capture_lock", None), getattr(self.hub, "_motion_lock", None)))
                or self.connection_busy())

    def _load_attempt(self):
        if self._attempt is None:
            try:
                row = json.loads(self.path.read_text(encoding="utf-8"))
                if (not isinstance(row, dict) or not isinstance(row.get("until"), (int, float))
                        or not math.isfinite(row["until"]) or row["until"] <= 0
                        or row.get("state") not in {"preparing", "ready", "failed", "interrupted", "in_use"}):
                    raise ValueError("invalid dusk record")
                self._attempt = row
            except FileNotFoundError:
                self._attempt = {}
            except (ValueError, OSError) as exc:
                raise DuskStateError() from exc
        return self._attempt

    async def _guider_busy(self):
        guider = getattr(self.hub, "guider", None)
        if guider is None or not getattr(guider, "connected", False):
            return False
        try:
            return bool(await asyncio.wait_for(guider.is_active(), DEVICE_TIMEOUT_S))
        except Exception:
            # Unlike dawn's stop-net, preparation must yield to an owner whose
            # state is unknown. All guider backends expose is_active().
            return True

    def _record(self, until, state):
        row = {"until": until, "state": state}
        # Do not act if the latch cannot be saved. The next process must know
        # that an attempt happened even if power fails during a device call.
        try:
            write_json_atomic(self.path, row)
        except OSError as exc:
            raise DuskStateError() from exc
        self._attempt = row

    def _finish(self, state, detail):
        self._camera = None
        self._record(self._attempt["until"], state)
        self._set(state, detail)

    def resume_veto(self):
        """Keep the recovery ladder from focusing/slewing ahead of cooling."""
        if self.connecting:
            return "Dusk preparation is connecting the equipment."
        if not config.config_store.cfg().dusk.enabled:
            return None
        if self.status["state"] not in ("ready", "completed", "in_use"):
            return "Dusk preparation: " + self.status["detail"]
        return None

    def snapshot(self):
        if not config.config_store.cfg().dusk.enabled:
            return {"state": "disabled", "detail": "Dusk preparation is off."}
        if self.status["state"] == "disabled":
            return {"state": "waiting", "detail": "Dusk preparation is enabled. Checking conditions shortly."}
        return dict(self.status)

    async def tick(self):
        cfg = config.config_store.cfg()
        if not cfg.dusk.enabled:
            self._camera = None
            self._set("disabled", "Dusk preparation is off.")
            return
        site = self.hub.site
        if (site.get("is_default", True)
                or any(not isinstance(site.get(k), (float, int))
                       or not math.isfinite(site[k]) for k in ("latitude", "longitude"))):
            self._set("waiting", "Set your observing location before enabling dusk preparation.")
            return
        now = self._clock()
        night = observing_night(site, cfg.dusk.sun_alt_deg, now)
        if night is None or not night[0] <= now < night[1]:
            self._camera = None
            self._set("waiting", "Waiting for dusk.", next_dusk=night[0] if night else None)
            return
        attempt = self._load_attempt()
        if self.engine.running:
            # A manually started run owns its own preparation and recovery.
            # Do not make its next auto-resume depend on our interrupted wait.
            if (self._camera is not None or now >= attempt.get("until", 0)
                    or attempt.get("state") != "in_use"):
                self._camera = None
                self._record(night[1], "in_use")
            self._set("in_use", "An existing run owns the rig. Dusk preparation is skipped tonight.")
            return
        signature = self._current_signature()
        guiding = await self._guider_busy()
        if self._camera is not None:
            if self._signature != signature or self._busy() or guiding:
                self._finish("interrupted", "Dusk preparation stopped because the settings or rig activity changed.")
                return
            await self._check_cooling(now)
            return
        if now < attempt.get("until", 0):
            # A fresh process cannot claim it measured a stable camera. Normal
            # sequence cooling checks still apply to any later manual/resumed run.
            if self.status["state"] not in ("ready", "failed", "interrupted", "completed", "in_use"):
                state = ("completed" if attempt.get("state") == "ready" else
                         "in_use" if attempt.get("state") == "in_use" else "interrupted")
                self._set(state, "Dusk preparation already ran tonight. Check the rig before starting manually.")
            return
        preparation_budget = (CONNECT_TIMEOUT_S + max(60, cfg.cooling.cool_timeout_s)
                              + COOLER_STABLE_S + 2 * DEVICE_TIMEOUT_S)
        if night[1] - now <= preparation_budget:
            self._set("waiting", "Dawn is too close to finish preparation. Waiting for the next dusk.")
            return
        if (not cfg.dusk.profile_id or cfg.cooling.setpoint_c is None
                or not math.isfinite(cfg.cooling.setpoint_c)):
            self._set("waiting", "Choose a profile and an imaging temperature for dusk preparation.")
            return
        if self._busy() or guiding or self.hub._connect_lock.locked():
            self._set("waiting", "Waiting for the rig to be idle.")
            return
        if self.weather is not None and self.weather.veto_reason(now):
            self._set("waiting", "Waiting for the weather policy to allow preparation.")
            return
        try:
            prof = await asyncio.to_thread(profiles.get, cfg.dusk.profile_id)
        except (KeyError, ValueError):
            self._set("waiting", "The dusk profile is missing. Choose a saved equipment profile.")
            return
        # Lookup yields to the event loop. Recheck ownership inside the same
        # lock used by every connection path, before any teardown can occur.
        if self.hub._connect_lock.locked():
            return
        async with self.hub._connect_lock:
            if self._busy() or signature != self._current_signature():
                return
            if self.hub.devices:
                if cfg.active_profile_id != cfg.dusk.profile_id:
                    self._set("waiting", "Another equipment profile is connected; dusk preparation will leave it alone.")
                    return
                if not self._required_connected():
                    self._set("waiting", "The rig is partly connected. Check its mount and camera before preparing.")
                    return
            self._record(night[1], "preparing")
            self._signature = signature
            self._warm_marker = getattr(self.hub, "_warm_state", None)
            self._target = float(cfg.cooling.setpoint_c)
            try:
                if not self.hub.devices:
                    self.connecting = True
                    self._set("connecting", "Connecting the saved dusk profile.")
                    try:
                        await asyncio.wait_for(self.hub._connect_rigspec_unlocked(
                            prof.to_rigspec(), set_active=prof.id, profile=prof), CONNECT_TIMEOUT_S)
                    finally:
                        self.connecting = False
                if not self._required_connected():
                    self._finish("failed", "The mount or camera did not connect. Check Equipment before starting.")
                    return
                if signature != self._current_signature() or self._busy():
                    self._finish("interrupted", "Dusk preparation stopped because the settings or rig activity changed.")
                    return
                if cfg.safety.enabled and self.hub.devices.get("safety") is not None:
                    reading = await asyncio.wait_for(self.engine.current_safety(), DEVICE_TIMEOUT_S)
                    if reading is None or reading.stale or not reading.is_safe:
                        self._finish("failed", "The safety monitor has not confirmed safe conditions. Cooling was not started.")
                        return
                cam = self.hub.devices["camera"]
                if not getattr(cam, "can_cool", False):
                    self._finish("failed", "This camera cannot cool to the requested temperature. Prepare this rig manually.")
                    return
                guiding = await self._guider_busy()
                if (signature != self._current_signature() or self._busy()
                        or guiding or self._clock() >= night[1]
                        or getattr(self.hub, "_warm_state", None) is not self._warm_marker):
                    self._finish("interrupted", "Conditions changed during connection; check the rig before starting.")
                    return
                # Always send BOTH on and the imaging target. Dawn's last
                # hardware setpoint may be an ambient warm-up temperature.
                await asyncio.wait_for(self.hub.cool_camera(self._target), DEVICE_TIMEOUT_S)
                self._camera = cam
                self._deadline = self._clock() + max(60, cfg.cooling.cool_timeout_s) + COOLER_STABLE_S
                self._in_band_since = None
                self._set("cooling", "Cooling the camera to the imaging temperature.", target_c=self._target)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._finish("failed", "Dusk preparation failed. Check Equipment before starting manually.")

    def _current_signature(self):
        cfg = config.config_store.cfg()
        site = self.hub.site
        return ((cfg.dusk.profile_id, cfg.dusk.sun_alt_deg, cfg.cooling.setpoint_c,
                 site.get("latitude"), site.get("longitude"), site.get("is_default", True))
                if cfg.dusk.enabled else None)

    def _required_connected(self):
        return all(getattr(self.hub.devices.get(role), "connected", False)
                   for role in ("camera", "telescope"))

    async def _check_cooling(self, now):
        cam = self._camera
        if cam is not self.hub.devices.get("camera") or not self._required_connected():
            self._finish("interrupted", "Equipment was disconnected; dusk preparation will not reconnect it tonight.")
            return
        if (getattr(self.hub, "_warm_state", None) is not self._warm_marker
                or (getattr(self.hub, "_warm_task", None) is not None and not self.hub._warm_task.done())):
            self._finish("interrupted", "The camera is warming; dusk preparation will not cool it again tonight.")
            return
        try:
            read_cooler = getattr(cam, "get_cooler", None)
            if callable(read_cooler):
                cooler = await asyncio.wait_for(read_cooler(), DEVICE_TIMEOUT_S)
                if cooler is None or cooler.get("on") is None:
                    raise ValueError("cooler state unavailable")
                if cooler.get("on") is False or (cooler.get("target_c") is not None
                        and abs(cooler["target_c"] - self._target) > 0.1):
                    self._finish("interrupted", "The cooler setting changed; dusk preparation will leave it alone tonight.")
                    return
            temperature = await asyncio.wait_for(cam.get_temperature(), DEVICE_TIMEOUT_S)
        except Exception:
            temperature = None
        # Both readbacks yielded. A disconnect, new run, or settings change
        # while the driver was replying must not produce a stale success.
        if (self._busy() or self._signature != self._current_signature()
                or getattr(self.hub, "_warm_state", None) is not self._warm_marker
                or cam is not self.hub.devices.get("camera") or not self._required_connected()):
            self._finish("interrupted", "The rig changed while cooling; check it before starting a run.")
            return
        now = self._clock()
        if now >= self._attempt["until"]:
            self._finish("interrupted", "Dawn arrived before preparation finished. Waiting for the next evening.")
            return
        if now >= self._deadline:
            self._finish("failed", "The camera did not settle at its imaging temperature in time. Check the cooler before starting.")
            return
        if temperature is not None and math.isfinite(temperature) and abs(temperature - self._target) <= COOLER_AT_TARGET_C:
            if self._in_band_since is None:
                self._in_band_since = now
            if now - self._in_band_since >= COOLER_STABLE_S:
                self._finish("ready", "Dusk preparation completed. The camera reached its imaging temperature.")
                return
        else:
            self._in_band_since = None
        self._set("cooling", "Waiting for the camera temperature to settle.",
                  target_c=self._target, temperature_c=temperature if temperature is not None and math.isfinite(temperature) else None)
