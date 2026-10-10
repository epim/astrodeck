# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#946: a stamp that means "now" must read the clock when it is made, not when
its class was defined.

``SafetyReading.ts`` (devices/base.py) and ``FlowRecord.created_ts`` /
``updated_ts`` (flows/models.py) were ``default_factory=time.time``. That binds
the function object once, when the class body runs, so a test that pinned a
clock afterwards could not move the stamp. ``Hub.safety_reading()`` ages a
reading with ``time.time() - r.ts`` in hub.py: pin hub's clock to make a
reading fresh or stale and only the AGE side moved, while the stamp stayed on
the real clock. That is the #682 shape, two clocks on one path with one
pinned, and it reads as a safety bug or a clock dependence when it is neither.
``events.Event.ts`` already looked its clock up per call for this reason.

The tests pin the clock both ways a test can (the module's own ``time``, and
the shared ``time.time``), make the stamp, and read it back. The last two grade
the class: a sweep of every ``astrodeck`` dataclass and pydantic model for a
field whose factory IS a clock function, with a stand-in of each shape that
proves the detector can see one.

MUTATIONS RUN (each from a byte-for-byte backup of the production file, the
file restored with a copy and compared by md5sum afterwards):

  M1, ``SafetyReading.ts`` back to ``default_factory=time.time``
  (devices/base.py). test_a_safety_reading_is_stamped_by_the_pinned_clock,
  both pins, test_a_pinned_clock_ages_the_reading_it_also_stamped and
  test_no_astrodeck_field_binds_a_clock_at_class_creation.

  M2, ``FlowRecord.created_ts`` / ``updated_ts`` back to
  ``default_factory=time.time`` (flows/models.py).
  test_a_flow_record_is_stamped_by_the_pinned_clock, both pins, and the sweep.

  M3, in this file's own ``_clock_bound_fields``, the pydantic branch removed
  (``if issubclass(cls, BaseModel):`` -> ``if False:``).
  test_the_detector_sees_a_clock_bound_field_of_each_shape.

The failing line each printed is in the docstring of the test that caught it."""
from __future__ import annotations

import dataclasses
import sys
import time
from dataclasses import dataclass, field

import pytest
from pydantic import BaseModel, Field

import astrodeck.api.app  # noqa: F401  (loads the surface the server runs)
import astrodeck.devices.base as base_mod
import astrodeck.flows.models as models_mod
import astrodeck.hub as hub_module
from astrodeck.devices.base import SafetyReading
from astrodeck.flows.models import FlowRecord
from astrodeck.hub import (
    SAFETY_POLL_INTERVAL_S,
    SAFETY_READ_TIMEOUT_S,
    SAFETY_STALE_SLACK_S,
    Hub,
)

#: 2100-01-01 00:00 UTC. Decades from any real clock the suite runs on, so a
#: stamp that reads the real clock cannot equal it by accident.
_PINNED = 4_102_444_800.0


_real_monotonic = time.monotonic


class _Clock:
    """A stand-in for the ``time`` module that reads a settable instant.
    ``monotonic`` stays real: nothing here pins elapsed time."""

    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return _real_monotonic()


def _pin(monkeypatch, module, style: str) -> _Clock:
    """Pin the wall clock one of the two ways a test does it: swap the
    module's own ``time`` (the pin that reaches only that module), or swap
    ``time.time`` for every module at once."""
    clock = _Clock(_PINNED)
    if style == "module":
        monkeypatch.setattr(module, "time", clock)
    else:
        monkeypatch.setattr(time, "time", clock.time)
    return clock


PINS = pytest.mark.parametrize("style", ["module", "shared"])


@PINS
def test_a_safety_reading_is_stamped_by_the_pinned_clock(monkeypatch, style):
    """The stamp follows the clock the test pinned, and follows it again when
    that clock moves.

    RED under M1, both pins (observed, verbatim, [module]):

        AssertionError: assert 1791628669.3173513 == 4102444800.0
    """
    clock = _pin(monkeypatch, base_mod, style)
    assert SafetyReading(is_safe=True).ts == _PINNED
    clock.now = _PINNED + 42.0
    assert SafetyReading(is_safe=True).ts == _PINNED + 42.0


async def test_a_pinned_clock_ages_the_reading_it_also_stamped(
        monkeypatch, tmp_path):
    """The path the engine gates on. With hub's clock and the stamp's clock
    pinned to one instant, a reading made now is FRESH now and STALE once the
    pinned clock has moved past the poll window, however far the pin sits from
    the real clock. With the stamp on the real clock it was 2.3 billion
    seconds old the moment the test pinned hub's.

    RED under M1 (observed, verbatim):

        assert fresh.is_safe and not fresh.stale
        AssertionError: assert (False)
         +  where False = SafetyReading(is_safe=False, reason='safety reading
        stale (poller stopped)', source='pinned', detail={}, stale=True, ...
        ).is_safe
    """
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    hub = Hub()
    hub.devices["safety"] = object()          # any monitor: the cache is read
    clock = _Clock(_PINNED)
    monkeypatch.setattr(hub_module, "time", clock)
    monkeypatch.setattr(base_mod, "time", clock)
    hub._safety_reading = SafetyReading(is_safe=True, source="pinned")
    window = (SAFETY_POLL_INTERVAL_S + SAFETY_READ_TIMEOUT_S
              + SAFETY_STALE_SLACK_S)

    fresh = await hub.safety_reading()
    assert fresh.is_safe and not fresh.stale

    clock.now = _PINNED + window - 1.0
    assert not (await hub.safety_reading()).stale

    clock.now = _PINNED + window + 1.0
    aged = await hub.safety_reading()
    assert aged.stale and not aged.is_safe


@PINS
def test_a_flow_record_is_stamped_by_the_pinned_clock(monkeypatch, style):
    """A new record's created and updated stamps are the pinned instant.

    RED under M2, both pins (observed, verbatim, [module]):

        AssertionError: assert (1791628683.2...28683.2985098) == (4102444800.0, 4102444800.0)
    """
    clock = _pin(monkeypatch, models_mod, style)
    record = FlowRecord(name="pinned")
    assert (record.created_ts, record.updated_ts) == (_PINNED, _PINNED)
    clock.now = _PINNED + 60.0
    assert FlowRecord(name="later").created_ts == _PINNED + 60.0


# ---------------------------------------------------------------- the class

def _clock_functions() -> tuple:
    """The clock functions a default_factory must not BE, read now (not at
    import) so a replay plugin that swapped ``time.time`` is the one compared."""
    return (time.time, time.time_ns, time.monotonic, time.monotonic_ns,
            time.perf_counter)


def _clock_bound_fields(cls) -> list[str]:
    """Names of ``cls``'s own dataclass or pydantic fields whose default
    factory is a clock function, so is bound at class creation."""
    clocks = _clock_functions()
    factories: dict[str, object] = {}
    if dataclasses.is_dataclass(cls):
        for f in dataclasses.fields(cls):
            factories[f.name] = f.default_factory
    if issubclass(cls, BaseModel):
        for name, info in cls.model_fields.items():
            factories[name] = info.default_factory
    return [name for name, fn in factories.items()
            if any(fn is c for c in clocks)]


def test_the_detector_sees_a_clock_bound_field_of_each_shape():
    """The sweep below is only worth its green if it can go red: a dataclass
    and a pydantic model binding ``time.time`` are caught, and the same two
    looking it up per call are not.

    RED under M3, the pydantic branch removed (observed, verbatim):

        AssertionError: assert [] == ['ts', 'other']
    """
    @dataclass
    class BoundData:
        ts: float = field(default_factory=time.time)

    @dataclass
    class LookedUpData:
        ts: float = field(default_factory=lambda: time.time())

    class BoundModel(BaseModel):
        ts: float = Field(default_factory=time.time)
        other: float = Field(default_factory=time.monotonic)
        fine: list = Field(default_factory=list)

    class LookedUpModel(BaseModel):
        ts: float = Field(default_factory=lambda: time.time())

    assert _clock_bound_fields(BoundData) == ["ts"]
    assert _clock_bound_fields(BoundModel) == ["ts", "other"]
    assert _clock_bound_fields(LookedUpData) == []
    assert _clock_bound_fields(LookedUpModel) == []


def test_no_astrodeck_field_binds_a_clock_at_class_creation():
    """Every dataclass and pydantic model the server has loaded: none has a
    field whose factory is a clock function. The sweep names the classes it
    MUST have reached (the sites this issue fixed and the one that was already
    right), so a loader change that stops reaching them fails here instead of
    passing on an empty list.

    RED under M1 (observed, verbatim):

        AssertionError: fields bound to the real clock at class creation (use
        `lambda: time.time()` so a test can pin them):
        {'astrodeck.devices.base.SafetyReading': ['ts']}

    RED under M2 (observed, verbatim):

        AssertionError: fields bound to the real clock at class creation (use
        `lambda: time.time()` so a test can pin them):
        {'astrodeck.flows.models.FlowRecord': ['created_ts', 'updated_ts']}
    """
    swept: dict[str, type] = {}
    for modname, mod in list(sys.modules.items()):
        if mod is None or not (modname == "astrodeck"
                               or modname.startswith("astrodeck.")):
            continue
        for obj in list(vars(mod).values()):
            if isinstance(obj, type) and obj.__module__ == modname:
                swept[f"{modname}.{obj.__qualname__}"] = obj

    for reached in ("astrodeck.devices.base.SafetyReading",
                    "astrodeck.flows.models.FlowRecord",
                    "astrodeck.events.Event"):
        assert reached in swept, f"the sweep never reached {reached}"

    offenders = {name: bad for name, cls in swept.items()
                 if (bad := _clock_bound_fields(cls))}
    assert not offenders, (
        f"fields bound to the real clock at class creation (use "
        f"`lambda: time.time()` so a test can pin them): {offenders}")
