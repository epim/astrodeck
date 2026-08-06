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


async def test_watchdog_trip_is_warn_only_and_ignores_on_unsafe(sim_hub, monkeypatch):
    """When it DOES trip, the watchdog logs and publishes a ``warn`` — it never
    pauses, parks, or aborts, whatever ``safety.on_unsafe`` says.

    This is the difference between the setting's UI copy ("Ends the run after N
    min with nothing saved.", EscalationPanel.tsx) and the code: ``_watchdog_check``
    raises nothing and returns a latch. A stalled night with a watchdog set stays
    stalled; the only thing that changes is that a line appears in the log and —
    IF a sink is configured — an alert goes out."""
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
    assert engine.paused is False, "watchdog must not pause the run"
    assert engine.state.get("state") == "running", "watchdog must not end the run"
    assert len(published) == 1, published
    assert published[0]["is_safe"] is False
    assert published[0]["action"] == "warn", \
        "on_unsafe=abort_park_warm is NOT consulted by the watchdog"
    assert any(lvl == "error" and "no frame in" in msg for lvl, msg, _ in logs), logs

    # latched: a second evaluation inside the same stall stays silent (one page
    # per stall, not one every WATCHDOG_TICK_S).
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

async def test_require_cooling_is_inert_without_a_plan_setpoint(sim_hub, temp_store):
    """GAP. ``require_cooling`` is only ever consulted inside ``_cool_and_wait``,
    and ``_cool_and_wait`` is only called when ``plan.cool_to is not None``.

    The rig has NO cooling setpoint anywhere in its config and the camera sat at
    31.2 °C, so turning ``require_cooling`` on — even with the harshest
    ``cooling_action="abort"`` — changes nothing: a plan with no ``cool_to``
    shoots warm lights all night and the run completes normally.

    "Require cooling" does not require a setpoint to exist. Delete this test
    when a required-cooling run with no setpoint refuses to start."""
    temp_store.set_safety(SafetyConfig(enabled=False))
    temp_store.set_escalation(EscalationConfig(require_cooling=True,
                                               cooling_action="abort"))
    engine = SequenceEngine(sim_hub)
    plan = light_plan()
    assert plan.cool_to is None, "precondition: the plan carries no setpoint"

    engine.start(plan)
    assert await wait_for(lambda: not engine.running, timeout=20), engine.state
    assert engine.state.get("state") == "complete"
    assert engine._frames_done == 3, "warm lights were taken, not refused"


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


def test_reconnect_resume_and_retries_are_read_by_no_production_code():
    """GAP — the #14 shape exactly. ``reconnect_resume`` and ``reconnect_retries``
    exist in the config model, round-trip through ``/api/config``, and are
    rendered by ``EscalationPanel.tsx`` (with the retries field only shown when
    the toggle is on). Nothing reads either one.

    So on this rig ``reconnect_retries: 1`` means nothing at all — not "one
    retry", not "one attempt". A dropped USB link mid-night is not retried by
    this setting under any value.

    Delete this test when something consumes them."""
    for name in ("reconnect_resume", "reconnect_retries"):
        hits = _code_name_hits(name)
        assert [f for f, _ in hits] == ["config.py"], \
            f"{name} is now read outside config.py: {hits}"


def test_hub_reconnect_role_has_no_caller():
    """The machinery the setting would drive exists and is unreachable.

    ``Hub.reconnect_role`` replays a recorded Alpaca connection and is documented
    as "consumed by reconnect_role() (escalation/reconnect_resume)" in three
    separate comments — but the only NAME-token occurrence in the whole package
    is its own ``def``. Two halves of a feature, no wire between them.

    (Also note the method is Alpaca-only: it returns False for sim/NINA/native
    roles. This rig is native for every device, so even a wired-up
    ``reconnect_resume`` would not reconnect any of its six devices.)"""
    hits = _code_name_hits("reconnect_role")
    assert len(hits) == 1, f"reconnect_role now has callers: {hits}"
    assert hits[0][0] == "hub.py"


def test_nothing_ever_publishes_the_reconnect_alert_event():
    """The third dangling end of the same feature. ``AlertDispatcher`` maps a
    ``reconnect`` bus event to a warning-level alert and lists it in
    ``_NEVER_DEDUPE`` — "the alert that matters most must always go out". No
    producer exists: nothing in the package publishes that event type.

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
    assert producers == [], f"a reconnect producer now exists: {producers}"


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

async def test_safety_gate_is_fail_OPEN_when_no_monitor_device_exists(
        sim_hub, monkeypatch):
    """GAP, and the dangerous one. ``_safety_gate`` fetches
    ``hub.devices.get("safety")`` and, when it is ``None``, skips the entire
    monitor branch — no reading, no verdict, no ``_on_unsafe``.

    ``safety.enabled: true`` + ``on_unsafe: "pause"`` therefore permits
    everything on a rig with no monitor assigned, which is precisely the rig's
    state (``/api/safety/state`` -> ``connected: false, reading: null``). The
    Settings screen shows an armed safety system; the engine has no input.

    Contrast the next test: a monitor that EXISTS and is disconnected fails
    CLOSED. Absent and disconnected are the same situation to an operator and
    opposite situations to this code.

    Delete this test when an armed-but-sourceless safety config is refused or
    surfaced."""
    engine = SequenceEngine(sim_hub)
    engine._cfg = AppConfig(safety=SafetyConfig(enabled=True, on_unsafe="pause",
                                                unsafe_consecutive=3,
                                                min_alt_deg=0.0))
    engine.plan = light_plan()
    assert engine.plan.safety_check is True, "precondition: the plan honours safety"

    sim_hub.devices.pop("safety", None)
    sim_hub._safety_reading = None
    assert sim_hub.safety is None, "precondition: no monitor, as on the rig"

    published = capture_safety_publishes(sim_hub, monkeypatch)
    await asyncio.wait_for(engine._safety_gate(context="frame"), timeout=5)

    assert published == [], "armed safety with no source raises nothing"
    assert engine.paused is False


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


def test_preflight_reports_READY_with_safety_armed_and_no_monitor(api_client):
    """GAP, at the other end of the same wire. POST ``/api/sequence/preflight``
    is the go/no-go screen, and its safety check is guarded by
    ``mon is not None and connected`` — so an armed-but-sourceless rig gets a
    clean, unqualified green.

    The route's own docstring says a go/no-go screen that omits the go/no-go
    input is worse than no screen. An absent monitor omits it just as
    completely as an ignored one.

    Delete this test when preflight says something about safety being armed with
    nothing behind it."""
    c, store = api_client
    store.set_site(Site(name="Test", latitude=45.0, longitude=-122.0,
                        is_default=False))
    cfg = store.cfg()
    assert cfg.safety.enabled is True, "precondition: safety armed (shipped default)"

    r = c.get("/api/safety/state")
    assert r.status_code == 200
    assert r.json()["connected"] is False, "precondition: no monitor, as on the rig"

    body = c.post("/api/sequence/preflight", json=_preflight_plan()).json()
    assert body["blocked"] is False
    assert [w for w in body["warnings"] if w["kind"] == "unsafe"] == [], body


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
        sim_hub, temp_store):
    """End-to-end shape of the gap: the rig's exact safety config, no monitor,
    and the plan runs to completion with every frame taken.

    A safety system that is armed with no source and silently permits everything
    is worse than one that is off, because the Settings screen reads as
    protection."""
    temp_store.set_safety(SafetyConfig(enabled=True, on_unsafe="pause",
                                       unsafe_consecutive=3, min_alt_deg=0.0))
    temp_store.set_escalation(EscalationConfig())      # all rig defaults (warn)
    sim_hub.devices.pop("safety", None)
    sim_hub._safety_reading = None
    assert sim_hub.safety is None, "precondition: no monitor, as on the rig"

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan())
    assert await wait_for(lambda: not engine.running, timeout=20), engine.state
    assert engine.state.get("state") == "complete"
    assert engine._frames_done == 3
