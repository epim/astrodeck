"""Equipment profiles: uuid identity, rename-in-place, mode derivation,
capture-from-device-identity, the per-device apply outcome, and path-traversal
hardening of the client-controllable id."""
import uuid

import pytest

from astrodeck.config import Optics
from astrodeck.profiles import (
    LibraryFull,
    MAX_PROFILES,
    Profile,
    ProfileDevice,
    ProfileLibrary,
)


def _lib(tmp_path):
    return ProfileLibrary(directory=tmp_path / "profiles")


def test_mode_is_derived():
    assert Profile(name="empty").mode == "empty"
    alpaca = Profile(name="a", devices=[
        ProfileDevice(role="camera", backend="alpaca", host="h", port=1)])
    assert alpaca.mode == "alpaca"
    mixed = Profile(name="m", devices=[
        ProfileDevice(role="camera", backend="alpaca"),
        ProfileDevice(role="telescope", backend="nina")])
    assert mixed.mode == "mixed"
    nina = Profile(name="n", nina_host="127.0.0.1")
    assert nina.mode == "nina"


def test_save_get_round_trip(tmp_path):
    lib = _lib(tmp_path)
    p = Profile(name="Rig 1", devices=[
        ProfileDevice(role="camera", backend="alpaca", host="10.0.0.5",
                      port=11111, dev_type="camera", dev_num=0, name="ASI2600")],
        optics=Optics(focal_length_mm=600))
    row = lib.save(p)
    assert row["id"] == p.id and row["mode"] == "alpaca" and row["devices_count"] == 1
    loaded = lib.get(p.id)
    assert loaded.name == "Rig 1"
    assert loaded.devices[0].host == "10.0.0.5"
    assert loaded.optics.focal_length_mm == 600


def test_rename_keeps_id_and_file(tmp_path):
    lib = _lib(tmp_path)
    p = Profile(name="Old")
    lib.save(p)
    row = lib.rename(p.id, "New Name")
    assert row["id"] == p.id                 # immutable identity
    assert row["name"] == "New Name"
    # exactly one file (no orphan from the rename)
    files = list((tmp_path / "profiles").glob("*.json"))
    assert len(files) == 1
    assert files[0].stem == p.id


def test_name_collision_detection(tmp_path):
    lib = _lib(tmp_path)
    a = Profile(name="Backyard")
    lib.save(a)
    assert lib.name_exists("Backyard") is True
    assert lib.name_exists("Backyard", exclude_id=a.id) is False  # exclude self
    assert lib.name_exists("Other") is False


def test_list_marks_active(tmp_path):
    lib = _lib(tmp_path)
    a, b = Profile(name="A"), Profile(name="B")
    lib.save(a)
    lib.save(b)
    rows = lib.list(active_id=b.id)
    by_id = {r["id"]: r for r in rows}
    assert by_id[b.id]["active"] is True
    assert by_id[a.id]["active"] is False


def test_delete_removes_file(tmp_path):
    lib = _lib(tmp_path)
    p = Profile(name="Gone")
    lib.save(p)
    lib.delete(p.id)
    assert not (tmp_path / "profiles" / f"{p.id}.json").exists()
    assert lib.active(p.id) is None          # gone → no active profile


def test_active_none_for_missing(tmp_path):
    lib = _lib(tmp_path)
    assert lib.active(None) is None
    assert lib.active("does-not-exist") is None


class _FakeAlpacaDev:
    """Stands in for a connected _AlpacaDevice (the A.6 device-identity shape)."""
    def __init__(self):
        self.backend = "alpaca"
        self.host = "192.168.1.50"
        self.port = 11111
        self.dev_type = "camera"
        self.dev_num = 0
        self.name = "ASI2600MC"
        self.connected = True


# ---------------------------------------------------------- path-traversal guard

# Decoded forms of the exploit ids (the API layer / Starlette decode %2F, %5C
# before they ever reach the library, so the library sees these literals).
TRAVERSAL_IDS = [
    "../../pwned",
    "..\\victim",            # decoded "..%5Cvictim" — the Windows backslash vector
    "../astrodeck",          # would exfiltrate the AppConfig if unguarded
    "subdir/escape",
    "C:\\Windows\\System32\\evil",   # absolute path
    "/etc/passwd",           # absolute POSIX path
]


@pytest.mark.parametrize("bad_id", TRAVERSAL_IDS)
def test_path_traversal_ids_raise_keyerror(tmp_path, bad_id):
    """A client-controlled id that escapes the profiles dir must raise KeyError
    (the API maps KeyError → 404) on every id-keyed operation, and never touch a
    file outside the directory."""
    lib = _lib(tmp_path)
    with pytest.raises(KeyError):
        lib.get(bad_id)
    with pytest.raises(KeyError):
        lib.save(Profile(id=bad_id, name="evil"))
    with pytest.raises(KeyError):
        lib.delete(bad_id)
    with pytest.raises(KeyError):
        lib.rename(bad_id, "x")
    # active() swallows the traversal and returns None (never raises to callers).
    assert lib.active(bad_id) is None


def test_no_file_written_outside_dir_on_traversal(tmp_path):
    """Saving a traversal id must not create a file above the profiles dir."""
    lib = _lib(tmp_path)
    sentinel = tmp_path / "pwned.json"
    with pytest.raises(KeyError):
        lib.save(Profile(id="../pwned", name="evil"))
    assert not sentinel.exists()


def test_normal_uuid_id_round_trips(tmp_path):
    """A well-formed uuid id is accepted and round-trips through save/get."""
    lib = _lib(tmp_path)
    pid = str(uuid.uuid4())
    p = Profile(id=pid, name="Good Rig")
    row = lib.save(p)
    assert row["id"] == pid
    assert lib.get(pid).name == "Good Rig"
    assert (tmp_path / "profiles" / f"{pid}.json").exists()


# ------------------------------------------------------------------- quota cap

def test_save_rejects_new_record_at_cap(tmp_path, monkeypatch):
    """At the cap, a *new* profile is rejected with LibraryFull; upserting an
    existing one is still allowed."""
    import astrodeck.profiles as profiles_mod
    monkeypatch.setattr(profiles_mod, "MAX_PROFILES", 2)
    lib = _lib(tmp_path)
    a = Profile(name="A")
    b = Profile(name="B")
    lib.save(a)
    lib.save(b)
    with pytest.raises(LibraryFull):
        lib.save(Profile(name="C"))          # over cap → rejected
    # upsert of an existing id is still fine even at cap
    a.name = "A renamed"
    assert lib.save(a)["name"] == "A renamed"


@pytest.mark.asyncio
async def test_capture_profile_reads_device_identity(tmp_path, monkeypatch):
    import astrodeck.config as config_mod
    import astrodeck.profiles as profiles_mod
    from astrodeck.config import ConfigStore
    from astrodeck.hub import Hub

    lib = ProfileLibrary(directory=tmp_path / "profiles")
    # point the hub's module-level singletons at temp locations so the test
    # never touches the real server/config dir.
    monkeypatch.setattr(profiles_mod, "profiles", lib)
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(hub_mod, "profiles", lib)
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(config_mod, "config_store", temp_store)

    hub = Hub()
    hub.devices = {"camera": _FakeAlpacaDev()}
    hub.mode = "alpaca"
    p = await hub.capture_profile("My Rig")
    assert p.devices[0].backend == "alpaca"
    assert p.devices[0].host == "192.168.1.50"
    assert p.devices[0].dev_type == "camera"
    # persisted to the temp library
    assert lib.get(p.id).name == "My Rig"
