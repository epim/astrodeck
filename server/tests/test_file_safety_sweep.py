"""The second wave of the 2026-08-03 file-safety sweep.

The SPA catch-all traversal (a READ primitive) is pinned in
``test_path_traversal.py``. This file pins what the sweep of the remaining
surfaces turned up, which was worse: a WRITE primitive, an unguarded delete,
two routes reachable with no credential at all, and a path leak that the
codebase had already written the rule against and then not applied.

Each test names the specific thing that was broken, so a future reader can tell
whether a failure here is a regression or a deliberate change.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.api.redact import _redact_report_for, report_csv_columns
from astrodeck.auth.capabilities import CAP_CONFIG_BACKEND, CAP_VIEW_STATUS
from astrodeck.calibration.keys import CalKey, key_index_id
from astrodeck.auth import reset_active_provider
from astrodeck.config import AuthConfig, ConfigStore
from astrodeck.persist import safe_id_path


# ------------------------------------------------- the write primitive (blocker)

# Every other component of a calibration bucket id is a number the function
# formats itself. `filter` is a string copied verbatim out of a FITS FILTER
# card, and the id becomes a FILENAME on a path that does mkdir(parents=True)
# then writeto(overwrite=True).
HOSTILE_FILTERS = [
    "../../../../pwned",
    "..\\..\\evil",
    "/etc/passwd",
    "C:\\Windows\\win.ini",
    "a/b",
    "..",
]


@pytest.mark.parametrize("hostile", HOSTILE_FILTERS)
def test_calibration_bucket_id_cannot_steer_a_path(hostile):
    """The id's docstring has always claimed "filesystem-safe". Make it true.

    Reproduction before the fix: filter ``../../../../pwned`` produced
    ``flat_g100_o30_b1_f../../../../pwned``, which resolved four levels above
    the masters directory.
    """
    key = CalKey(frame_type="FLAT", exposure_s=1.0, gain=100, offset=30,
                 temp_c=None, binning=1, filter=hostile)
    kid = key_index_id(key, 5.0)
    assert "/" not in kid and "\\" not in kid, kid
    assert ".." not in kid, kid
    assert ":" not in kid, kid
    # And the id must still be usable as a contained filename.
    with tempfile.TemporaryDirectory() as td:
        masters = Path(td)
        resolved = safe_id_path(masters, kid, ".fits")
        assert resolved.is_relative_to(masters.resolve())


def test_calibration_bucket_id_keeps_real_filter_names_intact():
    """The guard must not rename the filters an actual observatory uses — a
    changed id would orphan every existing master."""
    for name in ("L", "R", "G", "B", "Ha", "Oiii", "Sii", "Dark", "no-ir"):
        key = CalKey(frame_type="FLAT", exposure_s=1.0, gain=100, offset=30,
                     temp_c=None, binning=1, filter=name)
        assert key_index_id(key, 5.0).endswith(f"_f{name}")


def test_calibration_build_skips_a_bucket_it_cannot_contain(tmp_path, monkeypatch):
    """Enforcement, not just sanitization. key_index_id now refuses to MINT a
    steering id, but `build` writes whatever id it is handed, so the write must
    be contained independently — the delete path a few lines away has always
    routed through safe_id_path and the write path, the dangerous half, did not.
    """
    from astrodeck.calibration import library as lib_mod
    captures = tmp_path / "captures"
    (captures / lib_mod.MASTERS_DIRNAME).mkdir(parents=True)
    lib = lib_mod.CalibrationLibrary(lambda: captures)
    monkeypatch.setattr(lib, "_bucket_raw", lambda _w: {
        "../../escape": lib_mod._Bucket(
            key=CalKey(frame_type="BIAS", exposure_s=0.0, gain=0, offset=0,
                       temp_c=None, binning=1, filter=""),
            paths=[]),
    })
    report = lib.build()
    assert report.masters_built == 0, "a refused bucket must be skipped, not written"
    assert not list(tmp_path.rglob("escape*.fits"))


# --------------------------------------------------------- the delete primitive

def test_unlink_saved_refuses_a_path_outside_capture_dir(tmp_path, monkeypatch):
    """`_unlink_saved`'s docstring stated the NINA rule from the day it was
    written; nothing enforced it. For a NINA rig `saved_path` is whatever string
    the imaging host returned, and this method unlinked it. It fires whenever a
    frame fails the HFR/eccentricity gate with action `discard` or `retake`."""
    from astrodeck.sequence.engine import SequenceEngine
    victim = tmp_path / "not-a-capture.fits"
    victim.write_text("precious", encoding="utf-8")
    SequenceEngine._unlink_saved({"saved_path": str(victim)})
    assert victim.exists(), "a path outside CAPTURE_DIR must never be unlinked"


def test_unlink_saved_still_deletes_a_real_local_save(tmp_path, monkeypatch):
    """The guard must not break the thing the method exists for."""
    import astrodeck.hub as hub_mod
    from astrodeck.sequence.engine import SequenceEngine
    cap = tmp_path / "captures"
    cap.mkdir()
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", cap)
    doomed = cap / "rejected.fits"
    doomed.write_text("bad frame", encoding="utf-8")
    SequenceEngine._unlink_saved({"saved_path": str(doomed)})
    assert not doomed.exists()


# ------------------------------------------------------------- the open routes

@pytest.fixture(autouse=True)
def _clean_provider():
    reset_active_provider()
    yield
    reset_active_provider()


def _client(tmp_path, monkeypatch):
    """An app where an unauthenticated caller is genuinely ANONYMOUS.

    This is the trap that made my first version of these tests vacuous: with no
    auth configured every route returns 200, including the ones that are
    correctly gated, so "assert != 200" passes for the wrong reason and
    "assert == 200" would too. `provider="none"` + `methods=["local"]` +
    `local_enabled_first_run=False` is the on-disk shape that makes a
    credential-less request a real 401 (see test_auth_epoch.py)."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    temp_store.cfg().auth = AuthConfig(provider="none", methods=["local"],
                                       local_enabled_first_run=False)
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    return app_module.create_app()


@pytest.mark.parametrize("path", [
    "/api/survey/cutout.jpg?ra=1&dec=1&fov=1",
    "/api/survey/tile/dss2color/0/0.jpg",
])
def test_survey_routes_require_a_capability(tmp_path, monkeypatch, path):
    """These two were the only /api GETs with no capability. They are GETs that
    WRITE — both populate a cache inside the capture volume, and with
    online_fetch on they make outbound network requests — so an anonymous
    caller could drive disk and egress through the relay.

    A 200 here is the regression."""
    app = _client(tmp_path, monkeypatch)
    with TestClient(app) as c:
        # Prove the harness itself denies anonymous callers, or the assertion
        # below is vacuous.
        assert c.get("/api/status").status_code == 401, "harness is not anonymous"
        assert c.get(path).status_code == 401


# ---------------------------------------------------------------- the 500s

@pytest.mark.parametrize("path", [
    "/api/profiles/..%5Cx",
    "/api/plans/..%5Cx",
])
def test_refused_id_on_delete_is_404_not_500(tmp_path, monkeypatch, path):
    """Both stores resolve through safe_id_path and both docstrings promise
    "routes map KeyError -> 404". These two routes never kept it: with no global
    exception handler the KeyError surfaced as a 500 with a full traceback,
    while every sibling (calibration masters, sessions, plan export) returned
    404. Nothing was ever deleted — the guard held — but a 500 confirms the
    input reached an unhandled path."""
    app = _client(tmp_path, monkeypatch)
    with TestClient(app) as c:
        assert c.delete(path).status_code != 500


# ------------------------------------------------------------- the path leak

class _P:
    def __init__(self, caps):
        self._caps = set(caps)

    def has(self, cap):
        return cap in self._caps


def test_report_frames_strip_saved_path_for_a_plain_viewer():
    """`/api/sessions/{id}` strips frame paths for a caller without
    config.backend — "endpoint identity == filesystem identity". Reports carry
    the same frames under a different field name (`saved_path`), predate that
    rule, and were never brought under it."""
    payload = {"frames": [{"ts": 1.0, "saved_path": "C:\\obs\\M42\\f_0001.fits"}]}
    viewer = _redact_report_for(payload, _P([CAP_VIEW_STATUS]))
    assert "saved_path" not in viewer["frames"][0]
    assert viewer["frames"][0]["ts"] == 1.0, "only the path is stripped"
    admin = _redact_report_for(payload, _P([CAP_VIEW_STATUS, CAP_CONFIG_BACKEND]))
    assert admin["frames"][0]["saved_path"] == "C:\\obs\\M42\\f_0001.fits"
    # the input must not be mutated in place
    assert payload["frames"][0]["saved_path"]


def test_report_csv_drops_the_path_column_for_a_plain_viewer():
    """A CSV export is not a loophole around the JSON route's redaction."""
    cols = ["ts", "target", "saved_path"]
    assert report_csv_columns(cols, _P([CAP_VIEW_STATUS])) == ["ts", "target"]
    assert report_csv_columns(cols, _P([CAP_VIEW_STATUS, CAP_CONFIG_BACKEND])) == cols
