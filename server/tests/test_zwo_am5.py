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
    fl.script["Gps"] = "0"
    fl.script["Spu"] = "1"
    await tel.unpark()
    assert "Spu" in fl.sent
    assert await tel.is_parked() is False


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
    s["GR"] = ["10:00:00", "10:30:00"]      # never converges (alternates drift)
    s["GD"] = ["+10*00:00", "+20*00:00"]
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
    s["GR"] = ["10:00:00", "10:30:00", "10:00:00", "10:30:00",
               "10:00:00", "10:30:00", "10:00:00", "10:30:00"]
    s["GD"] = ["+10*00:00", "+20*00:00", "+10*00:00", "+20*00:00",
               "+10*00:00", "+20*00:00", "+10*00:00", "+20*00:00"]
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
    fl, tel = await _connected_tel(_connect_script())
    await tel.move_axis("ra", 0.5)           # 0.5 deg/s -> R3, positive ra -> Me
    assert fl.sent == ["R3", "Me"]
    fl.sent.clear()
    await tel.move_axis("ra", 0.0)           # stop both directions of the axis
    assert fl.sent == ["Qe", "Qw"]
    fl.sent.clear()
    await tel.move_axis("dec", -20.0)        # fast negative dec -> R9 + Ms
    assert fl.sent == ["R9", "Ms"]


async def test_stop_sends_halt_first_and_only(fixed_env):
    # Emergency-stop semantics: :Q# goes out FIRST, nothing before it. Whether
    # :Q# disturbs tracking on this firmware is an at-scope runbook item; until
    # verified, stop() does not send follow-up commands.
    fl, tel = await _connected_tel(_connect_script())
    await tel.stop()
    assert fl.sent == ["Q"]


async def test_pulse_guide_format(fixed_env):
    fl, tel = await _connected_tel(_connect_script())
    await tel.pulse_guide("north", 500)
    assert fl.sent == ["Mgn0500"]
    assert type(tel).can_pulse_guide is False     # stays off until at-scope validation
