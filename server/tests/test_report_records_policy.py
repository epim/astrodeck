"""Stage A (#239): the report says what the night actually ran under.

Once twelve settings can come from either the plan or the rig, the plan alone
can no longer answer "which gates were on that night" - and that question gets
asked months later, from a report, about frames that already exist. The Polar
incident was exactly this shape: two layers, and the surface an operator reads
showed the losing one.
"""
import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.policy import MOVED_FIELDS


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def _plan(**kw) -> SequencePlan:
    return SequencePlan(
        name="policy", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(name="A", ra_hours=5.5881, dec_deg=-5.3911,
                        center=False, autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=1)])],
        **kw)


async def test_the_report_records_every_resolved_value_and_who_said_so(sim_hub):
    cfg = hub_module.config_store.cfg()
    cfg.standards.min_stars = 40
    eng = SequenceEngine(sim_hub)
    # one override, everything else inherited
    eng.start(_plan(max_eccentricity=0.7))
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    rep = eng.reporter.build()
    pol = rep.policy
    assert set(pol) == set(MOVED_FIELDS), "every moved field, or the record lies by omission"
    assert pol["min_stars"] == {"value": 40, "source": "rig"}
    assert pol["max_eccentricity"] == {"value": 0.7, "source": "plan"}


async def test_the_record_survives_serialization(sim_hub):
    """It is read back off disk months later, not out of memory."""
    eng = SequenceEngine(sim_hub)
    eng.start(_plan(min_stars=12))
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    raw = eng.reporter.build().model_dump()
    assert raw["policy"]["min_stars"] == {"value": 12, "source": "plan"}
