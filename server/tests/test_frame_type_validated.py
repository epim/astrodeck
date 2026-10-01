"""The frame type is one of four, checked where it comes in (#334).

``save_fits`` wrote ``IMAGETYP`` from the caller's ``frame_type`` as given,
and nothing upstream held that string to the four frame types: ``POST
/api/capture`` took ``CaptureBody.frame_type: str`` and a plan step took
``frame_type: str``. A frame type with one character outside printable ASCII
('L' + i-diaeresis + 'ght') failed every save, the #277 class ("a header
write never fails a capture") on one more card; an ASCII one that is not a
frame type was written, and no calibration reader expects it.

The fix, pinned here. ``fitsio.FRAME_TYPES`` is the four, spelled as
IMAGETYP has always carried them (Light, Dark, Bias, Flat), and
``fitsio.frame_type_name`` reads a value onto one of them:
CASE-INSENSITIVE, surrounding blanks ignored, and a blank is the default,
Light, which every reader's ``or "Light"`` already made of one. The capture
body and the plan step both take ``sequence.models.FrameType``, which
normalises through it, so 'light' and 'Light' are the same step and a bad
value is a 422, or a plan that refuses to load and names the step, before
any night starts. ``save_fits`` writes IMAGETYP only from a value that reads
as one of the four and omits the card otherwise, so a caller that bypassed
both edges still cannot fail the save.

Every mutation named below was run in a private scratch copy of ``server/``
(issue #254), never in the shared tree; the failure each produced is
recorded verbatim on the test that caught it.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from fastapi.testclient import TestClient
from pydantic import ValidationError

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.config import ConfigStore
from astrodeck.devices.base import CameraFrame
from astrodeck.imaging import fitsio
from astrodeck.plans import PlanImportError, PlanLibrary
from astrodeck.sequence.models import ExposureStep, SequencePlan

#: 'Light' with an i-diaeresis, written as an escape so this file is ASCII.
_LIGHT_DIAERESIS = "L\u00efght"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """test_guide_frame's app fixture: a config and captures of the test's
    own, and the singleton hub torn down after."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(app_module.hub.disconnect_all())
    finally:
        loop.close()


def _body(frame_type: str) -> dict:
    return {"exposure_s": 0.05, "gain": 100, "offset": 30, "binning": 1,
            "save": True, "target": "M31", "frame_type": frame_type}


# --- the capture route --------------------------------------------------------


def test_a_non_ascii_frame_type_is_a_422(client):
    """No camera is connected: the body is refused before the route runs, so
    the answer cannot be the camera's 409.

    Mutation 'plain str' (``frame_type: str = "Light"`` on ``CaptureBody``
    and on ``ExposureStep`` in place of ``FrameType``) went red on eight
    cases of this file: this one, the unknown-type case, the "light", "
    LIGHT " and blank spellings, the look-alike case and both plan cases;
    test_plans, test_guide_frame, test_capture_promote and test_group_models
    stayed green under it. Here, the route ran and found no camera:

        >       assert r.status_code == 422, r.text
        E       AssertionError: {"detail":"no camera connected"}
        E       assert 409 == 422
    """
    r = client.post("/api/capture", json=_body(_LIGHT_DIAERESIS))
    assert r.status_code == 422, r.text
    (err,) = r.json()["detail"]
    assert err["loc"] == ["body", "frame_type"], err
    assert _LIGHT_DIAERESIS in err["msg"], err


def test_an_ascii_value_that_is_no_frame_type_is_a_422_too(client):
    """'Snapshot' was saved, with an IMAGETYP no calibration reader knows.

    Mutation 'plain str' went red here with the same 409 (``AssertionError:
    {"detail":"no camera connected"}``)."""
    r = client.post("/api/capture", json=_body("Snapshot"))
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("given", ["light", "Light", " LIGHT ", ""])
def test_light_in_any_case_is_light(given):
    """CONTROL, and the documented rule: the case and surrounding blanks do
    not matter, a blank is Light, and every spelling reaches the hub as the
    one IMAGETYP has always carried.

    Mutation 'case-sensitive (exact spelling)' (``return text if text in
    FRAME_TYPES else None`` in place of the folded lookup in
    ``frame_type_name``) went red on "light" and " LIGHT ":

        E       pydantic_core._pydantic_core.ValidationError: 1 validation error for CaptureBody
        E       frame_type
        E         Value error, frame type 'light' is not one of Light, Dark, Bias, Flat (in any case) [type=value_error, input_value='light', input_type=str]

    Mutation 'blank refused' (the ``if not text: return "Light"`` lines
    removed) went red on the blank, with the same error for ``''``.
    Mutation 'plain str' went red on the three that are not already Light:
    ``AssertionError: assert 'light' == 'Light'``."""
    assert app_module.CaptureBody(frame_type=given).frame_type == "Light"
    assert ExposureStep(exposure_s=1, count=1,
                        frame_type=given).frame_type == "Light"


def test_a_look_alike_that_lower_cases_to_a_frame_type_is_refused():
    """'Dar' + KELVIN SIGN: ``str.lower`` maps the Kelvin sign onto a plain
    'k', so a lookup of the lower-cased text alone would read it as Dark. A
    value that needs a character outside ASCII to spell a frame type is not
    one, and would be a name no file or report ever carried.

    Mutation 'no ASCII check' (the ``if not text.isascii(): return None``
    lines removed from ``frame_type_name``) went red here alone, and so did
    'plain str':

        >       with pytest.raises(ValidationError):
        E       Failed: DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>
    """
    kelvin = "Dar" + chr(0x212A)
    assert kelvin.lower() == "dark"          # the trap this case is about
    with pytest.raises(ValidationError):
        app_module.CaptureBody(frame_type=kelvin)
    with pytest.raises(ValidationError):
        ExposureStep(exposure_s=1, count=1, frame_type=kelvin)


def test_a_light_in_lower_case_is_captured_and_written_as_light(client):
    """CONTROL through the route and the hub, on the sim: 'light' is
    accepted, and the frame it saves says ``IMAGETYP = 'Light'``, the
    spelling every frame this rig ever wrote carries.

    Mutation 'raw IMAGETYP' (``hdr["IMAGETYP"] = frame_type`` restored in
    ``save_fits``) left this case green, because the edge had already
    normalised the value: the writer's own check is pinned on the writer
    below. Mutation 'case-sensitive (exact spelling)' went red here:

        >       assert r.status_code == 200, r.text
        E       AssertionError: {"detail":[{"type":"value_error","loc":["body","frame_type"],"msg":"Value error, frame type 'light' is not one of Light, Dark, Bias, Flat (in any case)","input":"light","ctx":{"error":{}}}],"code":"invalid_request"}
        E       assert 422 == 200
    """
    assert client.post("/api/connect/sim").status_code == 200
    app_module.hub.last_frame = None
    r = client.post("/api/capture", json=_body("light"))
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 20
    path = None
    while time.monotonic() < deadline:
        frame = app_module.hub.last_frame
        path = getattr(frame, "saved_path", None) if frame else None
        if path and Path(path).exists():
            break
        time.sleep(0.05)
    assert path, "the capture never saved a frame"
    assert fits.getheader(path)["IMAGETYP"] == "Light"


# --- the plan step ------------------------------------------------------------


def _plan(*frame_types: str) -> dict:
    return {"name": "t", "targets": [{
        "name": "M31", "ra_hours": 0.7, "dec_deg": 41.3,
        "steps": [{"id": f"s{i}", "exposure_s": 60, "count": 3,
                   "frame_type": ft} for i, ft in enumerate(frame_types)]}]}


def test_a_plan_step_with_a_non_ascii_frame_type_refuses_to_load(tmp_path):
    """Refused when the plan loads, naming the step: its place in the plan
    (``targets.0.steps.1``) and the value. The plan library's import, the
    one door a hand-edited plan file comes through, says the same.

    Mutation 'plain str' went red here, the plan loading with the value as
    given:

        >       with pytest.raises(ValidationError) as caught:
        E       Failed: DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>
    """
    with pytest.raises(ValidationError) as caught:
        SequencePlan.model_validate(_plan("Light", _LIGHT_DIAERESIS))
    (err,) = caught.value.errors()
    assert err["loc"] == ("targets", 0, "steps", 1, "frame_type"), err
    assert _LIGHT_DIAERESIS in err["msg"], err
    with pytest.raises(PlanImportError) as imported:
        PlanLibrary(tmp_path / "plans").import_plan(
            {"schema_version": 1, "plan": _plan(_LIGHT_DIAERESIS)})
    assert "targets.0.steps.0.frame_type" in str(imported.value)


def test_a_plan_step_in_any_case_loads_as_the_frame_type():
    """CONTROL: the four in whatever case a hand edit gave them load, each
    as the spelling the engine and the UI compare against (the engine's
    dark queue tests ``!= "Light"``, the UI ``=== "Light"``).

    Mutation 'plain str' went red here, the values kept as given:

        >       assert [s.frame_type for s in plan.targets[0].steps] == [
        E       AssertionError: assert ['light', 'DA..., ' Flat', ''] == ['Light', 'Da...lat', 'Light']

    Mutation 'case-sensitive (exact spelling)' went red here with three
    errors, the first ``targets.0.steps.0.frame_type / Value error, frame
    type 'light' is not one of Light, Dark, Bias, Flat (in any case)``, and
    'blank refused' with one, on ``targets.0.steps.4.frame_type``."""
    plan = SequencePlan.model_validate(
        _plan("light", "DARK", "bias", " Flat", ""))
    assert [s.frame_type for s in plan.targets[0].steps] == [
        "Light", "Dark", "Bias", "Flat", "Light"]


# --- the writer ---------------------------------------------------------------


def _frame() -> CameraFrame:
    return CameraFrame(data=np.arange(12, dtype=np.uint16).reshape(3, 4),
                       exposure_s=1.0, gain=0, offset=0, binning=1,
                       bayer_pattern=None, temperature_c=None,
                       timestamp=1.7e9)


@pytest.mark.parametrize("given,written", [
    ("Light", "Light"), ("dark", "Dark"), (" FLAT ", "Flat"), ("Bias", "Bias"),
    (_LIGHT_DIAERESIS, None), ("Snapshot", None),
])
def test_save_fits_writes_imagetyp_only_from_a_frame_type(tmp_path, given,
                                                          written):
    """A caller that reached ``save_fits`` past both edges: the frame is
    saved whatever it passed, and IMAGETYP is one of the four or absent,
    never a placeholder and never a value a calibration reader cannot read.

    Mutation 'raw IMAGETYP' (``hdr["IMAGETYP"] = frame_type`` restored) went
    red on all but "Light" and "Bias". The non-ASCII case (the console's
    encoding turned the i-diaeresis into U+FFFD, written as an escape):

        >       path = fitsio.save_fits(_frame(), tmp_path / "f.fits", frame_type=given)
        ...
        E                   ValueError: FITS header values must contain standard printable ASCII characters; 'L\\ufffdght' contains characters not representable in ASCII or non-printable characters.

    and "dark": ``AssertionError: assert 'dark' == 'Dark'``. Mutation
    'case-sensitive (exact spelling)' went red on "dark" and " FLAT ",
    the card omitted: ``AssertionError: assert None == 'Dark'``."""
    path = fitsio.save_fits(_frame(), tmp_path / "f.fits", frame_type=given)
    assert fits.getheader(path).get("IMAGETYP") == written
