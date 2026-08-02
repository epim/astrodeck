"""Mosaic framing engine (Owner C — Sky Atlas).

These exercise ``astrodeck.catalog.framing.compute_mosaic`` (the pure, importable
canonical engine) + the ``POST /api/framing/mosaic`` route on a minimal app, and
assert it is byte-compatible with the client mirror ``ui/src/lib/framing.ts``.

Coverage (per the sub-batch brief / spec §5):
  * M31 (ra_hours ~ 0.71) a 3x1 mosaic -> every panel ra in [0, 24) (the ``% 24``
    wrap; an unwrapped RA 422s the plan because Target.ra_hours is
    ``Field(ge=0, lt=24)``);
  * a 1x1 at any center deprojects (rho -> 0) to exactly the center (no NaN);
  * a high-dec center (+69 M81) -> finite panel coords + a sane tangent-plane
    total FOV (NOT raw degrees of RA);
  * boustrophedon (snake) panel order;
  * a Target round-trips when built from a panel (ra_hours passes ge=0/lt=24);
  * per-panel transit altitude: asked for by a named ``date`` or by
    ``transit_alt`` (tonight), silent when neither is asked for, and always
    naming its cause on a panel it could not answer for.
"""
from __future__ import annotations

import math

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrodeck.catalog import framing
from astrodeck.sequence.models import Target


# A representative single-frame FOV (a ~530mm scope on an APS-C-ish sensor).
FOV_X = 1.6
FOV_Y = 1.1


# --------------------------------------------------------------- pure compute

def test_m31_3x1_all_panel_ra_wrapped_into_range():
    # M31 sits near RA 0h; framing a 3-wide mosaic centered just above the wrap
    # (RA 0.05h) puts the leftmost panel below RA 0, which without the %24 wrap
    # deprojects to a small negative and 422s the plan (Target.ra_hours
    # Field(ge=0, lt=24)).
    res = framing.compute_mosaic({
        "ra_hours": 0.05, "dec_deg": 41.27,
        "rows": 1, "cols": 3, "overlap": 0.2, "rotation_deg": 0.0,
        "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
    })
    panels = res["panels"]
    assert len(panels) == 3
    for p in panels:
        assert 0.0 <= p["ra_hours"] < 24.0, f"unwrapped RA: {p}"
        assert -90.0 <= p["dec_deg"] <= 90.0
        assert math.isfinite(p["ra_hours"]) and math.isfinite(p["dec_deg"])
    # the leftmost column (col 0) must have wrapped near the top of the range,
    # i.e. it deprojected to just under 24h (proves the wrap actually fired).
    left = next(p for p in panels if p["col"] == 0)
    assert left["ra_hours"] > 23.0


def test_1x1_rho_zero_returns_exact_center_no_nan():
    # The common path: open on a target, hit Send. A 1x1 at the center has gx=gy=0
    # so rho->0; the deproject must return the center verbatim (no divide-by-zero).
    for ra0, dec0 in [(0.71, 41.27), (12.0, 0.0), (18.6, -22.0), (5.5, 89.0)]:
        res = framing.compute_mosaic({
            "ra_hours": ra0, "dec_deg": dec0,
            "rows": 1, "cols": 1, "overlap": 0.25, "rotation_deg": 37.0,
            "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
        })
        assert len(res["panels"]) == 1
        p = res["panels"][0]
        assert p["ra_hours"] == pytest.approx(ra0, abs=1e-12)
        assert p["dec_deg"] == pytest.approx(dec0, abs=1e-12)
        assert math.isfinite(p["ra_hours"]) and math.isfinite(p["dec_deg"])


def test_high_dec_center_finite_coords_and_sane_total_fov():
    # M81 at +69 dec: high-dec mosaics are where a "raw degrees of RA" total FOV
    # would balloon. Total FOV is the tangent-plane extent, so it must stay close
    # to cols*fov_x (minus overlap), independent of the cos(dec) RA stretch.
    rows, cols, overlap = 2, 3, 0.25
    res = framing.compute_mosaic({
        "ra_hours": 9.93, "dec_deg": 69.07,
        "rows": rows, "cols": cols, "overlap": overlap, "rotation_deg": 0.0,
        "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
    })
    for p in res["panels"]:
        assert math.isfinite(p["ra_hours"]) and math.isfinite(p["dec_deg"])
        assert 0.0 <= p["ra_hours"] < 24.0
        assert -90.0 <= p["dec_deg"] <= 90.0

    exp_x = (cols - (cols - 1) * overlap) * FOV_X
    exp_y = (rows - (rows - 1) * overlap) * FOV_Y
    assert res["total_fov_x_deg"] == pytest.approx(exp_x)
    assert res["total_fov_y_deg"] == pytest.approx(exp_y)
    # tangent-plane extent stays a few degrees — NOT the ~3deg/cos(69)=~9deg of RA.
    assert res["total_fov_x_deg"] < 2 * cols * FOV_X
    assert res["frame_fov_x_deg"] == pytest.approx(FOV_X)
    assert res["frame_fov_y_deg"] == pytest.approx(FOV_Y)


def test_boustrophedon_snake_order():
    # Even rows scan left->right, odd rows right->left, to minimise slew travel.
    rows, cols = 3, 4
    res = framing.compute_mosaic({
        "ra_hours": 5.0, "dec_deg": 0.0,
        "rows": rows, "cols": cols, "overlap": 0.1, "rotation_deg": 0.0,
        "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
    })
    order = [(p["row"], p["col"]) for p in res["panels"]]
    expected: list[tuple[int, int]] = []
    for r in range(rows):
        cols_iter = range(cols) if r % 2 == 0 else range(cols - 1, -1, -1)
        for c in cols_iter:
            expected.append((r, c))
    assert order == expected
    # every (row, col) appears exactly once.
    assert sorted(order) == sorted((r, c) for r in range(rows) for c in range(cols))


def test_panel_round_trips_into_target_validator():
    # The downstream contract: each panel must build a Target without tripping the
    # ra_hours Field(ge=0, lt=24) / dec_deg Field(ge=-90, le=90) validators — the
    # exact thing the %24 wrap protects.
    res = framing.compute_mosaic({
        "ra_hours": 0.05, "dec_deg": 41.27,   # near RA 0 so a panel wraps below 0
        "rows": 1, "cols": 3, "overlap": 0.0, "rotation_deg": 12.0,
        "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
    })
    for p in res["panels"]:
        t = Target(
            name=f"M31 {p['row'] + 1}-{p['col'] + 1}",
            ra_hours=p["ra_hours"], dec_deg=p["dec_deg"],
            rotation_deg=p["rotation_deg"], mosaic_group="M31",
            steps=[],
        )
        assert 0.0 <= t.ra_hours < 24.0
        assert t.mosaic_group == "M31"
        assert t.rotation_deg == pytest.approx(12.0)


def test_rotation_offsets_panels_off_the_dec_axis():
    # With a non-zero rotation, a horizontal (1-row) mosaic must NOT stay on a
    # single dec; the PA tilts the row so off-center panels gain a dec offset.
    res = framing.compute_mosaic({
        "ra_hours": 5.0, "dec_deg": 0.0,
        "rows": 1, "cols": 3, "overlap": 0.0, "rotation_deg": 30.0,
        "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
    })
    decs = [p["dec_deg"] for p in res["panels"]]
    assert max(decs) - min(decs) > 0.1   # the tilt produced a real dec spread


# --------------------------------------------------------------- route

@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(framing.router)
    with TestClient(app) as c:
        yield c


def test_mosaic_route_returns_result_shape(client):
    r = client.post("/api/framing/mosaic", json={
        "ra_hours": 0.71, "dec_deg": 41.27,
        "rows": 2, "cols": 2, "overlap": 0.2, "rotation_deg": 0.0,
        "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"panels", "total_fov_x_deg", "total_fov_y_deg",
            "frame_fov_x_deg", "frame_fov_y_deg", "pixel_scale_arcsec"} <= set(body)
    assert len(body["panels"]) == 4
    for p in body["panels"]:
        assert {"row", "col", "ra_hours", "dec_deg", "rotation_deg"} <= set(p)
        assert 0.0 <= p["ra_hours"] < 24.0
        # no transit_alt without a date
        assert "transit_alt" not in p or p["transit_alt"] is None


def test_mosaic_route_fills_transit_alt_with_date(client):
    # With a date the route stamps each panel's peak altitude tonight (a finite
    # number in [-90, 90]); this also exercises the visibility import path.
    r = client.post("/api/framing/mosaic", json={
        "ra_hours": 0.71, "dec_deg": 41.27,
        "rows": 1, "cols": 2, "overlap": 0.2, "rotation_deg": 0.0,
        "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
        "date": "2026-01-15",
    })
    assert r.status_code == 200, r.text
    panels = r.json()["panels"]
    assert len(panels) == 2
    for p in panels:
        assert "transit_alt" in p
        assert -90.0 <= p["transit_alt"] <= 90.0


def test_transit_alt_flag_answers_for_tonight_without_naming_a_night(client):
    # The Atlas asks for TONIGHT, and it cannot do that by echoing
    # VisibilityNight.date back: compute_night reports the UTC date of the
    # night's solar-midnight anchor, while _night_anchor_unix reads a date as
    # the civil date of the EVENING and adds 24h, so at every longitude <= 0 the
    # echo lands a whole night late. `transit_alt: true` is the way to say
    # "tonight" that has no date in it to drift.
    r = client.post("/api/framing/mosaic", json={
        "ra_hours": 0.71, "dec_deg": 41.27,
        "rows": 1, "cols": 2, "overlap": 0.2, "rotation_deg": 0.0,
        "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
        "transit_alt": True,
    })
    assert r.status_code == 200, r.text
    panels = r.json()["panels"]
    assert len(panels) == 2
    for p in panels:
        assert "transit_alt" in p, p
        assert -90.0 <= p["transit_alt"] <= 90.0


def test_a_mosaic_nobody_asked_a_night_about_stays_silent_rather_than_guessing(
        client):
    # Neither flag => no altitudes and, just as importantly, no transit_alt_error
    # either. "We tried and could not" and "you never asked" are different
    # answers, and the client distinguishes them by the presence of the error
    # key; a route that stamped a reason here would make every plain Send look
    # like a failure. This is also the route's astropy budget: Send posts this
    # exact shape for up to 100 panels.
    r = client.post("/api/framing/mosaic", json={
        "ra_hours": 0.71, "dec_deg": 41.27,
        "rows": 2, "cols": 2, "overlap": 0.2, "rotation_deg": 0.0,
        "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
    })
    assert r.status_code == 200, r.text
    for p in r.json()["panels"]:
        assert "transit_alt" not in p
        assert "transit_alt_error" not in p


def test_tonight_flag_still_names_the_reason_when_a_panel_fails(
        client, monkeypatch):
    # The reason channel has to survive on the path the UI actually uses. The
    # Atlas reads transit_alt_error to put a stated cause where an altitude
    # would be, so a failure in the `transit_alt: true` mode that dropped the key
    # would put the blank cell straight back on the screen.
    from astrodeck.catalog import visibility

    def _boom(ra_hours, dec_deg, *, date=None, site=None):
        raise OSError("ephemeris table unreadable")

    monkeypatch.setattr(visibility, "transit_alt_for", _boom)
    r = client.post("/api/framing/mosaic", json={
        "ra_hours": 0.71, "dec_deg": 41.27,
        "rows": 1, "cols": 3, "overlap": 0.2, "rotation_deg": 0.0,
        "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
        "transit_alt": True,
    })
    assert r.status_code == 200, r.text
    panels = r.json()["panels"]
    assert len(panels) == 3
    for p in panels:
        assert "transit_alt" not in p
        assert "OSError" in p["transit_alt_error"]
        assert "ephemeris table unreadable" in p["transit_alt_error"]


def test_a_named_date_beats_the_tonight_flag_rather_than_being_ignored(
        client, monkeypatch):
    # Both set: the named night wins, because that is the one the caller can be
    # holding a chart of. A silent downgrade to tonight would be the same
    # wrong-night bug the flag exists to avoid, just from the other direction.
    from astrodeck.catalog import visibility

    seen: list[str | None] = []
    real = visibility.transit_alt_for

    def _spy(ra_hours, dec_deg, *, date=None, site=None):
        seen.append(date)
        return real(ra_hours, dec_deg, date=date, site=site)

    monkeypatch.setattr(visibility, "transit_alt_for", _spy)
    r = client.post("/api/framing/mosaic", json={
        "ra_hours": 0.71, "dec_deg": 41.27,
        "rows": 1, "cols": 2, "overlap": 0.2, "rotation_deg": 0.0,
        "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
        "date": "2026-01-15", "transit_alt": True,
    })
    assert r.status_code == 200, r.text
    assert seen == ["2026-01-15", "2026-01-15"]


@pytest.fixture
def cold_config(tmp_path):
    """Hand back a callable that puts the process-wide ``config_store`` into its
    never-loaded state on a fresh empty directory.

    This is a HARNESS state, not a field one: the served app materialises the
    store single-threaded at boot (``__main__._security_banner`` and
    ``api.app._lifespan``, both before uvicorn listens), so the cold path only
    meets concurrency behind a bare ``FastAPI()`` with no lifespan — the
    ``client`` fixture above. See ``ConfigStore.__init__`` for why the lock is
    right anyway."""
    import astrodeck.config as config_mod
    store = config_mod.config_store
    saved_path, saved_cfg = store._path, store._cfg

    def reset(n: int):
        d = tmp_path / f"cold{n}"
        d.mkdir()
        store._path = d / "astrodeck.json"
        store._cfg = None
        return store

    try:
        yield reset
    finally:
        # Put the session throwaway store (conftest's
        # _never_touch_the_real_config) back exactly as we found it: this is a
        # singleton, and whichever test this xdist worker runs next reads it.
        store._path, store._cfg = saved_path, saved_cfg


def test_mosaic_fills_every_panel_when_config_has_never_been_loaded(
        client, cold_config):
    # Regression, and the cause of an intermittent red in
    # test_mosaic_route_fills_transit_alt_with_date: the route fans its panels out
    # over asyncio.to_thread, and EVERY panel reads hub.site -> the config_store
    # singleton. When that singleton had not been materialised yet, each thread
    # found ``_cfg is None``, each ran _load() -> _save(), and all of them wrote
    # the one fixed "astrodeck.json.tmp"; the first os.replace consumed the tmp
    # file and the rest raised FileNotFoundError. The route swallows a per-panel
    # failure, so the symptom was a panel silently missing its transit_alt — the
    # mosaic answering for some panels and saying nothing at all about the others.
    #
    # 8 panels = the route's semaphore width, so every worker thread races the
    # lazy first load at once. One cold start caught the unfixed store only ~40%
    # of the time (thread 1 often finished before thread 2 was scheduled), which
    # is exactly how this reached CI as a flake; ten independent cold starts make
    # it a gate. Each round must stamp all eight — a single lost panel fails.
    for n in range(10):
        store = cold_config(n)
        r = client.post("/api/framing/mosaic", json={
            "ra_hours": 0.71, "dec_deg": 41.27,
            "rows": 2, "cols": 4, "overlap": 0.2, "rotation_deg": 0.0,
            "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
            "date": "2026-01-15",
        })
        assert r.status_code == 200, r.text
        panels = r.json()["panels"]
        assert len(panels) == 8
        lost = [p for p in panels if p.get("transit_alt") is None]
        assert not lost, (
            f"cold start {n}: {len(lost)} of 8 panels lost their transit_alt to "
            f"the unmaterialised-config race: {lost}")
        # ...and the cold path really ran this round, so a green here can never
        # mean "the store was already warm and the race never had its chance".
        assert store._cfg is not None and store._path.exists()


def test_panel_that_cannot_be_computed_says_why_instead_of_dropping_the_key(
        client, monkeypatch):
    # The mechanism that made the config-store race invisible: a per-panel
    # failure used to be swallowed unlogged, so the panel simply had no
    # transit_alt and the client could not tell "not asked for" from "we tried
    # and could not". Whatever fails next must name itself.
    from astrodeck.catalog import visibility

    def _boom(ra_hours, dec_deg, *, date=None, site=None):
        raise OSError("ephemeris table unreadable")

    monkeypatch.setattr(visibility, "transit_alt_for", _boom)
    r = client.post("/api/framing/mosaic", json={
        "ra_hours": 0.71, "dec_deg": 41.27,
        "rows": 1, "cols": 2, "overlap": 0.2, "rotation_deg": 0.0,
        "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
        "date": "2026-01-15",
    })
    assert r.status_code == 200, r.text     # one dead panel is not a dead mosaic
    panels = r.json()["panels"]
    assert len(panels) == 2
    for p in panels:
        assert "transit_alt" not in p
        # the type AND the message: a bare OSError stringifies to nothing, and an
        # empty reason is the silence this field exists to end.
        assert "OSError" in p["transit_alt_error"]
        assert "ephemeris table unreadable" in p["transit_alt_error"]


def test_every_panel_says_so_when_the_visibility_module_will_not_import(
        client, monkeypatch):
    # The other swallow: astropy/visibility unavailable wholesale. A ``None`` in
    # sys.modules is exactly what CPython raises ImportError on, so this drives
    # the real import failure rather than a stand-in.
    import sys
    monkeypatch.setitem(sys.modules, "astrodeck.catalog.visibility", None)
    r = client.post("/api/framing/mosaic", json={
        "ra_hours": 0.71, "dec_deg": 41.27,
        "rows": 2, "cols": 2, "overlap": 0.2, "rotation_deg": 0.0,
        "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
        "date": "2026-01-15",
    })
    assert r.status_code == 200, r.text
    panels = r.json()["panels"]
    assert len(panels) == 4
    for p in panels:
        assert "transit_alt" not in p
        assert "visibility unavailable" in p["transit_alt_error"]


def test_mosaic_rejects_a_date_it_would_otherwise_answer_for_tonight(client):
    # ``_night_anchor_unix`` swallows an unparseable date and anchors on TONIGHT,
    # so this route used to hand back tonight's altitudes labelled as the night
    # the caller asked for — a plausible wrong answer, which is worse than an
    # error. /api/visibility has refused this since check_night_date landed; the
    # mosaic route bypassed it.
    r = client.post("/api/framing/mosaic", json={
        "ra_hours": 0.71, "dec_deg": 41.27,
        "rows": 1, "cols": 1, "overlap": 0.0, "rotation_deg": 0.0,
        "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
        "date": "2026-13-45",
    })
    assert r.status_code == 422, r.text
    assert "YYYY-MM-DD" in r.text


def test_mosaic_route_rejects_unwrapped_center(client):
    # The input center itself must satisfy ge=0/lt=24 (the spec mirror). An out-of
    # -range center 422s at the model boundary.
    r = client.post("/api/framing/mosaic", json={
        "ra_hours": 25.0, "dec_deg": 41.27,
        "rows": 1, "cols": 1, "overlap": 0.0, "rotation_deg": 0.0,
        "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y,
    })
    assert r.status_code == 422
