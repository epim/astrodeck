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


def test_set_safety_round_trip(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_safety(SafetyConfig(close_dome_on_unsafe=True,
                                  close_dome_when_done=True))
    got = store.reload().safety
    assert got.close_dome_on_unsafe is True
    assert got.close_dome_when_done is True


def test_old_config_json_without_keys_deserializes_false():
    # A config file written before PRO-4 has neither key — pydantic fills the
    # protective defaults (both OFF).
    cfg = AppConfig(**{"version": 3, "safety": {"enabled": True,
                                                "preset": "backyard"}})
    assert cfg.safety.close_dome_on_unsafe is False
    assert cfg.safety.close_dome_when_done is False


def test_remote_preset_enables_close_on_unsafe():
    assert SAFETY_PRESETS["remote"]["close_dome_on_unsafe"] is True
    # backyard + the base default stay OFF (no surprise roof move for a
    # supervised backyard operator).
    assert "close_dome_on_unsafe" not in SAFETY_PRESETS["backyard"]
    assert SafetyConfig().close_dome_on_unsafe is False
