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
    redact_profile,
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


class _FakeSimDev:
    """Stands in for a connected sim device (backend='sim', no host/port)."""
    def __init__(self):
        self.backend = "sim"
        self.name = "Sim Camera"
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
    # ``primary_backend`` now defaults to "" (empty), NOT "sim" -- a truthy
    # "sim" default would silently resolve every unlisted role of a REAL rig
    # (including safety) to the simulator. A missing key on disk deserializes
    # to "", which is not "sim", so the ``_heal_sim_primary`` validator does
    # NOT fire here (it only heals a *stored* "sim" + real device rows); the
    # blank default is healed by derivation instead, in ``to_rigspec`` below.
    assert prof.primary_backend == ""
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
    # primary_backend is blank, so to_rigspec derives it from the device shape:
    # real device rows present -> "native" (NEVER "sim" -- a legacy Alpaca rig
    # must never inherit a sim primary that would fill unlisted roles, including
    # safety, with simulators).
    assert spec.primary == "native"


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
    # ``primary_backend`` defaults to "" (NOT "sim") -- a real device row must
    # never inherit a sim primary, which would fill unlisted roles (including
    # safety) with simulators. to_rigspec() derives "native" from the row.
    old = Profile(**{"id": "33333333-3333-3333-3333-333333333333",
                     "name": "Old", "devices": [
                         {"role": "camera", "backend": "alpaca"}]})
    assert old.primary_backend == ""
    assert old.devices[0].extra == {}
    assert old.to_rigspec().primary == "native"


def test_to_rigspec_empty_profile_defaults_to_sim():
    """A genuinely empty profile (no devices, no nina_host) has a blank literal
    ``primary_backend`` default (""), and to_rigspec() derives "sim" for it --
    the empty-rig fallback (nothing real to protect from a sim primary)."""
    p = Profile(name="empty")
    assert p.primary_backend == ""
    spec = p.to_rigspec()
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


# --------------------------------------------- _heal_sim_primary validator
#
# A ``primary_backend="sim"`` stored ALONGSIDE real (non-sim) device rows is
# (near-certainly) a fail-open artifact of the old default -- it would fill
# every unlisted role, including ``safety``, with a simulator. The validator
# heals it back to the derived primary at construction time (not just inside
# to_rigspec), and leaves a genuinely all-sim profile alone.

@pytest.mark.parametrize("devices, expected", [
    pytest.param(
        [ProfileDevice(role="camera", backend="alpaca", host="h", port=1)],
        "native",
        id="real-alpaca-row-heals-sim-to-derived-native",
    ),
    pytest.param(
        [],
        "sim",
        id="no-device-rows-genuinely-all-sim-left-alone",
    ),
    pytest.param(
        [ProfileDevice(role="camera", backend="sim")],
        "sim",
        # A device row that is ITSELF backend='sim' does not count as 'real' --
        # the healing check only fires on a REAL (non-sim) backend row, so this
        # profile is indistinguishable (for healing purposes) from the
        # no-device-rows case above and is also left alone.
        id="sim-backed-device-row-does-not-count-as-real",
    ),
])
def test_heal_sim_primary(devices, expected):
    """Profile(primary_backend='sim', devices=[...]) is healed at construction.

    A ``"sim"`` primary stored ALONGSIDE real (non-sim) device rows is
    (near-certainly) a fail-open artifact of the old default -- connecting such
    a profile would fill every unlisted role, including ``safety``, with a
    simulator, so the safety gate would read a fail-open always-SAFE monitor
    while a real mount runs. The validator coerces it back to the derived
    primary; a profile with no real (non-sim) device rows has nothing to
    protect and keeps 'sim'."""
    p = Profile(primary_backend="sim", devices=devices)
    assert p.primary_backend == expected
    # to_rigspec sees the already-healed value (a truthy primary_backend always
    # wins over derivation), so it agrees with the construction-time result.
    assert p.to_rigspec().primary == expected


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
    # the primary is stamped explicitly from the connected rig shape: a real
    # alpaca device row -> "native", NEVER left to fall back to "sim" (which
    # would silently fill unlisted roles, including safety, with simulators).
    assert p.primary_backend == "native"
    # persisted to the temp library
    assert lib.get(p.id).name == "My Rig"


@pytest.mark.asyncio
async def test_capture_profile_stamps_sim_primary_for_sim_rig(tmp_path, monkeypatch):
    """The counterpart of the alpaca capture above: a genuinely sim-mode rig
    (backend='sim' devices, no nina_client) captures with primary_backend
    stamped explicitly to 'sim' -- not left to inherit any stale default."""
    import astrodeck.config as config_mod
    import astrodeck.profiles as profiles_mod
    from astrodeck.config import ConfigStore
    from astrodeck.hub import Hub

    lib = ProfileLibrary(directory=tmp_path / "profiles")
    monkeypatch.setattr(profiles_mod, "profiles", lib)
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(hub_mod, "profiles", lib)
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(config_mod, "config_store", temp_store)

    hub = Hub()
    hub.devices = {"camera": _FakeSimDev()}
    hub.mode = "sim"
    p = await hub.capture_profile("Sim Rig")
    # a sim device isn't captured as a per-device row (only alpaca/nina rows
    # are), but the primary is still explicitly stamped "sim" for this mode.
    assert p.devices == []
    assert p.primary_backend == "sim"
    assert lib.get(p.id).primary_backend == "sim"


class _FakeNativeDev:
    """A connected NATIVE-driver device. Each native driver stamps its OWN
    backend name (``zwo-am5``, ``player-one``, ...) — never ``alpaca`` — which
    is precisely why the old alpaca-only capture recorded nothing on this rig."""
    def __init__(self, backend: str, name: str):
        self.backend = backend
        self.name = name
        self.connected = True


def _native_hub(tmp_path, monkeypatch):
    """A Hub with the module singletons pointed at ``tmp_path`` (never the real
    server/config dir), returned with the temp ProfileLibrary."""
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    import astrodeck.profiles as profiles_mod
    from astrodeck.config import ConfigStore
    from astrodeck.hub import Hub

    lib = ProfileLibrary(directory=tmp_path / "profiles")
    monkeypatch.setattr(profiles_mod, "profiles", lib)
    monkeypatch.setattr(hub_mod, "profiles", lib)
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    return Hub(), lib


@pytest.mark.asyncio
async def test_capture_profile_records_a_native_rig(tmp_path, monkeypatch):
    """THE blocker this guards: on a rig of native drivers, capture used to
    record ZERO device rows (it only looked at ``backend == "alpaca"``), and an
    empty profile resolves to ``primary="sim"`` — so boot auto-connect would
    bring the rig up on SIMULATORS, including a fail-open sim SafetyMonitor,
    with the console showing a connected rig.

    Capture reads the RigSpec the rig was actually connected with, so serial
    addressing (``transport``/``port_path``) and ``driver_id`` references
    survive the round trip."""
    from astrodeck.devices.backend import ConnSpec, RigSpec

    hub, lib = _native_hub(tmp_path, monkeypatch)
    hub._last_rigspec = RigSpec(primary="none", roles={
        "telescope": ConnSpec(backend="zwo-am5", role="telescope",
                              transport="serial", port_path="COM3"),
        "camera": ConnSpec(backend="player-one", role="camera",
                           driver_id="drv-poa", extra={"name": "Poseidon-C"}),
        "focuser": ConnSpec(backend="zwo-usb", role="focuser"),
    })
    hub.devices = {
        "telescope": _FakeNativeDev("zwo-am5", "AM5N"),
        "camera": _FakeNativeDev("player-one", "Poseidon-C"),
        "focuser": _FakeNativeDev("zwo-usb", "EAF"),
    }
    hub.mode = "alpaca"

    p = await hub.capture_profile("Native Rig")

    rows = {d.role: d for d in p.devices}
    assert set(rows) == {"telescope", "camera", "focuser"}, \
        "a native rig must capture its devices, not an empty profile"
    assert rows["telescope"].backend == "zwo-am5"
    assert rows["telescope"].transport == "serial"
    assert rows["telescope"].port_path == "COM3"
    assert rows["camera"].driver_id == "drv-poa"
    assert rows["camera"].name == "Poseidon-C"
    # the derived ``name`` key to_rigspec folds INTO extra is not written back
    # as extra — the row carries it in its own field.
    assert rows["camera"].extra == {}
    # and the primary must never be the simulator on a rig of real devices.
    assert p.primary_backend != "sim"
    assert {d.role for d in lib.get(p.id).devices} == {"telescope", "camera", "focuser"}


@pytest.mark.asyncio
async def test_capture_profile_skips_roles_that_did_not_come_up(tmp_path, monkeypatch,
                                                                bus_lines):
    """A role in the spec that is NOT live is left out — capturing it would
    persist a device the rig does not have. Saying so out loud is the point:
    a silently narrower profile is the same defect class in a new costume."""
    from astrodeck.devices.backend import ConnSpec, RigSpec

    hub, _lib_ = _native_hub(tmp_path, monkeypatch)
    hub._last_rigspec = RigSpec(primary="none", roles={
        "camera": ConnSpec(backend="player-one", role="camera"),
        "safety": ConnSpec(backend="zwo-usb", role="safety"),
    })
    hub.devices = {"camera": _FakeNativeDev("player-one", "Poseidon-C")}
    hub.mode = "alpaca"

    p = await hub.capture_profile("Half A Rig")

    assert {d.role for d in p.devices} == {"camera"}
    assert any("safety" in msg for _lvl, msg, _src in bus_lines), \
        f"the skipped role must be reported, got {bus_lines}"


# ------------------------------------------------- W2: extra-secret wire redaction
#
# A device row's ``extra`` (ConnSpec options) carries no credential in Stage B,
# but a future backend could stash one there — and GET /api/profiles/{id} is a
# VIEWER-visible read. ``redact_profile`` scrubs secret-bearing ``extra`` keys
# OVER THE WIRE while the persisted profile keeps the real value at-rest.

def test_redact_profile_scrubs_secret_extra_but_keeps_metadata():
    """A device ``extra`` secret is blanked with a ``_configured`` marker over the
    wire; benign metadata (name/port_path/dev_type) passes through unchanged."""
    p = Profile(name="Rig", devices=[ProfileDevice(
        role="guider", backend="phd2", name="ASI120",
        extra={"password": "hunter2", "name": "X", "port_path": "COM3",
               "pixel_scale_arcsec": 1.5})])
    wire = redact_profile(p)
    ex = wire["devices"][0]["extra"]
    # secret blanked + configured marker set
    assert ex["password"] == ""
    assert ex["password_configured"] is True
    # benign metadata untouched
    assert ex["name"] == "X"
    assert ex["port_path"] == "COM3"
    assert ex["pixel_scale_arcsec"] == 1.5
    # SOURCE never mutated — the real secret is still on the live Profile.
    assert p.devices[0].extra["password"] == "hunter2"


def test_redact_profile_at_rest_keeps_secret(tmp_path):
    """Redaction is over-the-wire ONLY: the persisted (at-rest) profile keeps the
    real secret so the rig can still connect; only the wire form is scrubbed."""
    lib = _lib(tmp_path)
    p = Profile(name="Rig", devices=[ProfileDevice(
        role="guider", backend="phd2",
        extra={"api_token": "sekret", "name": "X", "port_path": "COM3"})])
    lib.save(p)
    # at-rest: the real token survives a save/get round-trip untouched
    loaded = lib.get(p.id)
    assert loaded.devices[0].extra["api_token"] == "sekret"
    # over-the-wire: scrubbed + marked
    wire = redact_profile(loaded)
    assert wire["devices"][0]["extra"]["api_token"] == ""
    assert wire["devices"][0]["extra"]["api_token_configured"] is True
    assert wire["devices"][0]["extra"]["port_path"] == "COM3"


def test_redact_profile_covers_all_secret_key_shapes():
    """Every documented secret-marker substring is caught (case-insensitive), and
    a benign key that merely LOOKS adjacent (``username``) is left alone."""
    secret_keys = ["password", "PASSWD", "clientSecret", "access_token",
                   "apikey", "api_key", "credential", "passphrase",
                   "authToken", "session_key"]
    extra = {k: "x" for k in secret_keys}
    extra.update({"username": "bob", "host": "10.0.0.5", "dev_type": "camera"})
    p = Profile(name="Rig", devices=[ProfileDevice(role="camera", extra=extra)])
    ex = redact_profile(p)["devices"][0]["extra"]
    for k in secret_keys:
        assert ex[k] == "", f"{k} not scrubbed"
        assert ex[f"{k}_configured"] is True
    # non-secret keys pass through
    assert ex["username"] == "bob"
    assert ex["host"] == "10.0.0.5"
    assert ex["dev_type"] == "camera"


def test_redact_profile_accepts_dict_and_is_noop_for_rows():
    """``redact_profile`` accepts a dict (picker row) and, lacking a ``devices``
    list, returns it unchanged without mutating the input."""
    row = {"id": "x", "name": "R", "mode": "empty", "devices_count": 0}
    out = redact_profile(row)
    assert out == row
    assert out is not row  # deep-copied, never the same object


# --------------------------------------------------- clearing profile overrides
#
# #129. A profile's ``providers`` pins and its ``optics`` block are the layers
# that BEAT global config the moment the profile is active. The provenance
# readout can now name them; these tests pin the only way to remove one.


def test_clear_overrides_drops_one_provider_pin(tmp_path):
    lib = _lib(tmp_path)
    p = Profile(name="Rig", providers={"polar_align": "sim", "solve": "astap"})
    lib.save(p)
    row = lib.clear_overrides(p.id, providers=["polar_align"])
    # the row reports the survivor, and the file agrees with the row
    assert row["providers"] == {"solve": "astap"}
    assert lib.get(p.id).providers == {"solve": "astap"}


def test_clear_overrides_collapses_the_last_pin_to_none(tmp_path):
    """An empty dict and ``None`` mean the same thing to the resolver but not to
    a human reading the JSON; persist the collapsed form."""
    lib = _lib(tmp_path)
    p = Profile(name="Rig", providers={"guide": "auto"})
    lib.save(p)
    lib.clear_overrides(p.id, providers=["guide"])
    assert lib.get(p.id).providers is None


def test_clear_overrides_ignores_inert_keys_instead_of_refusing(tmp_path):
    """``providers`` is a bare dict that accepts anything an imported profile
    file carried. Rejecting the request over a key nothing reads would leave the
    user unable to clear the keys that ARE live."""
    lib = _lib(tmp_path)
    p = Profile(name="Rig", providers={"polar_align": "sim", "nonsense": "x"})
    lib.save(p)
    lib.clear_overrides(p.id, providers=["nonsense", "polar_align"])
    # the live pin is gone; the inert key is untouched rather than silently eaten
    assert lib.get(p.id).providers == {"nonsense": "x"}


def test_clear_overrides_drops_the_whole_optics_block(tmp_path):
    lib = _lib(tmp_path)
    p = Profile(name="Rig", optics=Optics(focal_length_mm=250.0),
                providers={"solve": "astap"})
    lib.save(p)
    lib.clear_overrides(p.id, optics=True)
    fresh = lib.get(p.id)
    assert fresh.optics is None
    # clearing optics must not touch the provider pins
    assert fresh.providers == {"solve": "astap"}


def test_clear_overrides_preserves_at_rest_device_secrets(tmp_path):
    """The reason this is a server-side mutation at all: a client read-modify-
    write would have to start from the wire-REDACTED GET, and writing that back
    would persist blanked credentials."""
    lib = _lib(tmp_path)
    p = Profile(name="Rig", providers={"guide": "sim"}, devices=[
        ProfileDevice(role="camera", extra={"password": "hunter2",
                                            "host": "10.0.0.5"})])
    lib.save(p)
    lib.clear_overrides(p.id, providers=["guide"])
    kept = lib.get(p.id)
    assert kept.devices[0].extra["password"] == "hunter2"
    assert kept.devices[0].extra["host"] == "10.0.0.5"


def test_clear_overrides_on_missing_profile_raises(tmp_path):
    """The routes map KeyError -> 404; nothing here may 500."""
    lib = _lib(tmp_path)
    with pytest.raises((KeyError, FileNotFoundError)):
        lib.clear_overrides(str(uuid.uuid4()), optics=True)


def test_clear_overrides_with_an_empty_request_changes_nothing(tmp_path):
    """A malformed/empty body must be inert, never destructive."""
    lib = _lib(tmp_path)
    p = Profile(name="Rig", providers={"guide": "sim"},
                optics=Optics(focal_length_mm=250.0))
    lib.save(p)
    lib.clear_overrides(p.id)
    fresh = lib.get(p.id)
    assert fresh.providers == {"guide": "sim"}
    assert fresh.optics is not None and fresh.optics.focal_length_mm == 250.0
