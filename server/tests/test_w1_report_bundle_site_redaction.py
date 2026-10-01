"""#567: a viewer's session report and bundle downloads carry the site.

TWO CARRIERS in the report itself, both the #19/#166 class this codebase has
closed on the live status frame and the resume-arm hold before (see
``astrodeck/api/redact.py``'s module docstring): ``FrameRecord.altitude_deg``
is a target's altitude at a known time and RA/Dec, which pins the observer to
a circle on the Earth (latitude, #19); a ``sky_angles`` row's ``exposed_at``
times a flip re-slew OR a mosaic's meridian-wait hop, both made AT a computed
transit, so it is the local sidereal time at a known instant (longitude,
#166). Neither route redacted either one before this fix -- only
``saved_path`` was ever stripped.

TWO MORE CARRIERS in the stacking-bundle routes, and the second is the one
that is easy to miss by reading only the issue's own guess at the mechanism:
``build_bundle`` copies ``FrameRecord.altitude_deg`` onto every light row
UNCONDITIONALLY, not only when the caller opts into ``weight_altitude``. So
``GET .../bundle.zip``'s ``manifest.json``/``weights.csv`` carried the raw
altitude to a plain viewer on the DEFAULT (``weight_altitude=False``)
request, and refusing the option alone (this file's ``TestWeightAltitudeIs
Refused``) would have left that leak standing. Both routes are covered here:
``TestBundleManifestAndCsvStripAltitude`` proves the unconditional leak is
closed independent of the flag.

Named mutants (each reproduced by hand from a byte backup, in-worktree, and
reverted byte-identically -- see this WP's dispatch return for the verbatim
failing assertion under each):

  M-FRAME-ALT   ``_withhold_report_site_derived`` stops dropping a frame's
                ``altitude_deg`` -> ``TestRedactReportFor::
                test_a_viewer_loses_altitude_deg_from_every_frame`` goes red.
  M-SKY-EXPOSED ``_withhold_report_site_derived`` stops nulling a
                ``sky_angles`` row's ``exposed_at`` -> ``TestRedactReportFor::
                test_a_viewer_loses_exposed_at_from_every_sky_angle_row``
                goes red.
  M-CSV-COL     ``report_csv_columns`` stops dropping ``altitude_deg`` for a
                non-holder -> ``TestReportCsvColumns::
                test_a_viewer_loses_the_altitude_column`` goes red.
  M-REFUSE      ``_refuse_weight_altitude_for`` never raises -> every case in
                ``TestWeightAltitudeIsRefused`` goes red.
  M-BUNDLE-SCRUB ``redact_bundle_manifest_for``/``redact_bundle_csv_for``
                return their input unchanged for a non-holder ->
                ``TestBundleManifestAndCsvStripAltitude`` goes red.
"""
from __future__ import annotations

import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.hub as hub_module
from astrodeck.api.redact import (_redact_report_for, _withhold_report_site_derived,
                                  redact_bundle_csv_for, redact_bundle_manifest_for,
                                  report_csv_columns)
from astrodeck.auth import (Principal, principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.auth.capabilities import CAP_CONTROL_CAPTURE
from astrodeck.sequence.report import FrameRecord, SessionReport

VIEWER = principal_for_role("viewer")
OPERATOR = principal_for_role("operator")
ADMIN = principal_for_role("admin")

# A control.capture holder that does NOT hold view.site_derived: Principal's
# own contract says a viewer-LINK's caps are an explicit per-link set, not
# necessarily a whole role's, so the materialize route must check the
# capability rather than assume every control.capture holder today also
# holds view.site_derived (both current holders -- operator, admin -- do).
CAPTURE_ONLY = Principal(role="capture-only", email=None,
                         caps=frozenset({CAP_CONTROL_CAPTURE}), jti=None)

SKY_ROW = {
    "exposed_at": 1_800_000_000.0,
    "target": "Made-Up Target",
    "pier_side": "west",
    "mechanical_deg": 12.5,
    "pa_deg": 88.0,
    "source": "rotate to PA",
}


def _report_payload(**sky_angles_override) -> dict:
    """A raw report-shaped dict, as ``SessionReport.model_dump()`` would hand
    ``_redact_report_for`` -- exactly the input the JSON route builds."""
    return {
        "id": "r1",
        "frames": [{"ts": 1.0, "target": "Made-Up Target", "altitude_deg": 41.7,
                    "hfr": 2.0}],
        "sky_angles": [dict(SKY_ROW, **sky_angles_override)],
    }


# ============================================================ _redact_report_for

class TestRedactReportFor:
    def test_a_viewer_loses_altitude_deg_from_every_frame(self):
        out = _redact_report_for(_report_payload(), VIEWER)
        assert "altitude_deg" not in out["frames"][0], (
            f"a viewer's frame still carried altitude_deg: {out['frames'][0]}")

    def test_a_viewer_loses_exposed_at_from_every_sky_angle_row(self):
        out = _redact_report_for(_report_payload(), VIEWER)
        assert out["sky_angles"][0]["exposed_at"] is None, (
            f"a viewer's sky_angles row still carried exposed_at: "
            f"{out['sky_angles'][0]}")

    def test_a_viewer_keeps_every_other_sky_angle_field(self):
        """Withholding the crossing time is not withholding the row: a viewer
        still needs to see which target/pier side/PA a rotator held."""
        out = _redact_report_for(_report_payload(), VIEWER)
        row = out["sky_angles"][0]
        assert row["target"] == "Made-Up Target"
        assert row["pier_side"] == "west"
        assert row["pa_deg"] == 88.0
        assert row["source"] == "rotate to PA"

    def test_every_sky_angle_row_is_withheld_not_only_a_flip(self):
        """H4 orchestrator ruling: a mosaic's meridian-wait hop is timed at a
        crossing too, not only a flip re-slew, so the rule cannot key off
        ``source`` -- it withholds every row."""
        payload = _report_payload()
        payload["sky_angles"].append(
            dict(SKY_ROW, source="meridian-wait hop", exposed_at=1_800_003_600.0))
        out = _redact_report_for(payload, VIEWER)
        assert all(row["exposed_at"] is None for row in out["sky_angles"]), (
            f"a non-flip row kept its exposed_at: {out['sky_angles']}")

    def test_an_operator_keeps_both_fields(self):
        out = _redact_report_for(_report_payload(), OPERATOR)
        assert out["frames"][0]["altitude_deg"] == 41.7
        assert out["sky_angles"][0]["exposed_at"] == SKY_ROW["exposed_at"]

    def test_an_admin_keeps_both_fields(self):
        out = _redact_report_for(_report_payload(), ADMIN)
        assert out["frames"][0]["altitude_deg"] == 41.7
        assert out["sky_angles"][0]["exposed_at"] == SKY_ROW["exposed_at"]

    def test_a_viewer_with_no_principal_object_is_treated_as_no_principal(self):
        """``principal=None`` (an unauthenticated resolution some callers use
        for a pre-auth path) must fail CLOSED, the same as an explicit
        no-cap principal -- never treated as a holder."""
        out = _redact_report_for(_report_payload(), None)
        assert "altitude_deg" not in out["frames"][0]
        assert out["sky_angles"][0]["exposed_at"] is None

    def test_the_csv_shaped_payload_with_no_sky_angles_key_is_a_no_op_there(self):
        """``report_frames_csv`` hands in a bare ``{"frames": [...]}`` with no
        top-level ``sky_angles`` at all -- must not KeyError."""
        payload = {"frames": [{"ts": 1.0, "altitude_deg": 41.7}]}
        out = _redact_report_for(payload, VIEWER)
        assert "altitude_deg" not in out["frames"][0]
        assert "sky_angles" not in out

    def test_the_input_payload_is_never_mutated(self):
        payload = _report_payload()
        frozen = json.dumps(payload, sort_keys=True)
        _redact_report_for(payload, VIEWER)
        assert json.dumps(payload, sort_keys=True) == frozen, (
            "the bus/report payload can be shared by other readers; mutating "
            "it in place would corrupt their copy too")

    def test_a_non_dict_frame_row_is_left_alone_not_raised_on(self):
        """Fail-closed on shape drift means 'do not crash the route', not
        'crash the route'. A row this module cannot key-strip is passed
        through as-is rather than exploding the whole response."""
        payload = {"frames": ["not-a-dict"], "sky_angles": ["also-not-a-dict"]}
        out = _redact_report_for(payload, VIEWER)
        assert out["frames"] == ["not-a-dict"]
        assert out["sky_angles"] == ["also-not-a-dict"]


# ============================================================ report_csv_columns

class TestReportCsvColumns:
    _COLS = ["ts", "target", "altitude_deg", "saved_path"]

    def test_a_viewer_loses_the_altitude_column(self):
        assert "altitude_deg" not in report_csv_columns(self._COLS, VIEWER)

    def test_an_operator_keeps_the_altitude_column(self):
        assert "altitude_deg" in report_csv_columns(self._COLS, OPERATOR)

    def test_saved_path_stays_for_a_viewer_too(self):
        """Only altitude is a site-derived value here; the relative path is
        not a secret (see redact.py's own frame-path section)."""
        assert "saved_path" in report_csv_columns(self._COLS, VIEWER)


# ================================================== bundle manifest/CSV redaction

def _manifest(altitude=41.7):
    return {"groups": [{"dir": "M1/L/60s", "lights": [
        {"src": "M1/a.fits", "altitude_deg": altitude, "weight": 1.0}]}]}


def _weights_csv_text():
    import csv as _csv
    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["target", "hfr", "altitude_deg", "weight"])
    w.writerow(["M1", 2.0, 41.7, 1.0])
    return buf.getvalue()


class TestBundleManifestAndCsvStripAltitude:
    def test_a_viewer_loses_altitude_deg_from_every_manifest_light_row(self):
        out = redact_bundle_manifest_for(_manifest(), VIEWER)
        assert "altitude_deg" not in out["groups"][0]["lights"][0]

    def test_a_viewer_keeps_the_rest_of_the_light_row(self):
        out = redact_bundle_manifest_for(_manifest(), VIEWER)
        row = out["groups"][0]["lights"][0]
        assert row["src"] == "M1/a.fits"
        assert row["weight"] == 1.0

    def test_an_operator_keeps_the_manifest_altitude(self):
        out = redact_bundle_manifest_for(_manifest(), OPERATOR)
        assert out["groups"][0]["lights"][0]["altitude_deg"] == 41.7

    def test_a_viewer_loses_the_csv_altitude_column_entirely(self):
        out = redact_bundle_csv_for(_weights_csv_text(), VIEWER)
        header = out.splitlines()[0].split(",")
        assert "altitude_deg" not in header
        assert "41.7" not in out

    def test_an_operator_keeps_the_csv_altitude_column(self):
        out = redact_bundle_csv_for(_weights_csv_text(), OPERATOR)
        assert "altitude_deg" in out.splitlines()[0]
        assert "41.7" in out

    def test_manifest_redaction_never_mutates_its_input(self):
        m = _manifest()
        frozen = json.dumps(m, sort_keys=True)
        redact_bundle_manifest_for(m, VIEWER)
        assert json.dumps(m, sort_keys=True) == frozen


# ============================================================ route-level tests

class _FixedPrincipal:
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture
def as_role():
    def _set(principal):
        set_active_provider(_FixedPrincipal(principal))
    yield _set
    reset_active_provider()


@pytest.fixture
def captures(tmp_path, monkeypatch):
    root = tmp_path / "captures"
    (root / "M1").mkdir(parents=True)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", root)
    return root


def _abs_fits(captures, rel="M1/a.fits"):
    p = captures / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"FITS")
    return str(p)


@pytest.fixture
def client(tmp_path, monkeypatch, captures):
    store = config_mod.ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    reset_active_provider()
    with TestClient(app_module.create_app()) as c:
        yield c
    reset_active_provider()


def _install_report(monkeypatch, captures, report_id="r1"):
    rep = SessionReport(
        id=report_id, plan_name="Test Plan", started_at=time.time(),
        sky_angles=[dict(SKY_ROW)])
    rep.frames.append(FrameRecord(
        ts=time.time(), target="Made-Up Target", filter="L",
        frame_type="Light", exposure_s=60.0, gain=100, binning=1,
        accepted=True, hfr=2.0, altitude_deg=41.7,
        saved_path=_abs_fits(captures)))
    monkeypatch.setattr(app_module.SessionReporter, "load",
                        staticmethod(lambda rid: rep if rid == report_id else None))
    return rep


class TestGetReportRoute:
    def test_a_viewer_gets_no_altitude_and_no_exposed_at(
            self, client, as_role, monkeypatch, captures):
        _install_report(monkeypatch, captures)
        as_role(VIEWER)
        r = client.get("/api/reports/r1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "altitude_deg" not in body["frames"][0]
        assert body["sky_angles"][0]["exposed_at"] is None

    def test_an_operator_gets_both(self, client, as_role, monkeypatch, captures):
        _install_report(monkeypatch, captures)
        as_role(OPERATOR)
        r = client.get("/api/reports/r1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["frames"][0]["altitude_deg"] == 41.7
        assert body["sky_angles"][0]["exposed_at"] == SKY_ROW["exposed_at"]


class TestReportCsvRoute:
    def test_a_viewer_gets_no_altitude_column_or_value(
            self, client, as_role, monkeypatch, captures):
        _install_report(monkeypatch, captures)
        as_role(VIEWER)
        body = client.get("/api/reports/r1/frames.csv").text
        header = body.splitlines()[0]
        assert "altitude_deg" not in header, header
        assert "41.7" not in body, body

    def test_an_operator_gets_the_altitude_column_and_value(
            self, client, as_role, monkeypatch, captures):
        _install_report(monkeypatch, captures)
        as_role(OPERATOR)
        body = client.get("/api/reports/r1/frames.csv").text
        assert "altitude_deg" in body.splitlines()[0]
        assert "41.7" in body


class TestWeightAltitudeIsRefused:
    """The 3 bundle routes named in #567's suggested fix -- ``bundle``,
    ``bundle.zip`` and ``bundle/materialize`` -- each refuse
    ``weight_altitude=true`` for a principal lacking ``view.site_derived``
    rather than silently folding altitude into the sub weights."""

    def test_bundle_refuses_for_a_viewer(self, client, as_role, monkeypatch, captures):
        _install_report(monkeypatch, captures)
        as_role(VIEWER)
        r = client.get("/api/reports/r1/bundle", params={"weight_altitude": "true"})
        assert r.status_code == 400, r.text
        assert "view.site_derived" in r.json()["detail"]

    def test_bundle_allows_for_an_operator(self, client, as_role, monkeypatch, captures):
        _install_report(monkeypatch, captures)
        as_role(OPERATOR)
        r = client.get("/api/reports/r1/bundle", params={"weight_altitude": "true"})
        assert r.status_code == 200, r.text

    def test_bundle_zip_refuses_for_a_viewer(self, client, as_role, monkeypatch, captures):
        _install_report(monkeypatch, captures)
        as_role(VIEWER)
        r = client.get("/api/reports/r1/bundle.zip",
                       params={"weight_altitude": "true"})
        assert r.status_code == 400, r.text

    def test_materialize_refuses_for_a_capture_only_principal(
            self, client, as_role, monkeypatch, captures):
        """Proves the check is on the CAPABILITY, not the role: this
        principal holds control.capture (so it clears the route's own gate)
        but not view.site_derived, a combination no built-in role has today
        but a viewer-LINK's explicit cap list could carry."""
        _install_report(monkeypatch, captures)
        as_role(CAPTURE_ONLY)
        r = client.post("/api/reports/r1/bundle/materialize",
                        params={"weight_altitude": "true"})
        assert r.status_code == 400, r.text
        assert "view.site_derived" in r.json()["detail"]

    def test_a_viewer_without_weight_altitude_is_not_refused(
            self, client, as_role, monkeypatch, captures):
        """The refusal is keyed on the OPTION being requested, not on the
        role -- a viewer's default (unweighted) request still works."""
        _install_report(monkeypatch, captures)
        as_role(VIEWER)
        r = client.get("/api/reports/r1/bundle")
        assert r.status_code == 200, r.text


class TestBundleZipRouteStripsAltitudeRegardlessOfTheFlag:
    """The unconditional leak (this file's module docstring): ``build_bundle``
    carries ``altitude_deg`` onto every light row whether or not
    ``weight_altitude`` was ever requested, so the DEFAULT request must be
    redacted too -- refusing the option alone would not have closed this."""

    def test_a_viewer_default_request_zip_has_no_altitude_anywhere(
            self, client, as_role, monkeypatch, captures):
        _install_report(monkeypatch, captures)
        as_role(VIEWER)
        r = client.get("/api/reports/r1/bundle.zip")  # weight_altitude defaults False
        assert r.status_code == 200, r.text
        z = zipfile.ZipFile(io.BytesIO(r.content))
        manifest = json.loads(z.read("manifest.json"))
        for g in manifest["groups"]:
            for light in g["lights"]:
                assert "altitude_deg" not in light, light
        wcsv = z.read("weights.csv").decode("utf-8")
        assert "altitude_deg" not in wcsv.splitlines()[0], wcsv
        assert "41.7" not in wcsv, wcsv

    def test_an_operator_default_request_zip_still_has_altitude(
            self, client, as_role, monkeypatch, captures):
        _install_report(monkeypatch, captures)
        as_role(OPERATOR)
        r = client.get("/api/reports/r1/bundle.zip")
        assert r.status_code == 200, r.text
        z = zipfile.ZipFile(io.BytesIO(r.content))
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["groups"][0]["lights"][0]["altitude_deg"] == 41.7
        wcsv = z.read("weights.csv").decode("utf-8")
        assert "41.7" in wcsv
