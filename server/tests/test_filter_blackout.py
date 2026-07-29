"""Blackout (opaque) filter slots.

A blackout slot is a carrier with no glass: it blocks the light path so darks
and bias can be shot without capping the scope. No wheel reports one, so the
flag is user-assigned and persisted per profile alongside the slot names.

Covers the four places the flag has to change behaviour, because getting any
one of them wrong produces a plausible-looking night of ruined data:
  * persistence + connect-time seeding (the flag has to outlive a reconnect);
  * the sequencer routing darks/bias to it and NOT applying a focus offset;
  * autofocus offset-learning refusing to sweep a slot that passes no light;
  * the status payload the UI reads to keep it out of the filter pickers.
"""
from __future__ import annotations

import pytest

import astrodeck.config as configmod
from astrodeck.devices.sim import SimFilterWheel, SimRig
from astrodeck.hub import Hub


@pytest.fixture
def filter_store(tmp_path, monkeypatch):
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    return tmp_path


# --------------------------------------------------------------- device model

def test_dark_slot_and_is_opaque():
    fw = SimFilterWheel(SimRig())
    # the sim wheel ships an 8th, blackout slot so this path is exercised
    assert len(fw.filter_names) == 8
    assert fw.dark_slot() == 7
    assert fw.is_opaque(7) and not fw.is_opaque(0)
    # out of range asks are False, not IndexError — callers ask about slots
    # that may predate an opaque list
    assert not fw.is_opaque(99) and not fw.is_opaque(-1)


def test_wheel_with_no_opaque_list_reports_no_dark_slot():
    fw = SimFilterWheel(SimRig())
    fw.filter_opaque = []
    assert fw.dark_slot() is None
    assert not fw.is_opaque(0)


# ---------------------------------------------------------------- persistence

def test_opaque_omitted_when_not_supplied(filter_store):
    """An older caller that never passes `opaque` must not write the key —
    otherwise every save would silently clear a stored blackout flag."""
    configmod.save_filter_config("p1", ["Lum", "Red"], [0, 12])
    assert configmod.load_filter_config("p1") == {
        "names": ["Lum", "Red"], "offsets": [0, 12]}


def test_opaque_roundtrips(filter_store):
    configmod.save_filter_config("p1", ["Dark", "Lum"], [0, 0], [True, False])
    assert configmod.load_filter_config("p1")["opaque"] == [True, False]


async def test_set_filter_names_persists_and_zeroes_opaque_offset(filter_store):
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        n = len(fw.filter_names)
        # mark slot 0 blackout and give it a (meaningless) offset
        opaque = [True] + [False] * (n - 1)
        res = await h.set_filter_names([""] * n, [55] + [7] * (n - 1), opaque)
        assert res["opaque"] == opaque
        # the offset through a slot with no light path is zeroed, not kept:
        # autofocus would otherwise apply a number that measures nothing
        assert res["offsets"][0] == 0
        assert res["offsets"][1] == 7
        saved = configmod.load_filter_config(
            configmod.config_store.cfg().active_profile_id)
        assert saved["opaque"] == opaque
    finally:
        await h.disconnect_all()


async def test_opaque_cleared_by_sending_all_false(filter_store):
    """Un-marking has to be expressible — the list is sent whole, not merged."""
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        n = len(fw.filter_names)
        await h.set_filter_names([""] * n, [], [True] + [False] * (n - 1))
        res = await h.set_filter_names([""] * n, [], [False] * n)
        assert res["opaque"] == [False] * n
        assert fw.dark_slot() is None
    finally:
        await h.disconnect_all()


async def test_seed_overlays_opaque_on_connect(filter_store):
    """The stored flags are the truth — no hardware fallback to preserve,
    because no wheel reports which of its slots is a blackout carrier."""
    configmod.save_filter_config(None, ["", "", ""], [0, 0, 0],
                                 [False, True, False])
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        assert fw.dark_slot() == 1
        # padded out to the wheel's real slot count, not truncated to 3
        assert len(fw.filter_opaque) == len(fw.filter_names)
    finally:
        await h.disconnect_all()


# ------------------------------------------------------------------- sequencer

def _engine(hub):
    from astrodeck.sequence.engine import SequenceEngine
    return SequenceEngine(hub)


def _plan():
    """A minimal real Plan — `_set_state` calls `plan.total_frames()`, so a
    bare attribute stub would fail inside the code under test rather than in it."""
    from astrodeck.sequence.models import SequencePlan
    return SequencePlan(targets=[], apply_filter_offsets=True)


def _step(frame_type: str, filt: str | None = None):
    from astrodeck.sequence.models import ExposureStep
    return ExposureStep(id="s1", filter=filt, exposure_s=1.0, count=1,
                        frame_type=frame_type)


@pytest.mark.parametrize("frame_type", ["Dark", "Bias", "dark", "BIAS"])
async def test_dark_and_bias_drive_to_the_blackout_slot(filter_store, frame_type):
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        await fw.set_position(0)
        eng = _engine(h)
        eng.plan = _plan()
        await eng._apply_filter(_step(frame_type))
        assert await fw.get_position() == fw.dark_slot()
    finally:
        await h.disconnect_all()


@pytest.mark.parametrize("frame_type", ["Light", "Flat"])
async def test_lights_and_flats_do_not_move_to_the_blackout_slot(
        filter_store, frame_type):
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        await fw.set_position(2)
        eng = _engine(h)
        eng.plan = _plan()
        await eng._apply_filter(_step(frame_type))
        assert await fw.get_position() == 2   # unchanged
    finally:
        await h.disconnect_all()


async def test_dark_without_a_blackout_slot_leaves_the_wheel_alone(filter_store):
    """The pre-existing behaviour for every wheel that has no blackout slot."""
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        fw.filter_opaque = []
        await fw.set_position(3)
        eng = _engine(h)
        eng.plan = _plan()
        await eng._apply_filter(_step("Dark"))
        assert await fw.get_position() == 3
    finally:
        await h.disconnect_all()


async def test_no_focus_offset_applied_moving_into_a_blackout_slot(filter_store):
    """A blackout slot's offset is a placeholder zero, not a measurement, so
    honouring it would yank the focuser to the reference position for a dark."""
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        foc = h.devices["focuser"]
        await fw.set_position(4)          # Ha, offset 120
        before = await foc.get_position()
        eng = _engine(h)
        eng.plan = _plan()
        await eng._apply_filter(_step("Dark"))
        assert await fw.get_position() == fw.dark_slot()
        assert await foc.get_position() == before
    finally:
        await h.disconnect_all()


# ------------------------------------------------------------ offset learning

async def test_learn_offsets_rejects_a_blackout_reference(filter_store):
    from astrodeck.devices.base import DeviceError
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        with pytest.raises(DeviceError, match="blackout"):
            await h.learn_filter_offsets(ref_slot=fw.dark_slot())
    finally:
        await h.disconnect_all()


async def test_learn_offsets_refuses_an_all_blackout_wheel(filter_store):
    from astrodeck.devices.base import DeviceError
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        fw.filter_opaque = [True] * len(fw.filter_names)
        with pytest.raises(DeviceError, match="nothing to focus through"):
            await h.learn_filter_offsets()
    finally:
        await h.disconnect_all()


# ---------------------------------------------------------------- status shape

async def test_status_carries_opaque_and_dark_slot(filter_store):
    h = Hub()
    await h.connect_sim()
    try:
        st = await h.poll_status()
        fwst = st["filterwheel"]
        assert fwst["opaque"] == [False] * 7 + [True]
        assert fwst["dark_slot"] == 7
        assert len(fwst["opaque"]) == len(fwst["names"])
    finally:
        await h.disconnect_all()
