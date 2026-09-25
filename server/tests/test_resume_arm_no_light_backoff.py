"""Auto-resume alerts once and backs off when its recovery solve finds no
light (#251).

2026-09-24/25: auto-resume was armed, the optic was capped, and the recovery
ladder's blind solve failed with "Not enough stars." every ten minutes from
19:59 to past 22:24. Nobody was told, and every retry after the first spent a
12 s exposure and an ASTAP run on a camera that could not see. The solve now
says whether light reached the sensor (``astrodeck.solve.light``), and on a
``NoLightError`` the ladder:

* sends ONE push alert naming it, through the alert pipeline, at a level the
  default sink delivers, latched per session per no-light spell;
* retries first after ``RETRY_INTERVAL_S`` (an operator who reads the alert
  and uncaps should not then wait an hour), and then every
  ``NO_LIGHT_RETRY_S`` while the verdict stays no light;
* ends the spell on a successful solve or a cloud verdict (light reached the
  sensor), and at the end of the night;
* holds with a reason in words.

A cloud verdict keeps today's ten-minute retry and sends no alert.

Each case names the mutation that turns it red and the failure it produced,
verbatim, from a run of that mutant against a byte copy of the file.
"""
from __future__ import annotations

import re
import types

import numpy as np
import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.resume_arm import (NO_LIGHT_RETRY_S, RETRY_INTERVAL_S,
                                           ResumeArm)
from astrodeck.sequence.session import Session, session_store
from astrodeck.solve import light

T0 = 1_700_000_000.0


def _plan() -> SequencePlan:
    return SequencePlan(name="cap", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=3)])])


def _dark_error() -> light.NoLightError:
    """What ``solve_and_sync`` raises through a cap, built by the real
    classifier from a #251-shaped frame. Numbers are put in its text on
    purpose: the hold must not carry them."""
    rng = np.random.default_rng(251)
    frame = np.clip(np.rint(rng.normal(251.0, 9.0, (200, 300))), 0,
                    65535).astype(np.uint16)
    v = light.classify(frame, light.Reference(level=250.0))
    assert v.kind == light.NO_LIGHT, v
    e = light.error_for(v, "Not enough stars.", "plate solve failed")
    return light.NoLightError(f"{e} [median {v.median:.0f} ADU]", v)


def _verdict_error(kind: str) -> light.FailedSolveError:
    """A failed solve whose frame showed light (``CLOUD``), or that nothing
    could judge (``UNKNOWN``)."""
    rng = np.random.default_rng(252)
    level = 290.0 if kind == light.CLOUD else 251.0
    frame = np.clip(np.rint(rng.normal(level, 11.0, (200, 300))), 0,
                    65535).astype(np.uint16)
    ref = light.Reference(level=250.0) if kind == light.CLOUD else None
    v = light.classify(frame, ref)
    assert v.kind == kind, v
    return light.error_for(v, "Not enough stars.", "plate solve failed")


class _Rig:
    """The ladder around the solve, held still: focus trusted, the slew-limit
    gate passing, and the re-centring goto failing, so a solve that SUCCEEDS
    ends in an ordinary ten-minute refusal and no run starts. The solve plays
    ``script``: ``"dark"``, ``"cloud"``, ``"unknown"`` or ``"solved"``."""

    def __init__(self, hub, monkeypatch, script):
        from astrodeck.devices import fingerprint as _fp
        self.hub = hub
        self.script = list(script)
        self.solves = 0
        monkeypatch.setattr(_fp, "verdict",
                            lambda **kw: _fp.Verdict(focus_trusted=True))
        self.engine = SequenceEngine(hub)

        async def limits(target, *, cfg=None, plan=None, projected=True):
            return None
        monkeypatch.setattr(self.engine, "check_slew_limits", limits)

        async def goto(*a, **kw):
            raise RuntimeError("the test's mount does not slew")
        monkeypatch.setattr(hub, "goto_and_center", goto)

        async def solve(*a, **kw):
            self.solves += 1
            step = self.script.pop(0)
            if step == "dark":
                raise _dark_error()
            if step in (light.CLOUD, light.UNKNOWN):
                raise _verdict_error(step)
            return {}
        monkeypatch.setattr(hub, "solve_and_sync", solve)
        self.now = {"t": T0}
        self.window = {"open": True}
        monkeypatch.setattr(ResumeArm, "_window_open",
                            lambda arm, s, t: self.window["open"])
        monkeypatch.setattr(ResumeArm, "_can_solve", lambda arm: True)
        self.arm = ResumeArm(self.engine, hub, clock=lambda: self.now["t"])

    async def tick_at(self, t: float) -> None:
        self.now["t"] = t
        await self.arm.tick()


def _arm(name: str = "cap") -> Session:
    s = Session(name=name, status="dormant", plan=_plan(), auto_resume=True)
    session_store.save(s)
    return s


def _alerts(lines) -> list[str]:
    """The push alerts about no light: error-level lines, the level a
    default sink delivers (see the next test)."""
    return [m for lv, m, _s in lines if lv == "error" and "no light" in m]


def _held(lines) -> list[str]:
    return [m for lv, m, _s in lines
            if lv == "warning" and m.startswith("auto-resume held:")]


async def test_no_light_alerts_once_then_backs_off_hourly(sim_hub, monkeypatch,
                                                          bus_lines):
    """Three no-light verdicts in a row: one alert, a first retry after ten
    minutes, then hourly; one hold throughout, in words, keeping its
    ``since``.

    RED under mutant "alert on every retry" (the latch is never set),
    observed verbatim:

        E   AssertionError: ["auto-resume for 'cap': no light: the optic is capped, covered or obstructed. Its recovery plate solve read the camer...ry 60 min while it stays dark.", "auto-resume for 'cap': no light: the optic is capped, covered or obstructed (still)"]
        E   assert 2 == 1

    RED under mutant "retry every 10 min on no light" (``NO_LIGHT_RETRY_S``
    never used), observed verbatim:

        E   AssertionError: 600.0
        E   assert 1700001200.0 == (1700000600.0 + 3600.0)

    RED under mutant "the hold carries the error text" (the no-light refusal
    returns ``f"blind plate solve failed after restart ({e})"``, today's
    shape), observed verbatim:

        E   AssertionError: the no-light hold carries a number: 'blind plate solve failed after restart (plate solve failed: no light: the optic is capped, covered or obstructed (the frame reads at the level this camera reads with no light on it; the solver said: Not enough stars.) [median 251 ADU]) — refusing to slew a mount whose true position is unknown'
    """
    s = _arm()
    rig = _Rig(sim_hub, monkeypatch, ["dark", "dark", "dark"])

    await rig.tick_at(T0)
    assert rig.solves == 1
    assert len(_alerts(bus_lines)) == 1, bus_lines
    assert s.name in _alerts(bus_lines)[0]
    assert rig.arm._retry_at == T0 + RETRY_INTERVAL_S
    first = dict(rig.arm.hold)
    assert re.search(r"\d", first["reason"]) is None, (
        f"the no-light hold carries a number: {first['reason']!r}")
    assert light.NO_LIGHT_WORDS in first["reason"], first

    await rig.tick_at(T0 + RETRY_INTERVAL_S - 1)
    assert rig.solves == 1, "retried inside the first backoff"

    t1 = T0 + RETRY_INTERVAL_S
    await rig.tick_at(t1)
    assert rig.solves == 2
    assert len(_alerts(bus_lines)) == 1, _alerts(bus_lines)
    assert rig.arm._retry_at == t1 + NO_LIGHT_RETRY_S, (
        rig.arm._retry_at - t1)

    await rig.tick_at(t1 + NO_LIGHT_RETRY_S - 1)
    assert rig.solves == 2, "retried inside the hourly backoff"

    t2 = t1 + NO_LIGHT_RETRY_S
    await rig.tick_at(t2)
    assert rig.solves == 3
    assert len(_alerts(bus_lines)) == 1, _alerts(bus_lines)
    assert rig.arm._retry_at == t2 + NO_LIGHT_RETRY_S
    assert rig.arm.hold["reason"] == first["reason"]
    assert rig.arm.hold["since"] == T0, "the hold restarted its clock"
    held = _held(bus_lines)
    assert [h.rsplit("retrying in ", 1)[1] for h in held] == [
        f"{int(RETRY_INTERVAL_S / 60)} min",
        f"{int(NO_LIGHT_RETRY_S / 60)} min",
        f"{int(NO_LIGHT_RETRY_S / 60)} min"], held
    assert not rig.engine.running


async def test_the_no_light_alert_reaches_a_default_sink(sim_hub, monkeypatch,
                                                         bus_lines):
    """The alert line, fed through the real ``AlertDispatcher`` to a sink
    configured with nothing but its defaults, is sent. ``bus.log`` becomes an
    alert of type ``level``, and a default sink subscribes to "error" and not
    to "warning", so the level decides whether anyone's phone rings.

    RED under mutant "alert at warning level", observed verbatim:

        E   AssertionError: []
        E   assert [] == [('phone', 'e...stays dark.')]
    """
    from astrodeck.alerting import AlertDispatcher
    from astrodeck.config import AlertSink
    _arm()
    rig = _Rig(sim_hub, monkeypatch, ["dark"])
    await rig.tick_at(T0)
    lines = [(lv, m, src) for lv, m, src in bus_lines if "no light" in m
             and lv in ("warning", "error") and not m.startswith("auto-resume held")]
    assert len(lines) == 1, bus_lines
    level, message, source = lines[0]

    sink = AlertSink(id="phone", kind="ntfy", url="https://ntfy.example/x")
    cfg = types.SimpleNamespace(alerts=[sink])
    disp = AlertDispatcher(types.SimpleNamespace(log=lambda *a: None),
                           lambda: cfg)
    sent = []

    async def send(s, ev):
        sent.append((s.id, ev.type, ev.message))
        return True, None
    monkeypatch.setattr(disp, "_send", send)
    await disp._on_bus_event(types.SimpleNamespace(
        type="log", data={"level": level, "message": message,
                          "source": source}))
    assert sent == [("phone", "error", message)], sent


async def test_a_cloud_verdict_keeps_the_ten_minute_retry_and_no_alert(
        sim_hub, monkeypatch, bus_lines):
    """Control: light reached the sensor, so this is today's hold: today's
    words, a ten-minute retry every time, and no alert."""
    _arm()
    rig = _Rig(sim_hub, monkeypatch, [light.CLOUD, light.CLOUD])
    await rig.tick_at(T0)
    assert rig.arm._retry_at == T0 + RETRY_INTERVAL_S
    assert rig.arm.hold["reason"].startswith(
        "blind plate solve failed after restart (plate solve failed: Not "
        "enough stars. "), rig.arm.hold
    assert rig.arm.hold["reason"].endswith(
        "— refusing to slew a mount whose true position is unknown")
    t1 = T0 + RETRY_INTERVAL_S
    await rig.tick_at(t1)
    assert rig.solves == 2
    assert rig.arm._retry_at == t1 + RETRY_INTERVAL_S
    assert _alerts(bus_lines) == []
    assert not [m for lv, m, _s in bus_lines if lv == "error"], bus_lines


async def test_a_successful_solve_ends_the_spell(sim_hub, monkeypatch,
                                                 bus_lines):
    """No light, then a solve that works (the re-centre then fails, an
    ordinary refusal), then no light again: that is a second spell, so a
    second alert and a ten-minute first retry.

    RED under mutant "a successful solve does not end the spell", observed
    verbatim:

        E   AssertionError: ["auto-resume for 'cap': no light: the optic is capped, covered or obstructed. Its recovery plate solve read the camer...nothing will be imaged until the optic is uncovered. It looks again in 10 min, then every 60 min while it stays dark."]
        E   assert 1 == 2
    """
    _arm()
    rig = _Rig(sim_hub, monkeypatch, ["dark", "solved", "dark"])
    await rig.tick_at(T0)
    t1 = T0 + RETRY_INTERVAL_S
    await rig.tick_at(t1)
    assert rig.solves == 2
    assert rig.arm._retry_at == t1 + RETRY_INTERVAL_S, "premise: re-centre refused"
    t2 = t1 + RETRY_INTERVAL_S
    await rig.tick_at(t2)
    assert rig.solves == 3
    assert len(_alerts(bus_lines)) == 2, _alerts(bus_lines)
    assert rig.arm._retry_at == t2 + RETRY_INTERVAL_S


async def test_a_cloud_verdict_ends_the_spell(sim_hub, monkeypatch,
                                              bus_lines):
    """No light, then cloud (light reached the sensor: someone uncapped it
    under a cloudy sky), then no light: a second spell.

    RED under mutant "a cloud verdict does not end the spell", observed
    verbatim:

        E   AssertionError: ["auto-resume for 'cap': no light: the optic is capped, covered or obstructed. Its recovery plate solve read the camer...nothing will be imaged until the optic is uncovered. It looks again in 10 min, then every 60 min while it stays dark."]
        E   assert 1 == 2
    """
    _arm()
    rig = _Rig(sim_hub, monkeypatch, ["dark", light.CLOUD, "dark"])
    await rig.tick_at(T0)
    t1 = T0 + RETRY_INTERVAL_S
    await rig.tick_at(t1)
    assert rig.arm._retry_at == t1 + RETRY_INTERVAL_S
    t2 = t1 + RETRY_INTERVAL_S
    await rig.tick_at(t2)
    assert len(_alerts(bus_lines)) == 2, _alerts(bus_lines)
    assert rig.arm._retry_at == t2 + RETRY_INTERVAL_S


async def test_a_solve_nothing_could_judge_does_not_end_the_spell(
        sim_hub, monkeypatch, bus_lines):
    """No light, then a failure with no verdict (no reference), then no light:
    nothing showed light, so it is the same spell: no second alert, and the
    hourly retry. The spell ends on EVIDENCE of light, not on any failure
    that is not a ``NoLightError``.

    RED under mutant "any failed solve ends the spell" (the verdict is not
    asked, only the type), observed verbatim:

        E   AssertionError: ["auto-resume for 'cap': no light: the optic is capped, covered or obstructed. Its recovery plate solve read the camer...nothing will be imaged until the optic is uncovered. It looks again in 10 min, then every 60 min while it stays dark."]
        E   assert 2 == 1
    """
    _arm()
    rig = _Rig(sim_hub, monkeypatch, ["dark", light.UNKNOWN, "dark"])
    await rig.tick_at(T0)
    t1 = T0 + RETRY_INTERVAL_S
    await rig.tick_at(t1)
    assert rig.arm._retry_at == t1 + RETRY_INTERVAL_S, "today's retry"
    t2 = t1 + RETRY_INTERVAL_S
    await rig.tick_at(t2)
    assert len(_alerts(bus_lines)) == 1, _alerts(bus_lines)
    assert rig.arm._retry_at == t2 + NO_LIGHT_RETRY_S


async def test_the_spell_ends_with_the_night(sim_hub, monkeypatch, bus_lines):
    """No light tonight, the window closes, and tomorrow's first solve finds
    no light again: tonight's reader of the alert may not be tomorrow's, so
    it alerts again and starts from the ten-minute retry.

    RED under mutant "the spell survives the night", observed verbatim:

        E   AssertionError: ["auto-resume for 'cap': no light: the optic is capped, covered or obstructed. Its recovery plate solve read the camer...nothing will be imaged until the optic is uncovered. It looks again in 10 min, then every 60 min while it stays dark."]
        E   assert 1 == 2
    """
    _arm()
    rig = _Rig(sim_hub, monkeypatch, ["dark", "dark"])
    await rig.tick_at(T0)
    rig.window["open"] = False
    await rig.tick_at(T0 + 8 * 3600)
    rig.window["open"] = True
    t1 = T0 + 20 * 3600
    await rig.tick_at(t1)
    assert rig.solves == 2
    assert len(_alerts(bus_lines)) == 2, _alerts(bus_lines)
    assert rig.arm._retry_at == t1 + RETRY_INTERVAL_S


async def test_the_spell_is_per_session(sim_hub, monkeypatch, bus_lines):
    """No light on one session; the operator disarms it and arms another,
    whose first solve also finds no light: that session's own alert.

    RED under mutant "the latch is not keyed on the session" (a plain flag),
    observed verbatim:

        E   AssertionError: ["auto-resume for 'first': no light: the optic is capped, covered or obstructed. Its recovery plate solve read the cam...nothing will be imaged until the optic is uncovered. It looks again in 10 min, then every 60 min while it stays dark."]
        E   assert (1 == 2)
    """
    a = _arm("first")
    rig = _Rig(sim_hub, monkeypatch, ["dark", "dark"])
    await rig.tick_at(T0)
    a.auto_resume = False
    session_store.save(a)
    b = _arm("second")
    await rig.tick_at(T0 + RETRY_INTERVAL_S)
    assert rig.solves == 2
    alerts = _alerts(bus_lines)
    assert len(alerts) == 2 and b.name in alerts[1], alerts


async def test_a_disarm_ends_the_spell(sim_hub, monkeypatch, bus_lines):
    """No light; the operator disarms the session and arms it again; the next
    solve finds no light: a new spell and its alert. Disarming already resets
    the backoff and the give-up latch (``tick``), and a re-arm is somebody
    asking the rig to try again, who should hear what it finds.

    RED under mutant "a disarm keeps the spell", observed verbatim:

        E   AssertionError: ["auto-resume for 'cap': no light: the optic is capped, covered or obstructed. Its recovery plate solve read the camer...nothing will be imaged until the optic is uncovered. It looks again in 10 min, then every 60 min while it stays dark."]
        E   assert 1 == 2
    """
    s = _arm()
    rig = _Rig(sim_hub, monkeypatch, ["dark", "dark"])
    await rig.tick_at(T0)
    s.auto_resume = False
    session_store.save(s)
    await rig.tick_at(T0 + 60)
    s.auto_resume = True
    session_store.save(s)
    await rig.tick_at(T0 + 120)
    assert rig.solves == 2, "premise: the disarm cleared the backoff"
    assert len(_alerts(bus_lines)) == 2, _alerts(bus_lines)
    assert rig.arm._retry_at == T0 + 120 + RETRY_INTERVAL_S
