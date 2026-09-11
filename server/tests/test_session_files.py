"""S5: GET /api/sessions/{id}/files + /api/sessions/current/files.

The index an operator reads to answer "which subs did I keep, and what did they
cost". Three things are actually under test and the rest is arithmetic:

* the fold uses the EFFECTIVE verdict (``SessionFrame.effective()`` -- override
  beats auto), so a regrade moves ``accepted`` and ``integration_s`` and not
  just a badge;
* the payload carries NO path, anywhere, at any nesting depth, for anybody --
  asserted by walking the whole JSON rather than by checking the keys this
  version happens to emit;
* the capability floor is ``view.preview``, which is what makes the route
  readable by an operator (no ``config.backend``) and by a viewer.

Harness mirrors tests/test_sessions_api.py (FakeAuthProvider + TestClient +
tmp CAPTURE_DIR) so a session with frames is built the same way here.
"""
import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.auth import principal_for_role, reset_active_provider, set_active_provider
from astrodeck.auth.capabilities import (CAP_CONFIG_BACKEND, CAP_VIEW_PREVIEW,
                                         VIEWER_LINK_CAPS)
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


def _two_filter_plan() -> SequencePlan:
    return SequencePlan(name="two", count_mode="accepted", targets=[Target(
        name="NGC 6946", ra_hours=20.58, dec_deg=60.15, steps=[
            ExposureStep(filter="L", exposure_s=120, count=10),
            ExposureStep(filter="Ha", exposure_s=300, count=10),
        ])])


def _write(tmp_path, name: str, size: int) -> str:
    p = tmp_path / "captures" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    return str(p)


def _session_with_frames(tmp_path, status="dormant") -> Session:
    """Two filters, six frames, one of each grading shape.

    L: two auto-accepted (1000 + 2000 bytes) and one auto-accepted frame
       OVERRIDDEN to reject (4000 bytes) -> 2 accepted, 240 s, 7000 bytes.
    Ha: one auto-rejected frame OVERRIDDEN to accept (8000 bytes), one plain
       auto-reject (16000 bytes), and one accepted frame whose FILE IS MISSING
       -> 2 accepted, 600 s, 24000 bytes.
    """
    plan = _two_filter_plan()
    s = Session(name="files", created_ts=1.0, status=status, plan=plan)
    tid = plan.targets[0].id
    lum, ha = plan.targets[0].steps

    def add(step, ts, path, *, auto, override=None, metrics=None):
        s.frames.append(SessionFrame(
            ts=ts, night="n1", target_id=tid, step_id=step.id, path=path,
            metrics=metrics or {}, auto_accepted=auto, override=override))

    add(lum, 30.0, _write(tmp_path, "l2.fits", 2000), auto=True,
        metrics={"hfr": 2.5, "stars": 412.0, "guide_rms": 0.61})
    add(lum, 10.0, _write(tmp_path, "l1.fits", 1000), auto=True)
    add(lum, 40.0, _write(tmp_path, "l3.fits", 4000), auto=True,
        override="reject")
    add(ha, 50.0, _write(tmp_path, "h1.fits", 8000), auto=False,
        override="accept")
    add(ha, 60.0, _write(tmp_path, "h2.fits", 16000), auto=False)
    add(ha, 70.0, str(tmp_path / "captures" / "gone.fits"), auto=True)
    session_store.save(s)
    return s


def _walk(node):
    """Every (key, value) pair anywhere in a JSON document."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


# ------------------------------------------------------------------ the fold

def test_counts_accepted_bytes_and_integration_fold_per_filter(client, tmp_path):
    s = _session_with_frames(tmp_path)
    r = client.get(f"/api/sessions/{s.id}/files")
    assert r.status_code == 200
    body = r.json()

    assert body["target"] == "NGC 6946"
    # plan step order, NOT the order the frames were shot
    assert [g["filter"] for g in body["by_filter"]] == ["L", "Ha"]

    lum, ha = body["by_filter"]
    assert (lum["count"], lum["accepted"]) == (3, 2)      # one overridden away
    assert lum["exposure_s"] == 120.0
    assert lum["bytes"] == 1000 + 2000 + 4000
    assert lum["integration_s"] == 240.0                  # accepted frames only

    assert (ha["count"], ha["accepted"]) == (3, 2)        # one overridden IN
    assert ha["exposure_s"] == 300.0
    assert ha["bytes"] == 8000 + 16000                    # the missing file is 0
    assert ha["integration_s"] == 600.0

    assert body["totals"] == {"frames": 6, "accepted": 4,
                              "bytes": 31000, "integration_s": 840.0}


def test_the_verdict_is_the_effective_one_and_a_regrade_moves_it(client, tmp_path):
    """The whole point of showing grades here is being able to change one.

    The row carries the LEDGER frame id, so PATCH takes it directly; if this
    index folded ``auto_accepted`` instead of ``effective()`` the accepted count
    would not move and the two surfaces would disagree about the same frame."""
    s = _session_with_frames(tmp_path)
    lum = client.get(f"/api/sessions/{s.id}/files").json()["by_filter"][0]
    rejected = [f for f in lum["frames"] if f["override"] == "reject"]
    assert len(rejected) == 1
    assert rejected[0]["accepted"] is False               # override beats auto

    r = client.patch(f"/api/sessions/{s.id}/frames/{rejected[0]['id']}",
                     json={"override": None})
    assert r.status_code == 200
    after = client.get(f"/api/sessions/{s.id}/files").json()
    assert after["by_filter"][0]["accepted"] == 3
    assert after["by_filter"][0]["integration_s"] == 360.0
    assert after["totals"]["accepted"] == 5


def test_frames_are_oldest_first_and_carry_their_metrics(client, tmp_path):
    s = _session_with_frames(tmp_path)
    lum = client.get(f"/api/sessions/{s.id}/files").json()["by_filter"][0]
    assert [f["ts"] for f in lum["frames"]] == [10.0, 30.0, 40.0]
    graded = lum["frames"][1]
    assert graded["hfr"] == 2.5
    assert graded["stars"] == 412 and isinstance(graded["stars"], int)
    assert graded["guide_rms"] == 0.61
    ungraded = lum["frames"][0]
    assert (ungraded["hfr"], ungraded["stars"], ungraded["guide_rms"]) == (
        None, None, None)


def test_a_frame_whose_file_is_missing_reports_zero_bytes(client, tmp_path):
    s = _session_with_frames(tmp_path)
    ha = client.get(f"/api/sessions/{s.id}/files").json()["by_filter"][1]
    missing = [f for f in ha["frames"] if f["ts"] == 70.0]
    assert len(missing) == 1 and missing[0]["bytes"] == 0
    assert missing[0]["accepted"] is True                 # still counted, still graded


def test_thumb_is_a_url_when_one_exists_and_null_otherwise(client, tmp_path):
    s = _session_with_frames(tmp_path)
    fid = s.frames[0].id
    tdir = session_store.thumbs_dir(s.id)
    tdir.mkdir(parents=True)
    (tdir / f"{fid}.jpg").write_bytes(b"\xff\xd8fake")
    rows = {f["id"]: f for g in
            client.get(f"/api/sessions/{s.id}/files").json()["by_filter"]
            for f in g["frames"]}
    assert rows[fid]["thumb"] == f"/api/sessions/{s.id}/frames/{fid}/thumb"
    assert client.get(rows[fid]["thumb"]).status_code == 200   # the URL resolves
    assert all(rows[f.id]["thumb"] is None
               for f in s.frames if f.id != fid)


def test_a_step_dropped_from_the_plan_lands_under_question_mark(client, tmp_path):
    """A dormant session's plan is editable and dropping a step does NOT delete
    the frames it produced. They still cost bytes, so they are still listed --
    under "?", because there is no longer a filter to list them under."""
    s = _session_with_frames(tmp_path)
    plan = s.plan.model_dump()
    plan["targets"][0]["steps"] = [plan["targets"][0]["steps"][0]]     # drop Ha
    assert client.patch(f"/api/sessions/{s.id}", json={"plan": plan}).status_code == 200
    body = client.get(f"/api/sessions/{s.id}/files").json()
    assert [g["filter"] for g in body["by_filter"]] == ["L", "?"]
    orphans = body["by_filter"][1]
    assert orphans["count"] == 3
    # no step left to ask for an exposure, and the ledger records none per
    # frame, so unknown integration is reported as 0 rather than guessed
    assert orphans["exposure_s"] == 0.0
    assert orphans["integration_s"] == 0.0
    assert orphans["bytes"] == 24000                      # bytes are still real


def test_a_filter_with_no_frames_yet_still_appears(client, tmp_path):
    plan = _two_filter_plan()
    s = Session(name="fresh", created_ts=1.0, status="dormant", plan=plan)
    session_store.save(s)
    body = client.get(f"/api/sessions/{s.id}/files").json()
    assert [g["filter"] for g in body["by_filter"]] == ["L", "Ha"]
    assert all(g["count"] == 0 and g["frames"] == [] for g in body["by_filter"])
    assert body["totals"]["frames"] == 0


def test_a_damaged_session_file_is_named_not_a_traceback(client, tmp_path):
    """A file that parses as JSON and is not a Session used to raise
    ``ValidationError`` out of the route -- a 500 with a traceback and no
    sentence, on six routes that each caught only ``KeyError``. It is still a
    500 (the server IS broken in a way the caller cannot fix) but a named one,
    and deliberately NOT a 404: the session is in the list, so saying it does
    not exist would send the user looking for something they can see."""
    s = _session_with_frames(tmp_path, status="dormant")
    path = tmp_path / "captures" / "sessions" / f"{s.id}.json"
    path.write_text('{"id": "' + s.id + '", "plan": 5}', encoding="utf-8")
    for route in (f"/api/sessions/{s.id}", f"/api/sessions/{s.id}/files"):
        r = client.get(route)
        assert r.status_code == 500, route
        assert r.json()["code"] == "session_unreadable", route
        assert s.id in r.json()["detail"], route


def test_unknown_session_404s(client):
    assert client.get("/api/sessions/nope/files").status_code == 404


# --------------------------------------------------------------- /current

def test_current_404s_with_no_active_session_and_200s_with_one(client, tmp_path):
    r = client.get("/api/sessions/current/files")
    assert r.status_code == 404
    assert r.json() == {"detail": "no active session"}

    # a dormant session is not a run in progress
    _session_with_frames(tmp_path, status="dormant")
    assert client.get("/api/sessions/current/files").status_code == 404

    live = _session_with_frames(tmp_path, status="active")
    r = client.get("/api/sessions/current/files")
    assert r.status_code == 200
    assert r.json() == client.get(f"/api/sessions/{live.id}/files").json()


def test_finding_the_live_session_does_not_validate_the_whole_archive(
        client, tmp_path, monkeypatch):
    """``active_session()`` folded ``load_all()``, which fully pydantic-
    validates EVERY stored session -- 200 sessions x 170 frames is 34 000
    ``SessionFrame`` models -- to read one string off each. Measured at the
    store's own soft cap: ``active_session()`` 0.982 s, the fold it feeds
    0.002 s, so the whole cost of opening the Files sheet was finding the
    session. The count below is the assertion: one validation, not one per
    stored session, whatever the library holds."""
    from astrodeck.sequence import session as session_mod

    live = _session_with_frames(tmp_path, status="active")
    for _ in range(5):
        _session_with_frames(tmp_path, status="complete")

    real = session_mod.Session.model_validate
    calls = {"n": 0}

    def _counted(cls_arg, *a, **kw):
        calls["n"] += 1
        return real(cls_arg, *a, **kw)

    monkeypatch.setattr(session_mod.Session, "model_validate",
                        classmethod(lambda cls, *a, **kw: _counted(*a, **kw)))
    r = client.get("/api/sessions/current/files")
    assert r.status_code == 200
    assert r.json()["target"] == "NGC 6946"
    assert calls["n"] == 1, (
        f"six stored sessions cost {calls['n']} full validations to find one")
    # And it is still the RIGHT session -- most recently updated, status active.
    assert r.json() == client.get(f"/api/sessions/{live.id}/files").json()


def test_a_corrupt_archive_does_not_hide_the_live_session(client, tmp_path):
    """The raw scan must keep ``load_all``'s skip-the-unreadable rule: a
    session file that no longer validates is not a reason for the running
    session to become unfindable."""
    _session_with_frames(tmp_path, status="active")
    junk = tmp_path / "captures" / "sessions" / "broken.json"
    junk.write_text('{"status": "active", "updated_ts": 9e9, "plan": 5}',
                    encoding="utf-8")
    r = client.get("/api/sessions/current/files")
    assert r.status_code == 200
    assert r.json()["target"] == "NGC 6946"


def test_current_is_not_matched_as_a_session_id(client, tmp_path):
    """Declaration order, asserted. With the parameterised route first,
    "current" binds as a session id and the route 404s 'session not found' --
    a routing bug wearing a data bug's error message."""
    _session_with_frames(tmp_path, status="active")
    r = client.get("/api/sessions/current/files")
    assert r.status_code == 200
    assert "by_filter" in r.json()


# ------------------------------------------------------------------- RBAC

def test_an_operator_lacks_config_backend_and_still_gets_grades(client, tmp_path):
    s = _session_with_frames(tmp_path)
    operator = principal_for_role("operator")
    assert not operator.has(CAP_CONFIG_BACKEND)      # the point of the route
    assert operator.has(CAP_VIEW_PREVIEW)
    set_active_provider(FakeAuthProvider(operator))
    body = client.get(f"/api/sessions/{s.id}/files").json()
    assert body["totals"]["accepted"] == 4
    assert body["by_filter"][0]["frames"][0]["accepted"] is True


def test_a_viewer_holds_view_preview_and_so_reads_it_too(client, tmp_path):
    assert CAP_VIEW_PREVIEW in VIEWER_LINK_CAPS      # per auth/capabilities.py
    s = _session_with_frames(tmp_path)
    set_active_provider(FakeAuthProvider(principal_for_role("viewer")))
    assert client.get(f"/api/sessions/{s.id}/files").status_code == 200
    assert client.get("/api/sessions/current/files").status_code == 404


def test_a_syncer_lacks_view_preview_and_is_refused(client, tmp_path):
    """The negative control. Without one of these, `require(CAP_VIEW_PREVIEW)`
    could be missing entirely and every assertion above would still pass."""
    syncer = principal_for_role("syncer")
    assert not syncer.has(CAP_VIEW_PREVIEW)
    s = _session_with_frames(tmp_path)
    set_active_provider(FakeAuthProvider(syncer))
    assert client.get(f"/api/sessions/{s.id}/files").status_code == 403
    assert client.get("/api/sessions/current/files").status_code == 403


# ------------------------------------------------------------- no paths out

@pytest.mark.parametrize("role", ["admin", "operator", "viewer"])
def test_no_path_key_and_no_disk_location_anywhere_in_the_payload(
        client, tmp_path, role):
    """Walked, not spot-checked.

    ``_redact_session_for`` exists because the ledger route handed out absolute
    paths; this payload must never have one to redact in the first place, at any
    nesting depth and for any role -- including the admin, who is the caller the
    ledger route used to make an exception for."""
    s = _session_with_frames(tmp_path)
    set_active_provider(FakeAuthProvider(principal_for_role(role)))
    body = client.get(f"/api/sessions/{s.id}/files").json()
    keys = [k for k, _ in _walk(body)]
    assert "path" not in keys
    assert not [k for k in keys if "path" in k.lower()]
    blob = json.dumps(body)
    assert ".fits" not in blob
    assert str(tmp_path).replace("\\", "/") not in blob.replace("\\", "/")


# ------------------------------------------------------ boot RBAC assertion

def test_create_app_passes_the_boot_rbac_assertion(client):
    """``create_app()`` runs ``assert_route_capabilities`` last and raises if a
    route's ``@declare`` disagrees with what it enforces. The fixture already
    built the app, so reaching here at all is the assertion -- but name it, so
    a future mis-declared route fails a test that says why."""
    from astrodeck.auth.rbac import iter_app_routes
    paths = {getattr(r, "path", "") for r in iter_app_routes(client.app)}
    assert "/api/sessions/{session_id}/files" in paths
    assert "/api/sessions/current/files" in paths
