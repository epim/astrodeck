"""The service must never hand ``"auto"`` to anything downstream.

``auto`` is a word the CONFIG speaks. Nothing else in the tree does:
``bucket_for`` raises on it by design, ``credit`` falls back to G18's URL and
would mis-attribute the imagery, and a status payload echoing it tells the
operator the MODE when the only thing they can act on is the BIRD.

These are the seam between ``config.CloudmapConfig.platform`` and
``cloudmap.platform.resolve_platform``. The arithmetic itself is tested in
``test_cloudmap_platform``; what is tested here is that every place the service
reports or uses a platform went through the resolver, because the failure mode
is not an exception -- it is a payload that says "auto" and a credit block
naming the wrong satellite, both of which look fine.
"""
from __future__ import annotations

import pytest

import astrodeck.cloudmap.service as service_mod
from astrodeck.cloudmap.granule import CloudmapUnavailable, SiteOutsideSector
from astrodeck.config import ConfigStore

# Well clear of the 106.1 W crossover in both directions, so no test here is
# secretly about a boundary -- test_cloudmap_platform owns that.
WEST = ([SITE-LAT], -[SITE-LON])          # the rig: San Jose
EAST = (40.713, -74.006)           # New York


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = ConfigStore(path=tmp_path / "astrodeck.json")
    site = s.cfg().site
    site.latitude, site.longitude = WEST
    site.elevation_m, site.is_default = 0.0, False
    s.cfg().cloudmap.enabled = True
    monkeypatch.setattr(service_mod, "config_store", s)
    return s


def _at(store, lat_lon):
    store.cfg().site.latitude, store.cfg().site.longitude = lat_lon


def test_auto_never_reaches_the_state_or_the_credit(store):
    """The payload names a satellite, not the mode that chose it."""
    store.cfg().cloudmap.platform = "auto"
    svc = service_mod.CloudmapService()

    _at(store, WEST)
    west = svc.payload()
    assert west["platform"] == "G18", "San Jose is GOES-West"
    assert "goes18" in west["credit"]["url"], west["credit"]

    _at(store, EAST)
    east = svc.payload()
    assert east["platform"] == "G19", "New York is GOES-East"
    assert "goes19" in east["credit"]["url"], (
        "the credit block follows the RESOLVED bird -- `credit` falls back to "
        "G18 for an unknown name, so a literal 'auto' would have attributed "
        "GOES-East imagery to GOES-West and nothing would have complained")


def test_auto_never_reaches_the_state_when_the_model_is_off(store):
    """The disabled arm builds its own CloudmapState and was a separate line.

    Two constructors, two chances to forget: the enabled one was fixed and the
    disabled one kept echoing the config for a whole edit.
    """
    store.cfg().cloudmap.platform = "auto"
    store.cfg().cloudmap.enabled = False
    _at(store, EAST)
    assert service_mod.CloudmapService().payload()["platform"] == "G19"


def test_an_explicit_choice_is_never_overruled(store):
    """An operator who pinned a bird gets it, wherever the rig is.

    The reason to pin is always local knowledge the arithmetic does not have --
    a sector edge, a satellite in eclipse, a NOAA outage on the nearer bird --
    so "the other one is closer" is not grounds to override it.
    """
    for pinned in ("G18", "G19"):
        store.cfg().cloudmap.platform = pinned
        for where in (WEST, EAST):
            _at(store, where)
            assert service_mod.CloudmapService().payload()["platform"] == pinned


def test_an_unset_site_still_names_a_real_satellite(store):
    """`is_default` means we do not know where the rig is.

    The service already refuses to poll in that state, but `payload()` is still
    served and `bucket_for` still has to be given something it recognises.
    """
    store.cfg().cloudmap.platform = "auto"
    store.cfg().site.is_default = True
    p = service_mod.CloudmapService().payload()
    assert p["platform"] in ("G18", "G19")
    assert p["platform"] != "auto"


def test_moving_the_rig_across_the_crossover_drops_the_held_grid(store):
    """A DIFFERENT SATELLITE IS A DIFFERENT GRID, and under `auto` the config
    string never changes while the satellite does.

    The service keys its "must I throw everything away" check on the platform.
    Comparing the CONFIGURED value would compare "auto" with "auto" and hold a
    GOES-West window while fetching GOES-East granules -- cells that are not
    the same cells, which stage 5 refuses to correlate and stage 4 would place
    every crossing somewhere else.
    """
    store.cfg().cloudmap.platform = "auto"
    svc = service_mod.CloudmapService()

    _at(store, WEST)
    svc._platform = service_mod._resolved_platform(store.cfg().cloudmap)
    assert svc._platform == "G18"

    _at(store, EAST)
    assert service_mod._resolved_platform(store.cfg().cloudmap) != svc._platform, (
        "the resolved bird must change when the rig crosses 106.1 W, or the "
        "clear-the-window branch never fires")


# ------------------------------------------------------- what the operator reads

def test_only_an_exception_that_says_it_is_safe_gets_echoed():
    """The filter's default is the class name and that must not soften.

    httpx puts the full request URL in its exception text, and `source.py`
    concatenates that text into its own CloudmapUnavailable message -- so
    "echo the exceptions we author" is not a safe rule and was never the rule.
    Only a class carrying SAFE_TO_ECHO, whose message is a reviewed constant,
    is echoed.
    """
    leaky = CloudmapUnavailable(
        "listing ABI-L2-ACMC in https://noaa-goes18.s3.amazonaws.com/"
        "?list-type=2&prefix=secret failed: timeout")
    assert service_mod._safe_error(leaky) == "CloudmapUnavailable", (
        "a message built from a request URL must never reach the operator")

    assert service_mod._safe_error(ValueError("half_rows must be >= 0")) == "ValueError"
    assert service_mod._safe_error(RuntimeError("boom")) == "RuntimeError"


def test_an_out_of_sector_site_is_told_in_a_sentence():
    """"ValueError" is what this used to say, and it is true of a typo too."""
    exc = SiteOutsideSector(centre_row=-790, centre_col=157,
                            n_rows=1500, n_cols=2500)
    echoed = service_mod._safe_error(exc)
    assert echoed == SiteOutsideSector.MESSAGE
    assert "sector does not reach the site" in echoed
    assert echoed not in ("ValueError", "SiteOutsideSector")


def test_the_echoed_sentence_carries_no_coordinate():
    """A cell index is a position wearing a hat.

    The fixed-grid transform is a bijection, so a centre cell run back through
    it is a latitude and a longitude to within about 2 km. This project has
    already had a viewer geolocate the rig to 2.9 km from a derived value
    nobody had thought of as a coordinate, so the rule is checked rather than
    remembered: the echoed text carries NO DIGITS AT ALL.
    """
    exc = SiteOutsideSector(centre_row=-790, centre_col=157,
                            n_rows=1500, n_cols=2500)
    echoed = service_mod._safe_error(exc)
    offenders = [ch for ch in echoed if ch.isdigit()]
    assert not offenders, (
        "the operator-facing sentence contains digits "
        + repr(offenders) + " -- an index, a shape or a coordinate has leaked "
        "into the one string that is allowed out: " + repr(echoed))
    for needle in ("-790", "157", "1500", "2500"):
        assert needle not in echoed

    # And the indices are still there for whoever is debugging, just not in
    # the sentence.
    assert exc.centre_row == -790 and exc.centre_col == 157


# ---------------------------------------------- the upgrade path for old rigs

def test_the_payload_suggests_the_better_bird_even_when_pinned(store):
    """The auto-pick reaches NOBODY who already has a config file.

    Pydantic writes defaults into the stored JSON -- no exclude_defaults on
    ConfigStore._save -- so every install predating `auto` carries an explicit
    "platform": "G18" it never chose. Measured on the rig 2026-08-25:
    {"enabled":true,"platform":"G18","poll_minutes":10,"half_px":100}. That
    explicit value correctly wins over the new default, which means the fix is
    inert on upgrade, which means an operator east of 106.1 W keeps getting an
    empty cloud map.

    It cannot be migrated silently either: AppConfig has no schema_version, so
    a written-out default is BYTE-IDENTICAL to a deliberate operator override
    and no read-time rule can tell them apart without overwriting somebody's
    real decision.

    So the payload carries what the geometry WOULD pick, always, and the panel
    offers the switch. This is the only channel that reaches an existing rig.
    """
    store.cfg().cloudmap.platform = "G18"
    _at(store, EAST)
    p = service_mod.CloudmapService().payload()
    assert p["platform"] == "G18", "the pin is still honoured"
    assert p["suggested_platform"] == "G19", (
        "and the operator is told which bird actually sees them, or the "
        "auto-pick never reaches a single deployed rig")


def test_it_stays_quiet_when_the_pin_is_already_right(store):
    """A nudge that fires everywhere is noise, and noise gets dismissed.

    The panel keys its warning on suggested != platform, so this is the half
    that decides whether the feature is useful: on the rig's own longitude the
    two must AGREE and nothing must be said.
    """
    store.cfg().cloudmap.platform = "G18"
    _at(store, WEST)
    p = service_mod.CloudmapService().payload()
    assert p["suggested_platform"] == p["platform"] == "G18"


def test_an_unset_site_suggests_nothing_rather_than_guessing(store):
    """There is no longitude to reason from, so there is no suggestion.

    Returning a default here would put a warning in front of an operator who
    has not told us where they are, about a satellite chosen from a position
    we invented.
    """
    store.cfg().cloudmap.platform = "G18"
    store.cfg().site.is_default = True
    assert service_mod.CloudmapService().payload()["suggested_platform"] is None
