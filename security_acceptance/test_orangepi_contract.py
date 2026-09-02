"""Target-state contract for Orange Pi first-boot Wi-Fi provisioning.

The tests are host-safe: they never touch networking, netplan, systemd, or the
real appliance state paths.  Hardware-only checks remain in the execution doc.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import re
import stat
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROVISIONER = ROOT / "orangepi5" / "provision" / "astrodeck-provision.py"
SERVICE = ROOT / "orangepi5" / "provision" / "astrodeck-provision.service"
ROOTFS_INSTALLER = ROOT / "orangepi5" / "provision" / "install-to-rootfs.sh"
RECOVERY_RECORD_SERVICE = (
    ROOT / "orangepi5" / "provision" / "astrodeck-recovery-record.service"
)
RECOVERY_CLEAR_SERVICE = (
    ROOT / "orangepi5" / "provision" / "astrodeck-recovery-clear.service"
)
RECOVERY_CLEAR_TIMER = (
    ROOT / "orangepi5" / "provision" / "astrodeck-recovery-clear.timer"
)


def _load_provisioner():
    spec = importlib.util.spec_from_file_location(
        "astrodeck_security_acceptance_provision", PROVISIONER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _require(module: Any, name: str):
    value = getattr(module, name, None)
    if value is None:
        pytest.fail(
            f"Orange Pi security contract is missing {name}(); "
            "implement the execution design before changing this test"
        )
    return value


def _field(value: Any, name: str):
    if isinstance(value, dict):
        return value[name]
    return getattr(value, name)


def test_no_shared_factory_password_and_no_secret_value_is_logged():
    source = PROVISIONER.read_text(encoding="utf-8")
    assert not re.search(
        r"(?m)^\s*AP_PSK\s*=\s*(['\"])astrodeck\1", source, re.IGNORECASE
    ), "a public password must not unlock every shipped appliance"

    tree = ast.parse(source)
    weak_secret_assignments: list[str] = []
    leaking_calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            names = [
                target.id.lower()
                for target in targets
                if isinstance(target, ast.Name)
            ]
            if (
                any(part in name for name in names for part in ("psk", "password", "secret"))
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and value.value.lower() in {"astrodeck", "password", "changeme", "default"}
            ):
                weak_secret_assignments.append(ast.unparse(node))
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "log":
            continue
        for formatted in ast.walk(node.args[0]):
            if isinstance(formatted, ast.FormattedValue):
                expression = ast.unparse(formatted.value).lower()
                if "psk" in expression or "password" in expression:
                    leaking_calls.append(ast.unparse(node))
    assert not weak_secret_assignments, (
        f"shared/default setup secret remains: {weak_secret_assignments}"
    )
    assert not leaking_calls, f"setup secret is interpolated into logs: {leaking_calls}"

    provisioner = _load_provisioner()
    ap_up = _require(provisioner, "ap_up")
    assert list(inspect.signature(ap_up).parameters)[:2] == ["ssid", "psk"]
    ap_tree = ast.parse(inspect.getsource(ap_up))
    for call in (node for node in ast.walk(ap_tree) if isinstance(node, ast.Call)):
        called = ast.unparse(call.func).lower()
        arguments = " ".join(ast.unparse(arg).lower() for arg in call.args)
        if called == "run":
            assert "psk" not in arguments and "password" not in arguments, (
                "setup credential was placed in a subprocess argv that is logged/visible"
            )
    assert "AP_PSK" not in inspect.getsource(provisioner.main_run)


def test_setup_password_format_and_generation_are_per_device():
    provisioner = _load_provisioner()
    generate = _require(provisioner, "generate_setup_password")
    generator_source = inspect.getsource(generate)
    assert "secrets.choice" in generator_source
    assert not re.search(r"(?i)(serial|mac|uuid|getrandbits|random\.)", generator_source)

    generated = {generate() for _ in range(128)}
    assert len(generated) == 128
    assert all(re.fullmatch(r"[a-z0-9]{4}(?:-[a-z0-9]{4}){2}", p) for p in generated)
    assert "astrodeck" not in generated


def test_commissioned_identity_is_exclusive_private_and_stable(tmp_path: Path):
    provisioner = _load_provisioner()
    commission = _require(provisioner, "commission_setup_identity")
    load = _require(provisioner, "load_setup_identity")

    first_path = tmp_path / "unit-a" / "setup-identity.json"
    second_path = tmp_path / "unit-b" / "setup-identity.json"
    first = commission(first_path, ssid="AstroDeck-1001", now=1000.0)
    second = commission(second_path, ssid="AstroDeck-1002", now=1000.0)

    assert _field(first, "schema") == 1
    assert _field(first, "setup_ssid") == "AstroDeck-1001"
    assert _field(first, "generation") == 1
    assert _field(first, "factory_psk") == _field(first, "active_psk")
    assert _field(first, "factory_psk") != _field(second, "factory_psk")
    authorization = _field(first, "ap_authorization")
    assert _field(authorization, "armed") is True
    assert _field(authorization, "reason") == "factory"
    assert _field(load(first_path), "factory_psk") == _field(first, "factory_psk")

    with pytest.raises(FileExistsError):
        commission(first_path, ssid="AstroDeck-1001", now=1001.0)

    if os.name == "posix":
        assert stat.S_IMODE(first_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(first_path.stat().st_mode) == 0o600

        first_path.chmod(0o644)
        with pytest.raises((PermissionError, ValueError, RuntimeError, OSError)):
            load(first_path)


def test_identity_loader_fails_closed_for_missing_malformed_or_symlinked_state(
    tmp_path: Path,
):
    provisioner = _load_provisioner()
    load = _require(provisioner, "load_setup_identity")

    with pytest.raises((FileNotFoundError, ValueError, RuntimeError, OSError)):
        load(tmp_path / "missing.json")

    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"schema": 1, "factory_psk": "astrodeck"}', encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError, OSError)):
        load(malformed)

    non_regular = tmp_path / "directory.json"
    non_regular.mkdir()
    with pytest.raises((ValueError, RuntimeError, OSError)):
        load(non_regular)

    victim = tmp_path / "victim.json"
    victim.write_text("do not read as an identity", encoding="utf-8")
    link = tmp_path / "identity-link.json"
    try:
        link.symlink_to(victim)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a file symlink")
    with pytest.raises((ValueError, RuntimeError, OSError)):
        load(link)


def test_ap_authorization_is_one_shot_and_portal_is_time_bounded(tmp_path: Path):
    provisioner = _load_provisioner()
    commission = _require(provisioner, "commission_setup_identity")
    consume = _require(provisioner, "consume_ap_authorization")

    identity_path = tmp_path / "setup-identity.json"
    commission(identity_path, ssid="AstroDeck-BEEF", now=1000.0)

    assert consume(identity_path, now=1001.0) is True
    assert consume(identity_path, now=1002.0) is False
    assert int(_require(provisioner, "PORTAL_LIFETIME_S")) == 15 * 60

    main_source = inspect.getsource(provisioner.main_run)
    assert "consume_ap_authorization" in main_source
    assert "PORTAL_LIFETIME_S" in main_source


def test_three_distinct_short_boots_rearm_the_printed_factory_credential(
    tmp_path: Path,
):
    provisioner = _load_provisioner()
    commission = _require(provisioner, "commission_setup_identity")
    consume = _require(provisioner, "consume_ap_authorization")
    rotate = _require(provisioner, "rotate_setup_password")
    record = _require(provisioner, "record_short_boot")
    load = _require(provisioner, "load_setup_identity")

    identity_path = tmp_path / "setup-identity.json"
    original = commission(identity_path, ssid="AstroDeck-BEEF", now=1000.0)
    factory_psk = _field(original, "factory_psk")
    original_generation = int(_field(original, "generation"))
    assert consume(identity_path, now=1001.0) is True

    rotated = rotate(identity_path, now=1010.0)
    assert rotated != factory_psk
    assert consume(identity_path, now=1011.0) is False
    after_rotation = load(identity_path)
    assert _field(after_rotation, "factory_psk") == factory_psk
    assert _field(after_rotation, "active_psk") == rotated
    assert int(_field(after_rotation, "generation")) == original_generation + 1

    assert record(identity_path, boot_id="boot-a", now=1100.0) is False
    assert record(identity_path, boot_id="boot-a", now=1101.0) is False
    assert record(identity_path, boot_id="boot-b", now=1110.0) is False
    assert record(identity_path, boot_id="boot-c", now=1120.0) is True

    recovered = load(identity_path)
    assert _field(recovered, "active_psk") == factory_psk
    assert int(_field(recovered, "generation")) == original_generation + 2
    assert consume(identity_path, now=1121.0) is True
    assert consume(identity_path, now=1122.0) is False


def test_recovery_sequence_expires_and_a_healthy_boot_clears_it(tmp_path: Path):
    provisioner = _load_provisioner()
    commission = _require(provisioner, "commission_setup_identity")
    consume = _require(provisioner, "consume_ap_authorization")
    record = _require(provisioner, "record_short_boot")
    clear = _require(provisioner, "clear_short_boot_counter")

    assert int(_require(provisioner, "SHORT_BOOT_MAX_UPTIME_S")) == 60
    assert int(_require(provisioner, "RECOVERY_SEQUENCE_WINDOW_S")) == 180

    identity_path = tmp_path / "setup-identity.json"
    commission(identity_path, ssid="AstroDeck-BEEF", now=1000.0)
    assert consume(identity_path, now=1001.0) is True

    assert record(identity_path, boot_id="old-a", now=1100.0) is False
    assert record(identity_path, boot_id="new-a", now=1281.0) is False
    assert record(identity_path, boot_id="new-b", now=1282.0) is False
    assert record(identity_path, boot_id="new-c", now=1283.0) is True
    assert consume(identity_path, now=1284.0) is True

    assert record(identity_path, boot_id="clear-a", now=1300.0) is False
    clear(identity_path)
    assert record(identity_path, boot_id="clear-b", now=1301.0) is False
    assert record(identity_path, boot_id="clear-c", now=1302.0) is False
    assert record(identity_path, boot_id="clear-d", now=1303.0) is True


def test_portal_exposes_no_network_recovery_or_credential_route():
    provisioner = _load_provisioner()
    portal_source = inspect.getsource(provisioner.Portal).lower()
    for forbidden in (
        "/recover",
        "/recovery",
        "/factory-reset",
        "factory_psk",
        "active_psk",
        "setup_password",
    ):
        assert forbidden not in portal_source


def _unit_values(text: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "[")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values.setdefault(key, []).append(value)
    return values


def test_provisioning_unit_has_a_measured_root_sandbox():
    values = _unit_values(SERVICE.read_text(encoding="utf-8"))

    assert values.get("User") == ["root"]
    assert values.get("Group") == ["root"]
    assert values.get("RequiresMountsFor") == ["/data"]
    assert values.get("UMask") == ["0077"]
    assert values.get("NoNewPrivileges") in (["yes"], ["true"])
    assert values.get("ProtectSystem") == ["strict"]
    assert values.get("ProtectHome") in (["yes"], ["true"])
    assert values.get("PrivateTmp") in (["yes"], ["true"])
    assert values.get("ProtectProc") == ["invisible"]
    assert values.get("RuntimeDirectory") == ["astrodeck"]
    assert values.get("RuntimeDirectoryMode") == ["0700"]
    for key in (
        "ProtectKernelTunables",
        "ProtectKernelModules",
        "ProtectKernelLogs",
        "ProtectControlGroups",
        "ProtectClock",
        "ProtectHostname",
        "RestrictSUIDSGID",
        "RestrictRealtime",
        "LockPersonality",
        "MemoryDenyWriteExecute",
    ):
        assert values.get(key) in (["yes"], ["true"]), f"missing {key}"
    assert values.get("RestrictNamespaces") in (["yes"], ["true"])
    assert values.get("SystemCallArchitectures") == ["native"]
    assert values.get("LimitCORE") == ["0"]
    assert values.get("TasksMax") == ["64"]
    assert values.get("LimitNOFILE") == ["128"]

    caps = set(" ".join(values.get("CapabilityBoundingSet", [])).split())
    assert caps == {"CAP_NET_ADMIN", "CAP_NET_BIND_SERVICE", "CAP_NET_RAW"}

    families = set(" ".join(values.get("RestrictAddressFamilies", [])).split())
    assert {"AF_UNIX", "AF_INET", "AF_INET6", "AF_NETLINK", "AF_PACKET"} <= families

    writable = " ".join(values.get("ReadWritePaths", [])).split()
    assert "/run" not in writable, "do not grant the root helper all of /run"
    assert "/etc/netplan" in writable
    assert any(path.startswith("/run/astrodeck") for path in writable)
    assert any(path.startswith("/data/") for path in writable)

    filters = " ".join(values.get("SystemCallFilter", []))
    for denied_group in (
        "@clock",
        "@cpu-emulation",
        "@debug",
        "@module",
        "@mount",
        "@obsolete",
        "@raw-io",
        "@reboot",
        "@swap",
    ):
        assert denied_group in filters


def test_recovery_units_make_service_restart_insufficient_for_recovery():
    for path in (
        RECOVERY_RECORD_SERVICE,
        RECOVERY_CLEAR_SERVICE,
        RECOVERY_CLEAR_TIMER,
    ):
        assert path.is_file(), f"missing recovery unit {path.name}"

    record = _unit_values(RECOVERY_RECORD_SERVICE.read_text(encoding="utf-8"))
    clear_service = _unit_values(
        RECOVERY_CLEAR_SERVICE.read_text(encoding="utf-8")
    )
    clear_timer = _unit_values(RECOVERY_CLEAR_TIMER.read_text(encoding="utf-8"))

    assert record.get("Type") == ["oneshot"]
    assert record.get("User") == ["root"]
    assert "astrodeck-provision.service" in " ".join(record.get("Before", []))
    assert any("record" in value and "boot" in value for value in record.get("ExecStart", []))
    assert clear_service.get("Type") == ["oneshot"]
    assert any("clear" in value and "boot" in value for value in clear_service.get("ExecStart", []))
    assert clear_timer.get("OnBootSec") == ["60s"]
    assert clear_timer.get("Unit") == [RECOVERY_CLEAR_SERVICE.name]
    assert clear_timer.get("Persistent") not in (["yes"], ["true"]), (
        "a persistent timer could clear state immediately after a short powered-off boot"
    )
    assert "timers.target" in " ".join(clear_timer.get("WantedBy", []))

    installer = ROOTFS_INSTALLER.read_text(encoding="utf-8")
    for unit in (RECOVERY_RECORD_SERVICE.name, RECOVERY_CLEAR_TIMER.name):
        assert unit in installer, f"rootfs installer never enables {unit}"
