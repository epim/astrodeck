"""COM-T7 portable coverage for the parts of the acceptance gate that DON'T need
a real ASCOM Platform / comtypes / simulators — so the task has real green
coverage on this box, not just a skipped gate:

  1. doc-grep: docs/comhost.md carries the runbook + the on-ASCOM-box validation
     checklist + the astrotown migration note (guards the deliverable from rot).
  2. skip-logic: the gate's own skip decision (`_sim` / `_NEED` / the reason
     string) is correct — it skips with an informative reason when no simulators
     are registered, and its `_NEED` names the four real Simulator ProgIDs.
  3. real-subprocess lifecycle (Windows only): ComHostManager spawns the REAL
     `python -m astrodeck.comhost` child, learns its ephemeral loopback port,
     health-checks it, and tears it down without orphaning — the gate's spawn
     scaffold end-to-end, minus the real COM device connect.
"""
import sys
from pathlib import Path

import httpx
import pytest

_DOC = Path(__file__).resolve().parents[2] / "docs" / "comhost.md"


# --------------------------------------------------------------------------- 1
def test_comhost_doc_has_runbook_and_migration_checklist():
    assert _DOC.is_file(), f"missing operator doc: {_DOC}"
    text = _DOC.read_text(encoding="utf-8")
    required = [
        # single-install framing + the backend name
        "single-install",
        "ascom-local",
        # replaces the manually-installed ASCOM Remote at :11111
        "ASCOM Remote",
        "11111",
        # prerequisite + install
        "ASCOM Platform",
        ".[comhost]",
        # runbook: how to run the gate + what a pass proves
        "test_ascom_local_gate.py",
        # lifecycle facts
        "ephemeral",
        "deterministic",
        "device in use",
        # the on-ASCOM-box validation checklist (deferred real-run checks)
        "On-ASCOM-box validation checklist",
        "comtypes",     # first real comtypes connect
        "wedged",       # fault-eviction vs a real wedged driver (COM-T2)
        "evict",
        "Park()",       # COM-T3 real-mount slow-op vs deadline
        "SyncToCoordinates",
        "30s",          # the per-call deadline
        # migration note (spec §7)
        "migration",
    ]
    missing = [s for s in required if s not in text]
    assert not missing, f"docs/comhost.md missing required content: {missing}"


# --------------------------------------------------------------------------- 2
def test_gate_skip_logic_is_correct_and_informative():
    import tests.test_ascom_local_gate as gate  # importing runs its pytestmark

    # The four roles the gate needs, mapped to the real Simulator ProgIDs.
    assert gate._NEED == {
        "telescope": "ASCOM.Simulator.Telescope",
        "camera": "ASCOM.Simulator.Camera",
        "focuser": "ASCOM.Simulator.Focuser",
        "filterwheel": "ASCOM.Simulator.FilterWheel",
    }

    # On this box (no ASCOM Platform) the skip condition holds and names the sims.
    mark = gate.pytestmark
    assert mark.name == "skipif"
    assert mark.args[0] is True, "gate should be skipping (no simulators here)"
    assert "Simulators" in mark.kwargs["reason"]

    # _sim is honestly False for an unregistered driver (empty registry here / the
    # platform short-circuit off Windows).
    assert gate._sim("telescope", "ASCOM.Simulator.Telescope") is False


def test_gate_sim_helper_reads_the_registry(monkeypatch):
    """`_sim` reflects the enumeration: a fake 'registered' set flips it True on
    Windows; off Windows the platform guard keeps it False by design (correct
    degrade). Either way it's the enumeration that decides — no hidden state."""
    import tests.test_ascom_local_gate as gate
    from astrodeck.devices.ascom_registry import AscomDriver

    fake = [AscomDriver("Telescope", "telescope", "ASCOM.Simulator.Telescope",
                        "Sim", 0)]
    monkeypatch.setattr(gate.ascom_registry, "enumerate", lambda: fake)
    if sys.platform == "win32":
        assert gate._sim("telescope", "ASCOM.Simulator.Telescope") is True
        assert gate._num("telescope", "ASCOM.Simulator.Telescope") == 0
    else:
        assert gate._sim("telescope", "ASCOM.Simulator.Telescope") is False


# --------------------------------------------------------------------------- 3
@pytest.mark.skipif(sys.platform != "win32",
                    reason="ascom-local / comhost spawn is Windows-only")
@pytest.mark.asyncio
async def test_manager_spawns_real_comhost_subprocess_lifecycle(tmp_path):
    """The gate's spawn scaffold with a REAL subprocess (no COM, no sims): the
    manager spawns `python -m astrodeck.comhost`, learns the ephemeral port,
    health-checks the management API, then stops without leaving a portfile.
    A unique tmp portfile keeps this collision-free under xdist."""
    from astrodeck.comhost.manager import ComHostManager

    pf = str(tmp_path / "comhost.json")
    mgr = ComHostManager(portfile=pf)          # REAL default spawn
    try:
        port = await mgr.ensure()
        assert isinstance(port, int) and port > 0
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(
                f"http://127.0.0.1:{port}/management/v1/configureddevices")
        assert r.status_code == 200
        body = r.json()
        assert "Value" in body and isinstance(body["Value"], list)
        # idempotent: same port, no second spawn
        assert await mgr.ensure() == port
    finally:
        mgr.stop()
    assert not Path(pf).exists(), "clean stop() must remove the portfile"
