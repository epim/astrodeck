"""UX review 2026-07-26, pattern S4 ("the system knows the truth and shows
something else") — the server-side wires.

Only the pieces with real logic are covered here: a resolver, a unit conversion,
two pure formatters and the persisted-log store. The status/preflight payload
additions are plumbing and are asserted through the routes that already exist.
"""
from __future__ import annotations

import time

import pytest

from astrodeck.events import EventBus, NightLogWriter, night_key
from astrodeck.guide.base import GuideStats, rms_total_arcsec
from astrodeck.naming import (capture_tokens, format_sensor_temp_token,
                              render_relative_path, validate_template)
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep as Step
from astrodeck.sequence.models import SequencePlan, Target


# ------------------------------------------------------- #11 guide-RMS units

def test_rms_arcsec_passthrough_when_guider_reports_arcsec():
    s = GuideStats(rms_total=1.2, is_arcsec=True)
    assert rms_total_arcsec(s) == pytest.approx(1.2)


def test_rms_pixels_converted_with_known_scale():
    """240 mm / 3.76 um guide scope ~ 3.23"/px: 1.5 px is ~4.8", not 1.5"."""
    s = GuideStats(rms_total=1.5, is_arcsec=False, image_scale=3.23)
    assert rms_total_arcsec(s) == pytest.approx(4.845)


def test_rms_pixels_without_scale_is_unknown_not_a_number():
    """The bug: pixels compared against an arcsec threshold. Unknown -> None, so
    the gate SKIPS rather than judging in the wrong unit."""
    assert rms_total_arcsec(GuideStats(rms_total=1.5, is_arcsec=False)) is None
    assert rms_total_arcsec(GuideStats(rms_total=1.5, is_arcsec=False,
                                       image_scale=0.0)) is None
    assert rms_total_arcsec(None) is None


# ---------------------------------------------------------- #1 filter identity

def _step(filter_name=""):
    return Step(filter=filter_name, exposure_s=5.0, count=1)


def test_effective_filter_prefers_the_wheel_over_the_plan_text():
    """The wheel's actual slot IS what the FITS FILTER card says, so it is what
    the report/bundle/CSV must say."""
    assert SequenceEngine._effective_filter(_step(""), {"filter": "L"}) == "L"
    assert SequenceEngine._effective_filter(_step("SII"), {"filter": "SII"}) == "SII"


def test_effective_filter_falls_back_to_the_step_then_to_none():
    assert SequenceEngine._effective_filter(_step("Ha"), {"filter": ""}) == "Ha"
    assert SequenceEngine._effective_filter(_step("Ha"), {}) == "Ha"
    assert SequenceEngine._effective_filter(_step(""), {}) is None


# ------------------------------------------------- #8 escalated safety action

class _Safety:
    def __init__(self, close: bool, reopen: bool):
        self.close_dome_on_unsafe = close
        self.reopen_dome_when_safe = reopen


class _Cfg:
    def __init__(self, close=False, reopen=False):
        self.safety = _Safety(close, reopen)


@pytest.mark.parametrize("configured", ["pause", "warn", "park", "abort_park_warm"])
def test_closeable_roof_escalates_every_action(configured):
    """The reported action must be the action TAKEN. With a closeable roof and
    reopen off, EVERY on_unsafe value ends the run with a park-and-close — the
    report used to file that as 'pause'."""
    assert SequenceEngine._escalated_action(
        configured, closing=True, cfg=_Cfg(close=True)) == "abort_park_close"


def test_closeable_roof_with_reopen_reports_the_wait_cycle():
    assert SequenceEngine._escalated_action(
        "pause", closing=True, cfg=_Cfg(close=True, reopen=True)) == "close_roof_wait"


def test_no_dome_keeps_the_configured_action():
    for act in ("pause", "warn", "park"):
        assert SequenceEngine._escalated_action(act, closing=False,
                                                cfg=_Cfg()) == act


# --------------------------------------------------------- #39 integration_s

def test_light_seconds_excludes_calibration_targets():
    plan = SequencePlan(name="p", targets=[
        Target(name="M13", ra_hours=16.7, dec_deg=36.4, steps=[Step(filter="Ha", exposure_s=300.0, count=60)]),
        Target(name="flats", ra_hours=0.0, dec_deg=0.0, calibration=True,
               steps=[Step(frame_type="Flat", exposure_s=120.0, count=10)]),
    ])
    assert plan.total_seconds() == pytest.approx(60 * 300 + 10 * 120)
    assert plan.light_seconds() == pytest.approx(60 * 300)   # 5h 0m, not 5h 20m


def test_light_seconds_excludes_a_non_light_step_inside_a_light_target():
    plan = SequencePlan(name="p", targets=[
        Target(name="M13", ra_hours=16.7, dec_deg=36.4, steps=[Step(exposure_s=300.0, count=10),
                                  Step(frame_type="Dark", exposure_s=300.0, count=5)]),
    ])
    assert plan.light_seconds() == pytest.approx(3000)


# ---------------------------------------------------- #48 $$SENSORTEMP$$ token

@pytest.mark.parametrize("temp,expect", [
    (-10.0, "-10C"), (-10.2, "-10C"), (-9.6, "-10C"), (0.4, "0C"), (20, "20C"),
    (None, ""), (float("nan"), ""), (float("inf"), ""),
])
def test_sensor_temp_token_formatting(temp, expect):
    assert format_sensor_temp_token(temp) == expect


def test_dark_library_is_distinguishable_by_filename():
    """The finding: 300s g100 -10C and 600s g0 -20C both landed as
    ``Dark_..._0001.fits``."""
    tmpl = "$$FRAMETYPE$$_$$EXPOSURE$$_$$GAIN$$_$$SENSORTEMP$$_$$FRAMENR$$"
    validate_template(tmpl)
    a = render_relative_path(tmpl, dict(
        FRAMETYPE="Dark", FRAMENR="0001",
        **capture_tokens(gain=100, exposure_s=300.0, sensor_temp_c=-10.0)))
    b = render_relative_path(tmpl, dict(
        FRAMETYPE="Dark", FRAMENR="0001",
        **capture_tokens(gain=0, exposure_s=600.0, sensor_temp_c=-20.0)))
    assert a.as_posix() == "Dark_300_100_-10C_0001.fits"
    assert b.as_posix() == "Dark_600_0_-20C_0001.fits"
    assert a != b


def test_sensortemp_absent_leaves_old_templates_byte_identical():
    tmpl = "$$TARGET$$/$$FRAMETYPE$$_$$FRAMENR$$"
    fields = dict(TARGET="M42", FRAMETYPE="Light", FRAMENR="0001",
                  **capture_tokens())
    assert render_relative_path(tmpl, fields).as_posix() == "M42/Light_0001.fits"


# ----------------------------------------------------- #9 persisted event log

def test_log_events_persist_to_a_per_night_file(tmp_path, monkeypatch):
    import astrodeck.hub as hubmod
    monkeypatch.setattr(hubmod, "CAPTURE_DIR", tmp_path)
    bus = EventBus(history=2)                    # tiny ring: disk must outlive it
    for i in range(6):
        bus.log("info", f"line {i}", "sequence")
    assert len(bus.log_history) == 2              # the ring forgot four lines
    rows = bus.night_log.read(night_key())
    assert [r["data"]["message"] for r in rows] == [f"line {i}" for i in range(6)]
    assert bus.night_log.nights()[0]["night"] == night_key()


def test_persisted_log_filters_by_level_and_exports_readable_text(tmp_path,
                                                                  monkeypatch):
    import astrodeck.hub as hubmod
    monkeypatch.setattr(hubmod, "CAPTURE_DIR", tmp_path)
    bus = EventBus()
    bus.log("info", "simulator rig connected", "hub")
    bus.log("error", "UNSAFE: rain detected", "safety")
    errs = bus.night_log.read(night_key(), level="error")
    assert [r["data"]["message"] for r in errs] == ["UNSAFE: rain detected"]
    text = bus.night_log.export_text(night_key())
    assert "[error · safety] UNSAFE: rain detected" in text
    assert text.endswith("\n")


def test_night_rollover_uses_noon_not_midnight():
    """Same rollover as $$NIGHT$$, so a 02:00 log line files under the evening's
    date next to that night's frames."""
    evening = time.mktime((2026, 7, 26, 22, 0, 0, 0, 0, -1))
    after_midnight = time.mktime((2026, 7, 27, 2, 0, 0, 0, 0, -1))
    next_evening = time.mktime((2026, 7, 27, 22, 0, 0, 0, 0, -1))
    assert night_key(evening) == night_key(after_midnight) == "2026-07-26"
    assert night_key(next_evening) == "2026-07-27"


def test_log_persistence_never_raises_into_publish(tmp_path, monkeypatch):
    """A disk problem disables persistence; it must not break the capture path."""
    import astrodeck.hub as hubmod
    monkeypatch.setattr(hubmod, "CAPTURE_DIR", tmp_path)
    bus = EventBus()

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(NightLogWriter, "path_for", staticmethod(_boom))
    bus.log("info", "still fine", "hub")          # must not raise
    assert bus.night_log.failed is True
    assert bus.log_history[-1]["data"]["message"] == "still fine"


def test_old_nights_are_pruned(tmp_path, monkeypatch):
    import astrodeck.hub as hubmod
    monkeypatch.setattr(hubmod, "CAPTURE_DIR", tmp_path)
    d = tmp_path / "logs"
    d.mkdir()
    for day in range(1, 6):
        (d / f"2026-07-0{day}.jsonl").write_text("{}\n", encoding="utf-8")
    bus = EventBus()
    bus.night_log.keep_nights = 3
    bus.log("info", "tonight", "hub")
    kept = sorted(p.stem for p in d.glob("*.jsonl"))
    assert len(kept) == 3
    assert night_key() in kept


# ------------------------------------------- #33 one safety event per verdict

@pytest.fixture()
def _safety_hub(monkeypatch):
    """A Hub with a cached unsafe reading and a captured bus."""
    from astrodeck.devices.base import SafetyReading
    from astrodeck.hub import Hub
    import astrodeck.hub as hubmod

    published: list[dict] = []
    monkeypatch.setattr(hubmod.bus, "publish",
                        lambda t, **d: published.append({"type": t, **d}))
    h = Hub()
    h._safety_reading = SafetyReading(
        is_safe=False, reason="rain detected", source="Sim Safety Monitor",
        detail={"wet": 1.0}, stale=False)
    return h, published


def test_second_producer_does_not_re_announce_the_same_verdict(_safety_hub):
    """The poller announces the edge; the engine's debounced _on_unsafe reports
    the SAME reason moments later. One trip, one sticky UNSAFE toast."""
    h, published = _safety_hub
    assert h.publish_safety(h._safety_reading_dict(h._safety_reading)) is True
    assert h.publish_safety({"is_safe": False, "reason": "rain detected",
                             "action": "abort_park_close", "stale": False}) is False
    assert len(published) == 1


def test_a_genuinely_new_reason_still_publishes(_safety_hub):
    """The no-progress watchdog is the ONLY producer of its own verdict — it must
    never be swallowed by the dedup."""
    h, published = _safety_hub
    h.publish_safety(h._safety_reading_dict(h._safety_reading))
    assert h.publish_safety({"is_safe": False, "reason": "no progress in 21 min",
                             "action": "warn", "stale": False}) is True
    assert len(published) == 2


def test_partial_payload_does_not_clobber_the_sensor_readout(_safety_hub):
    """The engine used to publish {is_safe, reason, action, stale} only, wiping
    source/detail/ts out of the UI's safety state."""
    h, published = _safety_hub
    h.publish_safety({"is_safe": False, "reason": "cloud sensor: overcast",
                      "action": "abort_park_close", "stale": False})
    ev = published[-1]
    assert ev["source"] == "Sim Safety Monitor"
    assert ev["detail"] == {"wet": 1.0}
    assert ev["action"] == "abort_park_close"


def test_clearing_and_re_tripping_both_publish(_safety_hub):
    h, published = _safety_hub
    h.publish_safety(h._safety_reading_dict(h._safety_reading))
    h.publish_safety({"is_safe": True, "reason": "", "stale": False})
    h.publish_safety({"is_safe": False, "reason": "rain detected", "stale": False})
    assert [p["is_safe"] for p in published] == [False, True, False]
