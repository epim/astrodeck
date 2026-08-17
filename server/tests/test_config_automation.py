"""Batch 4b backend contracts: the automation config (safety/escalation/alerts/
deadman) round-trips through ConfigStore, old configs without the new keys still
load, tokens are redacted for WS/REST, and the sequence-model additions
(Schedule, plan toggles, total_lights) deserialize old plans unchanged."""
import json

import pytest

from astrodeck.config import (AlertSink, AppConfig, ConfigStore,
                              EscalationConfig, SAFETY_PRESETS, SafetyConfig,
                              redacted)
from astrodeck.config import AppConfig
from astrodeck.sequence.policy import resolve_policy
from astrodeck.sequence.models import Schedule, SequencePlan, Target


# ----------------------------------------------------- config round-trip

def test_automation_keys_save_and_reload(tmp_path):
    path = tmp_path / "astrodeck.json"
    store = ConfigStore(path=path)
    # `preset` is DERIVED from the numerics on every read (2026-08-04), so this
    # writes what "remote" IS and passes no label at all — which also pins the
    # derivation: these six values must come back named, not as "custom".
    store.set_safety(SafetyConfig(on_unsafe="abort_park_warm",
                                  unsafe_consecutive=2, resume_when_safe=False,
                                  max_pause_min=0, close_dome_on_unsafe=True,
                                  min_alt_deg=10.0, enabled=True))
    store.set_escalation(EscalationConfig(require_guiding=True,
                                          guiding_action="abort",
                                          no_progress_watchdog_s=900))
    store.set_alerts([AlertSink(id="ntfy1", kind="ntfy",
                                url="https://ntfy.sh/astro", min_level="error")])
    store.set_deadman("https://hc-ping.com/abc")

    # a fresh store reading the same file must see every appended key (restart sim)
    reborn = ConfigStore(path=path)
    cfg = reborn.cfg()
    assert cfg.safety.preset == "remote"
    assert cfg.safety.on_unsafe == "abort_park_warm"
    assert cfg.safety.min_alt_deg == pytest.approx(10.0)
    assert cfg.escalation.require_guiding is True
    assert cfg.escalation.guiding_action == "abort"
    assert cfg.escalation.no_progress_watchdog_s == 900
    assert len(cfg.alerts) == 1 and cfg.alerts[0].id == "ntfy1"
    assert cfg.deadman_url == "https://hc-ping.com/abc"
    # four mutating helpers, each bump_and_save => version 1 -> 5
    assert cfg.version == 5


def test_old_config_without_automation_keys_still_loads(tmp_path):
    """A pre-4b config file (no safety/escalation/alerts/deadman_url) must load
    and fill the new keys with defaults — _load already tolerates missing keys."""
    path = tmp_path / "astrodeck.json"
    path.write_text(json.dumps({
        "version": 3,
        "site": {"name": "Old", "latitude": 40.0, "longitude": -74.0},
        "optics": {"focal_length_mm": 600},
        "active_profile_id": None,
    }), encoding="utf-8")
    store = ConfigStore(path=path)
    cfg = store.cfg()
    assert cfg.version == 3
    assert cfg.site.name == "Old"
    # appended keys default cleanly
    assert cfg.safety.enabled is True
    assert cfg.safety.preset == "backyard"
    assert cfg.escalation.hfr_reject_action == "warn"
    assert cfg.alerts == []
    assert cfg.deadman_url == ""


def test_appconfig_with_automation_keys_persists_and_parses(tmp_path):
    path = tmp_path / "astrodeck.json"
    store = ConfigStore(path=path)
    store.set_safety(SafetyConfig(horizon=[(0.0, 20.0), (180.0, 15.0)]))
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    # the persisted file is valid and reparses into the model
    AppConfig(**on_disk)
    assert on_disk["safety"]["horizon"][0] == [0.0, 20.0]


def test_safety_presets_shape():
    assert set(SAFETY_PRESETS) == {"backyard", "remote"}
    assert SAFETY_PRESETS["backyard"]["on_unsafe"] == "pause"
    assert SAFETY_PRESETS["backyard"]["resume_when_safe"] is True
    assert SAFETY_PRESETS["remote"]["on_unsafe"] == "abort_park_warm"
    assert SAFETY_PRESETS["remote"]["resume_when_safe"] is False


# ----------------------------------------------------- redaction

def test_redacted_blanks_alert_tokens():
    cfg = AppConfig(alerts=[
        AlertSink(id="tg", kind="telegram", token="secret-bot-token",
                  chat_id="123", url=""),
        AlertSink(id="ntfy", kind="ntfy", url="https://ntfy.sh/x"),
    ])
    out = redacted(cfg)
    assert out["alerts"][0]["token"] == ""        # secret blanked
    assert out["alerts"][0]["chat_id"] == ""      # chat_id now redacted (P2-12)
    assert out["alerts"][1]["url"] == "https://ntfy.sh/x"   # no userinfo — kept
    # the source config object is NOT mutated by redaction
    assert cfg.alerts[0].token == "secret-bot-token"
    assert cfg.alerts[0].chat_id == "123"


def test_redacted_strips_url_userinfo_and_deadman(tmp_path):
    """P2-12 fail-safe redaction: url-embedded basic-auth creds are stripped,
    chat_id is blanked, and the deadman_url (which can carry a per-ping secret in
    its path/query) is fully redacted to a boolean marker — none broadcast verbatim."""
    cfg = AppConfig(
        alerts=[
            # webhook with user:pass@ creds embedded in the url
            AlertSink(id="wh", kind="webhook",
                      url="https://alice:s3cr3t@hooks.example.com/path?q=1"),
            # ntfy with a basic-auth ntfy topic
            AlertSink(id="nt", kind="ntfy",
                      url="http://user:pw@192.168.1.50:8080/topic"),
        ],
        deadman_url="https://hc-ping.com/9f8e7d6c-secret-uuid",
    )
    out = redacted(cfg)
    # userinfo stripped, host/path preserved; the secret is gone.
    assert out["alerts"][0]["url"] == "https://hooks.example.com/path?q=1"
    assert "s3cr3t" not in out["alerts"][0]["url"]
    assert out["alerts"][1]["url"] == "http://192.168.1.50:8080/topic"
    assert "user:pw" not in out["alerts"][1]["url"]
    # deadman fully redacted to a boolean marker — the secret uuid never leaves.
    assert out["deadman_url"] == ""
    assert out["deadman_configured"] is True
    assert "secret-uuid" not in str(out)
    # source cfg untouched (redaction is non-mutating / fail-safe).
    assert cfg.deadman_url == "https://hc-ping.com/9f8e7d6c-secret-uuid"
    assert cfg.alerts[0].url == "https://alice:s3cr3t@hooks.example.com/path?q=1"


def test_redacted_empty_deadman_marks_not_configured():
    cfg = AppConfig(deadman_url="")
    out = redacted(cfg)
    assert out["deadman_url"] == ""
    assert out["deadman_configured"] is False


# ----------------------------------------------------- sequence model additions

def test_old_plan_without_schedule_deserializes_unchanged():
    """A pre-4b plan (targets without `schedule`, plan without safety_check/
    meridian_flip_warn_min) must load with run-now defaults."""
    plan = SequencePlan(**{
        "name": "Old Plan",
        "targets": [{
            "name": "M31", "ra_hours": 0.71, "dec_deg": 41.2,
            "steps": [{"exposure_s": 120, "count": 10}],
        }],
    })
    assert plan.safety_check is True
    # #239 stage A: the plan no longer CARRIES this - `None` means "inherit the
    # rig's standard" - so the question "does an old plan still behave like a
    # 15-minute lead time" is now asked of the resolved policy. The intent of
    # the assertion is unchanged; the layer that answers it moved.
    assert plan.meridian_flip_warn_min is None
    assert resolve_policy(plan, AppConfig()).meridian_flip_warn_min == pytest.approx(15.0)
    t = plan.targets[0]
    assert isinstance(t.schedule, Schedule)
    assert t.schedule.start_mode == "now"
    assert t.schedule.min_altitude_deg == 0.0
    assert t.schedule.on_missed == "wait"
    # 4a fields untouched
    assert t.rotation_deg is None and t.mosaic_group is None


def test_on_missed_rejects_a_value_the_engine_cannot_honor():
    """``on_missed`` steers a real branch now, so a typo must 422 at the plan
    route instead of silently reading as "wait" (the two shipped values are the
    only two the scheduler implements)."""
    assert Schedule(on_missed="skip").on_missed == "skip"
    assert Schedule(on_missed="wait").on_missed == "wait"
    with pytest.raises(Exception):
        Schedule(on_missed="Skip")
    with pytest.raises(Exception):
        Schedule(on_missed="defer")


def test_total_lights_excludes_calibration():
    plan = SequencePlan(targets=[
        Target(name="L", ra_hours=1.0, dec_deg=20.0,
               steps=[{"exposure_s": 60, "count": 30}]),
        Target(name="darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
               steps=[{"exposure_s": 60, "count": 20}]),
    ])
    assert plan.total_frames() == 50    # all frames
    assert plan.total_lights() == 30    # calibration excluded
