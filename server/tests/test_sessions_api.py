"""Task 7: /api/sessions surface (sessions spec §6/§8) — RBAC per route, 409s,
id-merge, frame regrade + metrics merge, path redaction, thumb cap.

Harness mirrors tests/test_rbac_enforcement.py (FakeAuthProvider + TestClient)."""
import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.auth import principal_for_role, reset_active_provider, set_active_provider
from astrodeck.config import ConfigStore
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import Session, SessionFrame, session_store


class FakeAuthProvider:
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


@pytest.fixture
def client(tmp_path, monkeypatch):
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    reset_active_provider()
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def _plan(count=2, mode="accepted") -> SequencePlan:
    return SequencePlan(name="api", count_mode=mode, targets=[Target(
        name="M42", ra_hours=5.5881, dec_deg=-5.3911,
        steps=[ExposureStep(filter="L", exposure_s=60, count=count)])])


def _session(status="dormant", count=2) -> Session:
    plan = _plan(count=count)
    s = Session(name="api", created_ts=1.0, status=status, plan=plan)
    step = plan.targets[0].steps[0]
    for _ in range(2):
        s.frames.append(SessionFrame(ts=1.0, night="n1",
                                     target_id=plan.targets[0].id,
                                     step_id=step.id, path="C:/secret/f.fits",
                                     metrics={"hfr": 2.0}, auto_accepted=True))
    session_store.save(s)
    return s


def test_list_and_detail_no_absolute_path_even_for_an_admin(client):
    """TIGHTENED 2026-08-11 (owner ruling): an admin used to receive the frame's
    absolute on-disk location here. Nobody does now — a path outside the capture
    root has no relative form, so the field is absent rather than absolute.
    ``C:/secret/f.fits`` is exactly such a path, and its disappearance is the
    assertion."""
    s = _session()
    rows = client.get("/api/sessions").json()["sessions"]
    assert [r["id"] for r in rows] == [s.id]
    assert rows[0]["accepted"] == 2 and rows[0]["total"] == 2
    detail = client.get(f"/api/sessions/{s.id}").json()          # open default = admin
    assert "path" not in detail["frames"][0]
    assert "C:/secret" not in json.dumps(detail)


def test_viewer_reads_but_paths_stripped_and_mutations_403(client):
    s = _session()
    set_active_provider(FakeAuthProvider(principal_for_role("viewer")))
    detail = client.get(f"/api/sessions/{s.id}").json()
    assert "path" not in detail["frames"][0]                   # frame-path strip
    assert client.get("/api/sessions").status_code == 200
    assert client.post(f"/api/sessions/{s.id}/resume").status_code == 403
    assert client.patch(f"/api/sessions/{s.id}",
                        json={"auto_resume": True}).status_code == 403
    assert client.delete(f"/api/sessions/{s.id}").status_code == 403
    fid = s.frames[0].id
    assert client.patch(f"/api/sessions/{s.id}/frames/{fid}",
                        json={"override": "reject"}).status_code == 403


def test_operator_can_resume(client):
    """2026-07-17 decisions wave I1: operator holds control.mount, so resume's
    gate passes; no camera connected in this harness -> 409 (DeviceError), NOT
    403 (the RBAC boundary itself is what's under test here)."""
    s = _session()
    set_active_provider(FakeAuthProvider(principal_for_role("operator")))
    assert client.post(f"/api/sessions/{s.id}/resume").status_code != 403


def test_patch_plan_dormant_only_with_id_merge(client):
    s = _session()
    kept_step = s.plan.targets[0].steps[0]
    new_plan = _plan().model_dump()
    new_plan["targets"][0]["steps"] = [kept_step.model_dump(),
                                       {"exposure_s": 30, "count": 5}]
    r = client.patch(f"/api/sessions/{s.id}", json={"plan": new_plan}).json()
    assert kept_step.id in r["merge"]["kept"]
    assert len(r["merge"]["new"]) == 1
    assert r["merge"]["dropped"] == []
    # dropping the frame-bearing step reports it
    r2 = client.patch(f"/api/sessions/{s.id}",
                      json={"plan": _plan().model_dump()}).json()
    assert kept_step.id in r2["merge"]["dropped"]
    # frames stay recorded even though their step id no longer exists
    assert len(session_store.load(s.id).frames) == 2
    # 409 while not dormant
    active = _session(status="active")
    assert client.patch(f"/api/sessions/{active.id}",
                        json={"plan": _plan().model_dump()}).status_code == 409


def test_auto_resume_singleton_and_abandon(client):
    a, b = _session(), _session()
    client.patch(f"/api/sessions/{a.id}", json={"auto_resume": True})
    client.patch(f"/api/sessions/{b.id}", json={"auto_resume": True})
    assert session_store.load(a.id).auto_resume is False      # disarmed by b
    assert session_store.load(b.id).auto_resume is True
    r = client.patch(f"/api/sessions/{a.id}", json={"status": "abandoned"})
    assert r.json()["status"] == "abandoned"
    assert client.patch(f"/api/sessions/{a.id}",
                        json={"status": "complete"}).status_code == 422


def test_frame_patch_override_flips_remaining_and_metrics_merge(client):
    s = _session()                                   # accepted mode, count=2, 2 accepted
    step_id = s.frames[0].step_id
    fid = s.frames[0].id
    r = client.patch(f"/api/sessions/{s.id}/frames/{fid}",
                     json={"override": "reject"}).json()
    assert r["remaining"][step_id] == 1              # regrade adjusts the quota
    r = client.patch(f"/api/sessions/{s.id}/frames/{fid}",
                     json={"override": None}).json() # explicit null clears
    assert r["frame"]["override"] is None
    assert r["remaining"][step_id] == 0
    r = client.patch(f"/api/sessions/{s.id}/frames/{fid}",
                     json={"metrics": {"fwhm": 3.2}}).json()
    assert r["frame"]["metrics"] == {"hfr": 2.0, "fwhm": 3.2}   # float-merge
    # running session: regrade refused
    active = _session(status="active")
    assert client.patch(
        f"/api/sessions/{active.id}/frames/{active.frames[0].id}",
        json={"override": "reject"}).status_code == 409


def test_delete_removes_files_never_fits(client, tmp_path):
    s = _session()
    tdir = session_store.thumbs_dir(s.id)
    tdir.mkdir(parents=True)
    (tdir / "x.jpg").write_bytes(b"\xff\xd8xx")
    fits = tmp_path / "captures" / "keep.fits"
    fits.parent.mkdir(parents=True, exist_ok=True)
    fits.write_bytes(b"SIMPLE")
    assert client.delete(f"/api/sessions/{s.id}").json() == {"deleted": s.id}
    with pytest.raises(KeyError):
        session_store.load(s.id)
    assert not tdir.exists()
    assert fits.exists()                             # FITS never touched
    active = _session(status="active")
    assert client.delete(f"/api/sessions/{active.id}").status_code == 409


def test_thumb_route_serves_jpeg_with_preview_cap(client):
    s = _session()
    fid = s.frames[0].id
    tdir = session_store.thumbs_dir(s.id)
    tdir.mkdir(parents=True)
    (tdir / f"{fid}.jpg").write_bytes(b"\xff\xd8fake")
    r = client.get(f"/api/sessions/{s.id}/frames/{fid}/thumb")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/sessions/{s.id}/frames/nope/thumb").status_code == 404
