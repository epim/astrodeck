"""Stage A (#239): the rig's imaging standards live in config.

Every default here is copied from the plan model's current value, so a rig that
never opens Settings behaves byte-identically to one running the old code. That
is the property this file exists to hold: the migration is about WHERE a setting
lives, not about changing what any night does.
"""
from astrodeck.config import AppConfig
from astrodeck.sequence.models import SequencePlan


def test_every_moved_field_defaults_to_the_plan_models_value():
    """The twelve, with the defaults SequencePlan carried before this change.

    Hardcoded rather than read off SequencePlan: after A2 those fields are
    ``None`` on the model, so asking the model would be asking the thing that
    changed. These numbers are the pre-migration defaults, from git history.
    """
    cfg = AppConfig()
    assert cfg.guide.dither_pixels == 3.0
    assert cfg.guide.recover_guiding is True
    assert cfg.cooling.cool_timeout_s == 600
    assert cfg.escalation.hfr_reject_factor == 0.0
    assert cfg.safety.meridian_flip_warn_min == 15.0
    assert cfg.standards.apply_filter_offsets is True
    assert cfg.standards.refocus_on_temp_delta_c == 0.0
    assert cfg.standards.min_stars == 0
    assert cfg.standards.max_guide_rms == 0.0
    assert cfg.standards.max_eccentricity == 0.0
    assert cfg.standards.max_consecutive_rejects == 10
    assert cfg.standards.max_consecutive_rejects_night == 20


def test_standards_min_stars_is_not_the_wcs_one():
    """Two settings, one word, unrelated meanings.

    ``wcs_stamp.min_stars`` is the floor below which a saved light is not
    WCS-stamped. ``standards.min_stars`` is the floor below which a frame is
    REJECTED. The next person to notice both will want to consolidate them;
    this is here to say no.
    """
    cfg = AppConfig()
    cfg.standards.min_stars = 40
    assert cfg.wcs_stamp.min_stars == 0
    assert cfg.standards.min_stars == 40


def test_a_config_without_the_new_block_still_loads():
    """Purely additive: an existing config JSON has no `standards` key at all."""
    raw = AppConfig().model_dump()
    raw.pop("standards")
    raw["guide"].pop("dither_pixels")
    raw["cooling"].pop("cool_timeout_s")
    restored = AppConfig.model_validate(raw)
    assert restored.standards.max_consecutive_rejects == 10
    assert restored.guide.dither_pixels == 3.0
    assert restored.cooling.cool_timeout_s == 600


def test_a_fresh_plan_inherits_rather_than_deciding():
    """This guarded A1's ordering - "A1 only adds config, the plan model is
    untouched" - and A2 then changed the model on purpose, so it now states the
    post-A2 truth instead: a plan nobody edited says nothing about these, and
    `None` is what makes "the rig decides" expressible at all."""
    plan = SequencePlan()
    assert plan.dither_pixels is None
    assert plan.max_consecutive_rejects is None
    assert plan.min_stars is None
