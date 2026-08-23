"""Characterisation tests for the guards that are supposed to protect an
UNATTENDED night (audit, 2026-08-06).

The rig's live config on 2026-08-06 was::

    escalation.no_progress_watchdog_s   0        <- no watchdog at all
    escalation.require_cooling          false    cooling_action  "warn"
    escalation.require_guiding          false    guiding_action  "warn"
    escalation.af_failure_action        "warn"
    escalation.reconnect_resume         false    reconnect_retries 1
    alerts                              []       deadman_url ""
    safety.enabled true, on_unsafe "pause", unsafe_consecutive 3
    GET /api/safety/state -> {"connected": false, "reading": null, "stale": false}

This file does NOT assert that the guards are good. It asserts what they
ACTUALLY do today, so that the difference between "configurable" and
"implemented" is written down and cannot regress unnoticed. Several tests here
document a GAP; each of those says so in its docstring and names what would
have to change for it to be deleted. That is deliberate: the project's dominant
defect class is a setting that is stored, echoed by the API, rendered in the UI,
and consumed by nothing (audit findings #14 ``on_missed``, #22, #25
``SAFETY_PRESETS``), and the only cheap detector for it is a test that pins the
consumption — or its absence — at the seam.

Nothing here touches hardware: sim rig, temp ConfigStore, mocked HTTP.
"""
from __future__ import annotations

import asyncio
import io
import pathlib
import time
import tokenize

import httpx
import pytest

import astrodeck
import astrodeck.config as config_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.alerting import AlertDispatcher, AlertEvent
from astrodeck.config import (AlertSink, AppConfig, ConfigStore, EscalationConfig,
                              SafetyConfig, Site)
from astrodeck.events import EventBus
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.engine import SafetyAbort


# --------------------------------------------------------------------- fixtures
#
# Same shape as test_engine_safety.py — a ConfigStore in tmp wired onto every
# module that resolved ``config_store`` at import time, and a sim rig.

@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    # invented site (never the real observing site) so altaz/floor maths are real
    store.set_site(Site(name="Test", latitude=45.0, longitude=-122.0,
                        is_default=False))
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(engine_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(engine_mod, "SAFETY_PAUSE_POLL_S", 0.05)
    monkeypatch.setattr(engine_mod, "SAFETY_SEED_WAIT_S", 0.2)
    return store


@pytest.fixture
async def sim_hub(temp_store, monkeypatch):
    # Disarm the sun cone at the hub-method level (same reason as
    # test_engine_safety.py): the FIXED M42 targets below would otherwise be
    # sun-blocked on some dates and the test would measure the wrong guard.
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def light_plan(**overrides) -> SequencePlan:
    defaults = dict(
        name="guards-audit",
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="L", exposure_s=0.05, gain=100, count=3)],
        )],
        guide=False, dither_every=0, autofocus_every=0, meridian_flip=False,
    )
    return SequencePlan(**(defaults | overrides))


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def capture_logs(monkeypatch) -> list[tuple[str, str, str]]:
    """Collect ``(level, message, source)`` from the engine's bus.log calls."""
    seen: list[tuple[str, str, str]] = []
    real = engine_mod.bus.log

    def fake(level, message, source="", **kw):
        seen.append((level, str(message), source))
        return real(level, message, source, **kw)
    monkeypatch.setattr(engine_mod.bus, "log", fake)
    return seen


def capture_safety_publishes(hub: Hub, monkeypatch) -> list[dict]:
    """Collect every ``hub.publish_safety`` payload the engine emits."""
    seen: list[dict] = []
    monkeypatch.setattr(hub, "publish_safety",
                        lambda payload: (seen.append(dict(payload)), True)[1])
    return seen


# ============================================================================
# 1. no_progress_watchdog_s
# ============================================================================

async def test_watchdog_is_not_started_at_the_rigs_zero_setting(sim_hub):
    """0 (the rig's value, and the shipped default) starts NO task at all.

    So the watchdog is not merely quiet on this rig — it does not exist. Nothing
    is measuring elapsed time between frames, and nothing ever will until the
    number is changed."""
    assert EscalationConfig().no_progress_watchdog_s == 0, \
        "precondition: the shipped default is the rig's value"

    engine = SequenceEngine(sim_hub)
    engine._cfg = AppConfig(escalation=EscalationConfig(no_progress_watchdog_s=0))
    engine._start_watchdog()
    assert engine._watchdog_task is None

    # …and a non-zero value DOES create the task, so the assertion above is
    # about the setting, not about a broken _start_watchdog.
    engine._cfg = AppConfig(escalation=EscalationConfig(no_progress_watchdog_s=900))
    engine._start_watchdog()
    assert engine._watchdog_task is not None
    engine._stop_watchdog()
    assert engine._watchdog_task is None


async def test_watchdog_trip_hands_the_stall_to_the_run_task(sim_hub, monkeypatch):
    """WAS A GAP, closed 2026-08-06. The watchdog logged, published a
    ``warn``-shaped edge, and stopped there whatever ``safety.on_unsafe`` said —
    against the setting's own UI copy ("Ends the run after N min with nothing
    saved.", EscalationPanel.tsx). A stalled unattended night stayed stalled and
    tracking until dawn.

    It still must not raise HERE: ``_watchdog_check`` runs in a background task,
    and an exception raised there reaches nobody — SafetyAbort has to arrive in
    ``_run``'s except chain to get the shielded park/warm wind-down. So the trip
    is handed to the run task as a flag, and the published action is now the
    action that will actually be taken."""
    engine = SequenceEngine(sim_hub)
    engine._cfg = AppConfig(
        escalation=EscalationConfig(no_progress_watchdog_s=600),
        safety=SafetyConfig(enabled=True, on_unsafe="abort_park_warm"))
    engine.state["state"] = "running"
    engine._progress_expected = True
    engine._last_frame_at = time.time() - 3600.0     # an hour with no frame

    published = capture_safety_publishes(sim_hub, monkeypatch)
    logs = capture_logs(monkeypatch)

    warned = engine._watchdog_check(600.0, False)    # must not raise

    assert warned is True
    assert engine.state.get("state") == "running", "the background task ends nothing"
    assert len(published) == 1, published
    assert published[0]["is_safe"] is False
    assert published[0]["action"] == "abort_park_warm", \
        "the published action must be the one that will be taken, not 'warn'"
    assert engine._watchdog_tripped and "no progress" in engine._watchdog_tripped


async def test_the_run_task_acts_on_a_watchdog_trip_per_on_unsafe(sim_hub, monkeypatch):
    """The other half: the run task picks the flag up at its next frame boundary
    and drives ``on_unsafe``. This is what makes the UI copy true.

    A stall is not a weather verdict, so it is honoured even on a rig whose
    safety monitor is disarmed — the case this rig is in."""
    engine = SequenceEngine(sim_hub)
    engine._cfg = AppConfig(
        escalation=EscalationConfig(no_progress_watchdog_s=600),
        safety=SafetyConfig(enabled=False, on_unsafe="abort_park_warm"))
    engine.plan = light_plan(safety_check=False)
    engine._watchdog_tripped = "no progress in 60 min"

    with pytest.raises(SafetyAbort, match="no progress in 60 min"):
        await asyncio.wait_for(engine._safety_gate(context="frame"), timeout=5)
    assert engine._watchdog_tripped is None, "consumed, so it fires once"


async def test_a_watchdog_trip_is_consumed_exactly_once(sim_hub, monkeypatch):
    """With the gentler ``warn`` action the run carries on — and must not
    re-raise the same stall at every subsequent frame boundary."""
    engine = SequenceEngine(sim_hub)
    engine._cfg = AppConfig(
        escalation=EscalationConfig(no_progress_watchdog_s=600),
        safety=SafetyConfig(enabled=False, on_unsafe="warn"))
    engine.plan = light_plan(safety_check=False)
    published = capture_safety_publishes(sim_hub, monkeypatch)

    engine._watchdog_tripped = "no progress in 60 min"
    for _ in range(3):
        await asyncio.wait_for(engine._safety_gate(context="frame"), timeout=5)

    stalls = [p for p in published if "no progress" in str(p.get("reason"))]
    assert len(stalls) == 1, published
    assert stalls[0]["action"] == "warn"


async def test_the_watchdog_pages_once_per_stall_not_once_per_tick(sim_hub, monkeypatch):
    """The latch: a second evaluation inside the same stall stays silent, so a
    30-minute stall is one page rather than one every WATCHDOG_TICK_S."""
    engine = SequenceEngine(sim_hub)
    engine._cfg = AppConfig(
        escalation=EscalationConfig(no_progress_watchdog_s=600),
        safety=SafetyConfig(enabled=True, on_unsafe="pause"))
    engine.state["state"] = "running"
    engine._progress_expected = True
    engine._last_frame_at = time.time() - 3600.0
    published = capture_safety_publishes(sim_hub, monkeypatch)
    logs = capture_logs(monkeypatch)

    assert engine._watchdog_check(600.0, False) is True
    assert any(lvl == "error" and "no frame in" in msg for lvl, msg, _ in logs), logs
    assert engine._watchdog_check(600.0, True) is True
    assert len(published) == 1, published


async def test_watchdog_is_blind_while_frames_are_not_expected(sim_hub, monkeypatch):
    """GAP. The watchdog only looks while ``_progress_expected`` is True, which
    the engine sets at the END of ``_setup_target`` and clears at its start.

    So the whole slew → plate-solve → centre → initial-autofocus block, the
    ``plan.cool_to`` cool-and-wait that runs before any target, and every
    inter-target scheduler wait are invisible to it. On this rig that is exactly
    where the hang lives: 12 of the 13 recorded autofocus attempts failed, five
    of them on ``EAFMove failed (MOVING, code 5)``, and an EAF that never
    answers would wedge ``_setup_target`` with the watchdog switched off.

    Delete this test when a stall in setup can page someone."""
    engine = SequenceEngine(sim_hub)
    engine._cfg = AppConfig(escalation=EscalationConfig(no_progress_watchdog_s=60))
    engine.state["state"] = "running"
    engine._progress_expected = False                # i.e. inside _setup_target
    engine._last_frame_at = time.time() - 10 * 3600  # ten hours, no frame

    published = capture_safety_publishes(sim_hub, monkeypatch)
    assert engine._watchdog_check(60.0, False) is False
    assert published == [], "a ten-hour setup stall produces no signal"

    # the same clock WITH progress expected does fire — proving the silence
    # above is the _progress_expected gate and not a dead helper.
    engine._progress_expected = True
    assert engine._watchdog_check(60.0, False) is True
    assert len(published) == 1


# ============================================================================
# 2. require_cooling / cooling_action
# ============================================================================

async def test_require_cooling_now_REFUSES_a_run_with_no_plan_setpoint(
        sim_hub, temp_store):
    """GAP CLOSED 2026-08-23, and pinned in the direction it now points.

    This test used to assert the opposite, under the file's own rule that a
    documented gap says what would have to change for it to be deleted: it read
    "``require_cooling`` is only ever consulted inside ``_cool_and_wait``, and
    ``_cool_and_wait`` is only called when ``plan.cool_to is not None`` ...
    Delete this test when a required-cooling run with no setpoint refuses to
    start." It now refuses, so the characterisation is inverted rather than
    dropped — the assertion is the same seam, pointing the other way, and the
    old behaviour cannot come back unnoticed.

    What changed: the escalation block no longer lives inside
    ``if plan.cool_to is not None``. ``_no_setpoint_must_stop_the_run`` routes a
    missing setpoint through the same ``_cooling_failed`` helper a cool-timeout
    uses, so "abort" means abort whether the frames would be warm because the
    cooler could not keep up or because nothing ever asked it to.
    """
    temp_store.set_safety(SafetyConfig(enabled=False))
    temp_store.set_escalation(EscalationConfig(require_cooling=True,
                                               cooling_action="abort"))
    engine = SequenceEngine(sim_hub)
    plan = light_plan()
    assert plan.cool_to is None, "precondition: the plan carries no setpoint"

    engine.start(plan)
    assert await wait_for(lambda: not engine.running, timeout=20), engine.state
    assert engine.state.get("state") == "aborted", engine.state
    assert engine._frames_done == 0, "warm lights were taken, not refused"


# ============================================================================
# 3. require_guiding / guiding_action
# ============================================================================

async def test_require_guiding_is_inert_when_the_plan_does_not_ask_to_guide(sim_hub):
    """GAP. The whole escalation block sits under ``if self.plan.guide:``.

    A plan saved with the guide box unticked runs unguided with
    ``require_guiding=True`` + ``guiding_action="abort"`` and never says a word.
    The setting reads like a rig-level policy ("this rig must guide") and is in
    fact a per-plan assertion ("if this plan asked to guide, hold it to it").

    Delete this test when require_guiding refuses an unguided plan."""
    engine = SequenceEngine(sim_hub)
    engine._cfg = AppConfig(escalation=EscalationConfig(require_guiding=True,
                                                        guiding_action="abort"))
    engine.plan = light_plan(guide=False)
    assert engine.plan.guide is False, "precondition: the plan does not ask to guide"
    sim_hub.guider = None                       # and there is no guider either

    await engine._setup_target(0, engine.plan.targets[0])   # must not raise
    assert engine._progress_expected is True, "setup ran to completion"


@pytest.mark.parametrize("require", [
    pytest.param(True, id="require_guiding_on_with_action_warn"),
    pytest.param(False, id="require_guiding_off"),
])
async def test_guiding_action_warn_is_indistinguishable_from_the_guard_being_off(
        sim_hub, monkeypatch, require):
    """``warn`` is not a third behaviour — it is the guard doing nothing.

    Both legs produce the SAME log line and the same outcome (target runs
    unguided). Only ``abort`` and ``skip`` branch. Worth pinning because the
    Settings UI presents warn/abort/skip as three peers, and the rig is on
    ``warn`` for cooling, guiding AND autofocus — i.e. three guards that are
    off while reading as configured."""
    logs = capture_logs(monkeypatch)
    engine = SequenceEngine(sim_hub)
    engine._cfg = AppConfig(escalation=EscalationConfig(
        require_guiding=require, guiding_action="warn"))
    engine.plan = light_plan(guide=True)
    sim_hub.guider = None

    await engine._setup_target(0, engine.plan.targets[0])   # must not raise

    unguided = [m for lvl, m, _ in logs if "continuing UNGUIDED" in m]
    assert len(unguided) == 1, logs
    assert "no guider is connected" in unguided[0]
    assert not any(lvl == "error" for lvl, _, _ in logs), logs


# ============================================================================
# 4. reconnect_resume / reconnect_retries
# ============================================================================

def _code_name_hits(name: str) -> list[tuple[str, int]]:
    """Every NAME-token occurrence of ``name`` in the shipped package.

    Tokenising (rather than grepping) is what makes this a real detector: it
    drops comments and docstrings, and this codebase's comments mention dead
    settings by name in several places — a grep would report them as usage."""
    root = pathlib.Path(astrodeck.__file__).parent
    hits: list[tuple[str, int]] = []
    files = sorted(root.rglob("*.py"))
    assert len(files) > 50, f"precondition: package scan found only {len(files)} files"
    for p in files:
        try:
            src = p.read_text(encoding="utf-8")
            toks = tokenize.generate_tokens(io.StringIO(src).readline)
            for t in toks:
                if t.type == tokenize.NAME and t.string == name:
                    hits.append((p.relative_to(root).as_posix(), t.start[0]))
        except (OSError, UnicodeDecodeError, tokenize.TokenError, SyntaxError):
            continue
    return hits


def test_reconnect_resume_and_retries_are_read_by_production_code():
    """WAS THE #14 SHAPE EXACTLY, closed 2026-08-06. Both settings existed in the
    config model, round-tripped through ``/api/config``, and were rendered by
    ``EscalationPanel.tsx`` as a working toggle — and nothing read either one, so
    ``reconnect_retries: 1`` meant nothing at all, not even "one attempt".

    Kept as a POSITIVE detector rather than deleted: this is the exact shape the
    project keeps regrowing, and asserting that a consumer exists outside
    config.py is what stops it regrowing here. Tokenised, not grepped, so a
    comment mentioning the name by way of apology would not satisfy it."""
    for name in ("reconnect_resume", "reconnect_retries"):
        consumers = {f for f, _ in _code_name_hits(name) if f != "config.py"}
        assert consumers, f"{name} is read by nothing again"
        assert "sequence/engine.py" in consumers, consumers


def test_hub_reconnect_role_is_reachable():
    """WAS unreachable. ``Hub.reconnect_role`` was documented in three separate
    comments as the consumer of ``escalation/reconnect_resume``, and the only
    NAME-token occurrence of it in the whole package was its own ``def``: two
    halves of a feature with no wire between them.

    It was also Alpaca-only, returning False for every native role — so even a
    caller would have reconnected none of this rig's six devices. Both ends are
    fixed now; this pins the wire."""
    files = {f for f, _ in _code_name_hits("reconnect_role")}
    assert "hub.py" in files
    assert files - {"hub.py"}, "reconnect_role has no caller again"


def test_the_reconnect_alert_event_has_a_producer():
    """The third dangling end of the same feature, now joined up.
    ``AlertDispatcher`` maps a ``reconnect`` bus event to a warning-level alert
    and lists it in ``_NEVER_DEDUPE`` — "the alert that matters most must always
    go out" — and for a long time nothing published that event, so the most
    important alert could never fire.

    (Distinct from ``ResumeArm``, which is real and shipped: that resumes a
    SESSION after a process restart, gated by ``Session.auto_resume``. Nothing
    resumes after a DEVICE drop, which is what ``reconnect_resume`` names.)

    Textual rather than tokenised because the event type is a string literal;
    the two spellings below are the only ones ``bus.publish`` accepts."""
    root = pathlib.Path(astrodeck.__file__).parent
    files = sorted(root.rglob("*.py"))
    assert len(files) > 50, f"precondition: package scan found only {len(files)} files"
    producers = [
        p.relative_to(root).as_posix() for p in files
        if any(tok in p.read_text(encoding="utf-8", errors="ignore")
               for tok in ('publish("reconnect"', "publish('reconnect'"))
    ]
    assert producers, "the reconnect alert has a consumer and no producer again"
    assert "sequence/engine.py" in producers, producers


# ============================================================================
# 5. alerts == [] and deadman_url == ""
# ============================================================================

def _dispatcher(cfg: AppConfig, handler):
    bus = EventBus()
    disp = AlertDispatcher(bus, lambda: cfg)
    disp._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return disp, bus


async def test_empty_alert_list_swallows_a_dying_run_in_silence():
    """The rig's ``alerts: []``. A run_end/error alert is not queued, not
    retried, not logged as undeliverable — ``_sinks_for`` returns [] and
    ``_dispatch`` falls off the end.

    That is the answer to "is there any path that tells the owner a night died":
    there is exactly one (the alert sinks), and it is empty. Nothing anywhere
    warns that it is empty, either — grep for a 'no alert sinks configured'
    surface finds nothing in server or UI."""
    sent = []

    def handler(req):
        sent.append(str(req.url))
        return httpx.Response(200)

    cfg = AppConfig()                       # alerts=[] and deadman_url="" — the rig
    assert cfg.alerts == [] and cfg.deadman_url == "", "precondition: the rig's config"
    disp, _bus = _dispatcher(cfg, handler)
    try:
        await disp._dispatch(AlertEvent("run_end", "error", "Run aborted: unsafe"))
        await disp._dispatch(AlertEvent("safety", "error", "UNSAFE: rain"))
        assert sent == []
        assert disp.undelivered_count == 0, \
            "nothing is even queued — the loss is not recoverable later"
        assert disp.health()["deadman"]["configured"] is False
    finally:
        await disp._client.aclose()


async def test_one_configured_sink_does_receive_the_dying_run():
    """Control for the test above: the pipe works, the rig just has nothing
    plugged into it. Without this, the silence above could be a broken harness."""
    sent = []

    def handler(req):
        sent.append(str(req.url))
        return httpx.Response(200)

    cfg = AppConfig(alerts=[AlertSink(id="w", kind="webhook",
                                      url="https://example.invalid/hook",
                                      events=["run_end"], min_level="warning")])
    disp, _bus = _dispatcher(cfg, handler)
    try:
        await disp._dispatch(AlertEvent("run_end", "error", "Run aborted: unsafe"))
        assert sent == ["https://example.invalid/hook"]
    finally:
        await disp._client.aclose()


async def test_empty_deadman_url_pings_nothing_and_says_nothing():
    """``deadman_url: ""`` returns before any I/O and before the one-shot
    "your deadman is doing nothing" warning — that warning only fires for a
    url that is SET and unusable. An unset deadman is indistinguishable from a
    healthy one from inside the process; the whole point of a dead-man's switch
    is that something OUTSIDE notices, and nothing outside has been told to."""
    logged = []

    def handler(req):                      # pragma: no cover - must never run
        raise AssertionError(f"unexpected request to {req.url}")

    cfg = AppConfig()
    disp, bus = _dispatcher(cfg, handler)
    bus.log = lambda level, message, source="", **kw: logged.append(
        (level, str(message), source))
    try:
        await disp.deadman_ping()
        assert logged == []
        health = disp.health()["deadman"]
        assert health["configured"] is False
        # "healthy" is False only because nothing is configured — a set-and-working
        # url and a set-and-broken one are what this flag is for.
        assert health["healthy"] is False
    finally:
        await disp._client.aclose()


async def test_configured_deadman_url_is_actually_pinged():
    """Control: the ping path works when a url exists."""
    seen = []

    def handler(req):
        seen.append((req.method, str(req.url)))
        return httpx.Response(200)

    cfg = AppConfig(deadman_url="https://hc.example.invalid/ping/abc")
    disp, _bus = _dispatcher(cfg, handler)
    try:
        await disp.deadman_ping()
        assert seen == [("GET", "https://hc.example.invalid/ping/abc")]
        assert disp.health()["deadman"]["configured"] is True
    finally:
        await disp._client.aclose()


# ============================================================================
# 6. safety.enabled with no monitor
# ============================================================================

async def test_armed_safety_with_no_monitor_says_so_once_per_run(
        sim_hub, monkeypatch):
    """WAS THE DANGEROUS GAP, closed 2026-08-06. ``_safety_gate`` hung its whole
    monitor branch off ``mon is not None``, so an absent monitor meant no
    reading, no verdict, no ``_on_unsafe`` — ``safety.enabled: true`` +
    ``on_unsafe: "pause"`` silently permitted everything, which is exactly this
    rig's config. A monitor that EXISTS and is disconnected fails CLOSED (next
    test). Absent and disconnected are the same situation to an operator and
    were opposite situations to this code.

    The default stays permissive on purpose — the shipped default arms safety on
    a rig with no monitor, so failing closed would refuse to image out of the
    box — but it is now SAID rather than silently permitted. Once per run: it is
    a configuration fact, not an event, and repeating it every frame boundary
    would bury the night's real warnings."""
    engine = SequenceEngine(sim_hub)
    engine._cfg = AppConfig(safety=SafetyConfig(enabled=True, on_unsafe="pause",
                                                unsafe_consecutive=3,
                                                min_alt_deg=0.0))
    engine.plan = light_plan()
    assert engine.plan.safety_check is True, "precondition: the plan honours safety"

    sim_hub.devices.pop("safety", None)
    sim_hub._safety_reading = None
    assert sim_hub.safety is None, "precondition: no monitor, as on the rig"

    logged = capture_logs(monkeypatch)
    published = capture_safety_publishes(sim_hub, monkeypatch)
    for _ in range(3):
        await asyncio.wait_for(engine._safety_gate(context="frame"), timeout=5)

    said = [m for lvl, m, src in logged
            if src == "safety" and "no monitor is assigned" in m]
    assert len(said) == 1, f"said {len(said)} times across 3 frames: {logged}"
    assert "watching the weather" in said[0]
    # still permissive by default — this is a report, not an enforcement
    assert published == []
    assert engine.paused is False


async def test_require_safety_monitor_makes_an_absent_monitor_fail_closed(
        sim_hub, monkeypatch):
    """The opt-in an unattended night wants: with
    ``escalation.require_safety_monitor`` on, an ABSENT monitor drives
    ``on_unsafe`` exactly like a disconnected one, which is what an operator
    reading "safety: enabled, on unsafe: abort" already believes happens."""
    engine = SequenceEngine(sim_hub)
    engine._cfg = AppConfig(
        safety=SafetyConfig(enabled=True, on_unsafe="abort_park_warm",
                            unsafe_consecutive=1, min_alt_deg=0.0),
        escalation=EscalationConfig(require_safety_monitor=True))
    engine.plan = light_plan()

    sim_hub.devices.pop("safety", None)
    sim_hub._safety_reading = None

    published = capture_safety_publishes(sim_hub, monkeypatch)
    with pytest.raises(SafetyAbort, match="no safety monitor is assigned"):
        await asyncio.wait_for(engine._safety_gate(context="frame"), timeout=5)
    assert published and published[0]["is_safe"] is False
    assert published[0]["stale"] is True


async def test_safety_gate_fails_CLOSED_when_the_monitor_is_present_but_down(
        sim_hub, monkeypatch):
    """The other half of the contrast: a registered monitor whose ``connected``
    is False is treated as UNSAFE (``stale=True``) and drives ``on_unsafe``.

    With ``abort_park_warm`` that surfaces as a SafetyAbort. This is the
    behaviour an operator would assume applies to the absent case too."""
    engine = SequenceEngine(sim_hub)
    engine._cfg = AppConfig(safety=SafetyConfig(enabled=True,
                                                on_unsafe="abort_park_warm",
                                                unsafe_consecutive=1,
                                                min_alt_deg=0.0))
    engine.plan = light_plan()
    mon = sim_hub.devices.get("safety")
    assert mon is not None, "precondition: the sim rig has a safety monitor"
    monkeypatch.setattr(mon, "connected", False)
    sim_hub._safety_reading = None

    published = capture_safety_publishes(sim_hub, monkeypatch)
    with pytest.raises(SafetyAbort, match="safety monitor disconnected"):
        await asyncio.wait_for(engine._safety_gate(context="frame"), timeout=5)
    assert published and published[0]["is_safe"] is False
    assert published[0]["stale"] is True


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """Isolated app (same shape as test_automation_api.py) with NO rig connected
    — the pre-flight tests below are about a rig that has no safety device."""
    from fastapi.testclient import TestClient
    import astrodeck.api.app as app_module

    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    app = app_module.create_app()
    with TestClient(app) as c:
        try:
            yield c, store
        finally:
            try:
                c.post("/api/disconnect")
            except Exception:
                pass


def _preflight_plan() -> dict:
    """A high, always-up target with its filter named, so the ONLY warning the
    route can produce is the safety one under test."""
    return {"name": "preflight", "safety_check": True,
            "targets": [{"name": "Zenith", "ra_hours": 6.0, "dec_deg": 45.0,
                         "steps": [{"exposure_s": 60.0, "count": 5,
                                    "filter": "L"}]}]}


def test_preflight_warns_when_safety_is_armed_with_no_monitor(api_client):
    """WAS A GAP at the other end of the same wire, closed 2026-08-06. POST
    ``/api/sequence/preflight`` is the go/no-go screen and its safety check was
    guarded by ``mon is not None and connected``, so an armed-but-sourceless rig
    got a clean, unqualified green. The route's own docstring says a go/no-go
    screen that omits the go/no-go input is worse than no screen, and an absent
    monitor omits it as completely as an ignored one.

    It now warns. NON-blocking by default (same reasoning as the engine gate:
    the shipped default arms safety on a rig with no monitor), so ``blocked``
    stays False and no existing client changes behaviour — but the screen no
    longer claims a guard the run does not have."""
    c, store = api_client
    store.set_site(Site(name="Test", latitude=45.0, longitude=-122.0,
                        is_default=False))
    cfg = store.cfg()
    assert cfg.safety.enabled is True, "precondition: safety armed (shipped default)"

    r = c.get("/api/safety/state")
    assert r.status_code == 200
    assert r.json()["connected"] is False, "precondition: no monitor, as on the rig"

    body = c.post("/api/sequence/preflight", json=_preflight_plan()).json()
    src = [w for w in body["warnings"] if w["kind"] == "no_safety_source"]
    assert len(src) == 1, body
    assert "no safety monitor is assigned" in src[0]["message"]
    assert src[0]["blocking"] is False, "reported, not imposed, by default"
    assert body["blocked"] is False
    assert body["ok"] is False, "a clean green is the thing being fixed"


def test_preflight_DOES_block_when_a_monitor_reports_unsafe(api_client):
    """Control for the test above: with a connected monitor reading unsafe the
    same plan is blocked. The green verdict above is about the missing device,
    not about a preflight route that never blocks."""
    c, store = api_client
    store.set_site(Site(name="Test", latitude=45.0, longitude=-122.0,
                        is_default=False))
    assert c.post("/api/connect/sim").status_code == 200
    assert c.post("/api/safety/simulate",
                  json={"unsafe": True, "reason": "rain detected"}).status_code == 200
    # /api/safety/simulate flips the DEVICE; the route reads the hub's cached
    # own-cadence reading, which only catches up on the next 5 s poll tick. Seed
    # the cache directly rather than sleeping through a poll interval.
    import astrodeck.api.app as app_module
    from astrodeck.devices.base import SafetyReading
    app_module.hub._safety_reading = SafetyReading(
        is_safe=False, reason="rain detected", source="Sim Safety Monitor")

    body = c.post("/api/sequence/preflight", json=_preflight_plan()).json()
    unsafe = [w for w in body["warnings"] if w["kind"] == "unsafe"]
    assert len(unsafe) == 1, body
    assert unsafe[0]["blocking"] is True
    assert body["blocked"] is True


async def test_a_whole_run_completes_with_safety_armed_and_no_monitor(
        sim_hub, temp_store, monkeypatch):
    """End-to-end: the rig's exact safety config, no monitor, and the plan still
    runs to completion with every frame taken — deliberately, because a weather
    monitor is not part of a working rig and the shipped default arms safety
    without one.

    What changed 2026-08-06 is that the run no longer does it SILENTLY. The
    completion is the same; the night's log now carries the sentence saying
    nothing watched the weather. A safety system armed with no source that
    silently permits everything is worse than one that is off, because the
    Settings screen reads as protection."""
    temp_store.set_safety(SafetyConfig(enabled=True, on_unsafe="pause",
                                       unsafe_consecutive=3, min_alt_deg=0.0))
    temp_store.set_escalation(EscalationConfig())      # all rig defaults (warn)
    sim_hub.devices.pop("safety", None)
    sim_hub._safety_reading = None
    assert sim_hub.safety is None, "precondition: no monitor, as on the rig"

    logged = capture_logs(monkeypatch)

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan())
    assert await wait_for(lambda: not engine.running, timeout=20), engine.state
    assert engine.state.get("state") == "complete"
    assert engine._frames_done == 3
    said = [m for lvl, m, src in logged
            if src == "safety" and "no monitor is assigned" in m]
    assert len(said) == 1, (
        "the run completes, but exactly once it says nothing was watching")
