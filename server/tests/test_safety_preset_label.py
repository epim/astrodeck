"""Audit finding #25 -- ``SafetyConfig.preset`` was stored but never applied by
anything, so the label on screen could say "backyard" while the numerics said
something else entirely (``SAFETY_PRESETS`` was dead code).

Fix direction taken: option (b) from the finding -- DERIVE the label on every
read/construction (``SafetyConfig._derive_preset_label``, config.py) rather
than patching the numerics on write. That direction was required specifically
because ``SAFETY_PRESETS["backyard"]`` equals the field defaults exactly, so a
write-time patch would be a silent no-op for an untouched config and silently
DESTRUCTIVE for anyone who hand-tuned ``on_unsafe``/``max_pause_min`` while
leaving the label at "backyard". These tests pin both halves: the label must
never lie about the numerics, AND the numerics must never be touched by the
fix, no matter what the stored label claims.
"""
from astrodeck.config import AppConfig, ConfigStore, SAFETY_PRESETS, SafetyConfig


def test_default_config_reads_as_backyard():
    # SAFETY_PRESETS["backyard"] equals the field defaults exactly (by design,
    # see config.py) -- an untouched config must derive to "backyard", not
    # "custom", on construction.
    assert SafetyConfig().preset == "backyard"


def test_numerics_matching_no_preset_derive_to_custom_even_if_labelled_backyard():
    # The lying case the finding names directly: label says "backyard" but the
    # numerics were hand-tuned to values no preset defines.
    s = SafetyConfig(preset="backyard", on_unsafe="park", max_pause_min=45,
                     unsafe_consecutive=5, resume_when_safe=False)
    assert s.preset == "custom"
    # and the derivation must NOT have touched the numerics themselves
    assert s.on_unsafe == "park"
    assert s.max_pause_min == 45
    assert s.unsafe_consecutive == 5
    assert s.resume_when_safe is False


def test_numerics_matching_remote_exactly_derive_to_remote_even_if_mislabelled():
    remote = SAFETY_PRESETS["remote"]
    s = SafetyConfig(preset="backyard", **remote)  # wrong label on construction
    assert s.preset == "remote"
    # values still exactly what was passed in -- confirms this is a label fix,
    # not a numerics fix, in both directions
    for field, want in remote.items():
        assert getattr(s, field) == want


def test_numerics_matching_backyard_exactly_derive_to_backyard_even_if_labelled_custom():
    backyard = SAFETY_PRESETS["backyard"]
    s = SafetyConfig(preset="custom", **backyard)
    assert s.preset == "backyard"


def test_stale_disk_label_self_corrects_on_load():
    # Simulates an old/foreign config.json where "preset" was hand-edited (or
    # left over from a build that never validated it) and no longer matches
    # the numerics next to it. AppConfig(**raw) is exactly the disk-load path.
    cfg = AppConfig(**{
        "version": 3,
        "safety": {"preset": "remote", "on_unsafe": "pause",
                   "unsafe_consecutive": 3, "resume_when_safe": True,
                   "resume_safe_consecutive": 3, "max_pause_min": 120},
    })
    assert cfg.safety.preset == "backyard"


def test_hand_tuned_numerics_with_backyard_label_survive_round_trip_unchanged(tmp_path):
    """The regression that would hurt most: a write-time patch-on-preset would
    silently overwrite these numerics with SAFETY_PRESETS["backyard"] because
    the stored label says "backyard". It must not. The numerics are what
    decides whether the roof closes over a running sequence."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    tuned = SafetyConfig(preset="backyard", on_unsafe="park",
                         unsafe_consecutive=7, resume_when_safe=False,
                         max_pause_min=45)
    # construction itself already relabels this to "custom" -- confirm that,
    # then persist and reload to prove the numerics ride through untouched.
    assert tuned.preset == "custom"
    store.set_safety(tuned)
    got = store.reload().safety
    assert got.on_unsafe == "park"
    assert got.unsafe_consecutive == 7
    assert got.resume_when_safe is False
    assert got.max_pause_min == 45
    # and the label reads honestly, not "backyard"
    assert got.preset == "custom"


def test_set_safety_round_trip_preserves_a_genuine_remote_preset(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_safety(SafetyConfig(**SAFETY_PRESETS["remote"]))
    got = store.reload().safety
    assert got.preset == "remote"
    for field, want in SAFETY_PRESETS["remote"].items():
        assert getattr(got, field) == want
