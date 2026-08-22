"""The three cloud-map routes, stage 6a -- and the one architectural rule.

The routes are read-only, session-gated, and answer with 200 for every state of
the sky INCLUDING "off" and "no data yet", because a UI has to draw off, stale
and broken as three different things and a 404 collapses them into one.

The last test in this file is not about a route. It is the rule the whole stage
is built around, kept by a detector rather than by intent: **the cloud model has
no vote**. Nothing in the sequence engine, the safety gate or auto-resume may
consult it. That is not a stylistic preference -- this codebase's weather gate
refused two consecutive clear nights to a forecast that had a vote, and the fix
was to take the vote away. A satellite model over a 2 km cell is a better
instrument than a 10 km forecast and it is still not the camera.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.cloudmap.service as service_mod
from astrodeck.cloudmap.occlusion import MIN_USABLE_ALT_DEG
from astrodeck.cloudmap.service import CREDIT_SOURCE, CloudmapService
from astrodeck.config import ConfigStore

from test_cloudmap_service import (  # the synthetic sky, shared verbatim
    BASE,
    SITE_LAT,
    SITE_LON,
    _BusRecorder,
    _NoWeather,
    _RecordingClient,
    _Upstream,
)

REPO = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------- harness

def _make(tmp_path, monkeypatch, *, enabled=True, populate=True):
    """An isolated app whose cloud-map singleton is a service with a known sky.

    The service is the real one -- the routes are being tested on top of stage
    4 and stage 5's actual arithmetic, not on a canned payload -- with only the
    listing, the download and the HDF5 read replaced.
    """
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.auth import reset_active_provider
    from datetime import datetime, timezone

    store = ConfigStore(path=tmp_path / "astrodeck.json")
    site = store.cfg().site
    site.latitude, site.longitude = SITE_LAT, SITE_LON
    site.elevation_m, site.is_default = 0.0, False
    store.cfg().cloudmap.enabled = enabled
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(service_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(service_mod, "bus", _BusRecorder())
    monkeypatch.setattr(service_mod.httpx, "AsyncClient", _RecordingClient)
    _RecordingClient.urls = []

    upstream = _Upstream(datetime.fromtimestamp(BASE - 60.0, tz=timezone.utc))
    monkeypatch.setattr(service_mod, "latest_ref", upstream.latest_ref)
    monkeypatch.setattr(service_mod, "ensure_cached", upstream.ensure_cached)
    monkeypatch.setattr(service_mod, "read_window", upstream.read_window)
    monkeypatch.setattr(service_mod, "evict", lambda *a, **kw: 0)
    # The HDF5 read is faked here too, so the optional extra is not needed --
    # but the tick refuses to fetch without it and CI installs `.[dev]` only.
    # See the same line in test_cloudmap_service.py's fixture.
    monkeypatch.setattr(service_mod, "HAVE_H5PY", True)

    service = CloudmapService(clock=lambda: BASE, weather=_NoWeather())
    if populate:
        asyncio.run(service.tick())
    else:
        # The lifespan starts the poller, and the poller works -- so "nothing
        # fetched yet" has to be an upstream that is not answering rather than
        # a service nobody has ticked. That is also the state a rig actually
        # sees on the first ten minutes after a reboot with NOAA unreachable.
        from astrodeck.cloudmap.granule import CloudmapUnavailable
        upstream.fail = CloudmapUnavailable(
            "listing ABI-L2-ACMC in noaa-goes18 failed: ConnectError")
    monkeypatch.setattr(app_module, "cloudmap_service", service)
    reset_active_provider()
    return store, app_module.create_app(), service


# ==================================================== 2. off is not broken

def test_disabled_returns_two_hundred_not_a_404(tmp_path, monkeypatch):
    """A UI needs to render "off" differently from "broken", and a 404
    conflates them: the same status code would mean "you turned this off", "the
    route moved" and "the reverse proxy is misconfigured"."""
    _store, app, _svc = _make(tmp_path, monkeypatch, enabled=False,
                              populate=False)
    with TestClient(app) as c:
        for url in ("/api/cloudmap",
                    "/api/cloudmap/dome",
                    "/api/cloudmap/at?alt=45&az=180"):
            r = c.get(url)
            assert r.status_code == 200, url + " -> " + r.text
            body = r.json()
            assert body["enabled"] is False, url
            assert body["observed_at"] is None, url
            assert body["credit"]["source"] == CREDIT_SOURCE, url
        assert c.get("/api/cloudmap").json()["motion"] is None
        assert c.get("/api/cloudmap/dome").json()["rows"] is None
        assert c.get("/api/cloudmap/at?alt=45&az=180").json()["basis"] \
            == "no_data"


# ================================================== 12. NOAA gets named

def test_the_dome_route_carries_the_noaa_credit(tmp_path, monkeypatch):
    """The credit is not decoration.

    ``tools/credits_registry.py`` records that NOAA GOES is credited nowhere on
    screen yet and that the cloud-map panel should name it when it lands. These
    routes are how 6b does that without inventing the string, so the string is
    pinned against the registry's own entry rather than typed twice.
    """
    import sys
    tools = str(REPO / "tools")
    if tools not in sys.path:
        sys.path.append(tools)
    import credits_registry as registry

    entry = next(s for s in registry.SERVICES
                 if "noaa" in s["name"].lower())
    assert CREDIT_SOURCE == entry["name"], (
        "the routes name NOAA differently from the credits registry, which is "
        "how a credit line and the licence page come to disagree")

    _store, app, _svc = _make(tmp_path, monkeypatch)
    with TestClient(app) as c:
        r = c.get("/api/cloudmap/dome?alt_step=30&az_step=45")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["credit"]["source"] == CREDIT_SOURCE
        assert body["credit"]["url"].startswith("https://")
        assert body["rows"] is not None and body["rows"]
        assert body["alt_start"] == pytest.approx(MIN_USABLE_ALT_DEG)
        assert body["alt_step"] == pytest.approx(30.0)
        assert body["az_step"] == pytest.approx(45.0)
        assert len(body["rows"][0]) == 8            # 360 / 45
        assert body["observed_at"] is not None
        assert body["stale"] is False
        # Every other surface carries it too, or 6b has to remember which.
        assert c.get("/api/cloudmap").json()["credit"]["source"] == CREDIT_SOURCE
        assert c.get("/api/cloudmap/at?alt=45&az=180").json()["credit"][
            "source"] == CREDIT_SOURCE


# ======================================= 13. the honesty pair survives JSON

def test_the_at_route_reports_the_beam_and_the_cell_together(
        tmp_path, monkeypatch):
    """Design 4 section 4.3's pair. At 45 degrees the beam is a couple of
    hundred millimetres across where it meets the deck and the mask cell is
    kilometres wide -- over a hundred times the area. A payload that carried
    the probability without the ratio would be claiming a determination the
    data cannot support, and a serialiser that dropped a tuple would do it
    quietly."""
    _store, app, _svc = _make(tmp_path, monkeypatch)
    with TestClient(app) as c:
        r = c.get("/api/cloudmap/at?alt=45&az=180")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["basis"] in ("crossing", "mask_only")
        assert 0.0 <= body["probability"] <= 1.0
        assert body["beam_m"] is not None and body["beam_m"] > 0.0
        assert body["cell_km"] is not None
        assert len(body["cell_km"]) == 2
        ns_km, ew_km = body["cell_km"]
        assert ns_km > 0.0 and ew_km > 0.0
        # A beam measured in millimetres against a cell measured in kilometres:
        # the ratio is the thing 6b has to show, so both have to be here.
        assert body["beam_m"] * 1000.0 < ns_km * 1000.0 * 1000.0
        assert body["pierce_lat_deg"] is not None
        assert body["downrange_km"] is not None
        assert body["ahead_s"] == pytest.approx(0.0)
        assert body["reason"]


# ============================ 14. a caller error and a state of the sky

def test_a_bad_step_is_a_400_and_a_missing_mask_is_a_200(tmp_path, monkeypatch):
    """Two failures that look alike from a browser and are not alike at all.

    A step of zero is the caller asking for something that does not exist; no
    granule yet is the sky, and the route has to be able to say so with a
    reason rather than with a status code the client will retry.
    """
    _store, app, _svc = _make(tmp_path, monkeypatch)
    with TestClient(app) as c:
        for bad in ("alt_step=0", "alt_step=46", "az_step=0", "az_step=45.1",
                    "alt_step=-2"):
            r = c.get("/api/cloudmap/dome?" + bad)
            assert r.status_code == 400, bad + " -> " + str(r.status_code)
        for bad in ("alt=0&az=180", "alt=91&az=180", "alt=45&az=nan",
                    "alt=45&az=180&ahead_s=-1", "alt=45&az=180&ahead_s=nan"):
            r = c.get("/api/cloudmap/at?" + bad)
            assert r.status_code == 400, bad + " -> " + str(r.status_code)
        # 45 exactly is inside stage 4's (0, 45] and must be accepted.
        assert c.get("/api/cloudmap/dome?alt_step=45&az_step=45"
                     ).status_code == 200
        # A step with no lower bound is 300 million rays for the asking, so a
        # grid finer than the whole route will compute is a caller error too.
        # THAT ONE IS NOT ASKED FOR HERE: with a populated sky and the budget
        # check missing, this request does not come back, and a test that hangs
        # instead of failing is worse than no test. It lives in
        # ``test_a_grid_too_fine_is_refused_before_a_single_ray_is_cast``
        # below, where the dome function is stubbed so the refusal is provable
        # in milliseconds.
        # ...and one ray per square degree, the finest anyone has a use for,
        # is still allowed.
        assert c.get("/api/cloudmap/dome?alt_step=1&az_step=2"
                     ).status_code == 200

    # ...and with the feature on but nothing fetched yet, 200 with a reason.
    _store, app, _svc = _make(tmp_path, monkeypatch, populate=False)
    with TestClient(app) as c:
        # A caller error is still a caller error with no sky to draw. Checked
        # after the no-data branch, these would answer 200 "nothing fetched
        # yet" and the bug would keep until the night the fetch worked.
        assert c.get("/api/cloudmap/dome?alt_step=0").status_code == 400
        assert c.get("/api/cloudmap/dome?alt_step=0.01&az_step=0.01"
                     ).status_code == 400
        assert c.get("/api/cloudmap/at?alt=0&az=180").status_code == 400
        assert c.get("/api/cloudmap/at?alt=45&az=180&ahead_s=-1"
                     ).status_code == 400
        r = c.get("/api/cloudmap/dome")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enabled"] is True
        assert body["rows"] is None
        assert body["reason"], "no rows and no reason is indistinguishable"
        r = c.get("/api/cloudmap/at?alt=45&az=180")
        assert r.status_code == 200
        assert r.json()["basis"] == "no_data"
        assert r.json()["probability"] is None
        assert r.json()["reason"]


# ================= 14b. the refusal has to be provable without the work
#
# Written after a mutation sweep. With ``_require_budget`` deleted, the
# 306-million-ray request in the test above does not go red -- it never comes
# back, and the suite hangs. Measured: two minutes with no verdict, against
# three seconds for the whole file. A guard whose only test is a request that
# hangs when it is missing is not guarded; a hung CI job looks like a slow day
# and gets restarted.

def test_a_grid_too_fine_is_refused_before_a_single_ray_is_cast(
        tmp_path, monkeypatch):
    """The budget check runs BEFORE the sky is walked, and here that is proved
    by making the walk itself the detector: stage 4's ``dome`` is replaced by a
    stand-in that fails the test if it is ever entered, so a missing guard
    fails in milliseconds instead of taking a core off a guiding rig for an
    hour and a test runner with it."""
    _store, app, _svc = _make(tmp_path, monkeypatch)

    def _never(*a, **kw):
        raise AssertionError(
            "the route began casting rays for a grid it should have refused")

    monkeypatch.setattr(service_mod, "dome", _never)
    with TestClient(app) as c:
        r = c.get("/api/cloudmap/dome?alt_step=0.01&az_step=0.01")
        assert r.status_code == 400, r.text
        assert "rays" in r.json()["detail"], r.text
        # A step of zero is stage 4's domain error and must still be stage 4's
        # sentence, not this one -- the budget check returns for a step it
        # cannot count rays for rather than dividing by it.
        r = c.get("/api/cloudmap/dome?alt_step=0")
        assert r.status_code == 400 and "rays" not in r.json()["detail"], r.text


# ============ 14d. the finest grid anyone can ask for was the one 500

def test_a_denormal_step_is_a_400_like_every_other_bad_one(
        tmp_path, monkeypatch):
    """``5e-324`` is positive, finite and inside stage 4's ``(0, 45]``.

    Its reciprocal is not: ``360.0 / 5e-324`` overflows to inf, and
    ``math.ceil(inf)`` raises OverflowError, which the route does not catch.
    So the single finest grid a caller can name came back as a server error
    while every other pathological step -- nan, inf, -1, 1e-300, 1e-150, 1e-8,
    0.001 -- was correctly a 400. No compute is performed either way, so this
    was never a denial of service; it was the route blaming itself for the
    caller's argument, and a client cannot tell "retry later" from "stop
    asking for this".
    """
    _store, app, _svc = _make(tmp_path, monkeypatch)

    def _never(*a, **kw):
        raise AssertionError(
            "the route began casting rays for a grid it should have refused")

    monkeypatch.setattr(service_mod, "dome", _never)
    with TestClient(app) as c:
        for bad in ("alt_step=5e-324&az_step=5e-324",
                    "az_step=5e-324",
                    "alt_step=5e-324",
                    "alt_step=1e-320&az_step=2"):
            r = c.get("/api/cloudmap/dome?" + bad)
            assert r.status_code == 400, (
                bad + " -> " + str(r.status_code) + " " + r.text)
            assert "rays" in r.json()["detail"], bad + ": " + r.text


# ================================ 14c. the poller is actually wired in
#
# Also from the sweep: with ``cloudmap_service.start()`` deleted from the app
# lifespan, all seventeen tests passed. Every route reads the service object
# directly and every fixture ticks it by hand, so nothing here would have
# noticed a cloud map that is switched on, answers "no cloud granule has been
# read yet" for ever, and never makes one request.

def test_the_app_lifespan_starts_and_stops_the_poller(tmp_path, monkeypatch):
    """The routes can all be right and the feature still be dead.

    Started UNCONDITIONALLY, like the weather poller: the tick no-ops unless
    the feature is on and the site is set, so this costs a disabled rig one
    coroutine and a 60 s sleep, and it is what makes a runtime toggle take
    effect without a restart. Stopped on the way out, or a reload leaves an
    orphan fetching NOAA on its own timer.
    """
    _store, app, svc = _make(tmp_path, monkeypatch)
    assert svc._task is None, "the poller was running before the app started"
    with TestClient(app) as c:
        assert c.get("/api/cloudmap").status_code == 200
        assert svc._task is not None and not svc._task.done(), (
            "the app lifespan never started the cloud-map poller")
    assert svc._task is None, (
        "the app lifespan never stopped the cloud-map poller")


# ================================================ 15. never without a session

def test_no_route_here_is_reachable_without_a_session(tmp_path, monkeypatch):
    """The dome and the pierce point are within 30 km of the rig. A viewer link
    handed to a stranger must not geolocate the observatory."""
    from astrodeck.auth import set_active_provider, reset_active_provider

    class _Anonymous:
        name = "fake"

        async def resolve(self, request):
            return None

    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: None)
    _store, app, _svc = _make(tmp_path, monkeypatch)
    set_active_provider(_Anonymous())
    try:
        with TestClient(app) as c:
            for url in ("/api/cloudmap",
                        "/api/cloudmap/dome",
                        "/api/cloudmap/at?alt=45&az=180"):
                r = c.get(url)
                assert r.status_code == 401, url + " -> " + str(r.status_code)
    finally:
        reset_active_provider()


# ===================================== 16. THE ONE ARCHITECTURAL RULE

#: Everything that can stop a night, refuse to start one, or decide a frame is
#: worth taking. Not "the engine" alone: the gate is spread across the resume
#: arm, the roof, the twilight schedule and the two dawn/sun watchdogs, and the
#: rule is about all of them.
GATE_FILES = (
    "server/astrodeck/sequence/engine.py",
    "server/astrodeck/sequence/resume_arm.py",
    "server/astrodeck/sequence/policy.py",
    "server/astrodeck/sequence/roof.py",
    "server/astrodeck/sequence/schedule.py",
    "server/astrodeck/sequence/cloudstate.py",
    "server/astrodeck/sequence/session.py",
    "server/astrodeck/sequence/instructions.py",
    "server/astrodeck/weather.py",
    "server/astrodeck/dawn_park.py",
    "server/astrodeck/sun_watch.py",
)

#: An import of anything under the cloudmap package, however it is spelled or
#: renamed, at module scope or inside a function.
_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+[\w.]*\bcloudmap\b|import\s+[\w.]*\bcloudmap\b"
    r"|from\s+[\w.]+\s+import\s+.*\bcloudmap\b)")


def test_the_cloud_model_does_not_reach_the_safety_gate():
    """The cloud model has no vote, and this is what keeps it that way.

    Two detectors, because one of them is beatable. The source grep catches the
    import; the namespace walk catches an import renamed to something that does
    not look like one, which is exactly the shape of cheat that would make this
    test pass while the rule was broken.

    If this ever needs to change it is a separate design with its own argument,
    and the argument had better be better than the one that refused two clear
    nights.
    """
    offenders = []
    for name in GATE_FILES:
        path = REPO / name
        assert path.is_file(), name + " has moved; this detector is now blind"
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            if _IMPORT_RE.match(line):
                offenders.append(name + ":" + str(lineno) + ": " + line.strip())
    assert not offenders, (
        "the cloud model reached the safety gate: " + repr(offenders))

    # The same claim asked of the loaded modules, so an alias cannot hide it.
    import importlib
    leaked = []
    for name in GATE_FILES:
        module_name = (name[len("server/"):].removesuffix(".py")
                       .replace("/", "."))
        module = importlib.import_module(module_name)
        for attr, value in vars(module).items():
            origin = getattr(value, "__module__", None) or getattr(
                value, "__name__", "")
            if str(origin).startswith("astrodeck.cloudmap"):
                leaked.append(module_name + "." + attr + " -> " + str(origin))
    assert not leaked, (
        "a gate module holds an object from the cloud model: " + repr(leaked))

    # And the detector itself has to be able to fail, or it is decoration.
    assert _IMPORT_RE.match("from ..cloudmap.service import cloudmap_service")
    assert _IMPORT_RE.match("    from astrodeck.cloudmap import service as s")
    assert _IMPORT_RE.match("import astrodeck.cloudmap.service")
    assert _IMPORT_RE.match(
        "from astrodeck.cloudmap.service import cloudmap_service as _c")
    assert not _IMPORT_RE.match("from .cloudstate import CloudState")
