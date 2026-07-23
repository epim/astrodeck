from astrodeck.calibration.keys import (
    CalKey, key_from_header, temp_bin, key_index_id, CAL_FRAME_TYPES)


def test_light_header_is_not_a_cal_key():
    assert key_from_header({"IMAGETYP": "Light", "EXPTIME": 300}) is None


def test_dark_key_extraction():
    k = key_from_header({"IMAGETYP": "Dark Frame", "EXPTIME": 300.0, "GAIN": 100,
                         "OFFSET": 30, "CCD-TEMP": -10.0, "XBINNING": 1})
    assert k == CalKey("DARK", 300.0, 100, 30, -10.0, 1, "")


def test_bias_folds_exposure_to_zero_and_drops_filter():
    k = key_from_header({"IMAGETYP": "Bias", "EXPTIME": 0.001, "FILTER": "Ha",
                         "GAIN": 100, "OFFSET": 30, "XBINNING": 1})
    assert k.frame_type == "BIAS" and k.exposure_s == 0.0 and k.filter == ""


def test_flat_keeps_filter():
    k = key_from_header({"IMAGETYP": "FlatField", "EXPTIME": 2.5, "FILTER": "Ha",
                         "GAIN": 100, "XBINNING": 2})
    assert k.frame_type == "FLAT" and k.filter == "Ha" and k.binning == 2


def test_missing_ccdtemp_is_none():
    assert key_from_header({"IMAGETYP": "Dark", "EXPTIME": 1}).temp_c is None


def test_temp_bin_quantizes_to_center():
    assert temp_bin(-9.3, 5.0) == -10.0
    assert temp_bin(-7.4, 5.0) == -5.0
    assert temp_bin(None, 5.0) is None
    assert temp_bin(-9.3, 0) == -9.3          # disabled


def test_index_id_buckets_by_binned_temp():
    a = key_from_header({"IMAGETYP": "Dark", "EXPTIME": 300, "GAIN": 100,
                         "OFFSET": 30, "CCD-TEMP": -9.3, "XBINNING": 1})
    b = key_from_header({"IMAGETYP": "Dark", "EXPTIME": 300, "GAIN": 100,
                         "OFFSET": 30, "CCD-TEMP": -10.7, "XBINNING": 1})
    assert key_index_id(a, 5.0) == key_index_id(b, 5.0)   # same 5C bucket


def test_index_id_flat_ignores_exposure():
    a = key_from_header({"IMAGETYP": "Flat", "EXPTIME": 2.0, "FILTER": "Ha", "GAIN": 100})
    b = key_from_header({"IMAGETYP": "Flat", "EXPTIME": 3.5, "FILTER": "Ha", "GAIN": 100})
    assert key_index_id(a, 5.0) == key_index_id(b, 5.0)
