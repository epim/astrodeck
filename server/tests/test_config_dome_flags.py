# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""PRO-4 Task 5 — the two additive ``SafetyConfig`` dome flags.

``close_dome_on_unsafe`` defaults False (every existing rig/test
byte-identical; #657, #192, an open owner question left alone).
``close_dome_when_done`` defaults True since W7 (D-16, owner-approved
2026-09-30, #192, #561, #562): leaving the roof open through the day is
the worse of the two failures #192 found, and closing at a normal
end-of-night is not a surprise escalation the way an unsafe-trip close
is. A round-trip through ``set_safety`` persists both; an old config
JSON without the keys deserializes with the protective (False) default
for ``close_dome_on_unsafe`` and the new True default for
``close_dome_when_done``. The ``remote`` preset carries
close_dome_on_unsafe.
"""
from astrodeck.config import (
    AppConfig,
    ConfigStore,
    SAFETY_PRESETS,
    SafetyConfig,
)


def test_defaults_are_false():
    # W7 INTEGRATION RE-PIN (WP-50, D-16, owner-approved 2026-09-30):
    # close_dome_when_done now defaults True (#192, #561, #562); every
    # other default here, close_dome_on_unsafe included, is unchanged.
    # Deliberate, not a regression.
    s = SafetyConfig()
    assert s.close_dome_on_unsafe is False
    assert s.close_dome_when_done is True
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
    # A config file written before PRO-4 has neither key — pydantic fills
    # each field's current default. W7 INTEGRATION RE-PIN (WP-50, D-16,
    # owner-approved 2026-09-30): close_dome_when_done's default is now
    # True (#192, #561, #562), so an old file without the key reads True,
    # not the protective False it used to; close_dome_on_unsafe and
    # reopen_dome_when_safe keep their protective False default.
    # Deliberate, not a regression.
    cfg = AppConfig(**{"version": 3, "safety": {"enabled": True,
                                                "preset": "backyard"}})
    assert cfg.safety.close_dome_on_unsafe is False
    assert cfg.safety.close_dome_when_done is True
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
