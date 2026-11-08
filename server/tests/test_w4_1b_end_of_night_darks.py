# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The quick sheet's DARKS AFTER chip takes darks at the end of the night
(#818, the engine half of #1111 C-05; consolidation wave 4, WP 4.1b).

THE CLASS: a claim nothing keeps. The chip draws SESSION REPORT 'target done'
into a CALIBRATION QUEUE's 'do' port, which compiles to the rule
``on_target_complete -> calib``. The engine has no ``calib`` rule action, so the
compile reported "this rule will not run" and funded no end-of-night darks
(``tests/test_w16_quick_darks_after_wiring.py`` held that, as the premise).

THE END-OF-NIGHT ACTION IS THE WIND-DOWN'S DAY-DARKS PHASE
(``SequenceEngine._day_darks``): once, when the run ends the way its plan said
it would (every target done, dawn, a window closed, the stop time), between the
park and the warm ramp, the one moment the mount is stowed, the cover is shut
and the sensor is still at setpoint. A dark is indexed by temperature, so a
dark taken before the park (cover open, mount tracking) or after the warm ramp
has started would be cover for nothing. The phase is funded by
``to_plan.plan_extras`` (``plan.day_darks``) for a queue wired to SHUTDOWN
COMPLETE, and now for a queue wired to 'target done' too.

What these hold:

* the compile of the chip's wiring funds the lane and reports the wire as
  honoured, not as a rule that will not run;
* the control: a queue fed from nothing the lane reads is still unfunded;
* a sim night whose plan carries that funding takes exactly its quota of darks
  after the lights, once, on every planned ending;
* an aborted night, an unsafe stop and an error take none.

Named mutant: ``plan_extras``'s end-of-night triggers narrowed back to
``on_shutdown_complete`` alone: ``test_the_chip_funds_the_end_of_night_lane``
fails on ``plan.day_darks``.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowNode
from astrodeck.flows.nodes import create_params
from astrodeck.flows.to_plan import plan_extras, to_sequence_plan
from astrodeck.flows.wizard import quick
from astrodeck.hub import Hub
from astrodeck.sequence.engine import SafetyAbort, SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target

_TARGET = {"name": "M31", "ra": "00h 42m 44s", "dec": f"+41{chr(176)} 16' 09\""}


def _quick_with_darks_after():
    """The quick flow with the chip's edit applied: a CALIBRATION QUEUE with
    darks and bias Always and flats Skip, started by the report's 'done'
    event (``flowGraphExtras.withDarksAfter``, mirrored; the UI test
    ``w16QuickDarksCopy`` pins the other end of the mirror)."""
    graph = quick(_TARGET, subs_per_filter=10, filters=["L", "R"],
                  wheel=["L", "R", "G", "B"]).graph
    report = next(n for n in graph.nodes if n.type == "report")
    graph.nodes.append(FlowNode(
        id="darksQueue", type="calib", x=0.0, y=900.0,
        params={**create_params("calib"), "darks": "Always", "bias": "Always",
                "flats": "Skip"}))
    graph.edges.append(FlowEdge(**{"from": report.id, "fromPort": "done",
                                   "to": "darksQueue", "toPort": "do"}))
    return graph


def _compiled(graph):
    plan, unmapped = to_sequence_plan(compile_plan(graph, "quick"), graph)
    return plan, {u["key"]: u for u in unmapped}


# ------------------------------------------------------------- the compile

def test_the_chip_funds_the_end_of_night_lane():
    """THE HEADLINE. The chip's wiring funds ``plan.day_darks`` from the
    queue's quota, so the engine has something to take at the end of the
    night. RED under the named mutant (the lane's trigger narrowed back to
    SHUTDOWN COMPLETE): ``day_darks`` is 0."""
    plan, _notes = _compiled(_quick_with_darks_after())
    assert plan.day_darks > 0, (
        "the DARKS AFTER chip funds no end-of-night darks: the queue wired to "
        "'target done' never reaches the day-darks lane")


def test_the_wire_is_reported_as_honoured_not_as_a_loss():
    """The compile used to say 'this rule will not run' of this wire, at warn
    weight, and the saved flow carried the warning."""
    _plan, notes = _compiled(_quick_with_darks_after())
    row = notes.get("instructions[on_target_complete -> calib.do]")
    assert row is not None, f"the wire vanished from the report: {sorted(notes)}"
    assert row["level"] == "note", row["detail"]
    assert "will not run" not in row["detail"]
    assert "end of the night" in row["detail"], (
        "the note must say WHEN the darks are taken (the wire says 'target "
        f"done', the engine takes them when the night ends): {row['detail']}")
    assert not [k for k in notes if k == "instructions[on_target_complete -> calib]"], (
        "the old 'engine has no calib action' row is still reported")


def test_the_lane_still_funds_the_cloud_hold_darks():
    """Funding the end-of-night lane takes nothing from the hold's."""
    plan, _notes = _compiled(_quick_with_darks_after())
    assert plan.cloud_hold_darks > 0


def test_control_a_queue_wired_to_neither_is_unfunded():
    """A queue fed only from a CLOUD WATCH asks for hold darks, and holding a
    camera cold for an extra hour on a night nobody asked for it is a real
    cost: it stays unfunded. Without this the headline above could pass
    against a lane that is funded for every queue."""
    compiled = {"automation": {"calibration_queue": {"quota": 20}},
                "instructions": [{"when": "on_clouds_in", "action": "calib",
                                  "to_port": "do"}]}
    extras = plan_extras(compiled)
    assert extras.get("cloud_hold_darks") == 20
    assert not extras.get("day_darks")


def test_a_zero_quota_funds_nothing_whoever_feeds_it():
    compiled = {"automation": {"calibration_queue": {"quota": 0}},
                "instructions": [{"when": "on_target_complete",
                                  "action": "calib", "to_port": "do"}]}
    assert not plan_extras(compiled).get("day_darks")


# ------------------------------------------------------------ the sim night

@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


_QUOTA = 3


def _night_plan() -> SequencePlan:
    """What the chip's compile funds, on a target the sim can shoot: the
    compiled plan's own ``day_darks`` and its end-of-night ending. The quota
    is the compile's (checked equal to the queue's below), shortened so the
    night is quick."""
    compiled, _ = _compiled(_quick_with_darks_after())
    assert compiled.day_darks > 0
    light = Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                   autofocus_first=False,
                   steps=[ExposureStep(filter="L", exposure_s=1.0, gain=100,
                                       offset=30, count=2)])
    return SequencePlan(name="Darks after", guide=False, dither_every=0,
                        targets=[light], day_darks=min(compiled.day_darks,
                                                       _QUOTA),
                        park_when_done=True, warm_cooler_when_done=False)


class Order:
    """What the night does, in order: the scheduler marker, every Dark capture
    (saved or not), the park and the warm."""

    def __init__(self, hub, eng, monkeypatch, scheduler):
        self.events: list[tuple] = []
        real_capture = hub.capture
        tel = hub.devices["telescope"]
        real_park = tel.park

        async def capture(exposure_s, gain, offset, binning=1, **kw):
            self.events.append(("capture", kw.get("frame_type", "Light"),
                                bool(kw.get("save", True))))
            return await real_capture(exposure_s, gain, offset, binning, **kw)

        async def park(*a, **kw):
            self.events.append(("park",))
            return await real_park(*a, **kw)

        async def run_scheduled(plan):
            self.events.append(("scheduled",))
            await scheduler(eng, plan)

        monkeypatch.setattr(hub, "capture", capture)
        monkeypatch.setattr(tel, "park", park)
        monkeypatch.setattr(eng, "_run_scheduled", run_scheduled)

    def darks(self) -> list[tuple]:
        return [e for e in self.events if e[:2] == ("capture", "Dark")]


async def _night(hub, monkeypatch, scheduler):
    eng = SequenceEngine(hub)
    order = Order(hub, eng, monkeypatch, scheduler)
    eng.start(_night_plan())
    await eng._task
    return eng, order


async def _ends_normally(eng, plan):
    return None


async def _ends_at_dawn(eng, plan):
    eng._dawn_cutoff = True


async def _is_aborted_unsafe(eng, plan):
    raise SafetyAbort("test: rain")


async def _errors(eng, plan):
    raise RuntimeError("test: the scheduler fell over")


@pytest.mark.parametrize("scheduler", [_ends_normally, _ends_at_dawn],
                         ids=["targets done or stopped", "dawn"])
@pytest.mark.asyncio
async def test_a_planned_ending_takes_the_darks_once_after_the_lights(
        sim_hub, monkeypatch, scheduler):
    """The quota, and no more, after the scheduler returned and the mount is
    parked: one lane, once. (Both endings reach the wind-down's day-darks
    phase; the unplanned ones below do not.)"""
    eng, order = await _night(sim_hub, monkeypatch, scheduler)
    saved = [e for e in order.darks() if e[2]]
    assert len(saved) == _QUOTA, order.events
    names = [e[0] for e in order.events]
    marker = names.index("scheduled")
    first_dark = next(i for i, e in enumerate(order.events)
                      if e[:2] == ("capture", "Dark"))
    assert marker < first_dark, "darks were taken before the scheduler ended"
    assert "park" in names and names.index("park") < first_dark, (
        f"the darks were taken before the mount was parked: {order.events}")


@pytest.mark.parametrize("scheduler", [_is_aborted_unsafe, _errors],
                         ids=["unsafe stop", "error"])
@pytest.mark.asyncio
async def test_an_unplanned_ending_takes_no_darks(sim_hub, monkeypatch,
                                                  scheduler):
    """The rig is being put away because something is wrong: no hour of
    calibration first."""
    _eng, order = await _night(sim_hub, monkeypatch, scheduler)
    assert order.darks() == [], order.events


@pytest.mark.asyncio
async def test_an_operator_abort_takes_no_darks(sim_hub, monkeypatch):
    """An Abort landing in the night (the operator's STOP) is no planned
    ending either."""
    import asyncio
    eng = SequenceEngine(sim_hub)

    async def held(plan):
        await asyncio.sleep(3600)

    order = Order(sim_hub, eng, monkeypatch, lambda e, p: held(p))
    eng.start(_night_plan())
    for _ in range(50):
        await asyncio.sleep(0)
        if any(e == ("scheduled",) for e in order.events):
            break
    await eng.abort()
    assert order.darks() == [], order.events
    assert eng.state.get("end_reason") == "aborted", eng.state
