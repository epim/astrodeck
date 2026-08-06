"""A live capture loop must not be allowed to fight a plate solve.

THE FIELD REPORT (rig, server log verbatim):

    00:48:01  Live View on - stacking subs
    00:48:26  mount homed
    00:50:45  goto cancelled
    00:51:36  loop capture failed: camera is busy (plate solve); capture light refused
    00:51:37  loop capture failed: camera is busy (plate solve); capture light refused
    00:51:41  solve cancelled

and the mount was left at Dec +10 deg, alt 24 deg -- not the home position the
user had asked for, not the target, nowhere anyone chose.

The mechanism: ``Hub.exposure_guard`` is non-blocking by design (whoever finds
the lock held gets a DeviceError instead of queueing). That is correct for two
one-shot callers and wrong against a live loop, which comes back every couple of
seconds forever. ``goto_and_center`` treats a failed solve as "degrade to a raw
GoTo and return", so one lost race ends the centering run mid-slew.

These tests pin the fix: every camera-owning plate-solve path calls
``Hub.yield_camera_for`` FIRST, and the loop stays stopped afterwards (and says
so) rather than silently resuming.
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck.devices.base import DeviceError
from astrodeck.events import bus

from _simhub import sim_hub  # noqa: F401 (fixture import)


#: Gain used to mark the LIVE LOOP's exposures apart from a solve's (which
#: always uses gain 200, hard-coded in solve_and_sync/rotate_to_pa). The gate in
#: ``_hang_the_loop`` keys on it so we can wedge the loop's exposure without
#: wedging the solve exposure that follows.
LOOP_MARKER_GAIN = 111


@pytest.fixture
def _hang_the_loop(sim_hub, monkeypatch):  # noqa: F811
    """Make the live loop's exposure hold the capture lock FOREVER.

    This is the whole point of the reproduction: the real rig's loop held the
    camera across the solve's attempt, and a 0-dwell sim exposure would only
    ever collide by luck. With the loop parked inside ``exposure_guard``, a
    solve that does not stop the loop first is GUARANTEED to be refused -- so
    these tests fail loudly on a regression instead of flaking.

    Cancellation still works (the loop is parked on an awaitable, and
    ``exposure_guard``'s ``async with`` releases the lock on the way out), which
    is exactly what ``yield_camera_for`` relies on.
    """
    cam = sim_hub.devices["camera"]
    real_expose = cam.expose

    async def gated_expose(seconds, gain, offset, binning=1, **kw):
        if gain == LOOP_MARKER_GAIN:
            await asyncio.Event().wait()      # never returns; only cancel frees it
        return await real_expose(seconds, gain, offset, binning, **kw)

    monkeypatch.setattr(cam, "expose", gated_expose)
    return cam


async def _start_wedged_loop(hub) -> None:
    """Start the live loop and wait until it actually owns the capture lock."""
    await hub.start_loop(0.01, LOOP_MARKER_GAIN, 10)
    for _ in range(200):
        if hub._capture_lock.locked():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the wedged live loop never took the capture lock")


@pytest.fixture
async def log_lines():
    """Returns a drain() that yields every ``bus.log`` message published so far.

    Deliberately a SUBSCRIBER rather than index arithmetic against
    ``bus.log_history``: that ring keeps only the last 200 events and is shared
    by every test in the worker process, so once it wraps, "everything after the
    mark I took" silently evaluates to nothing and the assertion passes for the
    wrong reason."""
    q = bus.subscribe()
    seen: list[str] = []

    def drain() -> list[str]:
        while not q.empty():
            ev = q.get_nowait()
            if ev.type == "log":
                seen.append(ev.data.get("message", ""))
        return seen

    yield drain
    bus.unsubscribe(q)


# ------------------------------------------------------- yield_camera_for itself

@pytest.mark.asyncio
async def test_yield_camera_for_is_a_no_op_when_nothing_is_running(sim_hub, log_lines):  # noqa: F811
    """No loop, no Live View -> nothing to stop and nothing to say. A solve on a
    quiet rig must not log a scary "Live View stopped" line."""
    assert await sim_hub.yield_camera_for("plate solve") is False
    assert not [m for m in log_lines() if "needs the camera" in m]


@pytest.mark.asyncio
async def test_yield_camera_for_stops_loop_and_says_so(sim_hub, _hang_the_loop,  # noqa: F811
                                                       log_lines):
    await _start_wedged_loop(sim_hub)

    assert await sim_hub.yield_camera_for("plate solve") is True

    assert sim_hub.looping is False
    # The lock is the thing that actually mattered: the loop's in-flight expose
    # has to have RELEASED it, not merely been asked to stop, or the solve that
    # follows still 409s -- which was the bug.
    assert sim_hub._capture_lock.locked() is False
    said = [m for m in log_lines() if "needs the camera" in m]
    assert said, f"nothing told the user why the loop died: {log_lines()}"
    assert "plate solve" in said[0]


@pytest.mark.asyncio
async def test_yield_camera_for_disarms_live_view_too(sim_hub, _hang_the_loop,  # noqa: F811
                                                      log_lines):
    """The Capture screen's Live toggle renders from ``status.live_stack_active``
    (server truth). Leaving the stacker armed while the loop that feeds it is
    gone would leave the button lit over a stack that never grows again."""
    sim_hub.start_live_stack()
    await _start_wedged_loop(sim_hub)
    assert (await sim_hub.poll_status())["live_stack_active"] is True

    await sim_hub.yield_camera_for("plate solve")

    assert sim_hub.live_stacker is None
    status = await sim_hub.poll_status()
    assert status["live_stack_active"] is False
    assert status["looping"] is False
    said = [m for m in log_lines() if "needs the camera" in m]
    assert said and said[0].startswith("Live View stopped")


# ------------------------------------------------- the Bahtinov focus aid (carry-in)
#
# The aid is the OTHER camera-owning path: POST /api/focuser/bahtinov/start arms
# the per-frame analysis and starts the capture loop if one is not already
# running. The first pass left it out of the yield, so a solve stopped the loop
# that fed it and left `bahtinov_active` true over a verdict that would never
# update again.
#
# The deliberate decision (justified at length in yield_camera_for's docstring):
# YIELD it like Live View rather than let it 409 the other job the way autofocus
# and coarse focus do. The deciding argument is that the yield-class callers are
# not all interactive -- goto_and_center is called by the sequence engine and by
# meridian_flip -- so a "protect the aid" refusal would let a focus aid somebody
# forgot to disarm abort a target or strand the tube at a flip.

@pytest.mark.asyncio
async def test_yield_camera_for_disarms_the_bahtinov_aid_too(sim_hub, _hang_the_loop,  # noqa: F811
                                                             log_lines):
    """A frozen focus VERDICT is worse than a frozen picture: it looks like a
    live reading that has stopped responding to the focuser, and it is a number
    the user turns a knob against. Server truth has to stop claiming it."""
    sim_hub.arm_bahtinov()
    await _start_wedged_loop(sim_hub)
    assert (await sim_hub.poll_status())["bahtinov_active"] is True

    assert await sim_hub.yield_camera_for("plate solve") is True

    assert sim_hub.bahtinov is None
    status = await sim_hub.poll_status()
    assert status["bahtinov_active"] is False
    assert status["looping"] is False
    said = [m for m in log_lines() if "needs the camera" in m]
    assert said and said[0].startswith("Bahtinov focus aid stopped")


@pytest.mark.asyncio
async def test_the_aid_is_yielded_even_after_its_loop_already_died(sim_hub,  # noqa: F811
                                                                   log_lines):
    """The aid outlives the loop that feeds it (POST /api/capture/stop leaves it
    armed), and in that state it is ALREADY reporting active over a dead frame
    source. So the aid alone is enough to make the yield do something."""
    sim_hub.arm_bahtinov()
    assert sim_hub.looping is False and sim_hub.live_stacker is None

    assert await sim_hub.yield_camera_for("plate solve") is True

    assert sim_hub.bahtinov is None
    assert [m for m in log_lines() if "Bahtinov focus aid stopped" in m]


@pytest.mark.asyncio
async def test_both_casualties_are_named_when_both_were_running(sim_hub,  # noqa: F811
                                                                _hang_the_loop,
                                                                log_lines):
    """"Live View stopped" while a Bahtinov readout also went dark is a
    half-truth, and the half it leaves out is the one the user's hand was on."""
    sim_hub.arm_bahtinov()
    sim_hub.start_live_stack()
    await _start_wedged_loop(sim_hub)

    await sim_hub.yield_camera_for("plate solve")

    said = [m for m in log_lines() if "needs the camera" in m]
    assert said and said[0].startswith("Bahtinov focus aid and Live View stopped")


@pytest.mark.asyncio
async def test_an_armed_aid_does_not_refuse_the_run_that_needs_the_camera(
        sim_hub, _hang_the_loop):  # noqa: F811
    """THE DECISION, pinned. The alternative was a 409 that protects the aid --
    but ``goto_and_center`` runs unattended from the sequence engine and from
    meridian_flip, where a refusal aborts a target or strands the tube on the
    wrong side of the pier. A focus aid nobody remembered to disarm must not be
    able to cost a night, so the run wins and the aid is stopped and named."""
    sim_hub.arm_bahtinov()
    await _start_wedged_loop(sim_hub)

    result = await sim_hub.goto_and_center(5.0, 10.0)

    assert result["centered"] is True
    assert sim_hub.bahtinov is None and sim_hub.looping is False


@pytest.mark.asyncio
async def test_a_run_that_never_starts_leaves_the_aid_armed(sim_hub, monkeypatch):  # noqa: F811
    """Same ordering rule the sun-cone and pole cases pin for Live View: the
    yield sits after every refusal, so a solve that was never going to run does
    not take a focus aid down on its way out."""
    import astrodeck.providers as providers_module

    def no_solver(hub):
        raise DeviceError("no solver configured")
    monkeypatch.setattr(providers_module, "pick_solver", no_solver)
    sim_hub.arm_bahtinov()

    with pytest.raises(DeviceError):
        await sim_hub.solve_and_sync(exposure_s=0.05)

    assert sim_hub.bahtinov is not None


# ------------------------------------------------------------- solve_and_sync

@pytest.mark.asyncio
async def test_solve_and_sync_takes_the_camera_from_the_loop(sim_hub, _hang_the_loop):  # noqa: F811
    """The direct Solve & Sync button. Before the fix this raised
    "camera is busy (capture light); plate solve refused"."""
    await _start_wedged_loop(sim_hub)

    result = await sim_hub.solve_and_sync(exposure_s=0.05)

    assert result["ra_hours"] is not None
    assert sim_hub.looping is False


@pytest.mark.asyncio
async def test_a_solve_that_cannot_run_does_not_kill_live_view(sim_hub, monkeypatch):  # noqa: F811
    """Ordering guarantee: ``pick_solver`` raises in under a millisecond on a rig
    with nothing trustworthy to solve with. Amputating the user's Live View for a
    solve that was never going to happen would be a pure loss, so the yield sits
    AFTER solver resolution."""
    import astrodeck.providers as providers_module

    def no_solver(hub):
        raise DeviceError("no solver configured")
    monkeypatch.setattr(providers_module, "pick_solver", no_solver)
    sim_hub.start_live_stack()
    await sim_hub.start_loop(0.01, LOOP_MARKER_GAIN, 10)

    with pytest.raises(DeviceError):
        await sim_hub.solve_and_sync(exposure_s=0.05)

    assert sim_hub.looping is True
    assert sim_hub.live_stacker is not None
    await sim_hub.stop_loop_and_wait()


# ------------------------------------------------------------ goto_and_center

@pytest.mark.asyncio
async def test_goto_and_center_centers_with_a_loop_running(sim_hub, _hang_the_loop):  # noqa: F811
    """THE REPORTED BUG. With the loop holding the camera, the first solve was
    refused, ``goto_and_center`` degraded to "using raw GoTo" and returned after
    ONE uncorrected slew -- leaving the tube wherever that landed."""
    await _start_wedged_loop(sim_hub)

    result = await sim_hub.goto_and_center(5.0, 10.0)

    assert result["centered"] is True
    assert "solve_failed" not in result
    assert sim_hub.looping is False


@pytest.mark.asyncio
async def test_the_loop_is_gone_before_the_mount_ever_moves(sim_hub, monkeypatch):  # noqa: F811
    """Stopping the loop lazily at the first solve would still leave one
    uncorrected slew on the table, so the yield has to precede the motion."""
    looping_at_each_slew: list[bool] = []
    tel = sim_hub.devices["telescope"]
    real_slew = tel.slew

    async def watching_slew(ra_hours, dec_deg):
        looping_at_each_slew.append(sim_hub.looping)
        return await real_slew(ra_hours, dec_deg)
    monkeypatch.setattr(tel, "slew", watching_slew)
    await sim_hub.start_loop(0.01, LOOP_MARKER_GAIN, 10)
    assert sim_hub.looping is True

    await sim_hub.goto_and_center(5.0, 10.0)

    assert looping_at_each_slew, "the mount never slewed; the test proved nothing"
    assert not any(looping_at_each_slew)


@pytest.mark.asyncio
async def test_a_goto_refused_at_the_sun_cone_leaves_the_loop_alone(sim_hub, monkeypatch):  # noqa: F811
    """``_check_solar`` runs before the yield: a slew that is about to be refused
    for pointing near the Sun has no business killing the user's Live View."""
    def solar_block(ra_hours, dec_deg, *, force=False):
        raise DeviceError("target is inside the solar exclusion cone")
    monkeypatch.setattr(sim_hub, "_check_solar", solar_block)
    sim_hub.start_live_stack()
    await sim_hub.start_loop(0.01, LOOP_MARKER_GAIN, 10)

    with pytest.raises(DeviceError):
        await sim_hub.goto_and_center(5.0, 10.0)

    assert sim_hub.looping is True
    assert sim_hub.live_stacker is not None
    await sim_hub.stop_loop_and_wait()


@pytest.mark.asyncio
async def test_an_abort_during_the_loop_teardown_still_fences_the_slew(sim_hub, monkeypatch):  # noqa: F811
    """The yield is an AWAIT, and the motion fence works by snapshotting
    ``_motion_epoch`` BEFORE the first await. Snapshotting after the yield would
    capture an epoch a STOP had already bumped, and goto would then slew a mount
    the user had just halted. Simulate the STOP landing inside the teardown
    window and require the goto to abandon."""
    slews: list[tuple] = []
    tel = sim_hub.devices["telescope"]

    async def recording_slew(ra_hours, dec_deg):
        slews.append((ra_hours, dec_deg))
    monkeypatch.setattr(tel, "slew", recording_slew)

    real_yield = sim_hub.yield_camera_for

    async def yield_then_abort(what):
        out = await real_yield(what)
        sim_hub.bump_motion_epoch()      # the STOP lands mid-teardown
        return out
    monkeypatch.setattr(sim_hub, "yield_camera_for", yield_then_abort)

    result = await sim_hub.goto_and_center(5.0, 10.0)

    assert result["aborted"] is True
    assert slews == []


# ------------------------------------------------------- the fourth caller: TPPA

@pytest.mark.asyncio
async def test_polar_alignment_takes_the_camera_before_it_rotates_the_mount(
        sim_hub, _hang_the_loop, monkeypatch):  # noqa: F811
    """The caller the first pass MISSED, and the worst one to miss.

    Native TPPA is a camera-owning MOUNT-MOTION path: solve, rotate 12 deg in RA,
    solve, rotate 12 deg, solve. Losing the camera part-way does not merely fail
    the alignment -- it abandons the tube 12 or 24 degrees from wherever the user
    pointed it, with the session dead and only a log line about a camera to say
    why. With 30 s subs the loop holds the capture lock most of the time, so that
    was the LIKELY outcome, not the unlucky one.

    Drives the real `_drive` and records `hub.looping` at every RA rotation: the
    property is that the loop is gone BEFORE the mount first moves, not merely at
    some point during the run.
    """
    from astrodeck.polar import native as nat

    looping_at_each_rotate = []

    async def watching_rotate(hub, tel, epoch, result, step=None):
        # ``step`` is the signed RA step the driver decides once from the first
        # solved point and passes to every leg, so the arc cannot reverse
        # direction halfway. Accepted and ignored: this stub exists to observe
        # when the rotation happens relative to the camera handover, not to move
        # anything.
        looping_at_each_rotate.append(hub.looping)

    monkeypatch.setattr(nat, "_rotate_in_ra", watching_rotate)

    # Neutralise the low-arc refusal, which is about GEOMETRY and would decide
    # this test by the wall clock. The sim rig points at its default (roughly
    # M42), and _refuse_low_arc projects the arc's altitude from the SOLVED
    # position at the CURRENT time — so the same test passes at 02:00, when M42
    # is up, and fails at 13:30, when it is 84 degrees under the Earth and the
    # driver rightly refuses to drive there. Found exactly that way.
    # The subject here is the ORDERING of the camera yield against the first
    # mount motion; the guard has its own tests in test_polar_meridian_guard.py
    # and test_tppa_procedure.py, so removing it here costs no coverage.
    monkeypatch.setattr(nat, "_refuse_low_arc", lambda hub, result, step: None)

    # Stub the FIT as well as the rotation. The real engine rejects three solves
    # that landed on the same sky ("mount did not move between points"), which is
    # how this test stops the run — but it is a Rust wheel that CI's `server` job
    # does not install, so there `_native` is None and the run died on an
    # AttributeError instead. The subject here is the ORDERING of the camera
    # yield against the first mount motion; making the stop deterministic on any
    # host is what lets CI grade that at all.
    class _RejectingFit:
        @staticmethod
        def tppa_from_three(*a, **k):
            raise ValueError("mount did not move between points")

    monkeypatch.setattr(nat, "_native", _RejectingFit)
    await _start_wedged_loop(sim_hub)
    assert sim_hub.looping is True

    # The stubbed rotation records instead of moving, so the driver's own
    # did-the-mount-arrive check refuses at the second measurement point — it
    # now catches this before the fit ever sees it, which is the whole reason it
    # exists (a stuck mount used to reach the engine and, unless all THREE points
    # coincided, come back as a confident 0.00' "polar aligned"). Either way the
    # stop is expected and irrelevant here: the subject is the ORDERING of the
    # camera yield against the first mount motion, and the run reaches any stop
    # at all only by getting past the first rotation.
    with pytest.raises(DeviceError, match="did not move"):
        await nat._drive(_FakeSession(), sim_hub)

    assert looping_at_each_rotate, "the mount never rotated; the test proved nothing"
    assert not any(looping_at_each_rotate), (
        "the live loop was still running when TPPA moved the mount")
    status = await sim_hub.poll_status()
    assert status["looping"] is False
    assert status["live_stack_active"] is False


@pytest.mark.asyncio
async def test_polar_yield_is_ordered_after_the_pole_refusal(sim_hub, monkeypatch):  # noqa: F811
    """Same rule the sun-cone test pins for goto: a run about to be refused must
    not amputate someone's Live View on the way out. The mount-side pole check
    runs BEFORE the yield, so a TPPA started at the parked position -- which is
    where a mount usually sits -- leaves the loop alone."""
    from astrodeck.polar import native as nat

    yielded = []

    async def spy(what):
        yielded.append(what)
        return True
    monkeypatch.setattr(sim_hub, "yield_camera_for", spy)

    async def at_the_pole(tel):
        return 89.99
    monkeypatch.setattr(nat, "_mount_dec", at_the_pole)

    with pytest.raises(DeviceError):
        await nat._drive(_FakeSession(), sim_hub)
    assert yielded == [], "a run refused at the pole must not stop the loop"


class _FakeSession:
    """Minimal stand-in: _drive only ever calls _publish on it."""

    def __init__(self):
        self.published = []

    def _publish(self, **kw):
        self.published.append(kw)
