"""Aperture, reducer, and the f-ratio the rig is allowed to derive from them
(D-SET-1) -- plus the config blocks that landed in the same change.

THE ONE THING THESE TESTS DEFEND is a refusal. ``Optics.reducer`` is a RECORD,
not a multiplier: ``focal_length_mm`` stays the explicit number the framing
maths, the plate-solve hint and the FITS header all read, and saving a 0.8x
reducer must not move it by a millimetre. Multiplying on save is the obvious
implementation and it is the wrong one -- it turns a label into a control the
operator does not know they are operating, and every frame afterwards is
composed against a focal length nobody typed. So the assertions below are
mostly about a number NOT changing, which is exactly the kind of test that
passes for the wrong reason if it is never sabotaged. Each one names its
sabotage in its docstring.

The second half covers the blocks carried in `config.py` for the rest of wave
S7 (focus.temp_comp, dew, planning, active_location_id): that they persist,
that an old config file predating all of them still loads, and that the two
relational rules pydantic cannot express per-field actually reject.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

import astrodeck.config as config_mod
from astrodeck.config import (AppConfig, ConfigStore, DewConfig, Optics,
                              PlanningConfig, QuickDefaults, f_ratio, fov_deg)
from astrodeck.hub import Hub
from astrodeck.profiles import Profile
from astrodeck.provenance import OPTICS_KEYS, effective_config


# --------------------------------------------------------------------- fakes

class FakeCam:
    """A connected camera: only the attributes effective_optics inspects."""

    def __init__(self, px: float = 3.76, w: int = 6248, h: int = 4176):
        self.connected = True
        self.pixel_size_um = px
        self.sensor_width = w
        self.sensor_height = h
        self.backend = "sim"
        self.hardware = False


class FakeHub:
    """A hub-shaped object carrying only what the optics readout reads.

    ``effective_optics`` is BORROWED from the real ``Hub`` rather than
    reimplemented (the idiom in test_config_provenance.py): a stubbed copy
    could agree about the numbers while disagreeing about where they came
    from, which is the entire class of bug this file is about."""

    effective_optics = Hub.effective_optics

    def __init__(self, profile: Profile | None = None, devices=None):
        self._profile = profile
        self.devices = devices or {}
        self.nina_client = None
        self.sim_rig = None
        self.mode = "sim"

    def _active_profile(self):
        return self._profile


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the process-wide ``config_store`` singleton at a fresh file.

    Redirecting the PATH rather than swapping the object is what makes every
    module that bound ``config_store`` at import time -- hub, profiles,
    provenance -- read the SAME isolated store."""
    s = config_mod.config_store
    monkeypatch.setattr(s, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(s, "_cfg", None)
    return s


def _client(monkeypatch, tmp_path):
    """Isolated app + store, following test_config_surfaces._client.

    The store must be patched into EVERY module that bound it at import time:
    ``api.app`` does ``from ..config import config_store``, so reassigning
    ``config.config_store`` alone leaves the routes writing to the real
    singleton while the assertions read the temp one."""
    from fastapi.testclient import TestClient
    import astrodeck.api.app as app_module
    import astrodeck.hub as hub_mod
    from astrodeck.auth import reset_active_provider
    s = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", s)
    monkeypatch.setattr(hub_mod, "config_store", s)
    monkeypatch.setattr(app_module, "config_store", s)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    reset_active_provider()
    return TestClient(app_module.create_app()), s


#: The rig this file measures against: 530 mm at 203.2 mm of aperture is f/2.61,
#: and 203.2 / 0.8 is a reducer combination whose product (424) is a plausible
#: focal length -- so a multiply-on-save bug produces a believable number rather
#: than an obviously broken one. That is why it is the fixture.
FOCAL_MM = 530.0
APERTURE_MM = 203.2
REDUCER = 0.8


# ------------------------------------------------------ the fields round-trip

def test_the_optics_route_persists_the_aperture_and_the_reducer(
        monkeypatch, tmp_path):
    """SABOTAGE (performed): drop the ``reducer`` field from ``Optics``. The
    PUT still answers 200 -- pydantic's default ``extra="ignore"`` swallows the
    field the client sent, which is the honest shape of this bug: the save
    LOOKS successful and the value is gone. Red with ``AttributeError:
    'Optics' object has no attribute 'reducer'``."""
    c, s = _client(monkeypatch, tmp_path)
    body = s.cfg().optics.model_dump()
    body.update(focal_length_mm=FOCAL_MM, aperture_mm=APERTURE_MM,
                reducer=REDUCER)
    r = c.put("/api/optics", json={"optics": body, "version": s.cfg().version})
    assert r.status_code == 200, r.text

    stored = s.cfg().optics
    assert stored.aperture_mm == pytest.approx(APERTURE_MM)
    assert stored.reducer == pytest.approx(REDUCER)

    echoed = c.get("/api/config")
    assert echoed.status_code == 200, echoed.text
    assert echoed.json()["optics"]["aperture_mm"] == pytest.approx(APERTURE_MM)
    assert echoed.json()["optics"]["reducer"] == pytest.approx(REDUCER)


def test_both_fields_survive_a_restart(tmp_path):
    """``Optics`` is written WHOLE through ``set_optics``, so the two new
    fields inherit persistence from the block rather than needing their own.
    This is the test that says so out loud.

    SABOTAGE (performed): have ``set_optics`` normalise the reducer away on the
    way in (``optics.model_copy(update={"reducer": 1.0})``) -- red with
    ``assert 1.0 == 0.8``."""
    path = tmp_path / "astrodeck.json"
    s = ConfigStore(path=path)
    s.set_optics(Optics(focal_length_mm=FOCAL_MM, aperture_mm=APERTURE_MM,
                        reducer=REDUCER))
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["optics"]["aperture_mm"] == pytest.approx(APERTURE_MM)
    assert on_disk["optics"]["reducer"] == pytest.approx(REDUCER)
    reborn = ConfigStore(path=path).cfg().optics
    assert reborn.aperture_mm == pytest.approx(APERTURE_MM)
    assert reborn.reducer == pytest.approx(REDUCER)


@pytest.mark.parametrize("field,value", [
    ("aperture_mm", -1.0), ("aperture_mm", 5001.0),
    ("reducer", 0.0), ("reducer", -0.8), ("reducer", 10.1),
])
def test_out_of_range_values_are_rejected_by_the_model(field, value):
    """Bounds on the model reject at the route (422) rather than reaching the
    f-ratio maths. ``reducer`` is ``gt=0``, not ``ge=0``: a zero-times reducer
    is not a reducer, and it would make every derived focal length 0.

    SABOTAGE (performed): relax the reducer bound to ``ge=0`` -- red with
    ``DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>``
    on the ``reducer=0.0`` case."""
    with pytest.raises(ValidationError):
        Optics(**{field: value})


# ------------------------------------------------------------------ the ratio

def test_the_f_ratio_is_the_focal_length_over_the_aperture():
    """530 / 203.2 = 2.6082..., published to two places.

    SABOTAGE (performed): ``round(focal_mm / aperture_mm, 1)`` -- reads 2.6,
    red with ``assert 2.6 == 2.61``."""
    assert f_ratio(FOCAL_MM, APERTURE_MM) == 2.61


def test_the_f_ratio_is_none_when_the_aperture_was_never_filled_in():
    """None, never a plausible number. There is NO camera fallback for the
    aperture and none is possible -- a camera knows nothing about the telescope
    in front of it -- so an unset aperture has to read as "we do not know". An
    f-number printed beside a frame is taken as a fact about the rig.

    SABOTAGE (performed): ``return round(focal_mm / (aperture_mm or
    focal_mm / 5), 2)`` -- the "sensible guess" version. Red with
    ``assert 5.0 is None``, and note that 5.0 is a completely believable
    f-number for this rig, which is the whole hazard."""
    assert f_ratio(FOCAL_MM, 0.0) is None
    assert f_ratio(FOCAL_MM, Optics().aperture_mm) is None
    # and the degenerate other half: no focal length either
    assert f_ratio(0.0, APERTURE_MM) is None


def test_the_reducer_does_not_enter_the_f_ratio():
    """If "use the reduced focal length" was pressed, ``focal_length_mm``
    already carries the reduction; applying it again here double-counts. If it
    was not pressed, the rig is genuinely shooting at the explicit focal
    length. Either way the reducer is not a term in this function -- which is
    why it is not a parameter of it.

    SABOTAGE (performed): fold the reducer into the divide
    (``round(focal_mm * 0.8 / aperture_mm, 2)``) -- 424 / 203.2 = 2.09, red
    with ``assert 2.09 == 2.61`` while the "reduced" number still looks
    entirely plausible."""
    assert f_ratio(FOCAL_MM, APERTURE_MM) == 2.61
    assert f_ratio(FOCAL_MM * REDUCER, APERTURE_MM) == 2.09  # a DIFFERENT rig


# ------------------------------------------ the refusal: the reducer is inert

def test_the_reducer_moves_neither_the_focal_length_nor_the_field_of_view(
        store):
    """THE REFUSAL, stated as an equality between two saves that differ ONLY in
    the reducer. A multiply anywhere in the effective path -- on save, in
    ``effective_optics``, in the FOV maths -- separates them.

    SABOTAGE (performed): in ``ConfigStore.set_optics``, apply the reducer on
    the way in::

        optics = optics.model_copy(update={
            "focal_length_mm": optics.focal_length_mm * optics.reducer})

    which is the "helpful" version this field exists to refuse. Red with
    ``assert 424.0 == 530.0``."""
    hub = FakeHub(devices={"camera": FakeCam()})

    store.set_optics(Optics(focal_length_mm=FOCAL_MM, aperture_mm=APERTURE_MM,
                            reducer=1.0))
    plain = hub.effective_optics()

    store.set_optics(Optics(focal_length_mm=FOCAL_MM, aperture_mm=APERTURE_MM,
                            reducer=REDUCER))
    reduced = hub.effective_optics()

    assert reduced["focal_length_mm"] == FOCAL_MM
    assert reduced["focal_length_mm"] == plain["focal_length_mm"]
    assert reduced["fov_w_deg"] == plain["fov_w_deg"]
    assert reduced["fov_h_deg"] == plain["fov_h_deg"]
    assert reduced["image_scale_arcsec_px"] == plain["image_scale_arcsec_px"]
    # and the FOV is the one the EXPLICIT focal length produces, so the
    # equality above cannot be two equally-wrong numbers agreeing.
    fw, _fh, _d = fov_deg(FOCAL_MM, 3.76, 6248, 4176)
    assert reduced["fov_w_deg"] == pytest.approx(round(fw, 3))
    # the stored block still remembers the reducer -- inert is not discarded
    assert store.cfg().optics.reducer == pytest.approx(REDUCER)


def test_the_reducer_does_not_move_the_solver_hint_on_the_route(
        monkeypatch, tmp_path):
    """The same refusal at the seam the plate solver reads: the FOV hint in
    ``optics_computed``. SABOTAGE: same multiply in ``set_optics``; the
    ``fov_diag_deg`` equality below breaks."""
    c, s = _client(monkeypatch, tmp_path)
    body = s.cfg().optics.model_dump()
    body.update(focal_length_mm=FOCAL_MM, pixel_size_um=3.76,
                sensor_width_px=6248, sensor_height_px=4176,
                aperture_mm=APERTURE_MM, reducer=1.0)
    r = c.put("/api/optics", json={"optics": body, "version": s.cfg().version})
    assert r.status_code == 200, r.text
    plain = r.json()["optics_computed"]

    body["reducer"] = REDUCER
    r = c.put("/api/optics", json={"optics": body, "version": s.cfg().version})
    assert r.status_code == 200, r.text
    reduced = r.json()["optics_computed"]

    assert reduced["focal_length_mm"] == plain["focal_length_mm"] == FOCAL_MM
    assert reduced["fov_diag_deg"] == plain["fov_diag_deg"]


# ------------------------------------------------------ the profile overrides

def test_a_profile_optics_block_overrides_both_new_fields(store):
    """A profile's optics block is swapped WHOLE, so a profile that only meant
    to change the focal length also imposes its own aperture and reducer. The
    provenance readout has to say so on the new keys too -- otherwise they are
    displayed from the GLOBAL block while the rig runs the profile's, which is
    the twelve-day polar-aligner bug on two fresh fields.

    Leaving ``aperture_mm``/``reducer`` off ``effective.ts``'s OPTICS_KEYS is
    caught by the cross-boundary test in
    test_displayed_layer_is_the_running_layer.py, not here.

    SABOTAGE (performed): drop ``aperture_mm`` from ``config.Optics``, which is
    what ``provenance.OPTICS_KEYS`` is derived from -- red with
    ``KeyError: 'optics.aperture_mm'``."""
    store.set_optics(Optics(focal_length_mm=FOCAL_MM, aperture_mm=APERTURE_MM,
                            reducer=1.0))
    prof = Profile(id="p1", name="reduced train",
                   optics=Optics(focal_length_mm=424.0, aperture_mm=106.0,
                                 reducer=REDUCER))
    hub = FakeHub(profile=prof, devices={"camera": FakeCam()})

    eff = effective_config(hub)
    assert eff["optics.aperture_mm"]["value"] == pytest.approx(106.0)
    assert eff["optics.aperture_mm"]["layer"] == "profile"
    assert eff["optics.reducer"]["value"] == pytest.approx(REDUCER)
    assert eff["optics.reducer"]["layer"] == "profile"
    # the global block is untouched and is reported as the losing layer
    assert store.cfg().optics.aperture_mm == pytest.approx(APERTURE_MM)

    # ...and the computed readout is the PROFILE's optics, not the global one.
    computed = hub.effective_optics()
    assert computed["focal_length_mm"] == pytest.approx(424.0)
    fw, _fh, _d = fov_deg(424.0, 3.76, 6248, 4176)
    assert computed["fov_w_deg"] == pytest.approx(round(fw, 3))


def test_effective_config_publishes_the_two_new_optics_keys(store):
    """``OPTICS_KEYS`` is derived from the model, so the new fields have to
    appear in the provenance payload without anyone listing them.

    SABOTAGE (performed): drop ``aperture_mm`` from ``config.Optics`` -- red
    with ``KeyError: 'optics.aperture_mm'`` at the layer assertion below."""
    hub = FakeHub()
    eff = effective_config(hub)
    assert "aperture_mm" in OPTICS_KEYS and "reducer" in OPTICS_KEYS
    assert "optics.aperture_mm" in eff
    assert "optics.reducer" in eff
    # a never-set aperture reports the DEFAULT layer, not "config"
    assert eff["optics.aperture_mm"]["layer"] == "default"
    assert eff["optics.aperture_mm"]["value"] == 0.0
    assert eff["optics.reducer"]["value"] == 1.0


# ------------------------------------------- what effective_optics must carry

def test_effective_optics_carries_the_aperture_the_reducer_and_the_ratio(
        store):
    """The ONE profile-aware optics readout in the whole payload. Anything that
    wants the value the rig is really using comes through here, so a field that
    rides inside the profile-swapped ``Optics`` block and never comes back out
    of this function sends every caller to re-read GLOBAL config and get the
    LOSING layer -- which is exactly what happened to
    ``guide_focal_length_mm``.

    The three lines this needs live in ``hub.effective_optics`` (task S7c).
    Until they land the assertions are skipped, and the skip names the task."""
    hub = FakeHub(devices={"camera": FakeCam()})
    store.set_optics(Optics(focal_length_mm=FOCAL_MM, aperture_mm=APERTURE_MM,
                            reducer=REDUCER))
    computed = hub.effective_optics()
    if "f_ratio" not in computed:
        pytest.skip("hub.effective_optics has not carried aperture_mm / "
                    "reducer / f_ratio yet (task S7c)")
    assert computed["aperture_mm"] == pytest.approx(APERTURE_MM)
    assert computed["reducer"] == pytest.approx(REDUCER)
    assert computed["f_ratio"] == 2.61

    store.set_optics(Optics(focal_length_mm=FOCAL_MM, aperture_mm=0.0))
    assert hub.effective_optics()["f_ratio"] is None


# ------------------------------------------------- an older config still loads

#: A config file written before ANY of this wave's fields existed: no aperture,
#: no reducer, no ``focus.temp_comp``, no ``dew``, no ``planning``, no
#: ``active_location_id``. Stamped schema 2 so no migration runs -- the point is
#: additive-with-defaults, not migration behaviour.
LEGACY_CONFIG = {
    "schema_version": 2,
    "version": 7,
    "site": {"name": "astrotown", "latitude": 37.0, "longitude": -122.0,
             "is_default": False, "horizon_min_deg": 15.0},
    "optics": {"focal_length_mm": FOCAL_MM, "pixel_size_um": 3.76,
               "sensor_width_px": 6248, "sensor_height_px": 4176,
               "auto_from_camera": False, "telescope_name": "RedCat"},
    "focus": {"approach_overshoot_steps": 200},
    "active_profile_id": "rig-1",
}


def test_a_config_written_before_these_fields_loads_with_the_defaults(tmp_path):
    """The additive contract, checked on every block this change added rather
    than only the one it is named for. A field that fails to default does not
    raise somewhere obvious -- ``_load`` catches the ValidationError, falls
    through to ``_restore_from_bak``, finds nothing, and the whole server dies
    at boot with "configuration is invalid".

    SABOTAGE (performed): make ``DewConfig.margin_full_c`` required
    (``Field(..., ge=-5, le=20)``) -- red with ``RuntimeError: configuration
    is invalid and no valid backup is available``, from a pydantic
    ``margin_full_c / Field required`` underneath it."""
    path = tmp_path / "astrodeck.json"
    path.write_text(json.dumps(LEGACY_CONFIG), encoding="utf-8")
    cfg = ConfigStore(path=path).cfg()

    assert cfg.optics.focal_length_mm == pytest.approx(FOCAL_MM)   # kept
    assert cfg.optics.telescope_name == "RedCat"                   # kept
    assert cfg.optics.aperture_mm == 0.0                           # defaulted
    assert cfg.optics.reducer == 1.0
    assert cfg.focus.approach_overshoot_steps == 200               # kept
    assert cfg.focus.temp_comp.enabled is False                    # defaulted
    assert cfg.focus.temp_comp.steps_per_c == 0.0
    assert cfg.focus.temp_comp.reference_temp_c is None
    assert cfg.dew.enabled is False
    assert cfg.dew.margin_full_c == 1.0 and cfg.dew.margin_off_c == 5.0
    assert cfg.planning.pool == []
    assert cfg.planning.quick.learned is False
    assert cfg.planning.quick.hours == 2.0
    assert cfg.active_profile_id == "rig-1"                        # kept
    assert cfg.active_location_id is None                          # defaulted


def _mutate_dew(s):
    s.set_dew(DewConfig(enabled=True, margin_full_c=2.0, margin_off_c=6.0,
                        max_power=80))


def _check_dew(cfg):
    assert cfg.dew.enabled is True and cfg.dew.max_power == 80
    assert cfg.dew.margin_full_c == 2.0 and cfg.dew.margin_off_c == 6.0


def _mutate_planning(s):
    s.set_planning(PlanningConfig(
        quick=QuickDefaults(hours=4.0, dawn=True, learned=True,
                            on={"L": True, "Ha": False},
                            exp={"L": 60.0}, dither_n=5),
        pool=["m31", "ngc7331"]))


def _check_planning(cfg):
    assert cfg.planning.quick.dawn is True and cfg.planning.quick.learned is True
    assert cfg.planning.quick.exp == {"L": 60.0}
    assert cfg.planning.quick.dither_n == 5
    assert cfg.planning.pool == ["m31", "ngc7331"]


def _mutate_focus(s):
    focus = s.cfg().focus.model_copy(deep=True)
    focus.temp_comp.enabled = True
    focus.temp_comp.steps_per_c = -12.5
    s.set_focus(focus)


def _check_focus(cfg):
    assert cfg.focus.temp_comp.enabled is True
    assert cfg.focus.temp_comp.steps_per_c == pytest.approx(-12.5)
    assert cfg.focus.approach_overshoot_steps == 200      # untouched


@pytest.mark.parametrize("mutate,check", [
    (_mutate_dew, _check_dew),
    (_mutate_planning, _check_planning),
    (_mutate_focus, _check_focus),
])
def test_each_new_block_persists_through_its_own_setter(tmp_path, mutate, check):
    """Each block gets a typed setter in the ``set_standards`` idiom, and a
    block with no writer is a feature nobody can reach -- the defect
    ``set_cloudmap``'s docstring records, where 4,357 lines sat behind a switch
    that did not exist.

    ONE SETTER PER STORE, and that is the point of the parametrisation rather
    than one test that calls all three. ``bump_and_save`` writes the WHOLE
    config, so a setter that mutates memory and never saves is invisible in a
    test that calls another setter afterwards -- the next save carries the
    first one's mutation to disk and the assertion passes on a save that never
    happened. Drafted that way, this test stayed GREEN through a ``set_dew``
    with its ``bump_and_save`` removed.

    SABOTAGE (performed): drop the ``self.bump_and_save()`` from ``set_dew``
    and return ``cfg`` -- red with ``assert False is True``."""
    path = tmp_path / "astrodeck.json"
    s = ConfigStore(path=path)
    mutate(s)
    check(ConfigStore(path=path).cfg())


# ------------------------------------------------------------ the dew ramp

def test_a_dew_ramp_that_cannot_ramp_is_rejected():
    """``margin_off_c`` at or below ``margin_full_c`` inverts the ramp: the
    heater then runs hardest when the glass is SAFEST, all night, and nothing
    on any screen would look wrong.

    SABOTAGE (performed): change the validator to ``<`` -- the equal-margins
    case at the bottom stops raising, red with ``DID NOT RAISE <class
    'pydantic_core._pydantic_core.ValidationError'>``."""
    with pytest.raises(ValidationError, match="must be above margin_full_c"):
        DewConfig(margin_full_c=5.0, margin_off_c=2.0)
    # equal is refused too: a zero-wide ramp is the single-threshold switch the
    # two numbers exist to replace, and a switch flapping around one number is
    # how a heater spends a night at 0 and 100 and never at 40.
    with pytest.raises(ValidationError, match="must be above margin_full_c"):
        DewConfig(margin_full_c=3.0, margin_off_c=3.0)


def test_a_dew_ceiling_below_its_floor_is_rejected():
    """SABOTAGE (performed): drop the second clause of ``_check_ramp`` -- red
    with ``DID NOT RAISE <class
    'pydantic_core._pydantic_core.ValidationError'>``."""
    with pytest.raises(ValidationError, match="at least min_power"):
        DewConfig(min_power=60, max_power=40)
    # equal is fine: a fixed-power heater is a legitimate setting.
    assert DewConfig(min_power=50, max_power=50).max_power == 50


# -------------------------------------------------------------- the pool

def test_the_pool_is_deduplicated_preserving_first_seen_order():
    """Order is the only thing the user actually did to this list, so the
    de-duplication must not re-sort it and must keep the FIRST occurrence.

    SABOTAGE (performed): ``cleaned = sorted(set(cleaned))`` -- red with
    ``AssertionError: assert ['m31', 'm42', 'ngc7331'] == ['ngc7331', 'm31',
    'm42']``."""
    p = PlanningConfig(pool=["ngc7331", "m31", "ngc7331", "m42", "m31"])
    assert p.pool == ["ngc7331", "m31", "m42"]
    # and whitespace is normalised before the comparison, so " m31" is not a
    # second m31 in the shortlist
    assert PlanningConfig(pool=["m31", " m31 "]).pool == ["m31"]


def test_an_oversized_pool_is_rejected_by_the_model():
    """300 entries 422s at the route rather than growing the config file.

    SABOTAGE (performed): drop ``max_length=MAX_POOL`` from the field -- red
    with ``DID NOT RAISE <class
    'pydantic_core._pydantic_core.ValidationError'>``."""
    with pytest.raises(ValidationError):
        PlanningConfig(pool=[f"t{i}" for i in range(300)])
    assert len(PlanningConfig(pool=[f"t{i}" for i in range(200)]).pool) == 200


def test_an_over_long_or_empty_target_id_is_rejected():
    """A 100-character id is a client bug, and swallowing it would put a target
    in the shortlist that no lookup can ever resolve.

    SABOTAGE (performed): ``continue`` instead of ``raise`` on the length
    check -- the over-long id is silently dropped, red with ``DID NOT RAISE
    <class 'pydantic_core._pydantic_core.ValidationError'>``."""
    with pytest.raises(ValidationError, match="at most 64 characters"):
        PlanningConfig(pool=["x" * 100])
    with pytest.raises(ValidationError, match="must not be empty"):
        PlanningConfig(pool=["m31", "   "])


def test_the_quick_defaults_refuse_a_non_positive_or_infinite_exposure():
    """NaN and the infinities survive ``float()`` and every ge/le bound
    pydantic can express, and a NaN exposure reaches the camera as a NaN.

    SABOTAGE (performed): replace ``not (seconds > 0) or seconds ==
    float("inf")`` with ``seconds <= 0`` -- NaN compares False against
    everything and inf is greater than zero, so both slip through; red with
    ``DID NOT RAISE <class
    'pydantic_core._pydantic_core.ValidationError'>``."""
    with pytest.raises(ValidationError, match="positive, finite"):
        QuickDefaults(exp={"L": 0.0})
    with pytest.raises(ValidationError, match="positive, finite"):
        QuickDefaults(exp={"L": float("nan")})
    with pytest.raises(ValidationError, match="positive, finite"):
        QuickDefaults(exp={"L": float("inf")})


def test_the_quick_maps_are_capped():
    """Not a real limit -- a wheel has eight slots -- but a bound so a
    malformed client cannot grow the config file without end.

    SABOTAGE (performed): drop the ``_MAX_QUICK_KEYS`` check -- red with
    ``DID NOT RAISE <class
    'pydantic_core._pydantic_core.ValidationError'>``."""
    with pytest.raises(ValidationError, match="at most 64"):
        QuickDefaults(on={f"slot{i}": True for i in range(65)})
    with pytest.raises(ValidationError, match="1..64 characters"):
        QuickDefaults(exp={"x" * 65: 60.0})


def test_the_planning_blocks_forbid_unknown_keys():
    """``extra="forbid"``, like ConfigPatchBody: a typo'd key that binds
    silently is a setting the operator believes they saved and did not.

    SABOTAGE (performed): remove ``model_config`` from ``PlanningConfig`` --
    red with ``DID NOT RAISE <class
    'pydantic_core._pydantic_core.ValidationError'>``."""
    with pytest.raises(ValidationError):
        PlanningConfig(poool=["m31"])
    with pytest.raises(ValidationError):
        QuickDefaults(hourss=3.0)


def test_active_location_id_is_not_inside_the_planning_block():
    """A block a settings panel replaces WHOLESALE cannot hold a pointer the
    panel has never heard of: the client echoes the block back without it and
    erases which location is applied -- the same shape as
    ``cooling.setpoint_c`` being cancelled by a panel with no field for it.

    SABOTAGE (performed): add ``active_location_id`` to ``PlanningConfig`` --
    red with ``AssertionError: assert 'active_location_id' not in
    {'active_location_id': FieldInfo(...), 'pool': ..., 'quick': ...}``."""
    assert "active_location_id" in AppConfig.model_fields
    assert "active_location_id" not in PlanningConfig.model_fields
    assert "active_location_id" not in QuickDefaults.model_fields
