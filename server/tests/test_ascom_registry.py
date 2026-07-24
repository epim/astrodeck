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
        "Dome": {"ASCOM.Simulator.Dome": "Sim Dome"},  # real AlpacaDome -> offered
    }, )
    offers = reg.enumerate_offers_with(read)  # test hook: enumerate_offers over a reader
    roles = sorted(o["role"] for o in offers)
    # Dome now maps to the dome role (the real AlpacaDome client landed); camera
    # dual-offers as guide_camera.
    assert roles == ["camera", "dome", "guide_camera", "safety"]
    cam = [o for o in offers if o["role"] == "camera"][0]
    assert cam == {"role": "camera", "name": "Sim Cam",
                   "dev_type": "camera", "dev_num": 0,
                   "progid": "ASCOM.Simulator.Camera"}


def test_enumerate_is_empty_off_windows(monkeypatch):
    # Force the winreg-absent path: the module's default reader returns {} and
    # enumerate() yields no drivers, so nothing breaks on Mac/Linux.
    monkeypatch.setattr(reg, "_WINREG_OK", False, raising=False)
    assert reg.enumerate() == []


# --------------------------------------------------------------------------
# Direct coverage of _read_type_drivers — the ONLY code that actually touches
# `winreg` (dual-view HKLM walk, 64-bit-wins dedup, missing-key/missing-value
# OSError handling, .Close() lifecycle). We monkeypatch the module's `winreg`
# with a fake registry so these run cross-platform (Linux CI included) and pin
# the view-level logic the `enumerate(read=...)` seam bypasses. (COM-T1 review,
# Medium.)

_NO_VALUE = object()  # a ProgID subkey whose default value read raises (no name)


class _FakeTypeKey:
    """A "<Type> Drivers" key handle: holds {progid: value} for ONE view."""
    def __init__(self, entries):
        self.entries = entries
        self._progids = list(entries.keys())
        self.closed = False

    def Close(self):
        self.closed = True


class _FakeSubKey:
    """A ProgID subkey handle; its default value is the display name."""
    def __init__(self, value):
        self.value = value
        self.closed = False

    def Close(self):
        self.closed = True


class _FakeWinreg:
    """Minimal stand-in for stdlib `winreg` driving _read_type_drivers.

    `views` maps a WOW64 flag -> {base_path: {progid: value}}, so the 32- and
    64-bit registry views are independent trees keyed by the access flag the
    caller OR-s into KEY_READ (exactly how the real dual-view read works)."""
    HKEY_LOCAL_MACHINE = object()
    KEY_READ = 0x20019
    KEY_WOW64_32KEY = 0x0200
    KEY_WOW64_64KEY = 0x0100

    def __init__(self, views):
        self.views = views
        self.opened_type_keys = []  # every type-key handle we hand out (leak check)

    def OpenKey(self, root, subkey, reserved, access):
        if root is self.HKEY_LOCAL_MACHINE:
            flag = access & (self.KEY_WOW64_32KEY | self.KEY_WOW64_64KEY)
            view = self.views.get(flag, {})
            if subkey not in view:
                raise OSError(2, "type key absent in this view")
            k = _FakeTypeKey(view[subkey])
            self.opened_type_keys.append(k)
            return k
        # root is a type key -> open a ProgID subkey under it.
        if subkey not in root.entries:
            raise OSError(2, "progid subkey absent")
        return _FakeSubKey(root.entries[subkey])

    def EnumKey(self, key, i):
        try:
            return key._progids[i]
        except IndexError:
            raise OSError(259, "no more items")

    def QueryValueEx(self, sub, name):
        if sub.value is _NO_VALUE:
            raise OSError(2, "no default value")
        return (sub.value, 1)  # (data, REG_SZ)


def _install(monkeypatch, fake):
    monkeypatch.setattr(reg, "winreg", fake, raising=False)
    monkeypatch.setattr(reg, "_WINREG_OK", True, raising=False)


def test_read_type_drivers_dual_view_64_wins_and_default_value(monkeypatch):
    base = r"SOFTWARE\ASCOM\Camera Drivers"
    fake = _FakeWinreg({
        _FakeWinreg.KEY_WOW64_32KEY: {base: {
            "ASCOM.Simulator.Camera": "Cam (32-bit)",   # overlaps -> 64 wins
            "ASCOM.OnlyIn32.Camera": "Only 32",         # 32-only -> kept
        }},
        _FakeWinreg.KEY_WOW64_64KEY: {base: {
            "ASCOM.Simulator.Camera": "Cam (64-bit)",   # wins the overlap
            "ASCOM.NoName.Camera": _NO_VALUE,           # default-value read fails
        }},
    })
    _install(monkeypatch, fake)
    out = reg._read_type_drivers("Camera")
    assert out["ASCOM.Simulator.Camera"] == "Cam (64-bit)"    # 64-bit view wins
    assert out["ASCOM.OnlyIn32.Camera"] == "Only 32"          # 32-only merged in
    assert out["ASCOM.NoName.Camera"] == "ASCOM.NoName.Camera"  # falls back to progid
    # No key-handle leak: every "<Type> Drivers" key we opened was closed.
    assert fake.opened_type_keys and all(k.closed for k in fake.opened_type_keys)


def test_read_type_drivers_missing_in_one_view_uses_other(monkeypatch):
    base = r"SOFTWARE\ASCOM\Focuser Drivers"
    fake = _FakeWinreg({
        _FakeWinreg.KEY_WOW64_32KEY: {},  # no Focuser key in the 32-bit view
        _FakeWinreg.KEY_WOW64_64KEY: {base: {"ASCOM.Simulator.Focuser": "Sim Focuser"}},
    })
    _install(monkeypatch, fake)
    # The 32-bit OpenKey raises OSError -> `continue`; the 64-bit view still enumerates.
    assert reg._read_type_drivers("Focuser") == {"ASCOM.Simulator.Focuser": "Sim Focuser"}


def test_read_type_drivers_missing_in_both_views_is_empty(monkeypatch):
    fake = _FakeWinreg({
        _FakeWinreg.KEY_WOW64_32KEY: {},
        _FakeWinreg.KEY_WOW64_64KEY: {},
    })
    _install(monkeypatch, fake)
    # Both OpenKeys raise -> both `continue` -> empty dict, no exception escapes.
    assert reg._read_type_drivers("Dome") == {}
