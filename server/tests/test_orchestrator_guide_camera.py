"""Task 13: the guide_camera role resolves from its OWN endpoint (native two-
vendor rigs) while the sim/NINA one-session accessor path is preserved."""
from astrodeck.devices import orchestrator as orch


class _Dev:
    def __init__(self, tag):
        self.tag = tag
        self.hardware = False
        self.role = ""


class _SessAccessor:
    """A sim/NINA-style session that owns the guide cam via the accessor."""
    def __init__(self, gc):
        self._gc = gc

    def guide_camera(self):
        return self._gc


def test_prefers_rig_guide_camera_over_accessor():
    # rig already has a guide_camera filled by its OWN endpoint (separate backend)
    rig_gc = _Dev("asi")
    rig = {"camera": _Dev("poseidon"), "guide_camera": rig_gc}
    picked = orch._pick_guide_camera(None, {}, rig)
    assert picked is rig_gc


def test_falls_back_to_camera_session_accessor():
    sim_gc = _Dev("sim-gc")

    class Conn:  # normalizes to a key present in sessions
        backend = "sim"
        host = ""
        port = 0
        dev_type = ""
        dev_num = 0
        role = "camera"
        port_path = None
        transport = "local"
        extra = {}

    conn = Conn()
    sessions = {orch._normalize(conn): _SessAccessor(sim_gc)}
    picked = orch._pick_guide_camera(conn, sessions, {})   # no rig guide_camera
    assert picked is sim_gc


def test_none_when_no_camera_and_no_rig():
    assert orch._pick_guide_camera(None, {}, {}) is None
