"""Task 1: stable ids on Target/ExposureStep (sessions spec §1).

Pydantic backfills a uuid4 hex on every validation of an id-less plan (the
server-side backfill seam engine.start relies on); provided ids round-trip."""
from astrodeck.sequence.models import SequencePlan


def _plan_dict() -> dict:
    return {"name": "P", "targets": [{
        "name": "M42", "ra_hours": 5.5881, "dec_deg": -5.3911,
        "steps": [{"exposure_s": 60, "count": 3}],
    }]}


def test_idless_plan_gets_unique_backfilled_ids():
    a = SequencePlan.model_validate(_plan_dict())
    b = SequencePlan.model_validate(_plan_dict())
    assert a.targets[0].id and a.targets[0].steps[0].id
    assert len(a.targets[0].id) == 32          # uuid4().hex
    assert a.targets[0].id != b.targets[0].id  # fresh per validation
    assert a.targets[0].steps[0].id != b.targets[0].steps[0].id


def test_provided_ids_are_preserved():
    d = _plan_dict()
    d["targets"][0]["id"] = "tfixed"
    d["targets"][0]["steps"][0]["id"] = "sfixed"
    p = SequencePlan.model_validate(d)
    assert p.targets[0].id == "tfixed"
    assert p.targets[0].steps[0].id == "sfixed"


def test_model_dump_round_trip_keeps_ids():
    p = SequencePlan.model_validate(_plan_dict())
    q = SequencePlan.model_validate(p.model_dump())
    assert q.targets[0].id == p.targets[0].id
    assert q.targets[0].steps[0].id == p.targets[0].steps[0].id
