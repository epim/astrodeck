"""One move-completion contract, bound to every focuser AstroDeck drives.

THE PROMISE, stated once in ``devices/base.py`` and inherited by five drivers:

    async def move_to(self, position: int) -> None:
        \"\"\"Absolute move; waits for completion.\"\"\"

THE DEFECT CLASS (broken promises, class E — siblings that drifted apart under
one stated contract). On 2026-07-31 a ZWO EAF refused every move above position
360, against a mechanical stop, and ``EafFocuser.move_to`` returned SUCCESS
every time: the user typed 22000, pressed Go, nothing moved, and there was no
error in the UI, the log, or the API response. The loop had waited for
ABSENCE OF MOTION, which an idle motor satisfies on the first poll.

That was fixed in ``zwo_usb.py``. The audit then found the same mistake, made
independently, in ``asiair_backend.py`` (finding #15) and fixed it there too.
Both fixes were written against ONE driver each. Nothing bound the other three
to the same rule, and two of them did not keep it:

  * ``AlpacaFocuser.move_to`` polled ``ismoving`` with NO arrival check and NO
    timeout at all — so a driver that ignores the move returns success, and a
    driver that never clears ``ismoving`` hangs the caller forever. This is the
    ASCOM path, which is the path the bundled COM host and every Alpaca server
    take, i.e. most real hardware.
  * ``NinaFocuser.move_to`` polled ``IsMoving`` 800 times and then FELL OUT OF
    THE LOOP — returning normally, reporting success, on the one path that
    means "this move is still not finished".

So the deliverable is not another per-driver test. It is this: one corpus of
focuser behaviours, bound to every implementation, in the idiom
``test_camera_contract.py`` established for camera adapters. A sixth driver
cannot join the product without answering the same four questions.

WHAT THE CORPUS MODELS is the physical device, not the transport: a drawtube
that arrives, one that never moves, one that jams part-way, and one that reports
motion forever. Every driver is then asked the only question that matters —
*does a move that did not happen look like a move that did?*
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from astrodeck.devices.base import DeviceError

# The whole-move budget for one contract case. Every driver's own timeout is
# shrunk well inside this by its bench; this is the backstop that turns "no
# timeout at all" into a FAILURE rather than a hung suite.
CASE_BUDGET_S = 5.0

START = 1000
TARGET = 2000


class World:
    """One focuser, and what is physically happening to it.

    ``poll()`` is what a driver's wait loop sees each time round: the position
    it can read and whether the device claims to be moving. Behaviours are named
    for the FAILURE, because the contract is about failures — every driver gets
    the success case right."""

    def __init__(self, behaviour: str):
        self.behaviour = behaviour
        self.position = START
        self.target: int | None = None
        self.polls = 0
        self.halted = 0

    def command(self, target: int) -> None:
        self.target = int(target)
        if self.behaviour == "arrives":
            self.position = self.target
        elif self.behaviour == "stops_short":
            self.position = START + 10      # a jam ten steps out of the gate
        # never_moves / never_settles: the drawtube stays exactly where it was.

    def poll(self) -> tuple[int, bool]:
        self.polls += 1
        return self.position, self.behaviour == "never_settles"

    def halt(self) -> None:
        self.halted += 1


BEHAVIOURS = ("arrives", "never_moves", "stops_short", "never_settles")


# ------------------------------------------------------------------- benches
#
# One per driver. Each returns a CONNECTED focuser wired to the shared World and
# shrinks that driver's own clock, so a real timeout still fires inside
# CASE_BUDGET_S. Written against each driver's real transport double where one
# already exists, so a bench cannot pass by being kinder than the wire.

async def _bench_sim(world, monkeypatch, tmp_path):
    from astrodeck.devices.sim import SimFocuser, SimRig
    rig = SimRig()
    rig.focuser_pos = START
    foc = SimFocuser(rig)
    await foc.connect()
    return foc


class _AlpacaConn:
    """The Alpaca HTTP conn, shaped like ``test_alpaca.RecConn`` — every verb
    recorded, every read answered off the World."""

    def __init__(self, world: World):
        self.world = world
        self.host, self.port = "10.0.0.9", 11111
        self.calls: list[tuple] = []

    async def get(self, dev_type, dev_num, method, **params):
        self.calls.append(("get", method, params))
        if method == "position":
            return self.world.position
        if method == "ismoving":
            return self.world.poll()[1]
        return {"maxstep": 60000, "stepsize": 1.2, "absolute": True,
                "connected": True, "name": "Alpaca focuser",
                "temperature": 4.2}.get(method)

    async def put(self, dev_type, dev_num, method, **params):
        self.calls.append(("put", method, params))
        if method == "move":
            self.world.command(params["Position"])
        elif method == "halt":
            self.world.halt()
        return None


async def _bench_alpaca(world, monkeypatch, tmp_path):
    from astrodeck.devices import alpaca as alpaca_mod
    monkeypatch.setattr(alpaca_mod, "FOCUS_POLL_S", 0.01, raising=False)
    monkeypatch.setattr(alpaca_mod, "FOCUS_MOVE_TIMEOUT_S", 0.4, raising=False)
    foc = alpaca_mod.AlpacaFocuser(_AlpacaConn(world), 0, "Alpaca focuser")
    await foc.connect()
    return foc


class _NinaClient:
    """The NINA Advanced API client, reduced to the two calls a focuser move
    makes: the move request and the info poll."""

    def __init__(self, world: World):
        self.world = world
        self.host, self.port = "10.0.0.9", 1888

    async def get(self, path, **params):
        if path == "/equipment/focuser/move":
            self.world.command(params["position"])
            return {}
        if path == "/equipment/focuser/stop-move":
            self.world.halt()
            return {}
        pos, moving = self.world.poll()
        return {"Connected": True, "Name": "NINA focuser", "MaxStep": 60000,
                "Position": pos, "IsMoving": moving, "Temperature": 4.2}


async def _bench_nina(world, monkeypatch, tmp_path):
    from astrodeck.devices import nina as nina_mod
    monkeypatch.setattr(nina_mod, "FOCUS_POLL_S", 0.01, raising=False)
    monkeypatch.setattr(nina_mod, "FOCUS_MOVE_TIMEOUT_S", 0.4, raising=False)
    foc = nina_mod.NinaFocuser(_NinaClient(world), "NINA focuser")
    await foc.connect()
    return foc


async def _bench_zwo_usb(world, monkeypatch, tmp_path):
    from astrodeck import config as cfg_mod
    from astrodeck.devices.backends import zwo_usb as zu
    from test_zwo_usb import FakeEafSdk

    monkeypatch.setattr(cfg_mod, "FOCUSER_STATE_FILE",
                        tmp_path / "focuser_state.json")
    monkeypatch.setattr(zu, "POLL_S", 0.01)
    monkeypatch.setattr(zu, "EAF_MOVE_TIMEOUT_S", 0.4)

    class _WorldSdk(FakeEafSdk):
        def move(self, d, step):
            self._log("move")
            world.command(step)

        def is_moving(self, d):
            self._log("is_moving")
            pos, moving = world.poll()
            self.position = pos
            return moving, False

        def get_position(self, d):
            self._log("get_position")
            return world.position

        def stop(self, d):
            self._log("stop")
            world.halt()

    foc = zu.EafFocuser(_WorldSdk(position=START), 10)
    foc.connected = True
    foc.max_position = 60000
    return foc


async def _bench_asiair(world, monkeypatch, tmp_path):
    from astrodeck.devices.backends import asiair_backend as ab
    from test_asiair_backend import FakeAsiair

    monkeypatch.setattr(ab, "POLL_S", 0.01)
    monkeypatch.setattr(ab, "FOCUS_MOVE_TIMEOUT_S", 0.4)
    fake = FakeAsiair()

    class _Focuser:
        def info(self):
            pos, moving = world.poll()
            return SimpleNamespace(position=pos, temperature=18.2,
                                   max_step=60000, model="EAF-0-0",
                                   firmware="3.3.8",
                                   state="moving" if moving else "idle")

        def move_to(self, pos):
            world.command(pos)

        def stop(self):
            world.halt()

    fake.focuser = _Focuser()
    link = ab._Link(fake, "asiair.invalid")
    await link.connect()
    foc = ab.AsiairFocuser(link)
    await foc.connect()
    return foc


BENCHES = {
    "sim": _bench_sim,
    "alpaca": _bench_alpaca,
    "nina": _bench_nina,
    "zwo-usb-eaf": _bench_zwo_usb,
    "asiair": _bench_asiair,
}

#: Drivers that cannot be given a lying device, with the reason. The Simulator
#: IS the device — its ``move_to`` writes the position it then reports, so
#: "the motor never engaged" is not a state it has. Any REAL driver added here
#: would be an admission that its failure paths are untested.
_NO_FAULT_INJECTION = {
    "sim": "SimFocuser moves the value it reports; there is no transport "
           "between the command and the position that could lie about it",
}


@pytest.fixture(params=sorted(BENCHES), ids=lambda n: n)
def driver(request):
    return request.param


async def _move(driver: str, behaviour: str, monkeypatch, tmp_path):
    """Drive one contract case, bounded. Returns ``(focuser, world, error)``."""
    world = World(behaviour)
    foc = await BENCHES[driver](world, monkeypatch, tmp_path)
    err: Exception | None = None
    try:
        await asyncio.wait_for(foc.move_to(TARGET), CASE_BUDGET_S)
    except (asyncio.TimeoutError, TimeoutError) as e:
        err = e
    except DeviceError as e:
        err = e
    return foc, world, err


# ----------------------------------------------------------------- the corpus

async def test_a_move_that_arrives_returns_and_lands(driver, monkeypatch,
                                                     tmp_path):
    """The case every driver already gets right — here so a driver cannot pass
    the failure cases by refusing every move."""
    foc, world, err = await _move(driver, "arrives", monkeypatch, tmp_path)
    assert err is None, f"{driver}: a move that arrived raised {err!r}"
    assert abs(await foc.get_position() - TARGET) <= 2, (
        f"{driver}: returned from move_to at {world.position}, not {TARGET}")


@pytest.mark.parametrize("behaviour", ["never_moves", "stops_short"])
async def test_a_move_that_did_not_happen_is_an_error(driver, behaviour,
                                                      monkeypatch, tmp_path):
    """THE contract. An idle focuser is idle from the first poll, so any wait
    that keys on absence-of-motion reports success for a move that never
    started — which is what a user experiences as "I pressed Go and nothing
    happened, and nothing said so"."""
    if driver in _NO_FAULT_INJECTION:
        pytest.skip(_NO_FAULT_INJECTION[driver])
    _foc, world, err = await _move(driver, behaviour, monkeypatch, tmp_path)
    assert isinstance(err, DeviceError), (
        f"{driver}: move_to({TARGET}) returned SUCCESS with the drawtube at "
        f"{world.position}. Wait for ARRIVAL, not for absence of motion — see "
        f"zwo_usb.EafFocuser._move_to_locked for the shape that holds.")
    msg = str(err)
    assert str(TARGET) in msg and str(world.position) in msg, (
        f"{driver}: the error must name where it was going and where it "
        f"stopped, or the operator cannot tell a jam from a limit: {msg!r}")


async def test_a_move_that_never_settles_ends_in_a_stated_timeout(
        driver, monkeypatch, tmp_path):
    """A device that reports motion forever must not hold the caller forever.
    An autofocus sweep is thirteen of these moves; one unbounded wait strands
    the whole night with the shutter open."""
    if driver in _NO_FAULT_INJECTION:
        pytest.skip(_NO_FAULT_INJECTION[driver])
    _foc, world, err = await _move(driver, "never_settles", monkeypatch,
                                   tmp_path)
    assert not isinstance(err, (asyncio.TimeoutError, TimeoutError)), (
        f"{driver}: move_to never returned — the wait loop has no deadline of "
        f"its own, so a device stuck reporting 'moving' hangs the caller "
        f"(it polled {world.polls} times inside {CASE_BUDGET_S:.0f}s)")
    assert isinstance(err, DeviceError), (
        f"{driver}: a move that never settled reported success")
    assert str(TARGET) in str(err), str(err)


@pytest.mark.parametrize("behaviour", ["never_moves", "never_settles"])
async def test_a_failed_move_leaves_the_motor_stopped(driver, behaviour,
                                                      monkeypatch, tmp_path):
    """Halt on any abnormal exit. A driver that raises while the motor is still
    commanded leaves the drawtube travelling into whatever is at the end of it,
    with the caller already gone."""
    if driver in _NO_FAULT_INJECTION:
        pytest.skip(_NO_FAULT_INJECTION[driver])
    _foc, world, err = await _move(driver, behaviour, monkeypatch, tmp_path)
    assert isinstance(err, DeviceError)
    assert world.halted >= 1, (
        f"{driver}: move_to raised without halting the focuser")


# ------------------------------------------------------- the suite's own guards

def test_every_focuser_in_the_tree_has_a_bench():
    """The corpus is only worth what it covers. A new ``Focuser`` subclass that
    joins the product without a bench would silently inherit none of this."""
    import importlib
    import pkgutil

    import astrodeck.devices as devices_pkg
    from astrodeck.devices.base import Focuser

    seen: set[str] = set()
    for mod in pkgutil.walk_packages(devices_pkg.__path__,
                                     devices_pkg.__name__ + "."):
        try:
            m = importlib.import_module(mod.name)
        except Exception:            # optional SDKs (libasi, comtypes) absent
            continue
        for obj in vars(m).values():
            if (isinstance(obj, type) and issubclass(obj, Focuser)
                    and obj is not Focuser
                    and obj.__module__ == m.__name__):
                seen.add(obj.__name__)
    covered = {"SimFocuser", "AlpacaFocuser", "NinaFocuser", "EafFocuser",
               "AsiairFocuser"}
    missing = sorted(seen - covered)
    assert missing == [], (
        f"these focuser drivers have no bench in BENCHES, so nothing checks "
        f"that a move which did not happen raises: {missing}")


def test_the_bench_list_and_the_skip_list_stay_honest():
    assert set(BENCHES) >= {"alpaca", "nina", "zwo-usb-eaf", "asiair"}
    assert set(_NO_FAULT_INJECTION) <= {"sim"}, (
        "a REAL driver was excused from fault injection — that excuse is the "
        "same claim ('this one is fine') that left two drivers broken")
    for name, why in _NO_FAULT_INJECTION.items():
        assert name in BENCHES and why.strip()
