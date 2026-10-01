"""Auto-resume's limits step tells a mount that did not answer from a limit
(#327; spec 5.1 item 1, 6.9; the P0-2 dead-link policy).

THE DEFECT. Since #314 the slew gate's pier-guard reads are bounded by
``MOUNT_QUERY_TIMEOUT_S``, and a read past its bound raises the engine's
dead-link SafetyAbort. ResumeArm's limits step asks the same gate
(`SequenceEngine.check_slew_limits`), caught every exception, and gave ANY
SafetyAbort the limits refusal's words: "the target is outside this rig's
configured slew limits". So a mount that stopped answering during auto-resume
sent the operator to the limits configuration while the link was what had
failed, and the timeout's own line said "... timed out after 30s — aborting"
at error level with no run in flight to abort.

WHAT IT DOES NOW. A `SlewRefused` is the limits refusal, as before. A plain
SafetyAbort from the gate, a bound's dead link, gets its own words, "the
mount did not answer the slew-limit check, so the link to it may be down",
refuses at once without trying the next candidate (a link that did not answer
for one target will not for the next, and each try stalls a whole bound), and
the tick holds on its ten-minute retry, as it does for a failed solve or goto.
The timed-out read's own sentence is the hold's operator-only
``site_detail``. The timeout's log line ends with what the no-run seam does
instead of "aborting" (``SLEW_CHECK_TIMEOUT_SUFFIX``, " — not slewing"),
through `_timeout_abort`'s caller-supplied suffix.

THE HARNESS. ``_simhub.sim_hub``: the real ``Hub`` on the simulator rig with
an isolated config at a fixture site (40 N 74 W, not anybody's rig), a real
``SequenceEngine`` and a real ``ResumeArm`` through a whole ``tick``. The slew
gate is the REAL one, `check_slew_limits`, with the pier guard armed; only the
simulator mount's two pier reads are replaced: the destination read never
answers in the hung case (the bound is set to 0.2 real seconds, the engine's
module constant, read at the call) and answers WEST in the control, as the
side read always does. The blind solve and the re-centring goto are recorded,
never made, and focus is trusted, so the ladder walks straight to the limits
step.

Each case names the mutants it was shown RED under, with the failure
observed, verbatim (long lines wrapped). Every mutant was applied in a
private scratch copy of server/ (scratchpad s4-engb-mut), never in the shared
tree (#254), and run again on the finished S4 code in scratchpad
s4-engb-resume-mut, where each failed as recorded and the control passed:

* "every SafetyAbort is a limits refusal": the limits step's dead-link branch
  deleted and its words chosen by ``isinstance(e, SafetyAbort)`` again, as
  they were.
* "check_slew_limits keeps the run's suffix": `check_slew_limits`'s
  ``timeout_suffix`` defaulting to ``TIMEOUT_ABORTING``.
* "the dead link tries the next candidate": the dead-link branch filing its
  words as the first refusal and going on to the next candidate instead of
  returning.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

import astrodeck.sequence.engine as engine_mod
from astrodeck import events
from astrodeck.config import SafetyConfig, config_store
from astrodeck.devices.base import PierSide
from astrodeck.events import night_key
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.engine import _frame_altitude
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.resume_arm import (RETRY_INTERVAL_S, ResumeArm,
                                           recentre_candidates)
from astrodeck.sequence.session import Session, session_store

#: Two targets a few degrees apart, so one clock finds both high.
A = ("t-a", "M31", 0.7123, 41.269)
B = ("t-b", "M33", 1.5640, 30.660)
T0 = 1_700_000_000.0

DEAD_LINK_WORDS = ("re-centering after restart refused: the mount did not "
                   "answer the slew-limit check, so the link to it may be "
                   "down; not slewing")
LIMITS_WORDS = ("re-centering after restart refused: the target is outside "
                "this rig's configured slew limits (altitude floor, horizon, "
                "no-go wedges, pier side or zenith keep-out); not slewing yet")


def _target(tid: str, name: str, ra: float, dec: float) -> Target:
    return Target(id=tid, name=name, ra_hours=ra, dec_deg=dec, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(id=f"s-{tid}", filter="L",
                                      exposure_s=0.05, count=3)])


def _when(site: dict, targets: list[Target]) -> float:
    """A clock reading at which every target stands 40 to 85 degrees up:
    searched, never hardcoded, so the premise holds for the fixture site."""
    for i in range(3 * 24 * 12):
        t = T0 + i * 300.0
        alts = [_frame_altitude(tgt, site, t) for tgt in targets]
        if all(a is not None and 40.0 <= a <= 85.0 for a in alts):
            return t
    raise AssertionError("no clock reading in three days has both targets high")


@pytest.fixture
def rig(sim_hub, monkeypatch):
    """The ladder's two moves recorded, focus trusted, the pier guard armed
    on the real gate, the destination read counted (``reads``, the RA of
    each) and hung while ``hang`` is set, every log line captured
    (``lines``), and ``engine.start`` recorded instead of run."""
    from astrodeck.devices import fingerprint as _fp
    monkeypatch.setattr(_fp, "verdict",
                        lambda **kw: _fp.Verdict(focus_trusted=True))
    gotos: list = []

    async def goto(*args, **kwargs):
        gotos.append((args, kwargs))
        return {"centered": True, "error_arcmin": 0.2, "attempts": 1,
                "rotation": None}

    async def solve(*args, **kwargs):
        return {}

    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    monkeypatch.setattr(sim_hub, "solve_and_sync", solve)
    tel = sim_hub.devices["telescope"]
    assert tel.reports_destination_pier_side, "premise: the guard can read"
    state = SimpleNamespace(hang=True)
    reads: list[float] = []

    async def destination_pier_side(ra_hours, dec_deg):
        reads.append(round(float(ra_hours), 4))
        if state.hang:
            await asyncio.Event().wait()
        return PierSide.WEST

    async def pier_side():
        return PierSide.WEST

    monkeypatch.setattr(tel, "destination_pier_side", destination_pier_side)
    monkeypatch.setattr(tel, "pier_side", pier_side)
    monkeypatch.setattr(engine_mod, "MOUNT_QUERY_TIMEOUT_S", 0.2)
    lines: list[tuple[str, str]] = []
    monkeypatch.setattr(events.bus, "log",
                        lambda level, message, source="hub", **kw:
                        lines.append((level, message)))
    engine = SequenceEngine(sim_hub)
    started: list = []
    monkeypatch.setattr(engine, "start",
                        lambda *a, **kw: started.append((a, kw)))
    targets = [_target(*A), _target(*B)]
    t = _when(sim_hub.site, targets)
    session = Session(id="s-link", name="link", status="dormant",
                      plan=SequencePlan(name="link", guide=False,
                                        dither_every=0, autofocus_every=0,
                                        meridian_flip=False, targets=targets),
                      auto_resume=True)
    session_store.save(session)
    arm = ResumeArm(engine, sim_hub, clock=lambda: t)
    monkeypatch.setattr(arm, "_window_open", lambda s, now: True)
    # The premise the cases read their reads against: both targets are what
    # the run would shoot, A first.
    got = [c.id for c in recentre_candidates(
        session, night_key(t), arm._walk(session, t), site=sim_hub.site,
        twilight_deg=config_store.cfg().safety.twilight_deg, now=t)]
    assert got == ["t-a", "t-b"], got
    return SimpleNamespace(arm=arm, t=t, gotos=gotos, reads=reads,
                           state=state, lines=lines, started=started,
                           ra={tid: round(ra, 4) for tid, _n, ra, _d in (A, B)})


async def test_a_pier_read_past_its_bound_names_the_link(rig):
    """The destination read never answers. The hold names the link, not the
    slew limits; the retry is the ten-minute one; the second candidate is
    not tried, so the ladder spent one bound, not two; nothing moved and
    nothing started; the timed-out read is the operator's detail; and the
    log says what timed out without saying "aborting", since no run was in
    flight to abort.

    MUTANT "every SafetyAbort is a limits refusal": RED (observed):
        AssertionError: re-centering after restart refused: the target is
        outside this rig's configured slew limits (altitude floor, horizon,
        no-go wedges, pier side or zenith keep-out); not slewing yet
        assert 're-centering...t slewing yet' == 're-centering...; not
        slewing'
          - re-centering after restart refused: the mount did not answer the
        slew-limit check, so the link to it may be down; not slewing
          + re-centering after restart refused: the target is outside this
        rig's configured slew limits (altitude floor, horizon, no-go wedges,
        pier side or zenith keep-out); not slewing yet
    MUTANT "check_slew_limits keeps the run's suffix": RED (observed; the
    captured stream printed the em dash before "aborting" as a replacement
    character, shown here as [dash]):
        AssertionError: [('error', "the mount's destination pier side timed
        out after 0s [dash] aborting")]
        assert not [('error', "the mount's destination pier side timed out
        after 0s [dash] aborting")]
    MUTANT "the dead link tries the next candidate": RED (observed):
        AssertionError: [0.7123, 1.564]
        assert [0.7123, 1.564] == [0.7123]
          Left contains one more item: 1.564
    """
    await rig.arm.tick()

    hold = rig.arm.hold
    assert hold is not None, rig.lines
    assert hold["reason"] == DEAD_LINK_WORDS, hold["reason"]
    assert "configured slew limits" not in hold["reason"]
    assert hold["site_detail"] == (
        "the mount's destination pier side timed out after 0s"), hold
    assert rig.arm._retry_at == rig.t + RETRY_INTERVAL_S
    assert rig.reads == [rig.ra["t-a"]], rig.reads
    assert rig.gotos == [] and rig.started == []
    aborting = [(lv, m) for lv, m in rig.lines if "aborting" in m]
    assert not aborting, aborting
    timed_out = [m for _lv, m in rig.lines if "timed out" in m]
    assert timed_out == [
        "the mount's destination pier side timed out after 0s — not "
        "slewing"], timed_out
    held = [m for _lv, m in rig.lines if m.startswith("auto-resume held:")]
    assert held == [f"auto-resume held: {DEAD_LINK_WORDS} — retrying in "
                    f"{int(RETRY_INTERVAL_S / 60)} min"], held


async def test_a_real_floor_refusal_keeps_its_limits_words(rig):
    """CONTROL: the destination read answers, and the mount's altitude floor
    is set above anything either target reaches (89 degrees, so the real
    gate refuses at any clock, whatever the wall clock it reads). The gate
    raises its `SlewRefused` for each candidate in turn: the hold keeps the
    limits refusal's words and its numbers stay on ``site_detail``, nothing
    here is a dead link, and nothing timed out."""
    rig.state.hang = False
    safety = config_store.cfg().safety.model_copy(
        update={"enabled": False, "enforce_pier_limits": True,
                "min_alt_deg": 89.0})
    config_store.set_safety(safety)

    await rig.arm.tick()

    hold = rig.arm.hold
    assert hold is not None, rig.lines
    assert hold["reason"] == LIMITS_WORDS, hold["reason"]
    assert "below safety floor" in hold["site_detail"], hold
    assert rig.reads == [rig.ra["t-a"], rig.ra["t-b"]], rig.reads
    assert rig.arm._retry_at == rig.t + RETRY_INTERVAL_S
    assert not [m for _lv, m in rig.lines if "timed out" in m], rig.lines
    assert rig.gotos == [] and rig.started == []


@pytest.fixture(autouse=True)
def _pier_guard_armed(sim_hub):
    """The pier guard on, no altitude floor and no safety monitor to ask:
    the hung case's only refusal can be the dead link."""
    config_store.set_safety(SafetyConfig(enabled=False,
                                         enforce_pier_limits=True,
                                         solar_avoidance=False))
