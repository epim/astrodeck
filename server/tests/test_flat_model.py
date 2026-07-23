from astrodeck.sequence.models import ExposureStep


def test_flat_fields_default_off():
    s = ExposureStep(exposure_s=2, count=5)
    assert s.adu_target == 0 and s.panel_brightness is None


def test_flat_fields_roundtrip():
    s = ExposureStep(exposure_s=2, count=5, frame_type="Flat",
                     adu_target=25000, panel_brightness=120)
    assert s.model_dump()["adu_target"] == 25000
