"""ZWO AM5N native serial driver: link double, telescope behavior, backend +
framework integration. All coordinates fictional (site privacy)."""
from __future__ import annotations

import asyncio

import pytest

from astrodeck.devices.serial_link import LinkError, SerialLink


class FakeLink:
    """Test double for SerialLink: same ``request``/``close`` surface.

    ``script`` maps an unframed command string to either a reply string, a list
    of replies (consumed in order; last repeats), or a callable(cmd)->reply.
    Unscripted "hash"/"ack" requests raise LinkError (like a silent mount);
    unscripted "none" requests are simply logged.
    """

    def __init__(self, script: dict | None = None):
        self.script = dict(script or {})
        self.sent: list[str] = []
        self.closed = False

    async def open(self) -> None:  # parity with SerialLink
        pass

    async def request(self, cmd: str, *, reply: str = "hash",
                      timeout: float = 1.5):
        self.sent.append(cmd)
        entry = self.script.get(cmd)
        if callable(entry):
            entry = entry(cmd)
        elif isinstance(entry, list):
            entry = entry.pop(0) if len(entry) > 1 else entry[0]
        if reply == "none":
            return None
        if entry is None:
            raise LinkError(f"unscripted command {cmd!r}")
        return entry

    async def close(self) -> None:
        self.closed = True


# ------------------------------------------------------------- link double

async def test_fakelink_sequences_and_logging():
    fl = FakeLink({"GR": ["10:00:00", "11:00:00"], "Spu": "1"})
    assert await fl.request("GR") == "10:00:00"
    assert await fl.request("GR") == "11:00:00"
    assert await fl.request("GR") == "11:00:00"          # last repeats
    assert await fl.request("Spu", reply="ack") == "1"
    assert await fl.request("Me", reply="none") is None  # unscripted fire-forget ok
    assert fl.sent == ["GR", "GR", "GR", "Spu", "Me"]
    with pytest.raises(LinkError):
        await fl.request("GD")                            # unscripted read raises


async def test_seriallink_requires_open():
    link = SerialLink("COM99")
    with pytest.raises(LinkError):
        await link.request("GR")


# ------------------------------------------------------- telescope: connect/state

from datetime import datetime, timezone  # noqa: E402

from astrodeck.devices.base import DeviceError, PierSide  # noqa: E402
import astrodeck.devices.backends.zwo_am5 as am5  # noqa: E402

FIXED_UTC = datetime(2026, 7, 19, 22, 14, 58, tzinfo=timezone.utc)


def _connect_script(**over):
    """A FakeLink script for a successful connect (parked mount)."""
    s = {
        "GVP": "AM5N", "GV": "1.8.8",
        "SG+00:00": "1", "SH0": "1", "SC07/19/26": "1", "SL22:14:58": "1",
        "SMGE+40*00:00&+100*30:30": "1",
        "Gps": "2", "GU": "nGM000000005",
        "GR": "10:13:56", "GD": "+90*00:00", "GAT": "0",
    }
    s.update(over)
    return s


@pytest.fixture
def fixed_env(monkeypatch):
    monkeypatch.setattr(am5, "_utcnow", lambda: FIXED_UTC)
    monkeypatch.setattr(am5, "_site_latlon",
                        lambda: (40.0, -(100 + 30 / 60 + 30 / 3600)))


async def test_connect_flow_sequence_no_autounpark(fixed_env):
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    assert tel.connected is True
    assert tel.hardware is True and tel.backend == "zwo-am5"
    # exact init order, and NO :Spu# (connect must not auto-unpark)
    assert fl.sent[:7] == ["GVP", "GV", "SG+00:00", "SH0", "SC07/19/26",
                           "SL22:14:58", "SMGE+40*00:00&+100*30:30"]
    assert "Spu" not in fl.sent


async def test_connect_identity_mismatch_closes(fixed_env):
    fl = FakeLink(_connect_script(GVP="Prototype"))
    tel = am5.ZwoAm5Telescope(fl)
    with pytest.raises(DeviceError):
        await tel.connect()
    assert fl.closed is True and tel.connected is False


async def test_parked_state_and_unpark(fixed_env):
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    assert await tel.is_parked() is True          # Gps -> "2"
    fl.script["Spu"] = "1"
    await tel.unpark()                             # parked -> sends :Spu#
    assert "Spu" in fl.sent
    fl.script["Gps"] = "0"
    assert await tel.is_parked() is False


async def test_unpark_is_idempotent_when_already_unparked(fixed_env):
    """At-scope finding: :Spu# replies '0' when there is no park to cancel —
    unpark() must check state first and no-op, not raise."""
    fl = FakeLink(_connect_script(Gps="0"))       # already unparked
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    await tel.unpark()                             # must not raise
    assert "Spu" not in fl.sent


async def test_park_is_idempotent_when_already_parked(fixed_env):
    fl = FakeLink(_connect_script())               # Gps -> "2" (parked)
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    await tel.park()                               # must not raise / not send
    assert "hP" not in fl.sent


async def test_guide_rates_report_emulated_pulse_rate(fixed_env):
    """guide_rates reports what pulses ACTUALLY deliver — the R1 preset — not
    the mount's :GdG# setting (which governs only the inert :Mg*# path)."""
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    v = await tel.guide_rates()
    assert v == (am5._PULSE_RA_RATE_DEG_S, am5._PULSE_DEC_RATE_DEG_S)
    assert "GdG" not in fl.sent


async def test_position_and_tracking_reads(fixed_env):
    fl = FakeLink(_connect_script(GAT="1"))
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    ra, dec = await tel.get_position()
    assert abs(ra - (10 + 13 / 60 + 56 / 3600)) < 1e-9 and dec == 90.0
    assert await tel.get_tracking() is True
    fl.script["Gm"] = "E"
    assert await tel.pier_side() == PierSide.EAST


async def test_parked_refusal_maps_to_honest_error(fixed_env):
    fl = FakeLink(_connect_script())
    fl.script["Te"] = "e14"
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    with pytest.raises(DeviceError, match="parked"):
        await tel.set_tracking(True)


# --------------------------------------------------------- telescope: tracking rate

async def test_tracking_rate_sends_lx200_command_per_rate(fixed_env):
    """Each rate name maps to its classic LX200 select command, sent
    fire-and-forget (reply="none" -- see set_tracking_rate's docstring for why
    an ack is NOT assumed for :TQ#/:TL#/:TS#)."""
    fl, tel = await _connected_tel(_connect_script())
    assert tel.can_set_tracking_rate is True
    await tel.set_tracking_rate("sidereal")
    assert fl.sent == ["TQ"]
    fl.sent.clear()
    await tel.set_tracking_rate("lunar")
    assert fl.sent == ["TL"]
    fl.sent.clear()
    await tel.set_tracking_rate("solar")
    assert fl.sent == ["TS"]


async def test_get_tracking_rate_returns_cached_last_set(fixed_env):
    fl, tel = await _connected_tel(_connect_script())
    assert await tel.get_tracking_rate() == "sidereal"       # init default
    await tel.set_tracking_rate("lunar")
    assert await tel.get_tracking_rate() == "lunar"           # cache, not a re-read
    assert fl.sent == ["TL"]                                  # no read-back command sent


async def test_tracking_rate_rejects_unknown_and_sends_nothing(fixed_env):
    fl, tel = await _connected_tel(_connect_script())
    with pytest.raises(DeviceError):
        await tel.set_tracking_rate("king")
    assert fl.sent == []                                       # nothing sent
    assert await tel.get_tracking_rate() == "sidereal"          # cache untouched


# ---------------------------------------------------------- telescope: motion

async def _connected_tel(script):
    fl = FakeLink(script)
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    fl.sent.clear()                    # motion assertions start clean
    return fl, tel


async def test_slew_happy_path_settles(fixed_env, monkeypatch):
    monkeypatch.setattr(am5, "SETTLE_POLL_S", 0.01)
    target_ra, target_dec = 11.0, 45.0
    s = _connect_script()
    s["Sr11:00:00"] = "1"
    s["Sd+45*00:00"] = "1"
    s["MS"] = "0"
    # approach then converge: two consecutive stable polls end the settle
    s["GR"] = ["10:30:00", "10:59:00", "11:00:00", "11:00:00", "11:00:00"]
    s["GD"] = ["+60*00:00", "+46*00:00", "+45*00:00", "+45*00:00", "+45*00:00"]
    fl, tel = await _connected_tel(s)
    await tel.slew(target_ra, target_dec)
    assert fl.sent[:3] == ["Sr11:00:00", "Sd+45*00:00", "MS"]


async def test_slew_cancel_sends_stop(fixed_env, monkeypatch):
    monkeypatch.setattr(am5, "SETTLE_POLL_S", 0.01)
    s = _connect_script()
    s["Sr11:00:00"] = "1"; s["Sd+45*00:00"] = "1"; s["MS"] = "0"
    # never converges: alternate FOREVER via callables (a list would repeat its
    # last element and read as settled — the FakeLink semantics)
    n = {"ra": 0, "dec": 0}
    s["GR"] = lambda cmd: ["10:00:00", "10:30:00"][n.__setitem__("ra", n["ra"] + 1) or n["ra"] % 2]
    s["GD"] = lambda cmd: ["+10*00:00", "+20*00:00"][n.__setitem__("dec", n["dec"] + 1) or n["dec"] % 2]
    fl, tel = await _connected_tel(s)
    task = asyncio.create_task(tel.slew(11.0, 45.0))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "Q" in fl.sent                    # halt on cancel


async def test_slew_timeout_halts_and_raises(fixed_env, monkeypatch):
    monkeypatch.setattr(am5, "SETTLE_POLL_S", 0.01)
    monkeypatch.setattr(am5, "SLEW_TIMEOUT_S", 0.05)
    s = _connect_script()
    s["Sr11:00:00"] = "1"; s["Sd+45*00:00"] = "1"; s["MS"] = "0"
    m = {"ra": 0, "dec": 0}
    s["GR"] = lambda cmd: ["10:00:00", "10:30:00"][m.__setitem__("ra", m["ra"] + 1) or m["ra"] % 2]
    s["GD"] = lambda cmd: ["+10*00:00", "+20*00:00"][m.__setitem__("dec", m["dec"] + 1) or m["dec"] % 2]
    fl, tel = await _connected_tel(s)
    with pytest.raises(DeviceError, match="timeout|settle"):
        await tel.slew(11.0, 45.0)
    assert "Q" in fl.sent


async def test_slew_while_parked_is_honest(fixed_env):
    s = _connect_script()
    s["Sr11:00:00"] = "1"; s["Sd+45*00:00"] = "1"
    s["MS"] = "e14"
    fl, tel = await _connected_tel(s)
    with pytest.raises(DeviceError, match="parked"):
        await tel.slew(11.0, 45.0)


async def test_sync_sets_target_then_cm(fixed_env):
    s = _connect_script()
    s["Sr10:00:00"] = "1"; s["Sd+40*00:00"] = "1"; s["CM"] = "Synced"
    fl, tel = await _connected_tel(s)
    await tel.sync(10.0, 40.0)
    assert fl.sent == ["Sr10:00:00", "Sd+40*00:00", "CM"]


async def test_move_axis_rate_map_and_stop(fixed_env):
    # Calibrated table (hardware 2026-07-20): R-indices are sidereal-multiple
    # presets; 0.5 deg/s lands on R7 (~60x sid = 0.25 deg/s nearest preset
    # class), fast slews on R8.
    fl, tel = await _connected_tel(_connect_script())
    await tel.move_axis("ra", 0.5)           # -> R7 band, positive ra -> Me
    assert fl.sent == ["R7", "Me"]
    fl.sent.clear()
    await tel.move_axis("ra", 0.0)           # stop both directions of the axis
    assert fl.sent == ["Qe", "Qw"]
    fl.sent.clear()
    await tel.move_axis("dec", -20.0)        # fast negative dec -> R8 + Ms
    assert fl.sent == ["R8", "Ms"]
    fl.sent.clear()
    await tel.move_axis("dec", 0.008)        # ~2x sidereal -> R3
    assert fl.sent == ["R3", "Mn"]


async def test_park_fire_and_forget_polls_gps(fixed_env, monkeypatch):
    """VERIFIED ON HARDWARE: :hP# gives NO ack; the mount reports parked via
    :Gps# ~1s later. park() sends fire-and-forget and polls."""
    monkeypatch.setattr(am5, "PARK_POLL_S", 0.01)
    fl = FakeLink(_connect_script(Gps=["0", "0", "2"]))  # unparked -> parked
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    await tel.park()
    assert "hP" in fl.sent
    assert (await tel.is_parked()) is True


async def test_stop_sends_halt_first_and_only(fixed_env):
    # Emergency-stop semantics: :Q# goes out FIRST, nothing before it. Whether
    # :Q# disturbs tracking on this firmware is an at-scope runbook item; until
    # verified, stop() does not send follow-up commands.
    fl, tel = await _connected_tel(_connect_script())
    await tel.stop()
    assert fl.sent == ["Q"]


async def test_pulse_guide_direction_strategies(fixed_env):
    """Native :Mg*# is inert; :M<dir># REPLACES tracking (at-scope 2026-07-20)
    — so east suspends tracking, west drives R3+Mw, n/s use R1 moves."""
    fl, tel = await _connected_tel(_connect_script())
    await tel.pulse_guide("north", 50)
    assert fl.sent == ["R1", "Mn", "Qn"]
    fl.sent.clear()
    await tel.pulse_guide("west", 50)
    assert fl.sent == ["R2", "Mw", "Qw"]
    fl.sent.clear()
    fl.script["GAT"] = "1"                        # tracking on
    fl.script["Td"] = "1"
    fl.script["Te"] = "1"
    await tel.pulse_guide("east", 50)
    assert fl.sent == ["GAT", "Td", "Te"]         # exact-1x-sidereal drift
    fl.sent.clear()
    fl.script["GAT"] = "0"                        # tracking off -> move fallback
    await tel.pulse_guide("east", 50)
    assert fl.sent == ["GAT", "R1", "Me", "Qe"]
    assert type(tel).can_pulse_guide is True


async def test_pulse_guide_cancel_restores_state(fixed_env):
    fl, tel = await _connected_tel(_connect_script())
    fl.script["GAT"] = "1"
    fl.script["Td"] = "1"
    fl.script["Te"] = "1"
    task = asyncio.create_task(tel.pulse_guide("east", 5000))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fl.sent == ["GAT", "Td", "Te"]         # finally resumed tracking


# ------------------------------------------------- backend + framework integration

from astrodeck.devices import backends as _backends  # noqa: E402,F401  (registration)
from astrodeck.devices.backend import BACKENDS  # noqa: E402
import astrodeck.devices.backends._discovery as disc  # noqa: E402


@pytest.fixture
def registered(fixed_env):
    prior = BACKENDS.get("zwo-am5")         # entry-point discovery may have
    am5.register_all()                      # already registered it — restore,
    try:                                    # never leave the worker without it
        yield BACKENDS["zwo-am5"]
    finally:
        if prior is not None:
            BACKENDS["zwo-am5"] = prior
        else:
            BACKENDS.pop("zwo-am5", None)


def test_register_all_manifest(registered):
    from astrodeck.devices.backend import list_backends
    row = next(r for r in list_backends() if r["name"] == "zwo-am5")
    assert row["transport"] == "serial"
    assert row["hardware"] is True
    assert row["driver_type"] == "zwo-am5"
    assert row["roles"] == ("telescope",)
    from astrodeck import __version__
    assert row["version"] == __version__


def test_entry_point_discovery_loads_zwo_am5(monkeypatch, fixed_env):
    class _EP:
        name = "zwo_am5"
        dist = type("D", (), {"name": "astrodeck"})()
        def load(self):
            return am5.register_all
    monkeypatch.setattr(disc.md, "entry_points", lambda group=None: [_EP()])
    prior = BACKENDS.pop("zwo-am5", None)   # force a clean discovery run
    try:
        disc.discover_plugin_backends(app_version="99.0")
        assert "zwo-am5" in BACKENDS
        assert any(r["name"] == "zwo-am5" and r["status"] == "loaded"
                   for r in disc.plugin_load_report())
    finally:
        if prior is not None:
            BACKENDS["zwo-am5"] = prior
        else:
            BACKENDS.pop("zwo-am5", None)


async def test_connect_profile_end_to_end_serial_rig(registered, monkeypatch):
    """A profile serial row connects through the orchestrator against a FakeLink,
    and the A-framework safety stamp holds (device.hardware is True)."""
    from astrodeck.devices.orchestrator import connect_profile
    from astrodeck.profiles import Profile, ProfileDevice

    monkeypatch.setattr(am5, "_make_link", lambda port: FakeLink(_connect_script()))
    p = Profile(name="serial rig", primary_backend="none", devices=[
        ProfileDevice(role="telescope", backend="zwo-am5",
                      transport="serial", port_path="COM9")])
    res = await connect_profile(p.to_rigspec())
    tel = res.rig.get("telescope")
    assert tel is not None and tel.connected
    assert tel.hardware is True
    assert tel.firmware == "1.8.8"
    for s in res.sessions.values():
        await s.close()


# ------------------------------------------------- serial driver creation (A-minor 1)

def test_add_driver_serial(tmp_path, monkeypatch):
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    e = config_store.add_driver("zwo-am5", transport="serial", port_path="COM9")
    assert e.id.startswith("zwo-am5-")
    assert e.transport == "serial" and e.port_path == "COM9"
    assert e.label == "ZWO-AM5 @ COM9"
    with pytest.raises(ValueError, match="port_path"):
        config_store.add_driver("zwo-am5", transport="serial")
    # unknown network type without explicit port is rejected; with one, allowed
    with pytest.raises(ValueError, match="default port"):
        config_store.add_driver("demo-net", host="h")
    ok = config_store.add_driver("demo-net", host="h", port=4321)
    assert ok.port == 4321


def test_driver_id_resolution_carries_serial_addressing(tmp_path, monkeypatch):
    """B review I1: a serial driver REFERENCED BY ID must resolve with its
    transport/port_path intact — resolve_driver_ids used to drop them."""
    from astrodeck import drivers as drv
    from astrodeck.config import config_store
    from astrodeck.devices.backend import ConnSpec, RigSpec
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    d = config_store.add_driver("zwo-am5", transport="serial", port_path="COM9")
    spec = RigSpec(primary="none", roles={
        "telescope": ConnSpec(backend="", role="telescope", driver_id=d.id)})
    resolved, role_map, prefailed = drv.resolve_driver_ids(spec)
    assert prefailed == []
    cs = resolved.roles["telescope"]
    assert cs.transport == "serial" and cs.port_path == "COM9"
    assert cs.backend == "zwo-am5" or cs.backend == d.type   # registry-mapped


def test_api_creates_serial_driver(registered, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    import astrodeck.drivers as drv
    drv.invalidate()
    from astrodeck.api import app as app_module
    with TestClient(app_module.create_app()) as c:
        r = c.post("/api/config/drivers",
                   json={"type": "zwo-am5", "transport": "serial",
                         "port_path": "COM9"})
        assert r.status_code == 200
        assert r.json()["driver"]["port_path"] == "COM9"
        assert c.post("/api/config/drivers",
                      json={"type": "asiair", "host": "h"}).status_code == 422
        # legacy network create unchanged
        r2 = c.post("/api/config/drivers", json={"type": "nina", "host": "h"})
        assert r2.status_code == 200 and r2.json()["driver"]["port"] == 1888


# --------------------------------------------- solver defense (A-minor 2)

async def test_simsolver_refuses_serial_mount_rig(registered, monkeypatch, tmp_path):
    """zwo-am5 mount + sim camera, no ASTAP: the SimSolver fallback must REFUSE
    to fake a plate solve (the mode denylist never knew this session name — the
    device hardware flag is the truth that gates it now)."""
    from astrodeck.devices.orchestrator import connect_profile
    from astrodeck.profiles import Profile, ProfileDevice
    import astrodeck.solve as solve_mod

    monkeypatch.setattr(am5, "_make_link", lambda port: FakeLink(_connect_script()))
    monkeypatch.setattr(solve_mod, "find_astap", lambda: None)
    p = Profile(name="mixed", primary_backend="none", devices=[
        ProfileDevice(role="telescope", backend="zwo-am5",
                      transport="serial", port_path="COM9"),
        ProfileDevice(role="camera", backend="sim")])
    res = await connect_profile(p.to_rigspec())
    assert res.rig["telescope"].hardware is True
    assert res.solver is not None
    out = await res.solver.solve(tmp_path / "frame.fits", ra_hint=10.0, dec_hint=40.0)
    assert out.success is False and "refusing" in out.message
    for s in res.sessions.values():
        await s.close()


async def test_simsolver_pure_sim_rig_still_solves(monkeypatch, tmp_path):
    from astrodeck.devices.orchestrator import connect_profile
    from astrodeck.profiles import Profile
    import astrodeck.solve as solve_mod

    monkeypatch.setattr(solve_mod, "find_astap", lambda: None)
    res = await connect_profile(Profile(name="sim").to_rigspec())
    out = await res.solver.solve(tmp_path / "frame.fits", ra_hint=10.0, dec_hint=40.0)
    assert out.success is True
    for s in res.sessions.values():
        await s.close()


async def test_discover_filters_vid_pid(registered, monkeypatch):
    class _Port:
        def __init__(self, device, vid, pid):
            self.device, self.vid, self.pid = device, vid, pid
    fake_ports = [_Port("COM3", 0x03C3, 0x4001), _Port("COM8", 0x1A86, 0x7523)]
    from serial.tools import list_ports
    monkeypatch.setattr(list_ports, "comports", lambda: fake_ports)
    found = await registered.discover()
    assert found == [{"role": "telescope", "name": "ZWO AM5 (USB)",
                      "port_path": "COM3", "verified": True}]
