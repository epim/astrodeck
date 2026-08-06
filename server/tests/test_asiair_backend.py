"""ASIAIR backend — hardware-free tests against a fake libasi client.

THERE IS NO ASIAIR ON THIS NETWORK, and none of these tests pretend otherwise.
What they DO pin is everything that is ours to get right regardless of the box:

  * the role mapping (only the four roles the backend can actually drive);
  * the async wrapping (every blocking libasi call lands on a worker thread);
  * the single-lock discipline (no two coroutines inside the transport at once);
  * busy/contention behaviour (a stated-reason refusal; abort paths never gated);
  * settle/read-back honesty (no success return on an unverified write, no
    "slew complete" for a mount that never stopped -- or never started, and no
    "moved" for a focuser that never left where it was);
  * degradation with libasi absent (backend simply not offered).

What they CANNOT pin is the ASIAIR's own wire behaviour. The fake answers the
way ``CONFIRMED_FORMATS.md`` says the box does; if the real box differs, these
tests will still pass. That limit is stated here rather than papered over.

Fake-double style follows tests/test_zwo_usb.py's FakeEafSdk/FakeCaaSdk.
"""
from __future__ import annotations

import asyncio
import io
import threading
from types import SimpleNamespace

import numpy as np
import pytest

import astrodeck.devices.backends.asiair_backend as ab
from astrodeck.devices.backend import ROLES
from astrodeck.devices.base import DeviceError, PierSide


# ----------------------------------------------------------------- the double

class BusyError(Exception):
    """Stands in for ``asiair.transport.BusyError``.

    Named and shaped EXACTLY like libasi's (same class name, same
    ``activity``/``requested`` attributes, same message) — that is what makes it
    a faithful double rather than a convenient one, and it is what
    ``ab._is_busy`` structurally matches on when libasi is not installed."""

    def __init__(self, activity: str, requested: str):
        self.activity = activity
        self.requested = requested
        super().__init__(f"Cannot {requested}: ASIAIR is busy ({activity})")


class FakeAsiair:
    """Scripted ASIAIR client double.

    Records every call, the THREAD each ran on (so the async wrapping is
    provable), and asserts NON-REENTRANCY: if two coroutines are ever inside the
    transport at once the second one raises, which is precisely the corruption
    the session's single lock exists to prevent."""

    MOUNT_RAW = {
        "RA": 5.9, "Dec": 32.5, "Alt": 70.0, "Az": 263.0,
        "slew_rate_list": ["1x", "2x", "4x", "8x", "20x", "60x", "MAX/2", "MAX"],
        "slew_rate_index": 7, "guide_rate": 0.5, "is_enable_track": True,
        "pier_side": "east", "track_mode_list": ["Sidereal", "Solar", "Lunar"],
        "track_mode_index": 0, "park_status": "unparked",
        "caps": ["position", "park", "sync", "move", "goto", "ctrl_track",
                 "track_mode", "guide_rate"],
        "fw_ver": "1.8.8", "model": "ZWO AM5N", "move_status": "none",
        "is_parking": False,
    }

    def __init__(self, *, activity: dict | None = None, busy_on: set | None = None):
        self.calls: list[tuple] = []
        self.threads: set[int] = set()
        self._inside = 0
        self._reentered = False
        self.connected = False
        self.raw = dict(self.MOUNT_RAW)
        self._activity = activity or {}
        self._busy_on = busy_on or set()     # method labels that raise BusyError
        self.idle_checks: list[str] = []
        self.deleted: list[str] = []
        self.saved = 0
        self.files: set[str] = set()
        self.new_files_on_save: list[str] = ["Light_0001.fit"]
        self.fits_bytes = b""
        self.ports = [SimpleNamespace(index=i, port_type="other", on=False,
                                      value=0.0, is_pwm=(i == 3))
                      for i in range(4)]
        self.port_write_honoured = True
        self.focus_states: list[str] = []
        self.focus_position = 18500
        #: Scripted read-back, one entry per ``focuser.info`` poll. Empty means
        #: the drawtube lands the instant ``move_to`` is called; a script is how
        #: a test spells a motor that ramps, jams, or never engages at all.
        self.focus_positions: list[int] = []
        self.controls_written: dict[str, int] = {}
        self.wired()

    # -- bookkeeping -------------------------------------------------------

    def _rec(self, label, *args, **kw):
        if self._inside:
            self._reentered = True
            raise AssertionError(f"reentrant ASIAIR transport call: {label}")
        self._inside += 1
        try:
            self.threads.add(threading.get_ident())
            self.calls.append((label, args, kw))
            if label in self._busy_on:
                raise BusyError("capture/autorun in progress", label)
        finally:
            self._inside -= 1

    @property
    def labels(self) -> list[str]:
        return [c[0] for c in self.calls]

    # -- client surface ----------------------------------------------------

    def connect(self, heartbeat=True):
        self._rec("connect", heartbeat)
        self.connected = True

    def disconnect(self):
        self._rec("disconnect")
        self.connected = False

    def get_activity(self):
        self._rec("get_activity")
        return dict(self._activity)

    def check_idle(self, requested, *, allow_guiding=False, allow_capturing=False):
        self._rec("check_idle", requested)
        self.idle_checks.append(requested)
        act = self._activity
        conflicts = []
        if act.get("auto_goto"):
            conflicts.append("auto_goto in progress")
        if act.get("capturing") and not allow_capturing:
            conflicts.append("capture/autorun in progress")
        if act.get("guiding") and not allow_guiding:
            conflicts.append("guiding")
        if act.get("auto_focusing"):
            conflicts.append("auto focus in progress")
        if conflicts:
            raise BusyError(", ".join(conflicts), requested)

    def wired(self):
        fake = self

        class _Mount:
            def info(self):
                fake._rec("mount.info")
                return SimpleNamespace(raw=dict(fake.raw))

            def scope_goto(self, ra, dec, force=False):
                fake._rec("mount.scope_goto", ra, dec)
                fake.raw["move_status"] = "goto"

            def sync(self, ra, dec):
                fake._rec("mount.sync", ra, dec)

            def set_tracking(self, on):
                fake._rec("mount.set_tracking", on)
                fake.raw["is_enable_track"] = bool(on)

            def set_track_mode(self, idx):
                fake._rec("mount.set_track_mode", idx)
                fake.raw["track_mode_index"] = idx

            def park(self, force=False):
                fake._rec("mount.park")
                fake.raw["park_status"] = "parked"

            def unpark(self):
                fake._rec("mount.unpark")
                fake.raw["park_status"] = "unparked"

            def stop(self):
                fake._rec("mount.stop")
                fake.raw["move_status"] = "none"

            def set_slew_rate(self, idx):
                fake._rec("mount.set_slew_rate", idx)

            def move(self, direction, seconds, force=False):
                fake._rec("mount.move", direction, seconds)

        class _Focuser:
            def info(self):
                fake._rec("focuser.info")
                state = (fake.focus_states.pop(0) if fake.focus_states
                         else "idle")
                if fake.focus_positions:
                    fake.focus_position = fake.focus_positions.pop(0)
                return SimpleNamespace(position=fake.focus_position,
                                       temperature=18.2, max_step=60000,
                                       model="EAF-0-0", firmware="3.3.8",
                                       state=state)

            def move_to(self, pos):
                fake._rec("focuser.move_to", pos)
                if not fake.focus_positions:
                    fake.focus_position = pos      # no script: lands instantly

            def stop(self):
                fake._rec("focuser.stop")

        class _Camera:
            def info(self):
                fake._rec("camera.info")
                return SimpleNamespace(name="ASI2600MC", chip_size=(6248, 4176),
                                       pixel_size_um=3.76, bins=[1, 2, 3, 4],
                                       has_cooler=True, is_color=True,
                                       debayer_pattern="RG")

            def controls(self):
                fake._rec("camera.controls")
                names = ["Gain", "Exposure", "Offset", "Temperature",
                         "CoolPowerPerc", "TargetTemp", "CoolerOn",
                         "AntiDewHeater"]
                return [SimpleNamespace(name=n, min_val=0, max_val=300,
                                        read_only=False) for n in names]

            def get_control(self, name):
                fake._rec("camera.get_control", name)
                return {"Temperature": -102, "CoolerOn": 1,
                        "CoolPowerPerc": 42, "TargetTemp": -10}.get(name, 0)

            def set_control(self, name, value):
                fake._rec("camera.set_control", name, value)
                fake.controls_written[name] = value

            def start_exposure(self, frame_type, save, force=False):
                fake._rec("camera.start_exposure", frame_type, save)

            def stop_exposure(self, keep):
                fake._rec("camera.stop_exposure", keep)

        class _CameraWithBin(_Camera):
            @property
            def bin(self):
                return 1

            @bin.setter
            def bin(self, value):
                fake._rec("camera.bin", value)

        class _Images:
            def list_filenames(self, path):
                fake._rec("images.list_filenames", path)
                return set(fake.files)

            def save_current(self):
                fake._rec("images.save_current")
                fake.saved += 1
                fake.files |= set(fake.new_files_on_save)

            def fetch_fits(self, path):
                fake._rec("images.fetch_fits", path)
                return fake.fits_bytes

            def delete(self, path):
                fake._rec("images.delete", path)
                fake.deleted.append(path)
                fake.files.discard(path.split("/")[-1])

        class _Power:
            def get_ports(self):
                fake._rec("power.get_ports")
                return [SimpleNamespace(index=p.index, port_type=p.port_type,
                                        on=p.on, value=p.value, is_pwm=p.is_pwm)
                        for p in fake.ports]

            def set_port(self, idx, on=True, value=100.0, port_type=None):
                fake._rec("power.set_port", idx, on, value)
                if fake.port_write_honoured:
                    fake.ports[idx].on = bool(on)
                    fake.ports[idx].value = float(value)

        class _System:
            def info(self):
                fake._rec("system.info")
                return SimpleNamespace(model="ASIAIR Plus", cpu_temp=41.0)

        self.mount = _Mount()
        self.focuser = _Focuser()
        self.camera = _CameraWithBin()
        self.images = _Images()
        self.power = _Power()
        self.system = _System()


def _fits_bytes(width: int = 8, height: int = 6, *, bayer: str = "RGGB",
                temp: float = -9.8) -> bytes:
    """A tiny real FITS file, so the decode path is exercised for real."""
    from astropy.io import fits
    data = (np.arange(width * height, dtype=np.uint16)
            .reshape(height, width) * 7)
    hdu = fits.PrimaryHDU(data)
    hdu.header["BAYERPAT"] = bayer
    hdu.header["CCD-TEMP"] = temp
    hdu.header["EGAIN"] = 0.25
    buf = io.BytesIO()
    fits.HDUList([hdu]).writeto(buf)
    return buf.getvalue()


@pytest.fixture
def fake(monkeypatch) -> FakeAsiair:
    f = FakeAsiair()
    f.fits_bytes = _fits_bytes()
    monkeypatch.setattr(ab, "make_client", lambda host, timeout=10.0: f)
    return f


async def _open(conn_extra: dict | None = None) -> ab.AsiairSession:
    conn = SimpleNamespace(host="asiair.invalid", port=4700,
                           extra=dict(conn_extra or {}))
    return await ab.AsiairBackend().open(conn)


def _conn(**extra):
    return SimpleNamespace(host="asiair.invalid", port=4700, extra=dict(extra))


# ---------------------------------------------------- registration / absence

def test_libasi_is_not_installed_here():
    """The premise of the degradation tests below. If this ever fails, the
    environment gained libasi and those tests are no longer proving absence."""
    assert ab.libasi_available() is False


def test_register_all_registers_nothing_without_libasi(monkeypatch):
    from astrodeck.devices.backend import BACKENDS
    monkeypatch.setattr(ab, "libasi_available", lambda: False)
    before = dict(BACKENDS)
    ab.register_all()
    assert dict(BACKENDS) == before, "asiair must not register without libasi"
    assert "asiair" not in BACKENDS


def test_absent_libasi_leaves_the_driver_type_unofferable(monkeypatch):
    """A user with no ASIAIR sees no change: the type is not configurable, so
    POST /api/config/drivers rejects it (app.py gates on this exact set)."""
    from astrodeck import drivers
    monkeypatch.setattr(ab, "libasi_available", lambda: False)
    ab.register_all()
    assert "asiair" not in drivers.configurable_driver_types()


def test_make_client_without_libasi_raises_an_actionable_error():
    with pytest.raises(DeviceError) as ei:
        ab.make_client("asiair.invalid")
    msg = str(ei.value)
    assert "libasi" in msg and "not on PyPI" in msg


def test_register_all_registers_when_libasi_is_available(monkeypatch):
    from astrodeck.devices.backend import BACKENDS, get_backend
    monkeypatch.setattr(ab, "libasi_available", lambda: True)
    monkeypatch.setitem(BACKENDS, "__probe__", object())   # snapshot marker
    BACKENDS.pop("__probe__")
    try:
        ab.register_all()
        b = get_backend("asiair")
        assert b.name == "asiair" and b.driver_type == "asiair"
        assert b.hardware is True and b.transport == "network"
        assert b.discoverable is False and b.hostless is False
        from astrodeck import __version__
        assert b.version == __version__
    finally:
        BACKENDS.pop("asiair", None)


def test_advertised_roles_are_exactly_the_ones_it_can_serve():
    """The registry drift guard, applied to this backend: every advertised role
    maps to a real device factory, and nothing it refuses is advertised."""
    b = ab.AsiairBackend()
    assert set(b.roles) == set(ab._ROLE_FACTORIES)
    for role in b.roles:
        assert role in ROLES, f"asiair advertises non-canonical role {role!r}"
    for refused in ("rotator", "filterwheel", "guider", "safety", "dome",
                    "covercalibrator", "guide_camera"):
        assert refused not in b.roles


async def test_unserved_role_refuses_with_a_reason(fake):
    session = await _open()
    with pytest.raises(DeviceError) as ei:
        await session.get_device("rotator", _conn())
    assert "fills only" in str(ei.value)
    await session.close()


async def test_open_without_a_host_refuses():
    with pytest.raises(DeviceError) as ei:
        await ab.AsiairBackend().open(SimpleNamespace(host="", extra={}))
    assert "IP address" in str(ei.value)


async def test_discover_is_empty_and_honest():
    assert await ab.AsiairBackend().discover() == []


# ------------------------------------------------------------ async wrapping

async def test_every_blocking_call_runs_off_the_event_loop(fake):
    """asyncio.to_thread, not a blocking socket read on the loop: a stalled
    ASIAIR must never freeze the WebSocket UI."""
    loop_thread = threading.get_ident()
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    await tel.get_position()
    await session.close()
    assert fake.threads, "no libasi calls recorded"
    assert loop_thread not in fake.threads


async def test_the_event_loop_keeps_running_during_a_slow_rpc(fake, monkeypatch):
    """A blocking call must not stop other coroutines from making progress."""
    real_info = fake.mount.info
    gate = threading.Event()

    def slow_info():
        gate.wait(2.0)
        return real_info()

    monkeypatch.setattr(fake.mount, "info", slow_info)
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    ticks = 0

    async def ticker():
        nonlocal ticks
        for _ in range(5):
            await asyncio.sleep(0.01)
            ticks += 1
        gate.set()

    await asyncio.gather(tel.get_position(), ticker())
    assert ticks == 5
    await session.close()


# --------------------------------------------------------------- concurrency

async def test_roles_share_one_client_and_one_lock(fake):
    """One stateful box, one connection: every role hands back devices over the
    SAME client, and concurrent use never overlaps inside the transport."""
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    foc = await session.get_device("focuser", _conn())
    sw = await session.get_device("switch", _conn())
    assert tel.link is foc.link is sw.link is session.link
    assert tel.link.client is fake

    await asyncio.gather(*(tel.get_position() for _ in range(6)),
                         *(foc.get_position() for _ in range(6)),
                         *(sw.get_ports() for _ in range(6)))
    assert fake._reentered is False
    await session.close()


async def test_a_role_device_is_cached_per_session(fake):
    session = await _open()
    a = await session.get_device("telescope", _conn())
    b = await session.get_device("telescope", _conn())
    assert a is b
    await session.close()


# ------------------------------------------------------------ busy/contention

async def test_slew_refuses_while_the_box_is_capturing_and_says_so(monkeypatch):
    f = FakeAsiair(activity={"capturing": True})
    monkeypatch.setattr(ab, "make_client", lambda host, timeout=10.0: f)
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    with pytest.raises(DeviceError) as ei:
        await tel.slew(1.0, 2.0)
    msg = str(ei.value)
    assert "busy" in msg and "capture/autorun in progress" in msg
    assert "ASIAIR app" in msg, "the refusal must say where to stop it"
    # and the refusal is REAL: no slew command reached the box.
    assert "mount.scope_goto" not in f.labels
    await session.close()


async def test_exposure_refuses_while_the_box_is_autofocusing(monkeypatch):
    f = FakeAsiair(activity={"auto_focusing": True})
    f.fits_bytes = _fits_bytes()
    monkeypatch.setattr(ab, "make_client", lambda host, timeout=10.0: f)
    session = await _open()
    cam = await session.get_device("camera", _conn())
    with pytest.raises(DeviceError) as ei:
        await cam.expose(0.01, 100, 10)
    assert "auto focus in progress" in str(ei.value)
    assert "camera.start_exposure" not in f.labels
    await session.close()


async def test_guiding_does_not_block_an_exposure_or_a_jog(monkeypatch):
    """Guiding stays in the ASIAIR; it must not veto the things that coexist
    with it, or an ASIAIR rig could never image while guided."""
    f = FakeAsiair(activity={"guiding": True})
    f.fits_bytes = _fits_bytes()
    monkeypatch.setattr(ab, "make_client", lambda host, timeout=10.0: f)
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    await tel.move_axis("ra", 0.05)
    assert "mount.move" in f.labels
    cam = await session.get_device("camera", _conn())
    await cam.expose(0.01, 100, 10)
    assert "camera.start_exposure" in f.labels
    await session.close()


async def test_abort_paths_are_never_gated_on_the_box_being_idle(monkeypatch):
    """The worst possible failure would be an emergency stop refused because
    the box was busy. stop / halt / abort_exposure must never idle-check."""
    f = FakeAsiair(activity={"capturing": True, "auto_goto": True,
                             "auto_focusing": True})
    monkeypatch.setattr(ab, "make_client", lambda host, timeout=10.0: f)
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    foc = await session.get_device("focuser", _conn())
    cam = await session.get_device("camera", _conn())
    f.idle_checks.clear()

    await tel.stop()
    await tel.move_axis("ra", 0.0)          # rate 0 IS a stop
    await foc.halt()
    await cam.abort_exposure()

    assert f.idle_checks == [], "an abort must not be gated"
    assert "mount.stop" in f.labels
    assert "focuser.stop" in f.labels
    assert "camera.stop_exposure" in f.labels
    await session.close()


def test_busy_detection_is_structural_not_isinstance():
    assert ab._is_busy(BusyError("guiding", "mount.goto")) is True
    assert ab._is_busy(RuntimeError("boom")) is False
    assert ab._is_busy(ValueError("BusyError")) is False


# ----------------------------------------------------------------- telescope

async def test_telescope_reads_capabilities_from_the_box(fake):
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    assert tel.name == "ZWO AM5N (ASIAIR)"
    assert tel.can_set_tracking_rate is True
    assert tel.can_pulse_guide is False          # ASIAIR exposes no pulse guide
    assert tel.reports_destination_pier_side is False
    assert await tel.get_position() == (5.9, 32.5)
    assert await tel.get_tracking() is True
    assert await tel.is_parked() is False
    assert await tel.pier_side() is PierSide.EAST
    assert await tel.get_tracking_rate() == "sidereal"
    rates = await tel.guide_rates()
    assert rates == pytest.approx((0.5 * ab.SIDEREAL_DEG_S,) * 2)
    await session.close()


async def test_pulse_guide_still_refuses(fake):
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    with pytest.raises(DeviceError):
        await tel.pulse_guide("north", 200)
    await session.close()


async def test_missing_capability_refuses_instead_of_failing_mid_slew(fake):
    fake.raw["caps"] = ["position"]            # a mount that cannot goto/park
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    with pytest.raises(DeviceError) as ei:
        await tel.slew(1.0, 2.0)
    assert "does not support GoTo" in str(ei.value)
    with pytest.raises(DeviceError):
        await tel.park()
    assert "mount.scope_goto" not in fake.labels
    await session.close()


async def test_slew_waits_for_the_mount_to_actually_stop(fake, monkeypatch):
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    session = await _open()
    tel = await session.get_device("telescope", _conn())

    seen = {"n": 0}
    base_info = fake.mount.info

    def moving_then_settled():
        seen["n"] += 1
        if seen["n"] >= 4:                      # box reports motion for a while
            fake.raw["move_status"] = "none"
            fake.raw["RA"], fake.raw["Dec"] = 6.0, 32.0     # arrived
        else:
            fake.raw["RA"] = 5.9 + 0.1 * seen["n"]
        return base_info()

    monkeypatch.setattr(fake.mount, "info", moving_then_settled)
    await tel.slew(6.0, 32.0)
    assert "mount.scope_goto" in fake.labels
    assert seen["n"] >= 5, "returned before two consecutive stopped samples"
    await session.close()


async def test_a_mount_that_never_left_the_origin_is_not_reported_as_settled(
        fake, monkeypatch):
    """The defect this closes: a stationary mount is stopped from the first
    poll, so "stopped twice running" was satisfied before the motors ever
    engaged and a slew that never happened returned SUCCESS. Settled now means
    settled AT THE TARGET, so a mount still sitting on the origin keeps polling
    until the honest timeout."""
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    monkeypatch.setattr(ab, "SLEW_TIMEOUT_S", 0.05)

    def never_starts():
        raw = dict(fake.raw)
        raw["move_status"] = "none"      # the box insists the mount is stopped
        return SimpleNamespace(raw=raw)  # ...at RA 5.9 / Dec 32.5, forever

    monkeypatch.setattr(fake.mount, "info", never_starts)
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    with pytest.raises(DeviceError) as ei:
        await tel.slew(12.0, -20.0)
    msg = str(ei.value)
    assert "did not settle" in msg
    # Both numbers, so the message explains itself: where it is AND where it
    # was asked to go.
    assert "5.9" in msg and "32.5" in msg and "12.0" in msg and "-20.0" in msg
    assert "mount.stop" in fake.labels
    await session.close()


async def test_a_missing_move_status_needs_more_evidence_not_less(
        fake, monkeypatch):
    """A field that is not there is not a measurement. On firmware that omits
    ``move_status`` the wait has ONE signal (position stability), so it must
    demand a longer stable run rather than counting the absent field as a
    stopped sample."""
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    polls = []

    def no_status():
        raw = dict(fake.raw)
        raw.pop("move_status", None)             # older firmware: field absent
        raw["RA"], raw["Dec"] = 5.9, 32.5        # already parked on the target
        polls.append(1)
        return SimpleNamespace(raw=raw)

    monkeypatch.setattr(fake.mount, "info", no_status)
    await tel.slew(5.9, 32.5)
    assert len(polls) >= ab.STOPPED_SAMPLES_NO_STATUS > ab.STOPPED_SAMPLES, (
        f"settled on {len(polls)} samples with no move_status to corroborate")
    await session.close()


async def test_slew_that_never_settles_times_out_and_stops_the_mount(
        fake, monkeypatch):
    """No lie about a completed slew, and never left driving."""
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    monkeypatch.setattr(ab, "SLEW_TIMEOUT_S", 0.05)
    fake.raw["move_status"] = "goto"

    def stuck():
        return SimpleNamespace(raw=dict(fake.raw))     # never clears

    monkeypatch.setattr(fake.mount, "info", stuck)
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    with pytest.raises(DeviceError) as ei:
        await tel.slew(6.0, 32.0)
    assert "did not settle" in str(ei.value)
    assert "mount.stop" in fake.labels
    await session.close()


async def test_a_drifting_mount_is_not_reported_as_settled(fake, monkeypatch):
    """``move_status``'s in-motion vocabulary was never wire-captured, so the
    stop detector must not trust it alone: a mount whose RA/Dec keeps changing
    is still moving even while the box says "none"."""
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    monkeypatch.setattr(ab, "SLEW_TIMEOUT_S", 0.05)
    fake.raw["move_status"] = "none"
    n = {"i": 0}

    def drifting():
        n["i"] += 1
        fake.raw["RA"] = 5.9 + 0.5 * n["i"]     # far beyond SETTLE_EPS_DEG
        return SimpleNamespace(raw=dict(fake.raw))

    monkeypatch.setattr(fake.mount, "info", drifting)
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    with pytest.raises(DeviceError) as ei:
        await tel.slew(6.0, 32.0)
    assert "did not settle" in str(ei.value)
    await session.close()


def test_unknown_move_status_reads_as_moving_not_settled():
    assert ab._is_moving({"move_status": "none"}) is False
    assert ab._is_moving({"move_status": ""}) is False
    assert ab._is_moving({"move_status": "slewing"}) is True
    assert ab._is_moving({"move_status": "whatever-firmware-says"}) is True
    assert ab._is_moving({"move_status": "none", "is_parking": True}) is True


def test_an_absent_move_status_reads_as_unknown_not_as_stopped():
    """Tri-state. ``raw.get("move_status", "none")`` used to manufacture a
    stopped reading out of a field the box never sent."""
    assert ab._is_moving({}) is None
    assert ab._is_moving({"RA": 5.9, "Dec": 32.5}) is None
    # is_parking is its own signal and still wins when the field is missing.
    assert ab._is_moving({"is_parking": True}) is True


async def test_park_waits_for_parked_and_times_out_honestly(fake, monkeypatch):
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    await tel.park()
    assert await tel.is_parked() is True

    monkeypatch.setattr(ab, "PARK_TIMEOUT_S", 0.02)
    fake.raw["park_status"] = "unparked"
    monkeypatch.setattr(fake.mount, "park", lambda force=False: None)
    with pytest.raises(DeviceError) as ei:
        await tel.park()
    assert "park did not complete" in str(ei.value)
    await session.close()


async def test_tracking_rate_maps_onto_the_boxs_own_mode_list(fake):
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    await tel.set_tracking_rate("lunar")
    assert ("mount.set_track_mode", (2,), {}) in fake.calls   # Lunar is index 2
    assert await tel.get_tracking_rate() == "lunar"
    with pytest.raises(DeviceError):
        await tel.set_tracking_rate("king")
    await session.close()


async def test_tracking_rate_refuses_when_the_mount_lacks_the_mode(fake):
    fake.raw["track_mode_list"] = ["Sidereal"]
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    with pytest.raises(DeviceError) as ei:
        await tel.set_tracking_rate("solar")
    assert "offers no Solar rate" in str(ei.value)
    await session.close()


def test_jog_rate_never_selects_the_unknown_MAX_presets():
    """MAX/MAX-2 are mount-specific and unreported; picking one to satisfy a
    0.3 deg/s jog would slew orders of magnitude faster than asked."""
    rates = ["1x", "2x", "4x", "8x", "20x", "60x", "MAX/2", "MAX"]
    for want in (0.001, 0.01, 0.1, 0.6, 5.0, 1e6):
        idx = ab.pick_slew_rate_index(rates, want)
        assert rates[idx].endswith("x") and "MAX" not in rates[idx]
    assert ab.pick_slew_rate_index(rates, 0.0001) == 0        # slowest preset
    assert rates[ab.pick_slew_rate_index(rates, 60 * ab.SIDEREAL_DEG_S)] == "60x"
    assert rates[ab.pick_slew_rate_index(rates, 21 * ab.SIDEREAL_DEG_S)] == "20x"
    assert ab.pick_slew_rate_index([], 0.5) == 0


async def test_jog_direction_matches_the_ascom_axis_convention(fake):
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    for axis, positive, direction in (("ra", 1, "east"), ("ra", -1, "west"),
                                      ("dec", 1, "north"), ("dec", -1, "south")):
        fake.calls.clear()
        await tel.move_axis(axis, positive * 0.05)
        moves = [c for c in fake.calls if c[0] == "mount.move"]
        assert moves and moves[0][1][0] == direction
        assert moves[0][1][1] == ab.JOG_DURATION_S
    with pytest.raises(DeviceError):
        await tel.move_axis("alt", 0.1)
    await session.close()


async def test_disconnect_never_touches_the_boxs_mount(fake):
    """Bridge semantics: closing AstroDeck's view must not park, stop tracking
    or otherwise reconfigure equipment the ASIAIR owns."""
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    cam = await session.get_device("camera", _conn())
    fake.calls.clear()
    await session.close()
    assert tel.connected is False and cam.connected is False
    assert fake.labels == ["disconnect"], fake.labels
    assert fake.connected is False


# ------------------------------------------------------------------- focuser

async def test_focuser_reads_limits_from_the_box(fake):
    session = await _open()
    foc = await session.get_device("focuser", _conn())
    assert foc.max_position == 60000
    assert foc.name == "EAF-0-0 (ASIAIR)"
    assert await foc.get_position() == 18500
    assert await foc.get_temperature() == pytest.approx(18.2)
    assert foc.supports_native_autofocus is False
    await session.close()


async def test_focuser_move_returns_only_once_the_position_arrives(
        fake, monkeypatch):
    """ARRIVAL, not absence-of-motion. The drawtube ramps and the box reports
    "idle" for a poll in the middle of it; neither may end the wait early —
    only reaching the commanded step does."""
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    session = await _open()
    foc = await session.get_device("focuser", _conn())
    # Scripted after connect, so the entries below are exactly the move's own
    # polls: one read for the start position, then the ramp.
    fake.focus_states = ["moving", "idle", "idle", "moving", "moving", "idle"]
    fake.focus_positions = [18500, 18600, 18700, 18800, 18900, 19000]
    await foc.move_to(19000)
    assert fake.focus_position == 19000
    assert fake.focus_positions == [], "returned before the drawtube arrived"
    await session.close()


async def test_focuser_move_that_never_happens_fails_instead_of_succeeding(
        fake, monkeypatch):
    """The 2026-07-31 defect, in this backend: the EAF refuses the move (a
    mechanical stop, a jam), the box reports idle, and the old loop returned
    SUCCESS on the second idle poll without ever comparing position to target.
    A move that did not move must never look like a move that did."""
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    session = await _open()
    foc = await session.get_device("focuser", _conn())
    fake.focus_states = ["idle"] * 50
    fake.focus_positions = [18500] * 50          # drawtube does not budge
    with pytest.raises(DeviceError) as ei:
        await foc.move_to(22000)
    msg = str(ei.value)
    assert "did not happen" in msg
    assert "22000" in msg and "18500" in msg, msg   # asked for, and reached
    assert "focuser.stop" in fake.labels            # halted on the way out
    await session.close()


async def test_focuser_move_tolerates_an_off_by_one_read_back(fake, monkeypatch):
    """The EAF is an exact-step device, so the tolerance guards an SDK/firmware
    read-back off-by-one — not a real slop budget. Same 2 steps zwo_usb.py and
    ui/src/lib/focusMove.ts use."""
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    assert ab.ARRIVAL_TOLERANCE_STEPS == 2
    session = await _open()
    foc = await session.get_device("focuser", _conn())
    fake.focus_states = ["idle"] * 50
    fake.focus_positions = [18999] * 50          # one step short, forever
    await foc.move_to(19000)
    await session.close()


async def test_focuser_move_out_of_range_refuses_before_touching_the_box(fake):
    session = await _open()
    foc = await session.get_device("focuser", _conn())
    fake.calls.clear()
    with pytest.raises(DeviceError) as ei:
        await foc.move_to(999_999)
    assert "out of range" in str(ei.value)
    assert "focuser.move_to" not in fake.labels
    await session.close()


async def test_focuser_move_timeout_halts(fake, monkeypatch):
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    monkeypatch.setattr(ab, "FOCUS_MOVE_TIMEOUT_S", 0.02)
    session = await _open()
    foc = await session.get_device("focuser", _conn())
    fake.focus_states = ["moving"] * 10_000
    # Crawling, and still short of 19000 when the clock runs out — the idle
    # branch must not fire, so this is the timeout path proper.
    fake.focus_positions = [18500 + i for i in range(10_000)]
    with pytest.raises(DeviceError) as ei:
        await foc.move_to(19000)
    msg = str(ei.value)
    assert "did not settle" in msg
    assert "stopped at" in msg and "started from 18500" in msg, msg
    assert "focuser.stop" in fake.labels
    await session.close()


# -------------------------------------------------------------------- switch

async def test_switch_maps_pwm_and_boolean_ports(fake):
    session = await _open()
    sw = await session.get_device("switch", _conn())
    ports = await sw.get_ports()
    assert [p.id for p in ports] == [0, 1, 2, 3]
    assert ports[0].is_boolean is True and ports[0].max == 1.0
    assert ports[3].is_boolean is False and ports[3].unit == "%"
    await session.close()


async def test_switch_write_is_read_back_verified(fake):
    session = await _open()
    sw = await session.get_device("switch", _conn())
    await sw.set_port(3, 60.0)
    assert fake.ports[3].on is True and fake.ports[3].value == 60.0
    await sw.set_port(0, 1.0)
    assert fake.ports[0].on is True
    await session.close()


async def test_switch_write_that_does_not_take_is_reported_not_swallowed(fake):
    """The highest-severity defect class: a heater that reads ON while it is
    stone cold. A write the box did not honour must RAISE."""
    fake.port_write_honoured = False
    session = await _open()
    sw = await session.get_device("switch", _conn())
    with pytest.raises(DeviceError) as ei:
        await sw.set_port(3, 100.0)
    assert "did not switch on" in str(ei.value)
    await session.close()


async def test_switch_level_that_does_not_take_is_reported(fake):
    session = await _open()
    sw = await session.get_device("switch", _conn())

    def lying_set(idx, on=True, value=100.0, port_type=None):
        fake.ports[idx].on = bool(on)
        fake.ports[idx].value = 5.0          # box ignored the requested level

    fake.power.set_port = lying_set
    with pytest.raises(DeviceError) as ei:
        await sw.set_port(3, 80.0)
    assert "did not take level" in str(ei.value)
    await session.close()


async def test_switch_unknown_port_refuses(fake):
    session = await _open()
    sw = await session.get_device("switch", _conn())
    with pytest.raises(DeviceError) as ei:
        await sw.set_port(9, 1.0)
    assert "no DC output port 9" in str(ei.value)
    await session.close()


# -------------------------------------------------------------------- camera

async def test_camera_connect_reads_real_capabilities(fake):
    session = await _open()
    cam = await session.get_device("camera", _conn())
    assert (cam.sensor_width, cam.sensor_height) == (6248, 4176)
    assert cam.pixel_size_um == pytest.approx(3.76)
    assert cam.max_bin == 4 and cam.can_cool is True
    assert cam.bayer_pattern == "RGGB"          # "RG" expanded, not guessed
    assert cam.has_dew_heater is True and cam.can_report_cooler_power is True
    await session.close()


async def test_mono_camera_gets_no_bayer_pattern(fake):
    fake.camera.info = lambda: SimpleNamespace(
        name="ASI1600MM", chip_size=(4656, 3520), pixel_size_um=3.8,
        bins=[1, 2], has_cooler=True, is_color=False, debayer_pattern="RG")
    session = await _open()
    cam = await session.get_device("camera", _conn())
    assert cam.bayer_pattern is None
    await session.close()


def test_bayer_expansion_never_guesses():
    assert ab._expand_bayer("RG") == "RGGB"
    assert ab._expand_bayer("bg") == "BGGR"
    assert ab._expand_bayer("GRBG") == "GRBG"
    assert ab._expand_bayer("") is None
    assert ab._expand_bayer("XY") is None       # unknown -> None, not a guess


async def test_expose_downloads_a_real_fits_frame(fake, monkeypatch):
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    session = await _open()
    cam = await session.get_device("camera", _conn())
    frame = await cam.expose(0.01, 120, 30, binning=2, light=True, save=True)

    assert frame.data.shape == (6, 8) and frame.data.dtype == np.uint16
    assert frame.data_is_linear is True          # real sensor data, not a render
    assert frame.bayer_pattern == "RGGB"
    assert frame.temperature_c == pytest.approx(-9.8)
    assert frame.egain_e_per_adu == pytest.approx(0.25)
    assert frame.gain == 120 and frame.offset == 30 and frame.binning == 2
    assert frame.saved_path == "Preview/Light_0001.fit"

    assert fake.controls_written["Exposure"] == 10_000    # seconds -> us
    assert fake.controls_written["Gain"] == 120
    assert fake.controls_written["Offset"] == 30
    assert ("camera.bin", (2,), {}) in fake.calls
    assert fake.saved == 1
    assert fake.deleted == [], "a save=True light must be left on the box"
    await session.close()


async def test_a_throwaway_preview_is_removed_from_the_box(fake, monkeypatch):
    """The live-preview loop would otherwise fill the user's SD card at one
    file per tick. Only the file we just wrote is deleted."""
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    fake.files = {"someone_elses.fit"}
    session = await _open()
    cam = await session.get_device("camera", _conn())
    frame = await cam.expose(0.01, 100, 10, save=False)
    assert fake.deleted == ["Preview/Light_0001.fit"]
    assert "someone_elses.fit" in fake.files, "never deletes a file we didn't write"
    assert frame.saved_path is None
    await session.close()


async def test_cleanup_can_be_switched_off(fake, monkeypatch):
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    session = await _open()
    cam = await session.get_device("camera", _conn(cleanup_previews=False))
    await cam.expose(0.01, 100, 10, save=False)
    assert fake.deleted == []
    await session.close()


async def test_offset_is_reported_as_applied_not_as_requested(fake, monkeypatch):
    """A camera with no Offset control must not have the frame claim an offset
    that was never set — the frame header would then lie about the calibration
    the sub was taken with."""
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    fake.camera.controls = lambda: [
        SimpleNamespace(name=n, min_val=0, max_val=300, read_only=False)
        for n in ("Gain", "Exposure")]
    session = await _open()
    cam = await session.get_device("camera", _conn())
    frame = await cam.expose(0.01, 100, 42, save=True)
    assert "Offset" not in fake.controls_written
    assert frame.offset == 0
    await session.close()


async def test_expose_refuses_to_guess_when_no_frame_appears(fake, monkeypatch):
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    fake.new_files_on_save = []
    session = await _open()
    cam = await session.get_device("camera", _conn())
    with pytest.raises(DeviceError) as ei:
        await cam.expose(0.01, 100, 10)
    assert "no new file appeared" in str(ei.value)
    await session.close()


async def test_expose_refuses_to_guess_when_another_client_is_saving(
        fake, monkeypatch):
    """Two new files means we cannot tell which frame is ours. Returning one
    anyway would hand the caller somebody else's exposure and call it theirs."""
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    fake.new_files_on_save = ["Light_0001.fit", "Light_0002.fit"]
    session = await _open()
    cam = await session.get_device("camera", _conn())
    with pytest.raises(DeviceError) as ei:
        await cam.expose(0.01, 100, 10)
    msg = str(ei.value)
    assert "2 new files" in msg and "Refusing to guess" in msg
    await session.close()


async def test_expose_gives_up_honestly_if_the_box_never_stops_capturing(
        fake, monkeypatch):
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    monkeypatch.setattr(ab, "EXPOSURE_OVERHEAD_S", 0.02)
    session = await _open()
    cam = await session.get_device("camera", _conn())

    # The box goes busy AFTER the idle check passed and never comes back — the
    # realistic "it wedged mid-frame" case, distinct from a pre-flight refusal.
    real_start = fake.camera.start_exposure

    def start_and_wedge(frame_type, save, force=False):
        real_start(frame_type, save, force=force)
        fake._activity = {"capturing": True}

    fake.camera.start_exposure = start_and_wedge
    with pytest.raises(DeviceError) as ei:
        await cam.expose(0.01, 100, 10)
    assert "still capturing" in str(ei.value)
    # and the exposure WE started is aborted, not left running on the box
    assert "camera.stop_exposure" in fake.labels
    await session.close()


async def test_cancelling_an_exposure_aborts_it_on_the_box(fake, monkeypatch):
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    session = await _open()
    cam = await session.get_device("camera", _conn())
    task = asyncio.create_task(cam.expose(5.0, 100, 10))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "camera.stop_exposure" in fake.labels
    await session.close()


async def test_custom_save_dir_is_honoured(fake, monkeypatch):
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    session = await _open()
    cam = await session.get_device("camera", _conn(save_dir="Autorun/Light"))
    frame = await cam.expose(0.01, 100, 10, save=True)
    assert frame.saved_path == "Autorun/Light/Light_0001.fit"
    assert ("images.list_filenames", ("Autorun/Light/",), {}) in fake.calls
    await session.close()


async def test_cooler_and_dew_heater(fake):
    session = await _open()
    cam = await session.get_device("camera", _conn())
    await cam.set_cooler(True, -10)
    assert fake.controls_written["CoolerOn"] == 1
    assert fake.controls_written["TargetTemp"] == -10
    cooler = await cam.get_cooler()
    assert cooler == {"on": True, "power": 42.0, "target_c": -10.0,
                      "can_report_power": True}
    assert await cam.get_temperature() == pytest.approx(-10.2)
    await cam.set_dew_heater(50)                 # ASIAIR anti-dew is on/off
    assert fake.controls_written["AntiDewHeater"] == 1
    await cam.set_dew_heater(0)
    assert fake.controls_written["AntiDewHeater"] == 0
    await session.close()


async def test_dew_heater_reads_back(fake):
    """The level the box is actually holding, not the number a browser last
    sent. ``AntiDewHeater`` is a 0/1 control, so the read-back is 0 or 100 —
    including after ``set_dew_heater(50)``, which really did turn it fully on."""
    # Make the double's get_control reflect what was written, so the round-trip
    # is a round-trip and not two independent constants agreeing by luck.
    written = fake.controls_written
    plain = fake.camera.get_control
    fake.camera.get_control = lambda name: (
        written["AntiDewHeater"] if name == "AntiDewHeater" and name in written
        else plain(name))

    session = await _open()
    cam = await session.get_device("camera", _conn())
    assert cam.has_dew_heater is True             # precondition
    assert await cam.get_dew_heater() == 0        # box says off, and means it
    await cam.set_dew_heater(50)
    assert written["AntiDewHeater"] == 1          # precondition: on/off control
    assert await cam.get_dew_heater() == 100
    await cam.set_dew_heater(0)
    assert await cam.get_dew_heater() == 0
    await session.close()


async def test_dew_heater_is_unknown_not_zero_when_the_box_cannot_say(fake):
    def boom(name):
        raise RuntimeError("control not readable")   # -> DeviceError via link.call

    fake.camera.get_control = boom
    session = await _open()
    cam = await session.get_device("camera", _conn())
    assert cam.has_dew_heater is True             # precondition
    assert await cam.get_dew_heater() is None
    await session.close()


async def test_a_camera_with_no_dew_heater_reports_no_level(fake):
    names = ["Gain", "Exposure", "Offset", "Temperature", "CoolPowerPerc",
             "TargetTemp", "CoolerOn"]        # no AntiDewHeater
    fake.camera.controls = lambda: [
        SimpleNamespace(name=n, min_val=0, max_val=300, read_only=False)
        for n in names]
    session = await _open()
    cam = await session.get_device("camera", _conn())
    assert cam.has_dew_heater is False            # precondition
    assert await cam.get_dew_heater() is None
    await session.close()


async def test_a_camera_with_no_cooler_says_so(fake):
    fake.camera.info = lambda: SimpleNamespace(
        name="ASI220MM", chip_size=(1920, 1080), pixel_size_um=4.0, bins=[1],
        has_cooler=False, is_color=False, debayer_pattern="")
    session = await _open()
    cam = await session.get_device("camera", _conn())
    assert await cam.get_cooler() is None
    with pytest.raises(DeviceError):
        await cam.set_cooler(True, -10)
    await session.close()


async def test_connect_says_which_device_is_missing_on_the_box(fake):
    def boom():
        raise RuntimeError("no camera opened")

    fake.camera.info = boom
    session = await _open()
    with pytest.raises(DeviceError) as ei:
        await session.get_device("camera", _conn())
    assert "no main camera is open on the box" in str(ei.value)
    assert "ASIAIR app" in str(ei.value)
    await session.close()


def test_fits_decode_rejects_a_non_2d_frame():
    from astropy.io import fits
    buf = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(np.zeros((2, 3, 4), dtype=np.uint16))]).writeto(buf)
    with pytest.raises(DeviceError) as ei:
        ab._decode_fits(buf.getvalue())
    assert "3-D FITS" in str(ei.value)


# ------------------------------------------------------- session accessors

async def test_session_offers_no_guider_guide_camera_or_solver(fake):
    """All three are deliberate Nones (module docstring): guiding stays in the
    ASIAIR, and AstroDeck solves the frame it actually downloaded."""
    session = await _open()
    assert session.native_guider() is None
    assert session.guide_camera() is None
    assert session.native_solver() is None
    await session.close()


async def test_health_reports_what_the_box_is_doing(monkeypatch):
    f = FakeAsiair(activity={"guiding": True, "capturing": False})
    monkeypatch.setattr(ab, "make_client", lambda host, timeout=10.0: f)
    session = await _open()
    h = await session.health()
    assert h["backend"] == "asiair" and h["ok"] is True
    assert h["activity"]["guiding"] is True
    await session.close()


async def test_health_never_raises(monkeypatch):
    f = FakeAsiair()
    monkeypatch.setattr(ab, "make_client", lambda host, timeout=10.0: f)
    session = await _open()

    def boom():
        raise RuntimeError("link died")

    f.get_activity = boom
    h = await session.health()
    assert h["ok"] is False and h["activity"] is None
    assert "link died" in h["last_error"]
    await session.close()


# ---------------------------------------------------------------- the probe

async def test_probe_offers_only_devices_the_box_actually_has(fake):
    def no_focuser():
        raise RuntimeError("focuser not opened")

    fake.focuser.info = no_focuser
    res = await ab.probe_asiair("asiair.invalid")
    assert res["reachable"] is True
    assert res["detail"] == "ASIAIR Plus"
    roles = {d["role"] for d in res["offers"]["devices"]}
    assert roles == {"camera", "telescope", "switch"}
    assert "focuser" not in roles, "must not offer a device that is not there"
    assert fake.connected is False, "the probe must always disconnect"


async def test_probe_never_raises_when_the_box_is_unreachable(monkeypatch):
    def refuse(host, timeout=10.0):
        raise OSError("no route to host")

    monkeypatch.setattr(ab, "make_client", refuse)
    res = await ab.probe_asiair("asiair.invalid")
    assert res["reachable"] is False
    assert res["offers"] == {"devices": [], "tasks": []}


async def test_drivers_probe_table_routes_asiair(monkeypatch):
    """The Equipment UI's ONE RULE needs a probe for the configured driver, or
    an ASIAIR row would sit unreachable forever and never offer a role."""
    from astrodeck import drivers
    assert "asiair" in drivers._PROBES
    f = FakeAsiair()
    monkeypatch.setattr(ab, "make_client", lambda host, timeout=10.0: f)
    res = await drivers._PROBES["asiair"]("asiair.invalid", 4700)
    assert res["reachable"] is True
    assert {d["role"] for d in res["offers"]["devices"]} == set(ab.ASIAIR_ROLES)


def test_pyproject_declares_the_entry_point_and_it_resolves():
    """Drift guard: the entry-point target in pyproject.toml must name a real
    importable callable (an editable install caches the old metadata, so a typo
    here would only surface on someone else's fresh install)."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    eps = data["project"]["entry-points"]["astrodeck.backends"]
    target = eps["asiair"]
    assert target == "astrodeck.devices.backends.asiair_backend:register_all"
    mod, _, attr = target.partition(":")
    import importlib
    assert callable(getattr(importlib.import_module(mod), attr))
    # and the optional dependency that makes it register is declared
    assert any("libasi" in d
               for d in data["project"]["optional-dependencies"]["asiair"])


def test_entry_point_discovery_loads_it_when_libasi_is_present(monkeypatch):
    """The registration path a real install actually takes."""
    import astrodeck.devices.backends._discovery as disc
    from astrodeck.devices.backend import BACKENDS

    class _EP:
        name = "asiair"
        dist = type("D", (), {"name": "astrodeck"})()

        def load(self):
            return ab.register_all

    monkeypatch.setattr(disc.md, "entry_points", lambda group=None: [_EP()])
    monkeypatch.setattr(ab, "libasi_available", lambda: True)
    prior = BACKENDS.pop("asiair", None)
    try:
        disc.discover_plugin_backends(app_version="99.0")
        assert "asiair" in BACKENDS
        assert any(r["name"] == "asiair" and r["status"] == "loaded"
                   for r in disc.plugin_load_report())
    finally:
        BACKENDS.pop("asiair", None)
        if prior is not None:
            BACKENDS["asiair"] = prior


def test_entry_point_discovery_is_a_no_op_without_libasi(monkeypatch):
    """A user with no ASIAIR: discovery runs, registers nothing, stays healthy
    (no 'failed' row that would light up a plugin error in the UI)."""
    import astrodeck.devices.backends._discovery as disc
    from astrodeck.devices.backend import BACKENDS

    class _EP:
        name = "asiair"
        dist = type("D", (), {"name": "astrodeck"})()

        def load(self):
            return ab.register_all

    monkeypatch.setattr(disc.md, "entry_points", lambda group=None: [_EP()])
    monkeypatch.setattr(ab, "libasi_available", lambda: False)
    prior = BACKENDS.pop("asiair", None)
    try:
        disc.discover_plugin_backends(app_version="99.0")
        assert "asiair" not in BACKENDS
        rows = disc.plugin_load_report()
        assert [r["status"] for r in rows] == ["loaded"]
        assert rows[0]["detail"] == "registered no new backend"
    finally:
        if prior is not None:
            BACKENDS["asiair"] = prior


def test_asiair_driver_can_be_added_with_just_a_host(tmp_path, monkeypatch):
    from astrodeck.config import DRIVER_DEFAULT_PORTS, config_store
    assert DRIVER_DEFAULT_PORTS["asiair"] == 4700
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    e = config_store.add_driver("asiair", host="asiair.invalid")
    assert e.id.startswith("asiair-") and e.port == 4700
    assert e.transport == "network"
