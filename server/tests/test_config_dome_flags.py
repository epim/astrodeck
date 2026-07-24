"""PRO-4 Task 5 — the two additive ``SafetyConfig`` dome flags.

Both default False (every existing rig/test byte-identical); a round-trip through
``set_safety`` persists them; an old config JSON without the keys deserializes
with the protective defaults. The ``remote`` preset carries close_dome_on_unsafe.
"""
from astrodeck.config import (
    AppConfig,
    ConfigStore,
    SAFETY_PRESETS,
    SafetyConfig,
)


def test_defaults_are_false():
    s = SafetyConfig()
    assert s.close_dome_on_unsafe is False
    assert s.close_dome_when_done is False
    assert s.reopen_dome_when_safe is False       # PRO-4 D3 opt-in, OFF by default


def test_set_safety_round_trip(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_safety(SafetyConfig(close_dome_on_unsafe=True,
                                  close_dome_when_done=True,
                                  reopen_dome_when_safe=True))
    got = store.reload().safety
    assert got.close_dome_on_unsafe is True
    assert got.close_dome_when_done is True
    assert got.reopen_dome_when_safe is True


def test_old_config_json_without_keys_deserializes_false():
    # A config file written before PRO-4 has neither key — pydantic fills the
    # protective defaults (both OFF). PRO-4 D3's reopen flag is likewise additive.
    cfg = AppConfig(**{"version": 3, "safety": {"enabled": True,
                                                "preset": "backyard"}})
    assert cfg.safety.close_dome_on_unsafe is False
    assert cfg.safety.close_dome_when_done is False
    assert cfg.safety.reopen_dome_when_safe is False


def test_remote_preset_enables_close_on_unsafe():
    assert SAFETY_PRESETS["remote"]["close_dome_on_unsafe"] is True
    # backyard + the base default stay OFF (no surprise roof move for a
    # supervised backyard operator).
    assert "close_dome_on_unsafe" not in SAFETY_PRESETS["backyard"]
    assert SafetyConfig().close_dome_on_unsafe is False


def test_reopen_is_not_a_preset_flag():
    # PRO-4 D3: reopen is an ADVANCED opt-in — deliberately absent from every
    # preset (no surprise roof cycling driven by picking a preset). Only an
    # explicit user toggle sets it.
    for name, preset in SAFETY_PRESETS.items():
        assert "reopen_dome_when_safe" not in preset, name
