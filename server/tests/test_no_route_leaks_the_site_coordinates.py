"""No API response may carry the site's coordinates (#19).

The control this issue asks for, and the reason it asks for it: a key-name
redaction filter cannot withhold a value that a route computes and names itself.
`/api/weather` named `site_lat` and `site_lon` outright; a previous audit
geolocated the rig to about 3 km from derived fields alone. Both were found by a
human reading a response, which is not a control.

TWO SCANS, and the first is the one that runs everywhere:

  * A SYNTHETIC site with distinctive coordinates is configured, every
    parameterless GET route is called as a VIEWER - the role the redaction layer
    exists to answer for - and every response body is searched for
    those numbers in each rendering a route might produce them in. This grades
    the actual behaviour on any machine, with no secret present.

  * When the real needles file is on the machine, the same bodies are scanned
    against it. This catches a value that is not the configured site at all - a
    hard-coded coordinate, a cached one, a default - which the synthetic scan
    by construction cannot see. It reports ROUTE NAMES ONLY and never the
    match, and it skips where the file is absent.

The synthetic half exists because a test whose only mode skips without a secret
is a control that does not run, and this file would then be evidence of a
protection nobody has.

THE ONE ALLOWLIST ENTRY is `/api/weather`, and it is a decision rather than a
hole: the radar map centres its tiles client-side, so withholding the
coordinates from a holder of `view.weather` breaks the feature for every
non-admin. That was settled in July as exception I2. The allowlist is the honest
record of it, and it is what makes this test fail the day a SECOND route starts
carrying coordinates - which is the risk #19 is actually about.

AND THE SCAN HAS TO BE ABLE TO SEE THAT ENTRY, which for a while it could not.
`from .config import config_store` binds the singleton into the importing
module, and this file patched three modules by name while thirty-odd hold their
own reference - `weather.py` among them. So the route read the REAL store, found
its default site, answered `"site_lat":null` under both synthetic sites, and
disclosed nothing to a scan built around it. The allowlist entry was then
deleted as dead weight, with a docstring recording the vacuum as a measurement.
The store is now swept across every imported astrodeck module, and
`test_the_allowlisted_route_is_actually_scanned` fails the day the one known
positive stops being visible.

MUTATIONS RUN, and what each printed:

  M1, revert the sweep to the three modules this file used to name - the state
  the tree was actually in. 2 failed: the allowlisted route "disclosed nothing
  to the scan", and the sweep case. The main control PASSES under this
  mutation, which is the whole point: the leak was invisible, not absent.

  M2, delete the allowlist entry again. 2 failed, and this time the main
  control is one of them, naming /api/weather and seven renderings of the
  coordinates. That is the failure that should have happened when the entry was
  deleted, and did not.

  M3, drop the dated ruling from the entry's reason. 1 failed: an allowlist
  whose entries do not point at a decision is a list of excuses.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (CAP_VIEW_SITE_PRECISE, principal_for_role,
                            reset_active_provider, set_active_provider)
from astrodeck.config import ConfigStore, Site

NEEDLES = Path("C:/Users/bear/.astrodeck/privacy-needles.txt")

# TWO fake sites, and the second is not a spare - it is the whole detector.
#
# A scan for "does this body contain 41.23" finds `/api/catalog/tonight`
# whatever the site is, because that route returns a right ascension, a
# declination, a magnitude, a transit altitude, a moon separation and a score
# for every object in the catalogue, and at two or three decimal places one of
# those hundreds of floats matches almost any number. Measured: configured at
# 41.2345678/-73.9876543 it "leaks" 41.23 and -73.99; at 12.3456789/-45.6789012
# it "leaks" 12.35 and 45.68; at 3.1415926/-9.8765432, 3.14 and -9.88. Every
# one a collision, none a disclosure.
#
# So a hit only counts when it TRACKS the configured value: present under site
# A and absent under site B. A coincidence does not move when the site moves.
# That is also what lets the coarse renderings stay in the scan - three decimals
# still places the rig inside about 100 m and is a real leak - instead of being
# dropped to buy a quiet result.
#
# Both are far from 0 and from each other, neither is a round number, and no
# rounding of one is a rounding of the other.
SITE_A = (41.2345678, -73.9876543)
SITE_B = (12.3456789, -45.6789012)

# See the module docstring. A second entry here is a decision somebody has to
# make and write down, which is the whole point of the list being this short.
#
# This entry was briefly DELETED and the suite stayed green, which is how the
# vacuum below was found: the scan could not see the one leak it was built
# around, so the record of the decision looked like dead weight. It is back,
# and `test_the_allowlisted_route_is_actually_scanned` now fails if it ever
# stops being load-bearing again.
ALLOWED: dict[str, str] = {
    "/api/weather": (
        "Exception I2, 2026-07-17. The radar and satellite map centres its "
        "tiles client-side on site_lat/site_lon, so withholding them from a "
        "holder of view.weather breaks the map for every non-admin. Gated on "
        "view.weather and never reaching a viewer; the owner accepted that an "
        "operator can infer the rig's region. Revoking it means reworking the "
        "map to centre server-side, which is still an open question on #19."),
}


class _FixedPrincipal:
    """Every request resolves to one identity, the RBAC suite's pattern."""

    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture(autouse=True)
def _restore_provider():
    yield
    reset_active_provider()


def _sweep_config_store(monkeypatch, store) -> list[str]:
    """Point every already-imported astrodeck module's `config_store` at
    `store`. Returns the module names patched, so a case can assert the sweep
    actually reached something rather than silently matching nothing."""
    import sys
    patched = []
    for name, mod in list(sys.modules.items()):
        if not name.startswith("astrodeck") or mod is None:
            continue
        if getattr(mod, "config_store", None) is None:
            continue
        monkeypatch.setattr(mod, "config_store", store, raising=False)
        patched.append(name)
    return patched


def _client(tmp_path, monkeypatch, lat, lon, role: str = "operator"):
    """Isolated app and ConfigStore, mirroring tests/test_rbac_enforcement.py,
    with the synthetic site saved before the app is built and a fixed principal
    installed.

    NOT an admin, and that is the whole shape of the control. An admin holds
    `view.site_precise` and is entitled to the coordinates - they are the person
    who typed them in - so scanning as one finds `/api/site` returning the site
    and calls it a leak. Measured: as an admin six routes carry them, all
    correctly. The question this file asks is what a caller WITHOUT that
    capability can read, which is what the redaction layer exists to decide and
    what the 3 km geolocation finding was about.

    `operator` rather than `viewer` because it is the WIDEST role that still
    lacks the capability - 7 capabilities against the viewer's 2 - so it reaches
    the most routes while still being a caller the answer must be withheld from.
    A viewer is 403'd on routes an operator can read, and a scan that cannot
    reach a route cannot find a leak in it.
    """
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    # EVERY module that holds its own reference, not the three this file used
    # to name. `from .config import config_store` binds the singleton into the
    # importing module, so patching `astrodeck.config` alone leaves 30-odd
    # modules still reading the REAL store - and a route that reads the site
    # through one of them sees the default site under BOTH synthetic sites,
    # produces no coordinates at all, and passes the differential by having
    # nothing to disclose.
    #
    # That is not hypothetical: it is how `/api/weather` came to sit outside
    # the allowlist. It names `site_lat`/`site_lon` outright and returned
    # `null` for both throughout this scan, so the entry recording the I2
    # exception could be deleted with the suite staying green - a control whose
    # one known positive had stopped being visible to it.
    #
    # Swept rather than listed so a module added tomorrow is covered without
    # anyone remembering this file exists.
    _sweep_config_store(monkeypatch, store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    # The catalogue is trimmed to six objects for the scan. `/api/catalog/tonight`
    # computes a night per object and takes 44 s over the full list, against 0.4 s
    # for the other eighty routes combined; whether it DISCLOSES a coordinate does
    # not depend on how many objects it ranks. Six keeps the route in the scan.
    import astrodeck.catalog.objects as objects_mod
    monkeypatch.setattr(objects_mod, "CATALOG", objects_mod.CATALOG[:6])
    store.set_site(Site(name="fixture", latitude=lat, longitude=lon,
                        elevation_m=10.0, is_default=False))
    # Again after the app is built: create_app imports route modules, and a
    # module first imported there would otherwise keep the real store.
    reset_active_provider()
    principal = principal_for_role(role)
    # Only for the roles the answer must be WITHHELD from. `admin` is used
    # deliberately as the positive control below, and it is supposed to hold it.
    assert role == "admin" or not principal.has(CAP_VIEW_SITE_PRECISE), (
        f"the {role} role now holds view.site_precise, so this file is asking "
        "a caller that is entitled to the answer and grades nothing")
    set_active_provider(_FixedPrincipal(principal))
    client = TestClient(app_module.create_app())
    _sweep_config_store(monkeypatch, store)
    return client


def _routes(client) -> list[str]:
    """Every GET route that takes no path parameter. Parameterised routes are
    not skipped because they are safe; they are skipped because inventing an id
    for each one is a different test. Recorded as a known gap."""
    out = []
    for route in client.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if path and "GET" in methods and "{" not in path:
            out.append(path)
    return sorted(set(out))


def _renderings(value: float) -> list[str]:
    """Every way a route might print one coordinate. A response that rounds to
    three decimals still places the rig inside about 100 m, so the coarse forms
    are leaks too and are searched for.

    Two decimals is the floor. One decimal is about 11 km and is also the
    precision of an ordinary weather forecast for a region, so searching for it
    would match bodies that disclose nothing."""
    forms = {repr(value), str(value)}
    for places in (2, 3, 4, 5, 6):
        forms.add(f"{value:.{places}f}")
        forms.add(f"{abs(value):.{places}f}")
    # Trailing zeros are not how JSON prints a float, so a rendering that ends
    # in one would never appear and only slows the scan.
    return [f for f in forms if not (("." in f) and f.endswith("0"))]


def _bodies(client) -> dict[str, str]:
    """Route path -> response text, for every route that answered at all. A
    non-200 is kept: a 500 traceback and a 403 detail are response bodies too,
    and both have carried values they should not before."""
    out = {}
    for path in _routes(client):
        try:
            response = client.get(path)
        except Exception:
            continue                     # a route that cannot run cannot leak
        out[path] = response.text or ""
    return out


def _differential(a: dict[str, str], b: dict[str, str],
                  forms: list[str]) -> dict[str, list[str]]:
    """Route -> the renderings present under site A and ABSENT under site B.

    Pure, and separated from the client work so the rule itself can be graded
    on fabricated bodies. Both halves matter: `f in body` finds a candidate and
    `f not in other` is what tells a disclosure from a collision, because a
    number that appears whatever the site is was never the site's."""
    out = {}
    for path, body in a.items():
        other = b.get(path, "")
        hit = [f for f in forms if f in body and f not in other]
        if hit:
            out[path] = sorted(hit)
    return out


def _tracking_hits(tmp_path, monkeypatch, role: str = "operator") -> dict[str, list[str]]:
    """The differential run against the live app for one role."""
    a = _bodies(_client(tmp_path, monkeypatch, *SITE_A, role=role))
    b = _bodies(_client(tmp_path, monkeypatch, *SITE_B, role=role))
    return _differential(a, b, _renderings(SITE_A[0]) + _renderings(SITE_A[1]))


def test_a_viewer_is_never_shown_the_coordinates(tmp_path, monkeypatch):
    """THE OWNER'S RULING, 2026-09-22, and the one line of it that is absolute:

        "it's ok for the admin to see things like the site lat/long, or even
        operator. But not viewer roles. That's the difference. As long as
        viewers cannot see the lat/long we'll be ok."

    So the operator scan below consults ALLOWED and this one does not. There is
    no allowlist here and there is no argument for adding one: a route that
    discloses the site to a viewer is a defect whatever it is for, because the
    viewer link is the one you hand to someone you are not vouching for.

    Measured today: a viewer reaches 81 routes and NONE of them carries a
    rendering of the coordinates that tracks the configured site.
    `/api/weather` answers `{"detail":"capability not held"}`, and the site
    object in `/api/status`, `/api/site` and `/api/config` is stripped to
    `is_default` and `horizon_min_deg`.

    This case is what keeps that true. It is deliberately the same scan as the
    operator one - same renderings, same differential, same route walk - so
    neither can be strengthened without the other.
    """
    leaking = _tracking_hits(tmp_path, monkeypatch, role="viewer")
    assert not leaking, (
        "a VIEWER can read the rig's location from these routes:\n"
        + "\n".join(f"  {p}  as {sorted(h)}" for p, h in sorted(leaking.items()))
        + "\n\nThere is no allowlist for this one. Strip the field, or gate "
          "the route on a capability a viewer does not hold.")


def test_a_viewer_cannot_reach_the_weather_surfaces_at_all(tmp_path,
                                                           monkeypatch):
    """The other half of the same ruling: the radar map's tiles are centred on
    the site, so the WEATHER surfaces are gated too, not just the coordinate
    fields inside them.

    Checked as capability rather than as content, because content is what the
    case above already checks and a 200 with an empty body would pass it. A
    viewer must be REFUSED here - which is also what lets the UI hide the panel
    instead of rendering it empty (issue #129).
    """
    client = _client(tmp_path, monkeypatch, *SITE_A, role="viewer")
    for path in ("/api/weather", "/api/weather/tile/radar/5/5/5.png"):
        assert client.get(path).status_code == 403, (
            f"a viewer was not refused {path}; the radar and satellite tiles "
            f"are centred on the site, so reaching them at all discloses its "
            f"region")


def test_no_route_prints_the_configured_coordinates(tmp_path, monkeypatch):
    """The control. Every parameterless GET, as an OPERATOR, against a site
    whose coordinates are known to this test.

    An operator IS allowed to see them (the owner's ruling above), so this scan
    consults ALLOWED and the viewer scan does not. What it still catches is a
    SECOND route starting to carry them, which is the drift #19 is about.

    MUTATION: remove "/api/weather" from ALLOWED. Observed: this fails naming
    that route, which is also the proof the scan can see a leak at all.
    """
    leaking = {p: h for p, h in _tracking_hits(tmp_path, monkeypatch).items()
               if p not in ALLOWED}
    assert not leaking, (
        "these routes put the site's coordinates in their response body:\n"
        + "\n".join(f"  {p}  as {sorted(h)}" for p, h in sorted(leaking.items()))
        + "\n\nStrip them, or - if the route genuinely cannot work without "
          "them - add it to ALLOWED with the decision that says so.")


def test_the_allowlisted_route_is_actually_scanned(tmp_path, monkeypatch):
    """AN ALLOWLIST ENTRY FOR A ROUTE THE SCAN CANNOT SEE IS NOT A DECISION,
    IT IS A DECORATION - and this file shipped exactly that.

    `/api/weather` names `site_lat` and `site_lon` in its body, which is the
    disclosure #19 was opened about and the I2 exception permits. But
    `weather.py` binds `config_store` at import (`from .config import
    config_store`), and this file patched only `astrodeck.config`,
    `astrodeck.hub` and `astrodeck.api.app` - so the route read the REAL store,
    found its default site, and answered `"site_lat":null` under both synthetic
    sites. No hit, nothing to allow, and the entry recording the decision was
    deleted as dead weight with the suite staying green.

    Thirty-odd other modules bind the same reference, so the vacuum was never
    specific to weather: any route reading the site through one of them was
    being scanned against a site that was not the one configured.

    So this case asserts the positive: the allowlisted route must STILL be
    disclosing, every run. The day it stops, the entry goes - and the day the
    isolation breaks again, this fails instead of the allowlist quietly
    becoming fiction.

    MUTATION: drop `_sweep_config_store` back to the three named modules.
    Observed: this fails with "/api/weather disclosed nothing", while the main
    control passes - which is the failure that was live in the tree.
    """
    found = _tracking_hits(tmp_path, monkeypatch)
    for path in ALLOWED:
        assert path in found, (
            f"{path} is on the allowlist but disclosed nothing to the scan. "
            f"Either the route was fixed - in which case remove the entry - or "
            f"the scan can no longer see it, in which case the allowlist is "
            f"recording a decision about a route nobody is checking.")


def test_the_sweep_reaches_more_than_the_modules_this_file_names(tmp_path,
                                                                 monkeypatch):
    """The sweep is the mechanism the case above depends on, so it gets its own
    assertion rather than being trusted. Three modules were patched by name
    before; the real number is an order of magnitude larger, and `weather` must
    be among them."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.weather  # noqa: F401 - ensure it is imported to be swept
    patched = _sweep_config_store(monkeypatch, store)
    assert len(patched) > 10, (
        f"the sweep matched only {len(patched)} modules, which is about the "
        f"number this file used to name by hand: {sorted(patched)}")
    assert "astrodeck.weather" in patched, sorted(patched)


def test_the_scan_can_see_a_disclosure(tmp_path, monkeypatch):
    """The guard on the guard, and this file needs it more than most: a scan
    that found nothing - a broken renderer, an empty route list, a client that
    500s on everything, a differential that cancels every hit - would pass the
    case above forever and this whole file would be a protection nobody has.

    The positive control is `/api/site` read by an ADMIN, which is a true
    disclosure by construction: an admin holds `view.site_precise` and the route
    exists to give them the site.

    It runs through `_tracking_hits`, the same function the control above uses,
    and that is deliberate. A first version computed its own renderings and
    compared them itself; emptying `forms` INSIDE `_tracking_hits` then left
    both cases green, because the control passed vacuously and this one never
    touched the broken code. Sharing the path is what closes that.

    MUTATION: `forms = []` inside `_tracking_hits`. Observed: this fails, and
    before the refactor it did not.
    """
    found = _tracking_hits(tmp_path, monkeypatch, role="admin")
    assert len(_bodies(_client(tmp_path, monkeypatch, *SITE_A, role="admin"))) > 50, (
        "almost no routes answered, so the scan covered nothing")
    assert "/api/site" in found, (
        "/api/site did not disclose the coordinates to an ADMIN, so either the "
        f"scan cannot see a disclosure or the route no longer answers: {sorted(found)}")


def test_the_differential_removes_a_collision_and_keeps_a_disclosure():
    """The rule itself, on fabricated bodies, because the live app no longer
    produces a collision to grade it with.

    It used to. `/api/catalog/tonight` returns a right ascension, declination,
    magnitude, transit altitude, moon separation and score per object, and over
    the full catalogue one of those floats matches almost any two- or
    three-decimal number: configured at 41.2345678 it "leaked" 41.23, at
    12.3456789 it "leaked" 12.35, at 3.1415926 it "leaked" 3.14. The scan trims
    the catalogue to six objects for speed, which incidentally removed the
    collision - so without this case, deleting the `f not in other` half of the
    differential breaks nothing and the protection against coarse-rendering
    false positives is graded by nothing.

    MUTATION: drop `and f not in other` from `_differential`. Observed: this
    fails on the collision route.
    """
    a = {"/collides": '{"dec_deg": 41.23, "mag": 7.9}',
         "/discloses": '{"site_lat": 41.2345678}'}
    b = {"/collides": '{"dec_deg": 41.23, "mag": 7.9}',
         "/discloses": '{"site_lat": 12.3456789}'}
    out = _differential(a, b, _renderings(41.2345678))
    assert "/collides" not in out, (
        f"a number that is the same under both sites was called a disclosure: {out}")
    assert "/discloses" in out, (
        f"a value that tracked the configured site was missed: {out}")


def test_an_admin_is_told_and_an_operator_is_not(tmp_path, monkeypatch):
    """The two halves side by side, which is the actual rule: the capability
    decides, not the route. Without this the control above is satisfied by a
    server that withholds the site from everybody, which would be a broken
    product rather than a private one.

    MUTATION: grant `view.site_precise` to the operator role. Observed: this
    fails on the `_client` assertion that the role lacks it, which is the
    earlier guard doing its job.
    """
    admin = _bodies(_client(tmp_path, monkeypatch, *SITE_A, role="admin"))
    operator = _bodies(_client(tmp_path, monkeypatch, *SITE_A, role="operator"))
    exact = [repr(SITE_A[0]), repr(SITE_A[1])]
    assert any(f in admin.get("/api/site", "") for f in exact), (
        "an admin was not given the site's coordinates by /api/site")
    assert not any(f in operator.get("/api/site", "") for f in exact), (
        "an operator was given the site's coordinates in full by /api/site")


def test_the_allowlist_is_one_entry_and_it_states_its_reason():
    """ONE entry, `/api/weather`, and every entry carries the decision that put
    it there.

    THIS CASE PREVIOUSLY ASSERTED THE LIST WAS EMPTY, and said so as a
    measurement: "an operator calling /api/weather receives no rendering of the
    coordinates that tracks the configured site ... the exception is enforced
    by capability, not by that route handing them to everyone". That was not a
    measurement of the route, it was a measurement of a broken fixture. The
    scan patched three modules' `config_store` and `weather.py` holds a fourth,
    so the route read the real store's default site and answered
    `"site_lat":null` under both synthetic sites. The route does hand the
    coordinates to every `view.weather` holder; that is exactly what I2 says it
    does, and the empty list was recording the absence of a hole that is
    present and permitted.

    So the invariant here is not "empty" - it is "short, and every entry is
    owned". A size bound rather than zero, because zero was a claim the fixture
    could fake and this cannot: a second entry means somebody widened the
    disclosure and has to write down why.
    """
    assert set(ALLOWED) == {"/api/weather"}, (
        "the set of routes allowed to disclose the rig's location has changed: "
        f"{sorted(ALLOWED)}. Each one is a decision somebody has to own, and "
        "the list is short enough that a diff to it is worth reading.")
    for path, why in ALLOWED.items():
        assert len(why) > 40, f"{path} is allowed without a stated reason"
        assert any(k in why for k in ("I2", "202")), (
            f"{path}'s reason does not point at a dated ruling: {why}")


@pytest.mark.skipif(not NEEDLES.exists(),
                    reason="the needles file is not on this machine")
def test_no_route_prints_anything_from_the_needles_file(tmp_path, monkeypatch):
    """The second scan: the REAL values, which the synthetic one cannot see,
    because a hard-coded or cached coordinate is not the configured site.

    Reports route names only. The needle itself is never put in the message,
    never printed and never compared in a way that could echo it, which is why
    this reports `len(hit)` rather than the hit.
    """
    needles = [line.strip() for line in
               NEEDLES.read_text(encoding="utf-8", errors="ignore").splitlines()
               if line.strip()]
    assert needles, "the needles file is present but empty"
    client = _client(tmp_path, monkeypatch, *SITE_A)
    leaking = {}
    for path, body in _bodies(client).items():
        hit = [n for n in needles if n in body]
        if hit and path not in ALLOWED:
            leaking[path] = len(hit)
    assert not leaking, (
        "these routes put a value from the needles file in their response "
        f"body (counts only, never the value): {sorted(leaking.items())}")
