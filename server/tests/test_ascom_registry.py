"""COM-T1: ASCOM registry enumeration is deterministic, dual-view, and
mockable (inject a fake per-type reader — no real registry, runs on any OS)."""
import astrodeck.devices.ascom_registry as reg


def _fake_reader(view_map):
    """Return a read(ascom_type) that serves from a {ascom_type: {progid: name}}
    map, ignoring the view (tests merge is exercised via progid overlap)."""
    def read(ascom_type):
        return dict(view_map.get(ascom_type, {}))
    return read


def test_enumerate_assigns_stable_sorted_dev_nums():
    read = _fake_reader({
        "Camera": {"ASCOM.Simulator.Camera": "Sim Cam",
                   "ASCOM.ASICamera2.Camera": "ASI"},
        "Telescope": {"ASCOM.Simulator.Telescope": "Sim Scope"},
    })
    drivers = reg.enumerate(read=read)
    cams = sorted([d for d in drivers if d.dev_type == "camera"],
                  key=lambda d: d.dev_num)
    # sorted case-insensitively by progid: ASICamera2 (dev 0) < Simulator (dev 1)
    assert [(d.progid, d.dev_num) for d in cams] == [
        ("ASCOM.ASICamera2.Camera", 0), ("ASCOM.Simulator.Camera", 1)]
    scope = [d for d in drivers if d.dev_type == "telescope"][0]
    assert scope.dev_num == 0
    assert scope.ascom_type == "Telescope"


def test_progid_for_reverse_lookup_matches_enumerate():
    read = _fake_reader({"Focuser": {"ASCOM.Simulator.Focuser": "Sim Focuser"}})
    drivers = reg.enumerate(read=read)
    assert reg.progid_for("focuser", 0, drivers) == "ASCOM.Simulator.Focuser"
    assert reg.progid_for("focuser", 7, drivers) is None


def test_enumerate_offers_maps_roles_and_dual_offers_guide_camera():
    read = _fake_reader({
        "Camera": {"ASCOM.Simulator.Camera": "Sim Cam"},
        "SafetyMonitor": {"ASCOM.Simulator.SafetyMonitor": "Sim Safety"},
        "Dome": {"ASCOM.Simulator.Dome": "Sim Dome"},  # no role -> skipped
    }, )
    offers = reg.enumerate_offers_with(read)  # test hook: enumerate_offers over a reader
    roles = sorted(o["role"] for o in offers)
    assert roles == ["camera", "guide_camera", "safety"]  # no dome; camera dual-offers
    cam = [o for o in offers if o["role"] == "camera"][0]
    assert cam == {"role": "camera", "name": "Sim Cam",
                   "dev_type": "camera", "dev_num": 0,
                   "progid": "ASCOM.Simulator.Camera"}


def test_enumerate_is_empty_off_windows(monkeypatch):
    # Force the winreg-absent path: the module's default reader returns {} and
    # enumerate() yields no drivers, so nothing breaks on Mac/Linux.
    monkeypatch.setattr(reg, "_WINREG_OK", False, raising=False)
    assert reg.enumerate() == []
