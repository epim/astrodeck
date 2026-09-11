"""Lanes the UI cannot see — /api/polar, /api/dome/close, and the 409s.

``hub.busy_lanes()`` rides every status frame and is how a control answers "is
MY operation still running" (ui/src/lib/useBusy.ts). It is built from
``hub._busy``, which only ``_spawn`` populates, so three long operations were
invisible or mislabelled:

  * a polar session runs on its own task -> ``useBusy("polar")`` was false for
    the whole of a five-minute alignment;
  * a roof close ran in the ``goto`` lane -> "Close roof now" was dead during
    every unrelated slew, for a reason that named a slew;
  * the ``_spawn``/``_spawn_connect`` 409s carried a BARE STRING detail, so
    ``ApiError.code`` was undefined and the client had to match a naked 409.

The roof case is the interesting one: the shared lane was not laziness, it was
the exclusion — a close parks the mount and then travels a shutter over it. The
tests below pin BOTH halves of the replacement interlock, because a rename that
kept only the visible half would look identical on the screen and let a slew
start under a closing roof.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    app = app_module.create_app()
    with TestClient(app) as c:
        try:
            yield c
        finally:
            try:
                c.post("/api/polar/stop")
                c.post("/api/disconnect")
            except Exception:
                pass


def _lanes(c) -> list[str]:
    return c.get("/api/status").json().get("busy_lanes", [])


def _wait(predicate, c, tries=400) -> bool:
    for _ in range(tries):
        if predicate():
            return True
        c.get("/api/status")     # give the app loop a chance to run bg tasks
        time.sleep(0.02)
    return predicate()


# ------------------------------------------------------------------- polar

def test_polar_start_publishes_a_polar_lane(client, monkeypatch):
    """The lane must exist WHILE the session runs — that is the whole claim."""
    # The sim polar driver's pacing sleeps are collapsed suite-wide
    # (conftest's ASTRODECK_FAST_TEST), which would let the run finish between
    # the POST and the assertion and make a real lane look absent. Restore real
    # pacing for this one test so "the lane is live" is what is measured.
    monkeypatch.delenv("ASTRODECK_FAST_TEST", raising=False)
    assert "polar" not in _lanes(client), "precondition: nothing running"

    r = client.post("/api/polar/start")
    assert r.status_code == 200, r.text
    assert client.get("/api/polar/state").json()["running"] is True, (
        "precondition: the session really is running, so an absent lane below "
        "would be the bug and not a race")

    assert "polar" in _lanes(client), (
        "a running polar session must be visible to the Polar screen")

    assert client.post("/api/polar/stop").status_code == 200
    assert "polar" not in _lanes(client), "a stopped session is not busy"


def test_polar_second_start_409s_with_a_code(client, monkeypatch):
    monkeypatch.delenv("ASTRODECK_FAST_TEST", raising=False)
    assert client.post("/api/polar/start").status_code == 200
    r = client.post("/api/polar/start")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "lane_busy"
    assert detail["lane"] == "polar"
    assert "already running" in detail["detail"], "the human sentence survives"


# -------------------------------------------------------------------- dome

def test_close_runs_in_the_dome_lane_not_the_mount_s(client):
    assert client.post("/api/connect/sim").status_code == 200
    r = client.post("/api/dome/close")
    assert r.status_code == 200, r.text
    assert r.json().get("started") == "dome"

    lanes = _lanes(client)
    assert "dome" in lanes, (
        "precondition: the close is still in flight, so the next assertion is "
        "about lane NAMING and not about a finished task")
    assert "goto" not in lanes, (
        "the roof must not report itself as a mount slew — that is what made "
        "the button's disabled reason describe somebody else's operation")

    assert _wait(lambda: client.get("/api/dome/state").json()["shutter"]
                 == "closed", client), client.get("/api/dome/state").json()


def test_a_closing_roof_blocks_a_restart_exactly_as_a_slew_does(client):
    """The half the lane split DID drop, measured on a sim rig by the reviewer.

    ``goto`` was buying the roof two things, and only the mutual exclusion was
    carried over deliberately. The other was ``hub.busy_label``: the roof was
    not in its table, so ``restart_blocker`` was None for the whole close and
    ``POST /api/system/factory-reset`` — whose first act is
    ``hub.disconnect_all()``, which cancels every task in ``_busy``, the dome
    lane included — passed its idle gate while the shutter was travelling over a
    just-parked mount. Same for ``/api/update/apply`` on a supervised box (it
    re-reads ``restart_blocker`` at its point of no return).

    Written as a COMPARISON against the slew, because "as a slew does" is the
    actual requirement: an assertion that only pinned the dome row would still
    pass if somebody made both rows permissive.

    Every verdict is asserted as the EXACT SENTENCE, not merely "not None". The
    close runs its whole park-then-travel under ``hub._motion_lock``, and
    ``restart_blocker`` has a backstop clause that answers "the mount is moving"
    for any held lock — so a bare truthiness check here would pass with the
    lane's label deleted (measured: it does), and the roof would still be
    missing from ``status.busy``, which is a different consumer the lock cannot
    reach.
    """
    hub = app_module.hub
    assert client.post("/api/connect/sim").status_code == 200

    # Row 1: the reference. A slew has always blocked a restart.
    g = client.post("/api/mount/goto",
                    json={"ra_hours": 2.0, "dec_deg": 80.0, "center": False})
    assert g.status_code == 200, g.text
    assert "goto" in _lanes(client), "precondition: the slew is in flight"
    assert hub.busy_label == "slewing"
    assert hub.restart_blocker == "rig is slewing", (
        "precondition: a slew blocks a restart, and says so as its own lane")
    assert client.get("/api/system/factory-reset").json()["can_reset"] is False

    # Row 2: the roof, which must answer the same way.
    r = client.post("/api/dome/close")
    assert r.status_code == 200, r.text
    lanes = _lanes(client)
    assert lanes == ["dome"], (
        f"precondition: the close is in flight and owns the only lane, so the "
        f"assertions below are about the ROOF and cannot be satisfied by the "
        f"superseded slew; got {lanes}")

    # The CONSEQUENCE first, so a regression fails on the hazard rather than on
    # a word: restart_blocker is what /api/update/apply and the factory reset
    # both read.
    assert hub.restart_blocker == "rig is closing the roof", (
        "a factory reset or an update restart landing mid-close tears the rig "
        "down while the shutter is travelling over a just-parked mount — and "
        "the ROOF's own lane has to be the thing that says so")
    snap = client.get("/api/system/factory-reset").json()
    assert snap["can_reset"] is False, (
        "the reviewer measured can_reset true here with the lane split applied")
    assert snap["blocked_reason"] == "rig is closing the roof", (
        "the panel renders this sentence verbatim, so it has to read like one")

    # Then the consumer the motion-lock backstop cannot reach: status.busy is
    # the stale-telemetry suppressor and the NINA-link health grace, and a close
    # is the longest device-blocking operation the rig performs.
    assert hub.busy_label == "closing the roof"

    assert _wait(lambda: client.get("/api/dome/state").json()["shutter"]
                 == "closed", client), client.get("/api/dome/state").json()
    assert hub.restart_blocker is None, (
        "and it must let go when the roof is shut, or the gate is a latch")


def test_a_polar_run_blocks_a_restart(client, monkeypatch):
    """The other lane this change introduced.

    A polar run drives the mount to three positions; before it had a lane at all
    nothing here could see it, so this is a gap the lane made closable rather
    than a regression. Same measurement as the roof."""
    monkeypatch.delenv("ASTRODECK_FAST_TEST", raising=False)
    hub = app_module.hub
    assert client.post("/api/polar/start").status_code == 200
    assert client.get("/api/polar/state").json()["running"] is True, (
        "precondition: the session really is running")
    assert "polar" in _lanes(client), "precondition: it published its lane"

    assert hub.busy_label == "polar aligning"
    # The exact sentence, for the same reason the roof test gives: the session
    # takes hub._motion_lock for each of its slews, so "not None" alone would
    # not tell the lane from the backstop.
    assert hub.restart_blocker == "rig is polar aligning", (
        "restarting the process mid-alignment leaves the mount mid-slew with "
        "nothing left to stop it")
    snap = client.get("/api/system/factory-reset").json()
    assert snap["can_reset"] is False
    assert snap["blocked_reason"] == "rig is polar aligning"

    assert client.post("/api/polar/stop").status_code == 200
    assert hub.restart_blocker is None



def _without_comments(src: str) -> str:
    """``src`` with every ``#`` comment blanked out, positions preserved.

    Blanked rather than deleted so a tokenize failure on one file cannot shift
    the rest of it; on a SyntaxError the source comes back untouched, which
    fails LOUD (an invented lane) rather than silently scanning nothing."""
    import io
    import tokenize

    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return src
    lines = src.splitlines(keepends=True)
    for tok in toks:
        if tok.type != tokenize.COMMENT:
            continue
        (row, col), (_, end_col) = tok.start, tok.end
        line = lines[row - 1]
        lines[row - 1] = line[:col] + " " * (end_col - col) + line[end_col:]
    return "".join(lines)


def _lanes_in(src: str) -> set[str]:
    """Every lane name this source claims: ``_spawn("x")`` and
    ``hub._busy["x"] = ...`` (which also catches ``self._hub._busy[...]``)."""
    import re as _re
    found = set(_re.findall(r'_spawn\(\s*"([^"]+)"', src))
    found |= set(_re.findall(r'_busy\[\s*"([^"]+)"\s*\]\s*=', src))
    return found


def test_every_lane_is_classified_as_blocking_or_not():
    """The guard that would have caught the roof the day the lane moved.

    ``BUSY_LANE_LABELS`` is not a display detail: a lane in it blocks a restart
    and a factory reset, and a lane out of it does not. "Not in the table" was
    indistinguishable from "nobody thought about it", which is exactly how the
    roof lost its gate. So every lane name this app can put in ``hub._busy``
    must appear in one table or the other — the reasons in ``UNLABELLED_LANES``
    are the record of the decision.

    Read out of the SOURCE rather than a hand-kept list: a hand-kept list is the
    thing that went stale."""
    import inspect
    import pathlib

    import astrodeck.hub as hub_mod

    # EVERY .py IN THE PACKAGE, not just api/app.py. A lane is claimed wherever
    # a task is parked in ``hub._busy``, and since the routers started moving
    # out of app.py (the atlas routers, then imaging/video_routes.py) that is no
    # longer only here: ``video`` and ``video_stack`` are claimed in
    # imaging/video.py, so a guard reading app.py alone was quietly grading a
    # shrinking share of the app while still reporting a full pass. A guard that
    # narrows its own scope as the code moves is worse than no guard, because it
    # keeps saying yes.
    roots = [pathlib.Path(inspect.getfile(app_module))]
    roots += sorted(pathlib.Path(inspect.getfile(hub_mod)).parent.rglob("*.py"))
    lanes: set[str] = set()
    for path in roots:
        # COMMENTS FIRST. Widening to the package put hub.py's own prose in
        # range, and the sentence describing this very scrape contains a
        # ``_spawn("...")`` example -- so the guard's first act was to invent a
        # lane called "..." and fail on it. Blanking comments is the narrow fix:
        # the lane literals we are after are code, and nothing else in the
        # package writes one in a docstring.
        lanes |= _lanes_in(_without_comments(path.read_text(encoding="utf-8")))
    assert {"goto", "dome", "polar", "video"} <= lanes, (
        f"precondition: the scrape actually found the lanes it is judging "
        f"(if _spawn's call shape changed, fix this regex, not the tables); "
        f"``video`` is claimed OUTSIDE api/app.py, so it is also the witness "
        f"that the scrape still reaches the whole package; "
        f"found {sorted(lanes)}")

    known = {n for n, _ in hub_mod.BUSY_LANE_LABELS} | set(hub_mod.UNLABELLED_LANES)
    unclassified = sorted(lanes - known)
    assert not unclassified, (
        f"lanes in neither hub.BUSY_LANE_LABELS nor hub.UNLABELLED_LANES: "
        f"{unclassified}. A new lane must SAY whether the rig is too busy to "
        f"restart while it runs; silence defaults to 'go ahead, restart'.")

    assert all(hub_mod.UNLABELLED_LANES[n].strip()
               for n in hub_mod.UNLABELLED_LANES), "each exemption states why"


async def test_the_update_s_own_lane_does_not_block_the_update():
    """The one lane that MUST stay unlabelled, measured rather than asserted
    about the table.

    ``/api/update/apply`` runs as ``_spawn("system.update", ...)`` and re-reads
    ``restart_blocker`` at its point of no return (update/service.py, just before
    it writes the pending release). Label that lane while tightening this gate
    and every update aborts itself with "rig is updating" — a plausible mistake,
    since every other long lane now blocks."""
    import asyncio

    from astrodeck.hub import Hub

    h = Hub()
    task = asyncio.create_task(asyncio.sleep(30))
    h._busy["system.update"] = task
    try:
        assert "system.update" in h.busy_lanes(), (
            "precondition: the update's lane is live, the way it is during an "
            "apply")
        assert h.restart_blocker is None, (
            "the update must not be blocked by its own lane")
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_motion_with_no_lane_still_blocks_a_restart():
    """The backstop under the two mount-motion paths that publish NO lane.

    ``dawn_park`` parks under ``hub._motion_lock`` (pinned by
    test_dawn_park.py::test_parks_an_idle_mount_after_the_sun_comes_up, which
    asserts ``locked_during_park == [True]``) and
    ``/api/mount/move`` dispatches its jog under it, and neither goes through
    ``_spawn``, so no name in any table describes them. ``restart_blocker`` asks
    the lock itself, which is a fact about the rig rather than a name that can
    be renamed out from under it.

    A fresh ``Hub`` rather than the app singleton: the singleton carries
    whatever the rest of this worker left in ``_busy``, and this test's
    precondition is 'nothing is running'."""
    from astrodeck.hub import Hub

    h = Hub()
    assert h.busy_lanes() == [] and h.busy_label is None, (
        "precondition: no lane, so what the lock does is what is measured")
    assert h.restart_blocker is None, "precondition: an idle hub blocks nothing"

    async with h._motion_lock:
        assert h.restart_blocker, (
            "a mount under a device-level motion command must not be restarted "
            "out from under, lane or no lane")
    assert h.restart_blocker is None, "and the block lifts with the lock"


def test_a_slew_is_refused_while_the_roof_is_closing(client, bus_lines):
    """The half a plain rename would have silently dropped."""
    assert client.post("/api/connect/sim").status_code == 200
    assert client.post("/api/dome/close").status_code == 200
    assert "dome" in _lanes(client), "precondition: the close is in flight"

    r = client.post("/api/mount/goto",
                    json={"ra_hours": 2.0, "dec_deg": 80.0, "center": False})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "lane_blocked"
    assert detail["blocked_by"] == "dome"
    assert "roof" in detail["detail"], (
        "the reason must be about the roof; 'goto is already running' is what "
        "sent people hunting for a slew nobody started")

    # Park is mount motion too (it shares the goto lane), and the close is
    # already parking — refusing it is the same interlock, not a second rule.
    # And the refusal must cost NOTHING: park bumps the motion fence before it
    # spawns, and its own docstring names bump-then-409 as a past bug (the fence
    # bump abandons whatever motion was in flight, so a refusal after it leaves
    # the sabotage without the park). Measured on the epoch itself.
    epoch = app_module.hub._motion_epoch
    assert client.post("/api/mount/park").status_code == 409
    assert app_module.hub._motion_epoch == epoch, (
        "the refused park must not have fenced anything on its way out")
    # Home shares the lane and the bump, so it gets the same treatment. The sim
    # mount really does have a home (can_find_home), so a 409 here is the
    # interlock and not the "no home position" 400.
    assert client.post("/api/mount/home").status_code == 409
    assert app_module.hub._motion_epoch == epoch, "same for home"

    # UNPARK is the half the interlock's own sentence promised and did not
    # cover: it never goes through _spawn, so no lane was consulted, and it is
    # the one command that makes a parked mount able to move again under a roof
    # that has just shut over it.
    u = client.post("/api/mount/unpark")
    assert u.status_code == 409, u.text
    assert u.json()["detail"]["blocked_by"] == "dome"
    # And the driver was never touched. Asserted on the route's own log line
    # rather than on the parked flag: the close's park is itself a slew, so the
    # flag is legitimately in motion here and a 409-that-unparked-anyway would
    # hide inside that. ``bus_lines`` captures at the call, so this cannot be
    # satisfied by another test in the same worker.
    assert not any("mount unparked" in msg for _lvl, msg, _src in bus_lines), (
        "a 409 that unparked anyway would be the whole hazard with a status "
        "code on it")

    # STOP is deliberately NOT refused: an emergency stop during a close must
    # always work. (It never routes through _spawn either — this asserts the
    # decision, not an accident of plumbing.)
    assert client.post("/api/mount/stop").status_code == 200

    assert _wait(lambda: client.get("/api/dome/state").json()["shutter"]
                 == "closed", client), client.get("/api/dome/state").json()


def test_close_still_supersedes_an_inflight_slew(client, bus_lines):
    """The other half: a roof close is a safety action and must beat a slew.

    Asserted on the CANCELLATION, not on the lane going quiet: the route bumps
    the motion fence either way, so a slew that was merely abandoned also leaves
    the lane — an emptier assertion would pass with the supersede deleted."""
    assert client.post("/api/connect/sim").status_code == 200
    g = client.post("/api/mount/goto",
                    json={"ra_hours": 2.0, "dec_deg": 80.0, "center": False})
    assert g.status_code == 200, g.text
    assert "goto" in _lanes(client), "precondition: a slew is in flight"

    r = client.post("/api/dome/close")
    assert r.status_code == 200, r.text
    # ``bus_lines``, not the /api/logs ring: the ring is shared by the whole
    # test process, so a "goto cancelled" logged by an earlier test in this
    # worker would satisfy the search without this close doing anything.
    assert _wait(lambda: any("goto cancelled" in msg
                             for _lvl, msg, _src in bus_lines), client), (
        "the fenced slew's task must be CANCELLED, not left running beside the "
        "close (_spawn logs the cancellation)")
    # And the close itself completed: superseding the slew did not cost the
    # park-then-close ordering.
    assert _wait(lambda: client.get("/api/dome/state").json()["shutter"]
                 == "closed", client), client.get("/api/dome/state").json()


def test_second_close_supersedes_the_first(client):
    """Unchanged behaviour, now on the dome's own lane instead of the mount's."""
    assert client.post("/api/connect/sim").status_code == 200
    assert client.post("/api/dome/close").status_code == 200
    r = client.post("/api/dome/close")
    assert r.status_code == 200, r.text
    assert r.json().get("started") == "dome"


# ------------------------------------------------------------ 409 structure

def test_same_lane_409_carries_a_code(client):
    assert client.post("/api/connect/sim").status_code == 200
    g = client.post("/api/mount/goto",
                    json={"ra_hours": 2.0, "dec_deg": 80.0, "center": False})
    assert g.status_code == 200, g.text
    assert "goto" in _lanes(client), "precondition: the lane is occupied"

    r = client.post("/api/mount/goto",
                    json={"ra_hours": 5.0, "dec_deg": 10.0, "center": False})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "lane_busy"
    assert detail["lane"] == "goto"
    assert detail["detail"] == "'goto' is already running", (
        "the message a client already renders must be byte-identical")


async def test_connect_409_carries_a_code():
    """``_spawn_connect``'s 409 — the one the Equipment screen hits when a
    connect is already in flight. Driven through the guard itself: the two
    profile routes share it, and it is the guard that owns the refusal."""
    import asyncio

    async def _never() -> None:
        await asyncio.sleep(60)

    assert app_module._connect_task is None or app_module._connect_task.done(), \
        "precondition: no connect in flight"
    app_module._spawn_connect(_never())
    try:
        await asyncio.sleep(0)          # let the driver actually start
        assert not app_module._connect_task.done(), "precondition: it took"
        with pytest.raises(app_module.HTTPException) as ei:
            app_module._spawn_connect(_never())
        assert ei.value.status_code == 409
        detail = ei.value.detail
        assert detail["code"] == "lane_busy"
        assert detail["lane"] == "profile"
        assert detail["detail"] == "'profile' is already running", (
            "the message a client already renders must be byte-identical")
    finally:
        app_module._connect_task.cancel()
        try:
            await app_module._connect_task
        except (asyncio.CancelledError, Exception):
            pass
        app_module._connect_task = None


def test_the_connect_driver_stays_out_of_the_cancel_list():
    """A regression guard on the DECISION, not just the code.

    ``_busy`` is the list ``hub._teardown`` cancels and then CLEARS. A connect's
    first step IS a teardown, so a "profile" lane parked there would (a) have
    been the self-cancel ``_spawn_connect`` was written to escape and (b) still
    disappear at the clear, mid-connect, unlocking any control gated on it. The
    next agent who notices the missing lane must fail this test rather than
    discover both halves on a rig.
    """
    import inspect
    src = inspect.getsource(app_module._spawn_connect)
    assert "_connect_task = asyncio.create_task" in src, (
        "the connect driver is tracked in its own handle")
    assert 'hub._busy["profile"] =' not in src, (
        "the connect driver must NOT be registered in hub._busy")

    # And the reason, asserted against the hub rather than trusted: a teardown
    # empties the map, so anything placed there cannot survive a connect.
    from astrodeck.hub import Hub
    teardown = inspect.getsource(Hub._teardown)
    assert "self._busy.clear()" in teardown, (
        "if this ever stops being true, revisit _spawn_connect's docstring — "
        "the reason the lane cannot live in _busy would have changed")


# ------------------------------------------------- polar <-> goto (2026-08-06)
# Three-point alignment solves three fields separated by a PURE RA rotation. A
# slew or a sync between those points does not degrade the answer, it
# invalidates it — measured on the rig: a goto_and_center still running when an
# alignment started re-centred twice between the measurement points, the
# declination moved 1.1 deg (which a pure RA rotation cannot do), and the fit
# reported 4747' of total error for a mount that was very nearly aligned.
#
# Driven through the REAL routes, like the roof pair above, so the lane the UI
# provokes is the lane under test.

def test_a_slew_is_refused_while_an_alignment_is_measuring(client):
    # The polar lane is HELD OPEN, for the reason the sibling test below
    # already documents: a real /api/polar/start on the sim can finish before
    # the very next request reads the lane back, and then the precondition
    # fails and the test proves nothing. It did exactly that in CI on
    # 2026-08-08 (`assert 'polar' in []`) while passing on every developer
    # machine — a slower, more loaded runner simply lost the race more often.
    # The lane is the contract; how it got there is not.
    import asyncio
    import astrodeck.hub as hub_mod

    async def _held():
        await asyncio.Event().wait()

    assert client.post("/api/connect/sim").status_code == 200
    loop = asyncio.new_event_loop()
    task = loop.create_task(_held())
    hub_mod.hub._busy["polar"] = task
    try:
        # PRECONDITION: without a live lane a 409 below would prove nothing.
        assert "polar" in _lanes(client), "precondition: the alignment is measuring"

        r = client.post("/api/mount/goto",
                        json={"ra_hours": 5.6, "dec_deg": -5.4, "center": False})
        assert r.status_code == 409, (
            f"a slew was ACCEPTED during an alignment ({r.status_code}) — it moves "
            f"the mount between two of the three measured points: {r.text[:200]}")
        detail = r.json()["detail"]
        assert detail["blocked_by"] == "polar"
        assert "alignment" in detail["detail"], (
            f"the reason must name the alignment, not a bare lane: {detail}")
    finally:
        hub_mod.hub._busy.pop("polar", None)
        task.cancel()
        loop.close()


def test_an_alignment_is_refused_while_the_mount_is_slewing(client):
    # The goto lane is held OPEN deliberately rather than raced against a sim
    # slew: the sim finishes in milliseconds, so a version of this that posted a
    # real goto and hoped to catch the lane skipped itself most runs — and a
    # test that skips is a test that is not testing. The lane is the contract;
    # how it got there is not.
    import asyncio
    import astrodeck.hub as hub_mod

    async def _held():
        await asyncio.Event().wait()

    assert client.post("/api/connect/sim").status_code == 200
    loop = asyncio.new_event_loop()
    task = loop.create_task(_held())
    hub_mod.hub._busy["goto"] = task
    try:
        # PRECONDITION: the lane is live over the wire, not just in our dict.
        assert "goto" in _lanes(client), "precondition: the slew lane is live"

        r = client.post("/api/polar/start")
        assert r.status_code == 409, (
            f"an alignment STARTED while the mount was slewing ({r.status_code}) "
            "— this is the exact run that reported 4747' for an aligned mount")
        detail = r.json()["detail"]
        assert detail["blocked_by"] == "goto"
        assert "slew" in detail["detail"], (
            f"the reason must say a slew is running: {detail}")
    finally:
        task.cancel()
        hub_mod.hub._busy.pop("goto", None)
        loop.close()
