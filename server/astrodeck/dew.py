"""Dew heaters driven by the MARGIN to the dew point (D-RIG-3, task S7f).

THE MARGIN, NOT THE HUMIDITY. Relative humidity is a statement about the AIR:
how close it is to saturation at its own temperature. What fogs an objective is
the GLASS, which radiates to a clear sky and sits below air temperature all
night. So the quantity that decides whether the optic dews is how many degrees
the surface has left before it reaches the dew point - air temperature minus
dew point - and 90% humidity at 15 C (margin 1.6 C) is a far more dangerous
night than 90% humidity at -5 C (margin 1.2 C at the same reading is not even
the same hazard once the glass is factored in). ``weather.surface_now``
publishes both numbers already; their difference is the whole input to this
loop. Nothing here reads humidity.

TWO THRESHOLDS, NOT ONE, AND THE GAP BETWEEN THEM IS THE HYSTERESIS. A single
"heat below N degrees" makes the heater a SWITCH, and a switch whose input is a
noisy weather sample flaps: the margin wanders across N, the heater slams
between off and full, and the night is spent at 0% and 100% and never at the
40% that would actually have held the glass a degree above the dew point. The
usual fix is to bolt a deadband on afterwards - a second, hidden number. Here
the deadband is the FEATURE: ``margin_full_c`` is where the heater reaches
``max_power``, ``margin_off_c`` is where it falls back to ``min_power``, and in
between the power ramps linearly. There is no edge to flap across, because
there is no edge - a margin sitting on 3.0 C with 0.2 C of sample noise moves
the power by 5 points, not by 100. The ramp is also why the config validator
refuses ``margin_off_c <= margin_full_c``: with the two collapsed the ramp
inverts or divides by zero, and a heater that does the OPPOSITE of what the
panel says would do it all night with nothing to look at.

NEVER A DEFAULT POWER. When ``surface_now`` has no reading - no forecast yet,
the network down, the nearest sample older than ``SURFACE_MAX_AGE_S`` - this
loop does NOTHING and changes NOTHING. It does not fall back to 0 (which would
switch off a heater the operator set by hand and let the corrector plate dew
over on the one night the network was down) and it does not fall back to some
"safe" middle (which is a number nobody chose, applied to hardware whose power
budget nobody checked). A heater left where somebody put it is always better
than a heater at a guessed level, so the only thing an absent reading changes
is the ``reason`` string.

THE MANUAL OVERRIDE. Any hand write - the camera dew slider, a switch port -
calls :meth:`DewController.note_manual` from the route that performed it, and
following stops for ``manual_override_s``. It resumes ON ITS OWN afterwards
(with one log line), because an override that has to be cleared by hand is an
override somebody forgets at 22:00 and discovers at dawn as a dewed-up mirror.

WHAT IS REDACTED, AND THE ONE THING THAT IS NOT. ``margin_c``, ``temp_c`` and
``dewpoint_c`` in :meth:`DewController.snapshot` are weather readings AT the
site, so S7a's ``api/redact.py::_strip_dew`` removes them - and nulls
``power_pct``, which is a lossy but real function of them - for a principal
without ``view.weather``. ``camera.dew_heater`` in the hub's status payload is
deliberately NOT stripped: it predates this loop, it is a DEVICE READOUT of a
register an operator sets by hand from the Capture screen, and it exists
precisely so that slider can show the level the heater is actually at instead
of its own last write (hub.py:6497-6512). Stripping it would put back the bug
that reading it fixed, and it discloses nothing about the site - a percentage
on a register is not a weather observation.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time

from . import power_guard
from .config import DewConfig, config_store
from .events import bus

log = logging.getLogger(__name__)

#: Fallback tick cadence, used only when the config cannot be read at all. The
#: real cadence is ``cfg.dew.interval_s``, re-read every tick, so a change to it
#: takes effect within one interval with no restart.
DEFAULT_INTERVAL_S = 120.0

#: How far the target has to move, IN PERCENTAGE POINTS, before the loop
#: re-commands a heater. Dew heaters are resistive strips with a thermal time
#: constant of minutes; a 1-point correction changes nothing you could measure
#: and costs a device round trip every tick, on a bus that is also carrying the
#: guide camera. It also keeps the log and the ``dew`` event quiet: without it,
#: sample noise alone would write both heaters every two minutes all night.
CHANGE_THRESHOLD_PCT = 5

#: Bound on any single device write. A wedged heater write must not be able to
#: stop the loop, because a stopped loop stops heating - the same reasoning
#: ``power_guard.port_settings`` gives for never raising.
DEVICE_TIMEOUT_S = 30.0


def _round_half_up(value: float) -> int:
    """Round half AWAY FROM ZERO, not to even.

    ``round()`` is banker's rounding, so a port scaled to 127.5 lands on 128 and
    one scaled to 126.5 lands on 126 - the same input rule producing two
    different behaviours depending on the parity of the answer. Nobody debugging
    a dew port at dawn should have to know that.
    """
    return int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5))


def ramp_power(cfg: DewConfig, margin_c: float) -> int:
    """The heater level for this margin, clamped into ``[min_power, max_power]``.

    A pure function of the config and one number, so the ramp is testable
    without a device, a clock or a network - which is what makes "margin 3.0
    gives 50%" an assertion rather than a claim.
    """
    full = float(cfg.margin_full_c)
    off = float(cfg.margin_off_c)
    lo = int(cfg.min_power)
    hi = int(cfg.max_power)
    span = off - full
    if span <= 0:
        # The config validator forbids this, but the validator is not the only
        # way a config object reaches here (a test, a future partial update), and
        # a ZeroDivisionError inside a night-long loop is a worse answer than a
        # step function.
        power = float(hi if margin_c <= full else lo)
    elif margin_c <= full:
        power = float(hi)
    elif margin_c >= off:
        power = float(lo)
    else:
        power = lo + (off - margin_c) / span * (hi - lo)
    # TWO INDEPENDENT ENFORCEMENTS of the same bounds, deliberately. The
    # branches above already answer ``max_power`` below ``margin_full_c`` and
    # ``min_power`` above ``margin_off_c``, so on the shipped path this line
    # changes nothing - but breaking EITHER one alone still produces correct
    # answers (measured during the S7f sabotage run, where a branch returning 0
    # instead of ``min_power`` was silently repaired by this clamp). That is the
    # property worth having on the one number in this module that reaches
    # hardware: min_power exists because some strips take ten minutes to come
    # back from cold, and max_power because some of them are sized for a
    # 5 A fuse.
    return _round_half_up(min(max(power, float(lo)), float(hi)))


def _num(value, default: float) -> float:
    """A float from whatever a driver put on the dataclass, or ``default``."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(out) else out


def scale_to_port(port, power_pct: int) -> float:
    """``power_pct`` expressed in one port's OWN units.

    A percentage is not a device value. A Pegasus UPB dew port is an 8-bit PWM
    register (0..255), an Alpaca boolean port is 0..1, and writing 50 to either
    of them is wrong in a different direction: 50/255 is a fifth of the heat the
    operator asked for, and 50 on a boolean port is out of range. The port
    reports its own ``min``/``max`` and that is what the percentage is mapped
    onto.

    A range whose ends are whole numbers and at least one apart is a REGISTER,
    so the result is rounded to an integer - 50% of 0..255 is 128, and a driver
    handed 127.5 either truncates it (a silent 0.2% loss every write) or
    refuses. A fractional range is left fractional.
    """
    lo = _num(getattr(port, "min", 0.0), 0.0)
    hi = _num(getattr(port, "max", 1.0), 1.0)
    if hi <= lo:
        return hi
    value = lo + (hi - lo) * (max(0, min(100, int(power_pct))) / 100.0)
    if float(lo).is_integer() and float(hi).is_integer() and (hi - lo) >= 1:
        return float(_round_half_up(value))
    return round(value, 3)


class DewController:
    """The lifespan task. One instance, created in ``api.app`` beside
    ``dawn_park``/``sun_watch`` and started/stopped by the app lifespan.

    Started UNCONDITIONALLY, like both of those: a tick with the feature off is
    one attribute read, and starting it on a config flag would mean the flag
    only took effect on the next restart - which on this rig is the next night.
    """

    def __init__(self, hub, *, clock=None, weather=None) -> None:
        self.hub = hub
        # clock=None, NOT clock=time.time. A default argument is evaluated at
        # IMPORT and holds the original builtin, so monkeypatching time.time
        # would never reach it and a simulated night would tick hundreds of
        # times at one frozen instant with every assertion green. Same trap
        # DawnPark and SunWatch document.
        self._clock = clock or (lambda: time.time())
        # None means "the module singleton, resolved at CALL time" - importing
        # weather at module scope would bind whatever object existed at import
        # and make the service untestable from here.
        self._weather = weather
        self._task: asyncio.Task | None = None

        #: The last tick's answer, or None before the first tick. See snapshot().
        self._snap: dict | None = None
        #: What was last PUBLISHED, so the bus only sees edges.
        self._edge = None

        #: Wall-clock instant following resumes; 0.0 = not overridden,
        #: ``math.inf`` = an override configured never to expire on its own.
        self._override_until = 0.0
        self._override_kind: str | None = None
        self._override_port_id: int | None = None
        self._resume_logged = True

        #: Last level COMMANDED to each surface, in percent, so the change
        #: threshold compares intent with intent. None = never commanded.
        self._last_camera: int | None = None
        self._last_ports: dict[int, int] = {}
        #: Last target the ramp produced and the loop acted on, kept so a tick
        #: with no weather reading can say what the heaters are still at.
        self._last_power: int | None = None

        # The status node reads us off the hub (``getattr(hub, "dew_controller",
        # None)``) rather than importing this module, so hub.py keeps no import
        # of a service it does not own. Total: a hub that refuses attributes is
        # a test double, and a controller that cannot be found is a missing
        # status block, not a failed boot.
        try:
            hub.dew_controller = self
        except Exception:       # pragma: no cover - an exotic hub double
            log.debug("could not attach the dew controller to the hub")

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            # SLEEP FIRST, like dawn park and sun watch: a tick at t=0 lands
            # mid-boot, where the camera is still connecting and the weather
            # service has not fetched anything yet. One interval is free - the
            # glass takes longer than that to cool to the dew point.
            await asyncio.sleep(self._interval_s())
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:      # noqa: BLE001 - the loop outlives its own bugs
                # A heater loop that dies takes the optics with it, and takes
                # them silently. Say so and keep ticking.
                bus.log("warning", f"dew tick failed: {e} - the heater loop is "
                                   "still running and will retry", "dew")

    def _interval_s(self) -> float:
        try:
            return float(config_store.cfg().dew.interval_s)
        except Exception:           # pragma: no cover - an unreadable store
            return DEFAULT_INTERVAL_S

    # ------------------------------------------------------------------ tick

    async def tick(self) -> None:
        """One decision. Safe to call as often as you like."""
        cfg = config_store.cfg().dew
        if not cfg.enabled:
            # Zero weather reads, zero device reads, zero writes: one attribute
            # and out. The snapshot is still recorded so the panel can say the
            # loop is off rather than say nothing at all.
            self._record(cfg, following=False, reading=None,
                         power=None, reason="dew following is off", ports=[])
            return

        now_ts = self._clock()
        # Before anything else, so an override that has run out is GONE from the
        # snapshot rather than lingering as a stale timestamp on every readout.
        self._expire_override(now_ts)
        reading = self._surface_now(now_ts)
        temp = reading.get("temp_c") if isinstance(reading, dict) else None
        dewp = reading.get("dewpoint_c") if isinstance(reading, dict) else None
        if temp is None or dewp is None:
            # DO NOTHING and change nothing. Not 0, not a middle - see the
            # module docstring. ``power`` reports what the heaters were last put
            # at, because that is where they still are.
            self._record(cfg, following=self._following(now_ts), reading=None,
                         power=self._last_power, reason="no dew-point reading",
                         ports=await self._port_rows(None))
            return

        margin = float(temp) - float(dewp)
        power = ramp_power(cfg, margin)

        if not self._following(now_ts):
            # The target is still computed and reported: an operator looking at
            # a paused panel wants to see what the loop WOULD be doing, which is
            # how they decide whether to let the override run out.
            self._record(cfg, following=False,
                         reading={"temp_c": float(temp), "dewpoint_c": float(dewp),
                                  "margin_c": margin},
                         power=power, reason=self._pause_reason(now_ts),
                         ports=await self._port_rows(None))
            return

        wrote_camera = await self._drive_camera(cfg, power)
        rows = await self._port_rows(power)
        self._last_power = power

        # NO WEATHER NUMBER IN THE REASON. The obvious string here is
        # "margin 3.0 C - heaters at 50%", and it would walk the margin straight
        # past S7a's ``_strip_dew``, which removes ``margin_c``/``temp_c``/
        # ``dewpoint_c`` and nulls ``power_pct`` for a principal without
        # ``view.weather`` - a key filter cannot withhold a number spelled out
        # in a sentence. The numbers live in the structured fields, which are
        # strippable; this field says what the loop is DOING.
        reason = "following the dew margin"
        if not wrote_camera and not any(r.get("wrote") for r in rows):
            reason = "following the dew margin - no change worth commanding"
        self._record(cfg, following=True,
                     reading={"temp_c": float(temp), "dewpoint_c": float(dewp),
                              "margin_c": margin},
                     power=power, reason=reason, ports=rows)

    # ------------------------------------------------------------- the inputs

    def _surface_now(self, now_ts: float) -> dict | None:
        """The surface reading, or None. TOTAL - a weather service that raised
        would otherwise stop the heaters for the rest of the night."""
        try:
            service = self._weather
            if service is None:
                from .weather import weather_service
                service = weather_service
            reading = service.surface_now(now_ts)
        except Exception as e:      # noqa: BLE001
            log.debug("dew: surface reading unavailable (%s)", e)
            return None
        return reading if isinstance(reading, dict) else None

    def _device(self, role: str):
        """A CONNECTED device for this role, or None. Never raises."""
        try:
            devices = getattr(self.hub, "devices", None) or {}
            dev = devices.get(role)
        except Exception:           # pragma: no cover - an exotic hub double
            return None
        if dev is None or not getattr(dev, "connected", False):
            return None
        return dev

    # -------------------------------------------------------------- the writes

    async def _drive_camera(self, cfg: DewConfig, power: int) -> bool:
        """Command the camera window heater if it has one. True when written.

        EVERY failure is caught here rather than at the tick: a camera that
        cannot take a heater write must not cost the switch ports their tick.
        """
        if not cfg.camera_window:
            return False
        cam = self._device("camera")
        # ``has_dew_heater`` is the capability the hub's own camera status node
        # reads (hub.py:6476), so the loop and the panel agree about which
        # cameras have one. A camera without it raises DeviceError from
        # ``set_dew_heater`` by design (devices/base.py:213) - skipped here so
        # that is never an exception the tick has to survive.
        if cam is None or not getattr(cam, "has_dew_heater", False):
            return False
        if not self._should_command(self._last_camera, power):
            return False
        try:
            await asyncio.wait_for(cam.set_dew_heater(int(power)),
                                   timeout=DEVICE_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as e:      # noqa: BLE001
            bus.log("warning", f"could not set the camera dew heater: {e}", "dew")
            return False
        self._last_camera = int(power)
        return True

    async def _port_rows(self, power: int | None) -> list[dict]:
        """The followed switch ports, commanded to ``power`` when it is not None.

        ``power=None`` is the read-only pass a paused or reading-less tick makes:
        it still reports which ports follow the dew loop, because "nothing
        happened" is only legible next to what would have.
        """
        sw = self._device("switch")
        if sw is None:
            return []
        try:
            ports = await asyncio.wait_for(sw.get_ports(), timeout=DEVICE_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as e:      # noqa: BLE001
            log.debug("dew: could not list switch ports (%s)", e)
            return []

        rows: list[dict] = []
        for port in ports or []:
            port_id = getattr(port, "id", None)
            if port_id is None:
                continue
            # TOTAL by contract (power_guard.port_settings): an unknown port, a
            # missing file and a hand-mangled one all answer False here rather
            # than raising, so one bad JSON file cannot stop the heaters.
            if not power_guard.port_settings(port_id).get("follow_dew"):
                continue
            row = {"id": port_id, "name": getattr(port, "name", "") or "",
                   "follow_dew": True, "value": _num(getattr(port, "value", 0.0), 0.0),
                   "wrote": False}
            if power is not None and self._should_command(
                    self._last_ports.get(port_id), power):
                value = scale_to_port(port, power)
                try:
                    await asyncio.wait_for(sw.set_port(port_id, value),
                                           timeout=DEVICE_TIMEOUT_S)
                except asyncio.CancelledError:
                    raise
                except Exception as e:      # noqa: BLE001
                    bus.log("warning", f"could not set dew port "
                                       f"{row['name'] or port_id}: {e}", "dew")
                else:
                    self._last_ports[port_id] = int(power)
                    row["value"] = value
                    row["wrote"] = True
            rows.append(row)
        return rows

    @staticmethod
    def _should_command(last: int | None, power: int) -> bool:
        """Has the target moved far enough to be worth a device write?

        ``last is None`` means this surface has never been commanded (or the
        memory was dropped by a manual write), and that always commands: the
        threshold is about not re-sending the SAME level, and there is no same
        level yet.
        """
        return last is None or abs(int(power) - int(last)) >= CHANGE_THRESHOLD_PCT

    # ---------------------------------------------------------- manual override

    def note_manual(self, kind: str, port_id: int | None = None) -> None:
        """A human just set a level by hand; stop following for a while.

        Called from the two routes that write a heater
        (``POST /api/camera/dew-heater`` and ``POST /api/switch/set``) AFTER the
        write succeeds - a refused write is not an instruction. NEVER raises:
        this runs inside a request that has already changed the hardware, and
        failing the response over the loop's bookkeeping would tell the operator
        the write did not happen when it did.

        The last-commanded memory for that surface is DROPPED, and this is not
        housekeeping. Say the loop last commanded 50% and the operator sets 90%
        by hand. Two hours later the override expires and the ramp asks for 52%
        - within the 5-point threshold of the loop's own last write, so nothing
        would be sent and the heater would sit at 90% for the rest of the night,
        following nothing, with the panel reporting 52%.

        A WRITE TO SOMETHING THIS LOOP DOES NOT DRIVE IS NOT AN INSTRUCTION TO
        IT. ``POST /api/switch/set`` calls this for EVERY port, because the route
        has no business knowing which ports are dew strips - so the filtering is
        here. Without it, somebody power-cycling the USB hub at 22:00 would
        switch the dew heaters off the weather for two hours, and the only
        evidence would be a ``reason`` string nobody was reading. Same argument
        for the camera window when ``camera_window`` is off.
        """
        try:
            self._note_manual(kind, port_id)
        except Exception as e:      # noqa: BLE001 - see the docstring
            log.debug("dew: could not record the manual write (%s)", e)

    def _note_manual(self, kind: str, port_id: int | None) -> None:
        try:
            cfg = config_store.cfg().dew
            seconds = int(getattr(cfg, "manual_override_s", 7200))
        except Exception:           # pragma: no cover - an unreadable store
            cfg, seconds = None, 7200
        if not self._drives(cfg, kind, port_id):
            return
        now_ts = self._clock()
        was_paused = not self._following(now_ts)
        # 0 means "never expires on its own" (config.DewConfig), which is
        # infinity and not "expire immediately" - the opposite reading would
        # make the safest-looking setting the one that silently ignores the
        # operator's level on the very next tick.
        self._override_until = math.inf if seconds <= 0 else now_ts + seconds
        self._override_kind = str(kind)
        self._override_port_id = port_id
        self._resume_logged = False
        if kind == "camera":
            self._last_camera = None
        elif port_id is None:
            self._last_ports.clear()
        else:
            self._last_ports.pop(port_id, None)
        if not was_paused:
            # ONE line per override episode, not one per POST: a slider dragged
            # across the screen is a dozen writes and would be a dozen lines.
            bus.log("info", f"dew following paused - a level was set by hand "
                            f"({self._override_kind})", "dew")

    @staticmethod
    def _drives(cfg, kind: str, port_id: int | None) -> bool:
        """Would this loop ever command the thing that was just written by hand?

        FAIL TOWARD RESPECTING THE HUMAN: anything this cannot answer counts as
        a surface the loop drives, so the override stands. Ignoring a real
        manual write costs an operator the level they set; honouring a spurious
        one costs a couple of hours of following, which the ``reason`` string
        explains and which expires on its own.
        """
        if kind == "camera":
            return cfg is None or bool(getattr(cfg, "camera_window", True))
        if kind == "switch" and port_id is not None:
            try:
                return bool(power_guard.port_settings(port_id).get("follow_dew"))
            except Exception:       # pragma: no cover - port_settings is total
                return True
        return True

    def _following(self, now_ts: float) -> bool:
        return not (self._override_until and now_ts < self._override_until)

    def _pause_reason(self, now_ts: float) -> str:
        if self._override_until == math.inf:
            return ("following is paused - a level was set by hand and the "
                    "override does not expire on its own")
        minutes = max(0, int((self._override_until - now_ts) / 60.0))
        return f"following is paused for another {minutes} min - a level was set by hand"

    def _expire_override(self, now_ts: float) -> None:
        """Drop an override whose time is up, and say so ONCE.

        The resume is automatic on purpose (see the module docstring), and an
        automatic resume with no log line is a heater changing level for a
        reason nobody can find in the morning.
        """
        if not self._override_until or now_ts < self._override_until:
            return
        self._override_until = 0.0
        self._override_kind = None
        self._override_port_id = None
        if not self._resume_logged:
            self._resume_logged = True
            bus.log("info", "dew following resumed - the manual override has "
                            "expired", "dew")

    # ---------------------------------------------------------------- readout

    def _record(self, cfg: DewConfig, *, following: bool, reading: dict | None,
                power: int | None, reason: str, ports: list[dict]) -> None:
        """Store this tick's snapshot and publish it IF something changed."""
        snap = {
            "enabled": bool(cfg.enabled),
            "following": bool(following),
            # None when nothing is overridden AND when the override was
            # configured never to expire (``manual_override_s = 0``); the two
            # are told apart by ``following``, and by ``reason``, which says so
            # in words. A JSON payload has no infinity to put here.
            "override_until_ts": (self._override_until
                                  if self._override_until
                                  and self._override_until != math.inf else None),
            "margin_c": (round(reading["margin_c"], 2)
                         if reading is not None else None),
            "temp_c": reading["temp_c"] if reading is not None else None,
            "dewpoint_c": reading["dewpoint_c"] if reading is not None else None,
            "power_pct": None if power is None else int(power),
            "reason": reason,
            "ports": [{"id": r["id"], "name": r["name"],
                       "follow_dew": r["follow_dew"], "value": r["value"]}
                      for r in ports],
        }
        self._snap = snap
        # THE CHANGE-ONLY RULE, from hub._safety_loop:6096. The edge is what the
        # loop DID and what state it is in - never the weather, which moves a
        # hundredth of a degree every tick and would put this event on the bus
        # every two minutes for the whole night. A reader that wants the live
        # margin reads the snapshot (or the weather payload it came from).
        edge = (snap["enabled"], snap["following"], snap["reason"],
                snap["power_pct"], self._last_camera,
                tuple(sorted(self._last_ports.items())))
        if edge != self._edge:
            self._edge = edge
            try:
                bus.publish("dew", **snap)
            except Exception as e:  # noqa: BLE001 - a publish never fails a tick
                log.debug("dew: publish failed (%s)", e)

    def snapshot(self) -> dict | None:
        """This loop's last answer, or None before the first tick.

        None is a real state and not an empty dict: "the loop has not run yet"
        and "the loop ran and found nothing to do" are different things to show,
        and a status node that cannot tell them apart reports a heater as idle
        during the two minutes before the first tick.

        ``{enabled, following, override_until_ts, margin_c, temp_c, dewpoint_c,
        power_pct, reason, ports: [{id, name, follow_dew, value}]}``. ``ports``
        lists only the ports that FOLLOW the dew loop - the full inventory with
        its ``follow_dew`` annotation is ``GET /api/switch/ports``
        (``power_guard.annotate``), and a second copy of that list here would be
        a second copy to drift. The flag stays on the row so a row forwarded on
        its own still says what it is.

        A fresh dict every call: the caller redacts it (``_strip_dew``) and
        serialises it, and neither should be able to reach into the loop's own
        state.
        """
        if self._snap is None:
            return None
        snap = dict(self._snap)
        snap["ports"] = [dict(row) for row in self._snap["ports"]]
        return snap
