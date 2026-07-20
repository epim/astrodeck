"""Driver framework: manifest, safety flag, serial addressing, registry-derived
driver types, and entry-point discovery."""
from astrodeck.devices import backends as _backends  # noqa: F401  (registration)
from astrodeck.devices.backend import BACKENDS, list_backends


def _by_name():
    return {b["name"]: b for b in list_backends()}


def test_list_backends_emits_manifest_fields():
    rows = list_backends()
    assert rows, "built-in backends must be registered"
    keys = {"name", "label", "roles", "discoverable", "version", "author",
            "min_app_version", "transport", "hardware", "driver_type"}
    for row in rows:
        assert keys <= set(row), f"missing manifest keys in {row['name']}"


def test_builtin_hardware_flags():
    m = _by_name()
    assert m["native"]["hardware"] is True
    assert m["nina"]["hardware"] is True
    assert m["sim"]["hardware"] is False
    assert m["phd2"]["hardware"] is False


def test_builtin_driver_types_reproduce_todays_map():
    m = _by_name()
    assert m["nina"]["driver_type"] == "nina"
    assert m["native"]["driver_type"] == "alpaca"
    assert m["phd2"]["driver_type"] == "phd2"
    assert m["sim"]["driver_type"] == ""
