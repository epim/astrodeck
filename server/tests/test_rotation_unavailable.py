"""goto_and_center flags ``rotation_unavailable`` (#160, I-15; mosaic spec 5.6
step 4 and section 8 S1 item 10).

A rotation asked for with no connected rotator to do it used to be SILENT:
the rotate block was skipped on ``rot is not None and rot.connected``, and the
result read exactly like a goto that never asked for an angle. A caller that
must not shoot an unrotated mosaic panel could not tell a rotated frame from
one left at whatever angle the camera happened to sit. The flag is what the
engine will defer on in S2; this slice only makes the hub say it.

Red on the unmodified hub (before the flag existed), verbatim, from
``goto_and_center(..., rotation_deg=30)`` with the rotator disconnected
(``test_a_disconnected_rotator_is_flagged``)::

    >       assert result.get("rotation_unavailable") is True, result
    E       AssertionError: {'attempts': 2, 'centered': True, 'error_arcmin': 0.176842248926498, 'rotation': None}
    E       assert None is True

The same run failed the absent-rotator case and all five return paths on the
same assertion, and passed the three controls. The PA 0 case, added later,
fails on the unmodified hub with the same dict and the same assertion.

Every behaviour below was also shown red under a NAMED mutation of
``Hub.goto_and_center``, each run from a byte-for-byte backup of hub.py that
was restored and compared byte-identical (sha256) afterwards. The observed
failure is recorded on the test that caught it; pytest's ``+ where`` lines,
which only repeat the dict with an object address, are left out.
"""
import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck.devices.base import DeviceError
from astrodeck.events import bus


@pytest.fixture
def logs(monkeypatch):
    """Every bus.log line the goto emits, still forwarded to the real bus so
    nothing downstream of the log is starved by the test."""
    seen: list[tuple[str, str, str]] = []
    orig = bus.log

    def record(level, message, source="hub"):
        seen.append((level, message, source))
        orig(level, message, source)

    monkeypatch.setattr(bus, "log", record)
    return seen


#: The goto target every case uses. The same field test_goto_rotation.py uses.
RA, DEC = 5.0, 10.0


@pytest.fixture(autouse=True)
def _mount_on_the_target(sim_hub):
    """Start the sim mount on the goto target. ``SimTelescope.slew`` paces its
    dwell with a bare ``asyncio.sleep`` at 4 deg/s that ``_sim_delay`` does not
    route, so the fast-test path leaves it running: from the sim's default
    pointing each case here paid about 4.5 s of real dwell for a 17 degree
    slew. Every slew still happens, it is just short, and nothing graded here
    depends on how far the mount travelled."""
    sim_hub.sim_rig.ra_hours = RA
    sim_hub.sim_rig.dec_deg = DEC


def _rotation_warnings(seen) -> list[str]:
    """Warnings from the ``rotator`` source only. A successful rotate also
    logs 'camera rotated -- guide calibration may be stale' at warning level,
    but from ``guide``; that one is correct and is not what these tests count."""
    return [m for (lvl, m, src) in seen
            if lvl == "warning" and src == "rotator"]


@pytest.fixture
def rotate_calls(sim_hub, monkeypatch):
    """Spy on rotate_to_pa, delegating to the REAL method so the connected
    control still exercises the actual rotate loop on the sim rotator."""
    calls: list[float] = []
    real = sim_hub.rotate_to_pa

    async def spy(target_pa_deg, *a, **k):
        calls.append(target_pa_deg)
        return await real(target_pa_deg, *a, **k)

    monkeypatch.setattr(sim_hub, "rotate_to_pa", spy)
    return calls


async def _disconnect_rotator(hub):
    rot = hub.devices["rotator"]
    await rot.disconnect()
    # The harness must really be in "present but not connected", or the
    # disconnected cases collapse into the absent one and grade nothing new.
    assert hub.devices.get("rotator") is rot and not rot.connected


# --- the change -----------------------------------------------------------


async def test_an_absent_rotator_is_flagged(sim_hub, logs, rotate_calls):
    """Mutation 'no warning' (the ``bus.log`` call in the new branch replaced
    with ``pass``, the flag still set) went red here, and on the disconnected
    case and all five return paths:

        >       assert len(warnings) == 1, warnings
        E       AssertionError: []
        E       assert 0 == 1
        E        +  where 0 = len([])
    """
    sim_hub.devices.pop("rotator", None)
    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.0)
    assert result["centered"] is True
    assert result["rotation"] is None
    assert result.get("rotation_unavailable") is True, result
    assert "rotation_skipped" not in result
    assert rotate_calls == []
    warnings = _rotation_warnings(logs)
    assert len(warnings) == 1, warnings
    # In words: which angle was wanted, and why it was not honoured.
    assert "30" in warnings[0] and "no rotator" in warnings[0], warnings


async def test_a_disconnected_rotator_is_flagged(sim_hub, logs, rotate_calls):
    """Mutation 'flag only when rot is None' (``elif rotation_deg is not None
    and rot is None:``) went red here, and on all five return paths (which
    disconnect too), while the absent case stayed green:

        >       assert result.get("rotation_unavailable") is True, result
        E       AssertionError: {'attempts': 2, 'centered': True, 'error_arcmin': 0.176842248926498, 'rotation': None}
        E       assert None is True

    Mutation 'one reason for both cases' (``why`` fixed at "no rotator is in
    the rig") went red here alone, on the words:

        >       assert "30" in warnings[0] and "not connected" in warnings[0], warnings
        E       AssertionError: ['rotation to PA 30° was asked for but no rotator is in the rig; centring without rotating, so the frame keeps whatever angle the camera is at']
    """
    await _disconnect_rotator(sim_hub)
    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.0)
    assert result["centered"] is True
    assert result["rotation"] is None
    assert result.get("rotation_unavailable") is True, result
    assert "rotation_skipped" not in result
    assert rotate_calls == []
    warnings = _rotation_warnings(logs)
    assert len(warnings) == 1, warnings
    assert "30" in warnings[0] and "not connected" in warnings[0], warnings


async def test_pa_zero_is_a_rotation_request(sim_hub, logs, rotate_calls):
    """PA 0 is a real angle (north-up framing; flows/store.py says why), so a
    request for it with no connected rotator is flagged like any other. Every
    case above asks for 30, which a truthiness test on the angle would still
    honour, so without this case the most familiar way to get the condition
    wrong in this codebase went unseen.

    Mutation 'truthy PA' (``elif rotation_deg:``) went red here and nowhere
    else; the other ten cases all passed under it:

        >       assert result.get("rotation_unavailable") is True, result
        E       AssertionError: {'attempts': 2, 'centered': True, 'error_arcmin': 0.176842248926498, 'rotation': None}
        E       assert None is True
    """
    await _disconnect_rotator(sim_hub)
    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=0.0)
    assert result["centered"] is True
    assert result.get("rotation_unavailable") is True, result
    assert rotate_calls == []
    warnings = _rotation_warnings(logs)
    assert len(warnings) == 1, warnings
    assert "PA 0°" in warnings[0], warnings


def _off_target_solve(hub, *, bump_epoch_once=False):
    """A solve that lands one degree north of wherever it is asked about, so
    the loop can never converge and every attempt reads the same residual.
    With ``bump_epoch_once`` the first solve also bumps the motion epoch, which
    is exactly what an abort landing mid-solve does (``bump_motion_epoch``):
    the next attempt's fence then returns ``aborted`` after a real slew."""
    state = {"bumped": False}

    async def solve(exposure_s=3.0, **_k):
        if bump_epoch_once and not state["bumped"]:
            state["bumped"] = True
            hub.bump_motion_epoch()
        return {"ra_hours": RA, "dec_deg": DEC + 1.0}

    return solve


async def _failing_solve(exposure_s=3.0, **_k):
    raise DeviceError("clouds over the centring solve")


@pytest.mark.parametrize("path", ["centred", "not_converged", "did_not_move",
                                  "solve_failed", "aborted_after_slew"])
async def test_every_return_after_the_slew_carries_the_flag(
        sim_hub, logs, monkeypatch, path):
    """Every return that carries ``_rot_keys`` carries the flag, and the
    warning is logged once however many attempts the loop makes.

    Each case first asserts the marker of its own branch, so a harness that
    missed the branch it is named for fails loudly instead of re-grading the
    centred path five times.

    Mutation 'flag on the centred return only' (the entry dropped from
    ``_rot_keys`` and OR'd into the centred return alone) went red on the four
    non-centred cases and stayed green on ``centred``; ``did_not_move`` read:

        >       assert result.get("rotation_unavailable") is True, result
        E       AssertionError: {'attempts': 2, 'centered': False, 'did_not_move': True, 'error_arcmin': 60.00000000001588, ...}
        E       assert None is True

    Mutation 'warn on every attempt' (a second rotator warning, "rotation
    unavailable, still", logged at the top of each centring attempt) went red
    on all five cases, the two absent/disconnected tests too; ``did_not_move``
    read:

        >       assert len(warnings) == 1, warnings
        E       AssertionError: ['rotation to PA 30° was asked for but the rotator is not connected; centring without rotating, so the frame keeps whatever angle the camera is at', 'rotation unavailable, still', 'rotation unavailable, still']
        E       assert 3 == 1
    """
    await _disconnect_rotator(sim_hub)
    kwargs = {}
    if path == "not_converged":
        monkeypatch.setattr(sim_hub, "solve_and_sync", _off_target_solve(sim_hub))
        kwargs["max_attempts"] = 1
    elif path == "did_not_move":
        monkeypatch.setattr(sim_hub, "solve_and_sync", _off_target_solve(sim_hub))
    elif path == "solve_failed":
        monkeypatch.setattr(sim_hub, "solve_and_sync", _failing_solve)
    elif path == "aborted_after_slew":
        monkeypatch.setattr(sim_hub, "solve_and_sync",
                            _off_target_solve(sim_hub, bump_epoch_once=True))

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.0, **kwargs)

    markers = {"aborted", "solve_failed", "did_not_move"}
    if path == "centred":
        assert result["centered"] is True
    elif path == "not_converged":
        assert result["centered"] is False and result["attempts"] == 1
        assert not markers & result.keys(), result
    elif path == "did_not_move":
        assert result.get("did_not_move") is True, result
    elif path == "solve_failed":
        assert result.get("solve_failed") is True, result
    elif path == "aborted_after_slew":
        # attempts == 1: one slew and one solve happened before the fence.
        assert result.get("aborted") is True and result["attempts"] == 1, result
    assert result.get("rotation_unavailable") is True, result
    warnings = _rotation_warnings(logs)
    assert len(warnings) == 1, warnings


# --- controls: where nothing may change ------------------------------------


async def test_control_a_connected_rotator_rotates_and_is_not_flagged(
        sim_hub, logs, rotate_calls):
    """Mutation 'flag even with a connected rotator' (``rotation_unavailable =
    True`` set at the top of the connected branch too) went red here, and on
    the rotation_skipped control:

        >       assert "rotation_unavailable" not in result, result
        E       AssertionError: {'attempts': 2, 'centered': True, 'error_arcmin': 0.176842248926498, 'rotation': {'adjusted_to': None, 'attempts': 1, 'error_deg': 0.0, 'pa_deg': 30.0, ...}, ...}
        E       assert 'rotation_unavailable' not in {'centered': True, 'error_arcmin': 0.176842248926498, 'attempts': 2, 'rotation': {'rotated': True, 'pa_deg': 30.0, 'adjusted_to': None, 'attempts': 1, 'error_deg': 0.0}, 'rotation_unavailable': True}
    """
    # The camera already sits at PA 30, so the real rotate loop runs (a solve,
    # a rotator sync, a verdict) without paying ``SimRotator``'s unrouted
    # 5 deg/s move dwell. Whether the rotator had to travel is not what this
    # control is about; that it was ASKED, and no flag was raised, is.
    sim_hub.sim_rig.rotator_pa_offset_deg = 0.0
    sim_hub.sim_rig.rotator_mech_deg = 30.0
    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.0)
    assert result["centered"] is True
    assert rotate_calls == [30.0]
    assert result["rotation"]["rotated"] is True
    assert "rotation_unavailable" not in result, result
    assert "rotation_skipped" not in result
    assert _rotation_warnings(logs) == []


async def test_control_no_rotation_asked_no_rotator_no_flag_no_warning(
        sim_hub, logs, rotate_calls):
    """A plain goto on a rig with no rotator is the common case; it must not
    grow a flag or nag in the log.

    Mutation 'flag whenever the rotator is missing' (``elif rot is None or not
    rot.connected:``, the request check dropped) went red here and nowhere
    else. Its red is the new branch firing for a goto that asked for nothing
    and then formatting the None angle into its warning:

        >       result = await sim_hub.goto_and_center(RA, DEC)
        >                   f"rotation to PA {rotation_deg:.0f}° was asked for but {why}; "
        E           TypeError: unsupported format string passed to NoneType.__format__

    Because that red comes from the warning, not from the flag assertion,
    mutation 'flag keyed on the rotator alone' applies the same wrong
    condition to the ``_rot_keys`` entry only, leaving the warning correct. It
    went red here, and nowhere else, on the flag assertion itself:

        >       assert "rotation_unavailable" not in result, result
        E       AssertionError: {'attempts': 2, 'centered': True, 'error_arcmin': 0.176842248926498, 'rotation': None, ...}
        E       assert 'rotation_unavailable' not in {'centered': True, 'error_arcmin': 0.176842248926498, 'attempts': 2, 'rotation': None, 'rotation_unavailable': True}
    """
    sim_hub.devices.pop("rotator", None)
    result = await sim_hub.goto_and_center(RA, DEC)
    assert result["centered"] is True
    assert result["rotation"] is None
    assert "rotation_unavailable" not in result, result
    assert "rotation_skipped" not in result
    assert rotate_calls == []
    assert _rotation_warnings(logs) == []


async def test_control_rotation_skipped_is_unchanged(sim_hub, logs, monkeypatch):
    """A connected rotator whose rotate loop fails is ``rotation_skipped``, not
    ``rotation_unavailable``: the two want different remedies (retry versus
    connect a device), so they must not blur into one key.

    Mutation 'rotate failure also flags unavailable' (``rotation_unavailable =
    True`` added beside ``rotation_skipped = True`` in the except branch) went
    red here and nowhere else:

        >       assert "rotation_unavailable" not in result, result
        E       AssertionError: {'attempts': 2, 'centered': True, 'error_arcmin': 0.176842248926498, 'rotation': None, ...}
        E       assert 'rotation_unavailable' not in {'centered': True, 'error_arcmin': 0.176842248926498, 'attempts': 2, 'rotation': None, 'rotation_skipped': True, 'rotation_unavailable': True}
    """
    async def boom(*a, **k):
        raise DeviceError("clouds over the rotate solve")

    monkeypatch.setattr(sim_hub, "rotate_to_pa", boom)
    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.0)
    assert result["centered"] is True
    assert result["rotation_skipped"] is True
    assert result["rotation"] is None
    assert "rotation_unavailable" not in result, result
    # The existing failure warning, and only that one.
    warnings = _rotation_warnings(logs)
    assert len(warnings) == 1 and "failed" in warnings[0], warnings
