"""**No absolute filesystem path leaves this process. For anybody.**

Owner ruling, 2026-08-11: an external client should never know or hold an
absolute path — it is a security breach waiting to happen. An absolute path
names the account, the drive and the directory scheme of the machine holding
the observatory's data, and no client has any use for it: every route that
takes a frame location (``/api/gallery/file``, the sync manifest) takes the
capture-root-relative form and rejects the absolute one.

This replaces a HOLDER rule (``config.backend`` saw the real path, others saw
the field removed), which was wrong twice: it treated a filesystem layout as a
privilege to grant, and it lived as two separate functions — one for a
session's ``path``, one for a report's ``saved_path`` — that had already
drifted, with the report side shipping absolute paths to a plain viewer.

So the tests below are deliberately blunt: take a payload, look for the capture
root's own absolute prefix anywhere in it, and fail. A rule stated as "no X
anywhere" is testable that way; a rule stated per-field is not.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.hub as hub_module
from astrodeck.api.redact import _redact_report_for, _redact_session_for
from astrodeck.auth import Principal
from astrodeck.auth.capabilities import ALL_CAPS


@pytest.fixture
def captures(tmp_path, monkeypatch):
    root = tmp_path / "captures"
    (root / "NGC 6946").mkdir(parents=True)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", root)
    return root


def _abs(captures: Path, rel: str) -> str:
    p = captures / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"FITS")
    return str(p)


ADMIN = Principal(role="admin", email=None, caps=ALL_CAPS, jti=None)


class TestRelpathHelper:
    def test_converts_a_path_inside_the_library(self, captures):
        from astrodeck import gallery
        got = gallery.relpath_under_capture(_abs(captures, "NGC 6946/a.fits"))
        assert got == "NGC 6946/a.fits"

    def test_refuses_to_express_a_path_outside_it(self, captures, tmp_path):
        from astrodeck import gallery
        assert gallery.relpath_under_capture(str(tmp_path / "elsewhere.fits")) is None

    def test_none_and_empty_are_none(self):
        from astrodeck import gallery
        assert gallery.relpath_under_capture(None) is None
        assert gallery.relpath_under_capture("") is None

    def test_a_traversal_that_escapes_the_root_is_not_expressible(
            self, captures, tmp_path):
        """``captures/../secret.fits`` resolves OUTSIDE the root, so there is no
        relative form and the helper must say so rather than emit `../`."""
        from astrodeck import gallery
        assert gallery.relpath_under_capture(
            str(captures / ".." / "secret.fits")) is None


class TestPreviewEvent:
    def test_the_absolute_path_never_reaches_the_bus(self, captures):
        from astrodeck.hub import external_preview
        p = _abs(captures, "NGC 6946/a.fits")
        out = external_preview({"id": 1, "saved_path": p, "saved_local": True})
        assert "saved_path" not in out
        assert out["path"] == "NGC 6946/a.fits"
        assert str(captures) not in json.dumps(out)

    def test_a_remote_save_carries_no_path_at_all(self, captures, tmp_path):
        """A NINA save lives on the imaging host. There is no relative form, so
        the field is ABSENT — not fabricated, and not the absolute path."""
        from astrodeck.hub import external_preview
        out = external_preview({"id": 1, "saved_path": str(tmp_path / "nina.fits"),
                                "saved_local": False})
        assert "path" not in out and "saved_path" not in out
        assert out["saved_local"] is False

    def test_the_caller_still_gets_the_real_path(self, captures):
        """`_publish_preview` RETURNS the absolute path to its in-process caller
        (the sequence engine records it; the bundle builder copies the file).
        Sanitizing the return value too would break both."""
        from astrodeck.hub import external_preview
        info = {"id": 1, "saved_path": _abs(captures, "NGC 6946/a.fits")}
        external_preview(info)
        assert info["saved_path"], "external_preview mutated its input"


class TestReportsAndSessions:
    def test_a_report_gives_an_admin_a_relative_path_not_an_absolute_one(
            self, captures):
        payload = {"frames": [{"saved_path": _abs(captures, "NGC 6946/a.fits")}]}
        out = _redact_report_for(payload, ADMIN)
        assert out["frames"][0]["saved_path"] == "NGC 6946/a.fits"
        assert str(captures) not in json.dumps(out)

    def test_a_session_gives_an_admin_a_relative_path_too(self, captures):
        payload = {"frames": [{"path": _abs(captures, "NGC 6946/b.fits")}]}
        out = _redact_session_for(payload, ADMIN)
        assert out["frames"][0]["path"] == "NGC 6946/b.fits"

    def test_a_viewer_gets_the_same_relative_path(self, captures):
        """Not stripped for the unprivileged and absolute for the privileged —
        ONE answer. The relpath is not a secret; it is the handle a client
        needs, and withholding it only made the API less useful."""
        viewer = Principal(role="viewer", email=None,
                           caps=frozenset({"view.status"}), jti=None)
        payload = {"frames": [{"saved_path": _abs(captures, "NGC 6946/a.fits")}]}
        assert _redact_report_for(payload, viewer)["frames"][0]["saved_path"] \
            == "NGC 6946/a.fits"

    def test_a_frame_outside_the_library_loses_the_field(self, captures, tmp_path):
        payload = {"frames": [{"saved_path": str(tmp_path / "nina" / "x.fits")}]}
        assert "saved_path" not in _redact_report_for(payload, ADMIN)["frames"][0]

    def test_the_input_payload_is_never_mutated(self, captures):
        p = _abs(captures, "NGC 6946/a.fits")
        payload = {"frames": [{"saved_path": p}]}
        _redact_report_for(payload, ADMIN)
        assert payload["frames"][0]["saved_path"] == p, (
            "the bus Event.data is shared across subscribers; mutating it in "
            "place would corrupt every other consumer")


class TestCsvIsNotALoophole:
    def test_the_csv_carries_the_same_relative_value_as_the_json(
            self, captures, tmp_path, monkeypatch):
        """The CSV used to build its own row list by hand, which is how one
        disclosure rule became two implementations in the first place."""
        store = config_mod.ConfigStore(path=tmp_path / "astrodeck.json")
        monkeypatch.setattr(config_mod, "config_store", store)
        monkeypatch.setattr(app_module, "config_store", store)

        from astrodeck.sequence.report import FrameRecord, SessionReport

        rep = SessionReport(id="r1", name="n", started_ts=time.time())
        rep.frames.append(FrameRecord(
            ts=time.time(), target="NGC 6946", filter="L", frame_type="LIGHT",
            exposure_s=60.0, accepted=True,
            saved_path=_abs(captures, "NGC 6946/a.fits")))
        monkeypatch.setattr(app_module.SessionReporter, "load",
                            staticmethod(lambda rid: rep))

        c = TestClient(app_module.create_app())
        body = c.get("/api/reports/r1/frames.csv").text
        assert "NGC 6946/a.fits" in body
        assert str(captures) not in body, \
            f"the CSV export leaked the absolute path:\n{body}"

        j = c.get("/api/reports/r1").json()
        assert str(captures) not in json.dumps(j)
