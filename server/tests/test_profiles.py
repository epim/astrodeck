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


# ------------------------------------------------ Stage B: RigSpec persistence
#
# A persisted Profile must map onto a live RigSpec (the single Profile->RigSpec
# mapper the hub + boot path both call). These cover the three migration cases
# the spec T4(5) pins: legacy ``alpaca``->``native`` alias, legacy ``nina_host``-
# only, and the new ``primary_backend`` + per-device ``extra`` fields round-trip.

def test_to_rigspec_legacy_alpaca_aliases_to_native():
    """T4(5): a Profile in the OLD on-disk schema (a ProfileDevice with
    backend="alpaca", no extra/primary_backend) deserializes, and to_rigspec maps
    backend="alpaca" -> ConnSpec(backend="native") via the migration alias -- NOT
    a KeyError in get_backend."""
    import astrodeck.devices.backends as _b  # noqa: F401 -- registration side effect
    from astrodeck.devices.backend import get_backend

    # the literal old-schema dict (no ``extra``, no ``primary_backend`` keys)
    legacy_json = {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "Legacy Alpaca Rig",
        "devices": [
            {"role": "camera", "backend": "alpaca", "host": "10.0.0.5",
             "port": 11111, "dev_type": "camera", "dev_num": 0, "name": "ASI2600"},
            {"role": "telescope", "backend": "alpaca", "host": "10.0.0.5",
             "port": 11111, "dev_type": "telescope", "dev_num": 0},
        ],
    }
    prof = Profile(**legacy_json)            # old JSON still deserializes
    assert prof.primary_backend == "sim"     # field defaulted (forward-compat)
    spec = prof.to_rigspec()
    # every alpaca device is now on the ``native`` backend (the alias), and that
    # backend is registered (no KeyError).
    assert spec.roles["camera"].backend == "native"
    assert spec.roles["telescope"].backend == "native"
    get_backend(spec.roles["camera"].backend)   # would KeyError if unmapped
    # device identity preserved through the mapping; the display name folds into
    # ConnSpec.extra['name'] so the native session can label the device.
    cam = spec.roles["camera"]
    assert cam.host == "10.0.0.5" and cam.port == 11111 and cam.dev_type == "camera"
    assert cam.extra.get("name") == "ASI2600"
    # An old-schema dict deserializes with the default primary_backend="sim"
    # (truthy), so the pinned ``primary or derived`` keeps "sim". The per-role
    # native overrides above still drive the device connections; the primary is
    # only the fallback for roles WITHOUT an override. The derivation path is
    # exercised explicitly below via an empty primary_backend.
    assert spec.primary == "sim"


def test_to_rigspec_derives_native_when_primary_blank():
    """When ``primary_backend`` is explicitly blank (a hand-written/migrated
    profile), the device-shape derivation kicks in: any device row -> 'native'."""
    import astrodeck.devices.backends as _b  # noqa: F401 -- registration side effect

    p = Profile(name="d", primary_backend="", devices=[
        ProfileDevice(role="camera", backend="alpaca", host="h", port=1)])
    assert p.to_rigspec().primary == "native"
    # blank + nina_host + no rows -> nina
    n = Profile(name="n", primary_backend="", nina_host="127.0.0.1")
    assert n.to_rigspec().primary == "nina"
    # blank + nothing -> sim
    e = Profile(name="e", primary_backend="")
    assert e.to_rigspec().primary == "sim"


def test_to_rigspec_legacy_nina_host_only():
    """T4(5): a legacy nina_host-only profile (no device rows) deserializes and
    maps to RigSpec(primary="nina", roles={}); requested roles then resolve from
    NINA's fillable set."""
    import astrodeck.devices.backends as _b  # noqa: F401 -- registration side effect
    from astrodeck.devices.backend import get_backend

    prof = Profile(**{"id": "22222222-2222-2222-2222-222222222222",
                      "name": "Legacy NINA", "nina_host": "127.0.0.1"})
    spec = prof.to_rigspec()
    assert spec.primary == "nina"
    assert spec.roles == {}
    # a role with no override resolves to a default ConnSpec on the primary, so
    # the requested roles are exactly NINA's fillable set.
    nina_roles = set(get_backend("nina").roles)
    assert "camera" in nina_roles
    assert spec.resolve("camera").backend == "nina"


def test_profile_new_fields_round_trip(tmp_path):
    """New fields (primary_backend + per-device extra) save/load unchanged, and an
    old profile JSON without these keys still loads via pydantic defaults."""
    lib = _lib(tmp_path)
    p = Profile(
        name="Native Rig",
        primary_backend="native",
        devices=[ProfileDevice(role="guider", backend="phd2",
                               extra={"pixel_scale_arcsec": 1.5})],
    )
    lib.save(p)
    loaded = lib.get(p.id)
    assert loaded.primary_backend == "native"
    assert loaded.devices[0].extra == {"pixel_scale_arcsec": 1.5}
    # the extra survives into the RigSpec ConnSpec verbatim.
    spec = loaded.to_rigspec()
    assert spec.roles["guider"].backend == "phd2"
    assert spec.roles["guider"].extra.get("pixel_scale_arcsec") == 1.5
    # an OLD profile json with neither new key still loads (defaults fill in).
    old = Profile(**{"id": "33333333-3333-3333-3333-333333333333",
                     "name": "Old", "devices": [
                         {"role": "camera", "backend": "alpaca"}]})
    assert old.primary_backend == "sim"
    assert old.devices[0].extra == {}


def test_to_rigspec_empty_profile_defaults_to_sim():
    """A genuinely empty profile (no devices, no nina_host) keeps the literal
    primary_backend default of 'sim' -- the empty-rig fallback."""
    spec = Profile(name="empty").to_rigspec()
    assert spec.primary == "sim"
    assert spec.roles == {}


def test_to_rigspec_explicit_primary_wins_over_derived():
    """An explicit non-default primary_backend is honored over the legacy
    device-shape derivation (so a user can pin e.g. a sim primary on a rig that
    also carries native device rows)."""
    p = Profile(name="mixed", primary_backend="nina", devices=[
        ProfileDevice(role="camera", backend="alpaca", host="h", port=1)])
    spec = p.to_rigspec()
    assert spec.primary == "nina"               # explicit field, not derived
    assert spec.roles["camera"].backend == "native"  # device row still aliased


@pytest.mark.asyncio
async def test_to_rigspec_connects_a_sim_rig(monkeypatch):
    """End-to-end shape check on the hostless sim backend (no network): a profile
    with primary='sim' connects through the orchestrator and yields a live rig
    keyed by role -- proving to_rigspec produces an orchestrator-consumable spec."""
    import astrodeck.devices.backends as _b  # noqa: F401 -- registration side effect
    from astrodeck.devices.orchestrator import connect_profile

    spec = Profile(name="Sim", primary_backend="sim").to_rigspec()
    result = await connect_profile(spec)
    try:
        assert "camera" in result.rig and "telescope" in result.rig
        # every requested role got a tri-state RoleResult.
        assert {r.role for r in result.results}
        assert all(rr.attempted for rr in result.results if rr.ok)
    finally:
        for sess in result.sessions.values():
            await sess.close()


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
