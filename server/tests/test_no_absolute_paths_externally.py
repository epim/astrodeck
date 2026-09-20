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


class TestTheZipIsNotALoopholeEither:
    """``GET /api/reports/{id}/bundle.zip`` is the same report, the same
    frames, and the same ``view.status`` floor as the two routes above -- and
    it never took a principal and never called the externalizer. Its
    ``manifest.json``, ``weights.csv`` and ``build.sh``/``build.ps1`` members
    each carried ``fr.saved_path`` verbatim, so the file that gets mailed to a
    forum thread named the observatory's account, drive and directory scheme
    three times over. No FITS bytes leak; the filesystem layout did.
    """

    def _zip(self, captures, tmp_path, monkeypatch, rel="NGC 6946/a.fits"):
        store = config_mod.ConfigStore(path=tmp_path / "astrodeck.json")
        monkeypatch.setattr(config_mod, "config_store", store)
        monkeypatch.setattr(app_module, "config_store", store)

        from astrodeck.sequence.report import FrameRecord, SessionReport

        rep = SessionReport(id="r1", name="n", started_ts=time.time())
        rep.frames.append(FrameRecord(
            ts=time.time(), target="NGC 6946", filter="L", frame_type="LIGHT",
            exposure_s=60.0, gain=100, binning=1, hfr=2.0, accepted=True,
            saved_path=_abs(captures, rel)))
        monkeypatch.setattr(app_module.SessionReporter, "load",
                            staticmethod(lambda rid: rep))
        c = TestClient(app_module.create_app())
        r = c.get("/api/reports/r1/bundle.zip")
        assert r.status_code == 200, r.text
        import io
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(r.content))
        return {n: z.read(n).decode("utf-8") for n in z.namelist()}

    def test_no_member_carries_the_capture_root(self, captures, tmp_path,
                                                monkeypatch):
        members = self._zip(captures, tmp_path, monkeypatch)
        assert set(members) == {"manifest.json", "weights.csv", "README.txt",
                                "build.sh", "build.ps1"}
        for name, body in members.items():
            assert str(captures) not in body, \
                f"{name} leaked the absolute path:\n{body[:400]}"

    def test_the_relative_path_is_still_there_to_use(self, captures, tmp_path,
                                                     monkeypatch):
        """Withholding the location is not the same as withholding the file: a
        stacker still needs to know WHICH sub each row is."""
        members = self._zip(captures, tmp_path, monkeypatch)
        assert "NGC 6946/a.fits" in members["manifest.json"]
        assert "NGC 6946/a.fits" in members["weights.csv"]
        assert json.loads(members["manifest.json"])["groups"][0]["lights"][0][
            "src"] == "NGC 6946/a.fits"

    def test_the_build_scripts_still_resolve(self, captures, tmp_path,
                                             monkeypatch):
        """A relative source with nothing to resolve it against would be a
        script that no longer builds -- the fix has to keep the one-click
        promise, so both shells read the user's own capture folder from
        CAPTURE_ROOT and refuse to run without it."""
        members = self._zip(captures, tmp_path, monkeypatch)
        sh = members["build.sh"]
        assert 'CAPTURE_ROOT:?' in sh, sh
        assert 'cp -- "$CAPTURE_ROOT"/' in sh, sh
        assert "\\" not in sh, sh
        ps1 = members["build.ps1"]
        assert "$env:CAPTURE_ROOT" in ps1, ps1
        assert "Join-Path $CaptureRoot 'NGC 6946/a.fits'" in ps1, ps1
        assert "CAPTURE_ROOT" in members["README.txt"]

    def test_an_adversarial_filename_is_still_only_a_quoted_literal(
            self, captures, tmp_path, monkeypatch):
        """The CAPTURE_ROOT join must not become an escape: the variable is
        expanded in its own quoted word and the relative path stays a shell
        literal beside it."""
        import shlex
        evil = "NGC 6946/$(reboot)`id`; & rmdir 'y'.fits"
        members = self._zip(captures, tmp_path, monkeypatch, rel=evil)
        dest = json.loads(members["manifest.json"])[
            "groups"][0]["lights"][0]["dest"]
        sh = members["build.sh"]
        assert f'"$CAPTURE_ROOT"/{shlex.quote(evil)}' in sh, sh
        # Both halves of the cp are quoted literals; blank them and nothing
        # metacharacter-shaped is left loose in the script.
        bare = sh.replace(shlex.quote(evil), "<SRC>").replace(
            shlex.quote(dest), "<DEST>")
        assert "$(reboot)" not in bare, bare
        ps1 = members["build.ps1"]
        assert "'" + evil.replace("'", "''") + "'" in ps1
        bare_ps = ps1.replace(evil.replace("'", "''"), "<SRC>").replace(
            dest.replace("'", "''"), "<DEST>")
        assert "$(reboot)" not in bare_ps, bare_ps

    def test_a_source_outside_the_library_is_absent_not_absolute(
            self, captures, tmp_path, monkeypatch):
        """The same rule ``_externalize_frame_paths`` keeps for a NINA save on
        another host: a path we cannot express relatively is dropped, never
        fallen back to."""
        from astrodeck.sequence.bundle import (Bundle, Group, LightEntry,
                                               build_script, externalize_bundle,
                                               manifest_json)
        elsewhere = str(tmp_path / "nina" / "x.fits")
        light = LightEntry(src=elsewhere, dest="M42/L/60s/lights/x.fits", ts=1.0,
                           accepted=True, hfr=None, ecc=None, guide_rms=None,
                           sensor_temp_c=None, altitude_deg=None, weight=1.0)
        b = Bundle(report_id="r1", plan_name="p", layout="grouped",
                   groups=(Group(dir="M42/L/60s", target="M42", filter="L",
                                 exposure_s=60.0, gain=None, binning=None,
                                 lights=(light,), masters={}, master_sources={},
                                 missing_masters=()),),
                   warnings=())
        from astrodeck import gallery
        out = externalize_bundle(b, gallery.relpath_under_capture)
        assert out.groups[0].lights[0].src == ""
        assert "src" not in manifest_json(out)["groups"][0]["lights"][0]
        assert elsewhere not in build_script(out, "sh")
        assert elsewhere not in build_script(out, "ps1")


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


class TestLogLines:
    """A bus log line is an external payload too.

    It reaches the WS stream, the /api/logs ring and the durable night log,
    and /api/logs is gated on CAP_VIEW_STATUS -- the lowest capability there
    is. The ruling at the top of this file says no absolute path leaves this
    process for anybody, so a message built from a `Path` is exactly as much a
    disclosure as a JSON field, and it is the easier one to write by accident
    because nothing about a format string looks like a payload.

    Only PRODUCERS THAT BUILD A MESSAGE FROM A PATH belong here. Grading every
    bus.log call in the server would need each producer driven or a static
    scan of format strings, which is not this file's blunt shape.
    """

    def test_a_slow_fingerprint_write_warning_carries_no_absolute_path(
            self, captures, monkeypatch):
        """The fingerprint file lives under CAPTURE_DIR, whose path names the
        operator's Windows account. The warning is allowed to say how long the
        write took and which file it was; it is not allowed to say where."""
        from astrodeck.devices import fingerprint

        monkeypatch.setattr(fingerprint, "FINGERPRINT_SLOW_WRITE_S", 0.02)
        fingerprint.reset_for_tests()
        real_write = fingerprint.write_json_atomic

        def slow_write(target, payload, **kwargs):
            time.sleep(0.05)
            return real_write(target, payload, **kwargs)

        monkeypatch.setattr(fingerprint, "write_json_atomic", slow_write)
        try:
            fingerprint.record(focuser_position=11218, filter_slot=1,
                               ra_hours=1.0, dec_deg=2.0, parked=False,
                               tracking=True)
            notice = fingerprint.take_slow_write_notice()
            assert notice, "premise: the slow write produced a warning"
            assert str(captures) not in notice, \
                f"the slow-write warning leaked the capture root:\n{notice}"
            assert "device_fingerprint.json" in notice
        finally:
            fingerprint.reset_for_tests()
