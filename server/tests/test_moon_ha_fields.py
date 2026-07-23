from __future__ import annotations
import pytest
from pydantic import ValidationError
from astrodeck.sequence.models import Schedule, Target, SequencePlan


def test_defaults_are_off():
    s = Schedule()
    assert s.min_moon_sep_deg == 0.0
    assert s.max_moon_illum_pct == 0.0
    assert s.max_hour_angle_h == 0.0


def test_legacy_plan_without_fields_deserializes():
    # a saved plan predating PRO-14 has no moon/HA keys — must default, not 422.
    t = Target(name="M31", ra_hours=0.71, dec_deg=41.27,
               schedule={"start_mode": "now", "min_altitude_deg": 30})
    assert t.schedule.min_moon_sep_deg == 0.0
    assert t.schedule.max_hour_angle_h == 0.0


@pytest.mark.parametrize("field,bad", [
    ("min_moon_sep_deg", 200.0), ("min_moon_sep_deg", -1.0),
    ("max_moon_illum_pct", 101.0), ("max_moon_illum_pct", -1.0),
    ("max_hour_angle_h", 13.0), ("max_hour_angle_h", -0.5),
])
def test_out_of_range_rejected(field, bad):
    with pytest.raises(ValidationError):
        Schedule(**{field: bad})
