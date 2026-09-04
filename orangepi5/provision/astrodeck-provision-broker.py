#!/usr/bin/env python3
"""Privileged broker for AstroDeck's physically recoverable WiFi setup.

A factory-commissioned identity (or three-short-boot recovery sequence) grants
one bounded attempt to raise a WPA2 hotspot and accept home WiFi credentials.
Ordinary boots and route loss do not expose the portal.  See the superseding
platform security design dated 2026-09-01.

Stdlib only — the image this ships on cannot install packages.
"""

import argparse
import contextlib
import json
import math
import os
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time

AP_IP = "10.42.0.1"
AP_CIDR = "10.42.0.1/24"
AP_FREQ = 2437  # 2.4 GHz channel 6: phones universally see it
IFACE = "wlan0"
RUN_DIR = "/run/astrodeck"
STATE_DIR = "/data/astrodeck"
STATE_PATH = os.path.join(STATE_DIR, "provision-state.json")
SETUP_IDENTITY_PATH = os.path.join(STATE_DIR, "setup-identity.json")
NETPLAN_PATH = "/etc/netplan/30-astrodeck-wifi.yaml"
# 05- so it outranks netplan's generated 10-netplan-wlan0.network: systemd-networkd
# applies the first file (lexicographically) that matches an interface, and after a
# reboot with a netplan yaml present both files exist while the AP is up.
NETWORKD_DIR = "/run/systemd/network"
NETWORKD_PATH = os.path.join(NETWORKD_DIR, "05-astrodeck-ap.network")
PERSIST_LOG = os.path.join(STATE_DIR, "provision.log")
WPA_CONF = os.path.join(RUN_DIR, "ap.conf")
WPA_PID = os.path.join(RUN_DIR, "wpa-ap.pid")
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
PROC_UPTIME_PATH = "/proc/uptime"
DATA_MOUNT_PATH = "/data"
JOIN_WAIT_S = 45
PORTAL_LIFETIME_S = 15 * 60
SHORT_BOOT_MAX_UPTIME_S = 60
RECOVERY_SEQUENCE_WINDOW_S = 180
MAX_SSID_BYTES = 32
MAX_IDENTITY_BYTES = 16 * 1024
SETUP_IDENTITY_SCHEMA = 1
SETUP_PASSWORD_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
SETUP_PASSWORD_RE = re.compile(r"^[a-z0-9]{4}(?:-[a-z0-9]{4}){2}$")
BROKER_SOCKET = "/run/astrodeck-provision-broker.sock"
BROKER_FRONTEND_USER = "astrodeck-setup"
BROKER_PROTOCOL_VERSION = 1
MAX_BROKER_REQUEST_BYTES = 8192
MAX_BROKER_RESPONSE_BYTES = 64 * 1024
BROKER_OPERATIONS = frozenset({"open", "scan", "join", "close"})

# Root-owned cache of already validated scan results. It contains no HTTP/DNS
# state; all Internet-facing protocol parsing lives in the unprivileged file.
SCAN_CACHE: list[dict] = []
AP_MAY_BE_ACTIVE = threading.Event()


def _runtime_uid() -> int | None:
    getter = getattr(os, "geteuid", None)
    return getter() if getter is not None else None


def _require_durable_data_mount() -> None:
    if os.name == "posix" and not os.path.ismount(DATA_MOUNT_PATH):
        raise RuntimeError("the durable /data mount is unavailable")


def _lstat_checked(path: str, *, directory: bool) -> os.stat_result:
    """Inspect a path without following links and enforce its object type/owner."""
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        raise RuntimeError(f"refusing symlinked private path: {path}")
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(info.st_mode):
        kind = "directory" if directory else "regular file"
        raise RuntimeError(f"private path is not a {kind}: {path}")
    uid = _runtime_uid()
    if uid is not None and info.st_uid != uid:
        raise PermissionError(f"private path has an unexpected owner: {path}")
    return info


def _ensure_private_dir(path: str, *, private: bool = True) -> None:
    """Create or verify one trusted directory without following its final path."""
    path = os.fspath(path)
    try:
        _lstat_checked(path, directory=True)
    except FileNotFoundError:
        os.mkdir(path, 0o700 if private else 0o755)
        _lstat_checked(path, directory=True)
    if private:
        _chmod_nofollow(path, 0o700)
        mode = stat.S_IMODE(os.lstat(path).st_mode)
        if os.name == "posix" and mode != 0o700:
            raise PermissionError(f"private directory mode is not 0700: {path}")


def _verify_private_file(path: str, *, require_mode: bool = True) -> os.stat_result:
    info = _lstat_checked(path, directory=False)
    if require_mode and os.name == "posix" and stat.S_IMODE(info.st_mode) != 0o600:
        raise PermissionError(f"private file mode is not 0600: {path}")
    return info


def _chmod_nofollow(path: str, mode: int) -> None:
    if os.name == "posix":
        os.chmod(path, mode, follow_symlinks=False)
    else:
        os.chmod(path, mode)


def _make_fd_private(fd: int, path: str, mode: int = 0o600) -> None:
    """Set a descriptor to ``mode`` (0600 by default), with a path fallback for host-side Windows CI.

    Orange Pi targets provide ``fchmod``.  Windows does not, but the portable
    contract tests still exercise the identity state machine there; chmod by
    the already-open file's exact path is the closest available host seam.
    Unknown platforms fail closed instead of silently accepting a broad mode.
    """
    fchmod = getattr(os, "fchmod", None)
    if fchmod is not None:
        fchmod(fd, mode)
    elif os.name == "nt":
        _chmod_nofollow(path, mode)
    else:
        raise OSError("this platform cannot enforce private descriptor modes")


def _fsync_parent(path: str) -> None:
    """Make a rename/link durable on POSIX; directory fsync is not portable."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write_private(
    path: str,
    text: str,
    *,
    exclusive: bool = False,
    private_parent: bool = True,
    mode: int = 0o600,
) -> None:
    """Atomically publish a file (0600 unless ``mode`` says otherwise) in a
    verified same-directory parent.

    ``mode`` exists for exactly one caller: the systemd-networkd drop-in that
    gives the hotspot its address. networkd reads that directory as its own
    unprivileged user, so a root-only 0600 file is silently skipped, the
    access point beacons with no address and the portal cannot bind. Seen on
    the appliance 2026-09-03. Every credential-bearing file keeps 0600, and
    the published mode is verified after the rename either way.
    """
    path = os.fspath(path)
    parent = os.path.dirname(path) or "."
    _ensure_private_dir(parent, private=private_parent)

    if os.path.lexists(path):
        if exclusive:
            raise FileExistsError(path)
        _verify_private_file(path, require_mode=False)
        _chmod_nofollow(path, mode)

    fd, temporary = tempfile.mkstemp(prefix=".astrodeck-", dir=parent, text=True)
    try:
        _make_fd_private(fd, temporary, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            fd = -1
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            # link() is an atomic create-if-absent publication; it preserves the
            # fully fsynced inode and cannot overwrite another commissioning run.
            os.link(temporary, path)
            os.unlink(temporary)
            temporary = ""
        else:
            if os.path.lexists(path):
                _verify_private_file(path, require_mode=False)
            os.replace(temporary, path)
            temporary = ""
        if mode == 0o600:
            _verify_private_file(path)
        else:
            info = _verify_private_file(path, require_mode=False)
            if os.name == "posix" and stat.S_IMODE(info.st_mode) != mode:
                raise PermissionError(f"published file mode is not {mode:04o}: {path}")
        _fsync_parent(parent)
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _write_private(
    path: str, text: str, *, private_parent: bool = True, mode: int = 0o600
) -> None:
    _atomic_write_private(path, text, private_parent=private_parent, mode=mode)


_IDENTITY_THREAD_LOCK = threading.RLock()


@contextlib.contextmanager
def _identity_lock(path: str):
    """Serialize identity read-modify-write operations across threads/processes."""
    parent = os.path.dirname(os.fspath(path)) or "."
    _ensure_private_dir(parent)
    lock_path = os.fspath(path) + ".lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    with _IDENTITY_THREAD_LOCK:
        fd = os.open(lock_path, flags, 0o600)
        try:
            _make_fd_private(fd, lock_path)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError("identity lock is not a regular file")
            uid = _runtime_uid()
            if uid is not None and info.st_uid != uid:
                raise PermissionError("identity lock has an unexpected owner")
            try:
                import fcntl
            except ImportError:
                fcntl = None
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def log(msg: str) -> None:
    print(f"[provision] {msg}", flush=True)
    # journald is volatile under armbian-ramlog and dies with hard resets;
    # keep our own breadcrumb trail somewhere that survives power loss.
    try:
        _ensure_private_dir(STATE_DIR)
        if os.path.lexists(PERSIST_LOG):
            _verify_private_file(PERSIST_LOG, require_mode=False)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        fd = os.open(PERSIST_LOG, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise OSError("persistent log is not a regular file")
            uid = _runtime_uid()
            if uid is not None and info.st_uid != uid:
                raise PermissionError("persistent log has an unexpected owner")
            _make_fd_private(fd, PERSIST_LOG)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                fd = -1
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        finally:
            if fd >= 0:
                os.close(fd)
    except (OSError, RuntimeError):
        pass


def run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    log("+ " + " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _require_ok(result: subprocess.CompletedProcess, operation: str) -> None:
    if result.returncode != 0:
        raise RuntimeError(f"{operation} failed")


# ---------------------------------------------------------------- pure logic

def generate_setup_password() -> str:
    """Return a label-friendly, cryptographically random per-device password."""
    while True:
        groups = [
            "".join(secrets.choice(SETUP_PASSWORD_ALPHABET) for _ in range(4))
            for _ in range(3)
        ]
        password = "-".join(groups)
        if _valid_setup_password(password):
            return password


def _valid_setup_password(value) -> bool:
    """Reject malformed and conspicuously low-entropy persisted credentials."""
    return (
        isinstance(value, str)
        and SETUP_PASSWORD_RE.fullmatch(value) is not None
        and len(set(value.replace("-", ""))) >= 4
    )


def _timestamp(value, field: str, *, nullable: bool = False) -> float | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite timestamp")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be a finite timestamp")
    return result


def _validate_short_boots(value) -> dict:
    if not isinstance(value, dict) or set(value) != {"boot_ids", "first_at"}:
        raise ValueError("invalid short boot sequence")
    ids = value.get("boot_ids")
    if not isinstance(ids, list) or len(ids) > 2:
        # A completed third boot is immediately converted into recovery state.
        raise ValueError("invalid short boot identifiers")
    if any(
        not isinstance(item, str)
        or not item
        or len(item) > 128
        or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in item)
        for item in ids
    ):
        raise ValueError("invalid short boot identifier")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate short boot identifier")
    first_at = value.get("first_at")
    if ids:
        _timestamp(first_at, "short_boots.first_at")
    elif first_at is not None:
        raise ValueError("empty short boot sequence has a timestamp")
    return {"boot_ids": list(ids), "first_at": first_at}


def _validate_identity(data, *, allow_bad_short_boots: bool = False) -> dict:
    if not isinstance(data, dict):
        raise ValueError("setup identity must be an object")
    required = {
        "schema",
        "setup_ssid",
        "factory_psk",
        "active_psk",
        "generation",
        "commissioned_at",
        "rotated_at",
        "ap_authorization",
        "short_boots",
    }
    if not required <= set(data):
        raise ValueError("setup identity is missing required fields")
    if data["schema"] != SETUP_IDENTITY_SCHEMA or isinstance(data["schema"], bool):
        raise ValueError("unsupported setup identity schema")
    if not isinstance(data["setup_ssid"], str) or not valid_ssid(data["setup_ssid"]):
        raise ValueError("invalid setup SSID")
    for field in ("factory_psk", "active_psk"):
        value = data[field]
        if not _valid_setup_password(value):
            raise ValueError(f"invalid {field}")
    generation = data["generation"]
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise ValueError("invalid setup password generation")
    commissioned_at = _timestamp(data["commissioned_at"], "commissioned_at")
    rotated_at = _timestamp(data["rotated_at"], "rotated_at", nullable=True)
    if rotated_at is not None and rotated_at < commissioned_at:
        raise ValueError("rotation predates commissioning")
    if data["active_psk"] != data["factory_psk"] and rotated_at is None:
        raise ValueError("unrecorded setup password rotation")

    authorization = data["ap_authorization"]
    if not isinstance(authorization, dict):
        raise ValueError("invalid AP authorization")
    if set(authorization) != {"armed", "reason", "armed_at"}:
        raise ValueError("invalid AP authorization fields")
    if not isinstance(authorization["armed"], bool):
        raise ValueError("invalid AP authorization state")
    if authorization["reason"] not in {"factory", "recovery"}:
        raise ValueError("invalid AP authorization reason")
    armed_at = _timestamp(authorization["armed_at"], "ap_authorization.armed_at")
    if armed_at < commissioned_at:
        raise ValueError("AP authorization predates commissioning")

    try:
        _validate_short_boots(data["short_boots"])
    except ValueError:
        if not allow_bad_short_boots:
            raise
    return data


def _identity_json(identity: dict) -> str:
    return json.dumps(identity, sort_keys=True, indent=2) + "\n"


def _json_object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate setup identity field: {key}")
        result[key] = value
    return result


def _read_identity_unlocked(path: str, *, allow_bad_short_boots: bool = False) -> dict:
    path = os.fspath(path)
    _verify_private_file(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError("setup identity is not a regular file")
        if before.st_size > MAX_IDENTITY_BYTES:
            raise ValueError("setup identity is too large")
        if os.name == "posix" and stat.S_IMODE(before.st_mode) != 0o600:
            raise PermissionError("setup identity mode is not 0600")
        uid = _runtime_uid()
        if uid is not None and before.st_uid != uid:
            raise PermissionError("setup identity has an unexpected owner")
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = -1
            data = json.load(stream, object_pairs_hook=_json_object_without_duplicates)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed setup identity") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    return _validate_identity(data, allow_bad_short_boots=allow_bad_short_boots)


def load_setup_identity(path=SETUP_IDENTITY_PATH) -> dict:
    """Load and strictly validate commissioned state; there is no fallback."""
    with _identity_lock(os.fspath(path)):
        return _read_identity_unlocked(os.fspath(path))


def commission_setup_identity(path, *, ssid: str, now: float | None = None) -> dict:
    """Create a device identity exactly once and return it for label printing."""
    if not valid_ssid(ssid):
        raise ValueError("invalid setup SSID")
    created_at = time.time() if now is None else _timestamp(now, "now")
    password = generate_setup_password()
    identity = {
        "schema": SETUP_IDENTITY_SCHEMA,
        "setup_ssid": ssid,
        "factory_psk": password,
        "active_psk": password,
        "generation": 1,
        "commissioned_at": created_at,
        "rotated_at": None,
        "ap_authorization": {
            "armed": True,
            "reason": "factory",
            "armed_at": created_at,
        },
        "short_boots": {"boot_ids": [], "first_at": None},
    }
    _validate_identity(identity)
    path = os.fspath(path)
    with _identity_lock(path):
        _atomic_write_private(path, _identity_json(identity), exclusive=True)
    return identity


def rotate_setup_password(path=SETUP_IDENTITY_PATH, *, now: float | None = None) -> str:
    """Rotate the active setup password without silently authorizing an AP."""
    changed_at = time.time() if now is None else _timestamp(now, "now")
    path = os.fspath(path)
    with _identity_lock(path):
        identity = _read_identity_unlocked(path)
        password = generate_setup_password()
        while password in {identity["active_psk"], identity["factory_psk"]}:
            password = generate_setup_password()
        identity["active_psk"] = password
        identity["generation"] += 1
        identity["rotated_at"] = changed_at
        _atomic_write_private(path, _identity_json(identity))
    return password


def consume_ap_authorization(path=SETUP_IDENTITY_PATH, *, now: float | None = None) -> bool:
    """Atomically consume the single persisted permission to start the AP."""
    if now is not None:
        _timestamp(now, "now")
    path = os.fspath(path)
    with _identity_lock(path):
        identity = _read_identity_unlocked(path)
        if not identity["ap_authorization"]["armed"]:
            return False
        identity["ap_authorization"]["armed"] = False
        _atomic_write_private(path, _identity_json(identity))
        return True


def _fresh_short_boots(boot_id: str, now: float) -> dict:
    return {"boot_ids": [boot_id], "first_at": now}


def _valid_boot_id(boot_id: str) -> bool:
    return (
        isinstance(boot_id, str)
        and bool(boot_id)
        and len(boot_id) <= 128
        and all(ord(ch) >= 0x20 and ord(ch) != 0x7F for ch in boot_id)
    )


def record_short_boot(
    path=SETUP_IDENTITY_PATH,
    *,
    boot_id: str,
    now: float | None = None,
) -> bool:
    """Record one physical boot and arm recovery on the third distinct boot."""
    if not _valid_boot_id(boot_id):
        raise ValueError("invalid boot identifier")
    recorded_at = time.time() if now is None else _timestamp(now, "now")
    path = os.fspath(path)
    with _identity_lock(path):
        identity = _read_identity_unlocked(path, allow_bad_short_boots=True)
        try:
            sequence = _validate_short_boots(identity.get("short_boots"))
        except ValueError:
            sequence = _fresh_short_boots(boot_id, recorded_at)
        else:
            first_at = sequence["first_at"]
            expired = bool(sequence["boot_ids"]) and (
                recorded_at < float(first_at)
                or recorded_at - float(first_at) > RECOVERY_SEQUENCE_WINDOW_S
            )
            if expired or not sequence["boot_ids"]:
                sequence = _fresh_short_boots(boot_id, recorded_at)
            elif boot_id in sequence["boot_ids"]:
                return False
            else:
                sequence["boot_ids"].append(boot_id)

        recovered = len(sequence["boot_ids"]) >= 3
        if recovered:
            identity["active_psk"] = identity["factory_psk"]
            identity["generation"] += 1
            identity["ap_authorization"] = {
                "armed": True,
                "reason": "recovery",
                "armed_at": recorded_at,
            }
            identity["short_boots"] = {"boot_ids": [], "first_at": None}
        else:
            identity["short_boots"] = sequence
        _validate_identity(identity)
        _atomic_write_private(path, _identity_json(identity))
        return recovered


def clear_short_boot_counter(path=SETUP_IDENTITY_PATH) -> None:
    """Clear recovery progress after a continuously healthy 60-second boot."""
    path = os.fspath(path)
    with _identity_lock(path):
        identity = _read_identity_unlocked(path, allow_bad_short_boots=True)
        identity["short_boots"] = {"boot_ids": [], "first_at": None}
        _validate_identity(identity)
        _atomic_write_private(path, _identity_json(identity))


def derive_ssid(mac: str) -> str:
    """AstroDeck-XXXX from the last 4 hex digits of a MAC address."""
    digits = re.sub(r"[^0-9a-fA-F]", "", mac)
    return "AstroDeck-" + digits[-4:].upper()


def yaml_dq(s: str) -> str:
    """Escape a string into a YAML double-quoted scalar."""
    # JSON strings are valid YAML double-quoted scalars and correctly escape
    # every control character, not only quote/backslash.
    return json.dumps(s, ensure_ascii=False)


def wpa_dq(s: str) -> str:
    """Escape text for a wpa_supplicant quoted string."""
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in s):
        raise ValueError("control character in wpa_supplicant value")
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def valid_ssid(ssid: str) -> bool:
    """Text-safe IEEE 802.11 SSID: non-empty and at most 32 UTF-8 bytes."""
    if not isinstance(ssid, str):
        return False
    try:
        encoded = ssid.encode("utf-8")
    except UnicodeError:
        return False
    return (bool(encoded) and len(encoded) <= MAX_SSID_BYTES
            and all(ord(ch) >= 0x20 and ord(ch) != 0x7F for ch in ssid))


def valid_psk(psk: str) -> bool:
    """WPA passphrase byte-length/control rule; empty means an open network."""
    if not isinstance(psk, str):
        return False
    if psk == "":
        return True
    try:
        encoded = psk.encode("utf-8")
    except UnicodeError:
        return False
    return (8 <= len(encoded) <= 63
            and all(ord(ch) >= 0x20 and ord(ch) != 0x7F for ch in psk))


def emit_netplan(ssid: str, psk: str, sae: bool = False) -> str:
    if not valid_ssid(ssid) or not valid_psk(psk):
        raise ValueError("invalid WiFi credentials")
    if psk and sae:
        # WPA3-only network: plain `password:` emits WPA-PSK, which a
        # WPA3-only AP rejects regardless of the password being right.
        body = (f"          {yaml_dq(ssid)}:\n"
                "            auth:\n"
                "              key-management: sae\n"
                f"              password: {yaml_dq(psk)}\n")
    elif psk:
        body = (f"          {yaml_dq(ssid)}:\n"
                f"            password: {yaml_dq(psk)}\n")
    else:
        # an open network needs an explicit empty mapping value
        body = f"          {yaml_dq(ssid)}: {{}}\n"
    return (
        "# Written by astrodeck-provision. Do not hand-edit; rerun setup instead.\n"
        "network:\n"
        "  version: 2\n"
        "  renderer: networkd\n"
        "  wifis:\n"
        f"    {IFACE}:\n"
        "      dhcp4: true\n"
        "      dhcp6: true\n"
        "      access-points:\n"
        f"{body}"
    )


def emit_wpa_ap_conf(ssid: str, psk: str, freq: int = AP_FREQ) -> str:
    if not valid_ssid(ssid) or not psk or not valid_psk(psk):
        raise ValueError("invalid setup access-point credentials")
    return (
        f"ctrl_interface=DIR={RUN_DIR}/wpa_supplicant\n"
        "ap_scan=1\n"
        "network={\n"
        f"    ssid={wpa_dq(ssid)}\n"
        "    mode=2\n"
        "    key_mgmt=WPA-PSK\n"
        f"    psk={wpa_dq(psk)}\n"
        "    proto=RSN\n"
        "    pairwise=CCMP\n"
        "    group=CCMP\n"
        f"    frequency={freq}\n"
        "}\n"
    )


def emit_networkd_ap() -> str:
    return (
        "[Match]\n"
        f"Name={IFACE}\n"
        "\n"
        "[Network]\n"
        f"Address={AP_CIDR}\n"
        "DHCPServer=yes\n"
        "\n"
        "[DHCPServer]\n"
        "PoolOffset=10\n"
        "PoolSize=64\n"
        "EmitDNS=yes\n"
        f"DNS={AP_IP}\n"
    )


# ------------------------------------------------------------------- network

def wlan_mac() -> str | None:
    try:
        with open(f"/sys/class/net/{IFACE}/address") as f:
            return f.read().strip()
    except OSError:
        return None


def have_default_route() -> bool:
    try:
        # An Ethernet default route does not prove the submitted WiFi settings
        # worked.  Only accept a route attached to the interface we configured.
        p = run(["ip", "route", "show", "default", "dev", IFACE], timeout=10)
        return bool(p.stdout.strip())
    except Exception:
        return False


def wait_route(seconds: int) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if have_default_route():
            return True
        time.sleep(3)
    return have_default_route()


def parse_scan(text: str) -> list[dict]:
    """Parse `iw dev wlan0 scan` output → [{ssid, signal, akm}] strongest first.

    akm collects RSN authentication suites across all BSSes broadcasting the
    SSID (e.g. {"PSK"}, {"SAE"}, {"PSK","SAE"} for WPA2/WPA3 mixed mode).
    """
    nets: dict[str, dict] = {}

    def commit(ssid, sig, akm):
        if not valid_ssid(ssid) or "\\x00" in ssid:
            return
        e = nets.setdefault(ssid, {"signal": -100.0, "akm": set()})
        if sig is not None and sig > e["signal"]:
            e["signal"] = sig
        e["akm"] |= akm

    ssid, sig, akm = None, None, set()
    for raw in text.splitlines():
        line = raw.strip()
        if raw.startswith("BSS "):
            commit(ssid, sig, akm)
            ssid, sig, akm = None, None, set()
        elif line.startswith("signal:"):
            m = re.search(r"(-?\d+(?:\.\d+)?)", line)
            sig = float(m.group(1)) if m else None
        elif line.startswith("SSID:"):
            ssid = line[5:].strip()
        elif line.startswith("* Authentication suites:"):
            akm |= set(line.split(":", 1)[1].split())
    commit(ssid, sig, akm)
    return [
        {"ssid": s, "signal": v["signal"], "akm": sorted(v["akm"])}
        for s, v in sorted(nets.items(), key=lambda kv: -kv[1]["signal"])
    ]


def needs_sae(ssid: str, nets: list[dict]) -> bool:
    """True when the scanned network offers SAE but not plain PSK (WPA3-only)."""
    for n in nets:
        if n["ssid"] == ssid:
            akm = set(n.get("akm", ()))
            return "SAE" in akm and "PSK" not in akm
    return False


def scan_networks() -> list[dict]:
    """Best-effort scan; returns [{ssid, signal, akm}] sorted strongest first."""
    run(["ip", "link", "set", IFACE, "up"], timeout=10)
    try:
        p = run(["iw", "dev", IFACE, "scan"], timeout=25)
    except subprocess.TimeoutExpired:
        return SCAN_CACHE
    if p.returncode != 0:
        log(f"scan failed: {p.stderr.strip()[:200]}")
        return SCAN_CACHE
    return parse_scan(p.stdout)


def ap_up(ssid: str, psk: str) -> None:
    _ensure_private_dir(RUN_DIR)
    # Mark immediately before the first network mutation so a mid-start failure
    # is cleaned without flushing wlan0 when private-path validation itself fails.
    AP_MAY_BE_ACTIVE.set()
    # netplan's own supplicant fights us for wlan0 whenever a netplan wifi yaml
    # exists (i.e. after any previous provisioning attempt). It cannot be
    # masked: netplan GENERATES netplan-wpa-wlan0.service into
    # /run/systemd/system, the exact path a runtime mask has to create, so
    # `systemctl mask --runtime` fails with "already exists" on every netplan
    # host (observed on the appliance 2026-09-03; the August provisioner ran
    # the same mask unchecked and never noticed). Stop it and verify instead.
    # The generated unit has no Restart= and is started only by netplan apply
    # or a networkd restart, neither of which happens while a window is open.
    client_unit = f"netplan-wpa-{IFACE}.service"
    run(["systemctl", "stop", client_unit], timeout=20)
    if run(["systemctl", "is-active", "--quiet", client_unit], timeout=10).returncode == 0:
        raise RuntimeError("the client supplicant remained active")
    run(["pkill", "-F", WPA_PID], timeout=10)
    _ensure_private_dir(os.path.join(RUN_DIR, "wpa_supplicant"))
    _write_private(WPA_CONF, emit_wpa_ap_conf(ssid, psk))
    _ensure_private_dir(NETWORKD_DIR, private=False)
    # networkd reads this drop-in as the systemd-network user, so it must not
    # be root-only; it holds the hotspot address and DHCP pool, no secret. A
    # 0600 file here left the AP beaconing with no address (board, 2026-09-03).
    _write_private(NETWORKD_PATH, emit_networkd_ap(), private_parent=False, mode=0o644)
    _require_ok(run(["networkctl", "reload"], timeout=20), "reloading networkd")
    _require_ok(
        run(["ip", "link", "set", IFACE, "up"], timeout=10),
        "bringing up the WiFi interface",
    )
    for attempt in range(3):
        # -s: the hotspot supplicant reports each client's association and
        # handshake outcome to the journal (station addresses and state
        # names only; key material is never logged at this level). Without
        # it a phone that cannot join leaves no trace on the board.
        p = run(["wpa_supplicant", "-B", "-s", "-i", IFACE, "-c", WPA_CONF,
                 "-P", WPA_PID], timeout=20)
        if p.returncode == 0:
            break
        # Some wpa_supplicant diagnostics echo parsed configuration material.
        # Do not persist stderr from a command that reads the setup credential.
        log(f"wpa_supplicant AP start attempt {attempt + 1} failed")
        run(["pkill", "-f", f"wpa_supplicant.*{IFACE}"], timeout=10)
        time.sleep(2)
    else:
        raise RuntimeError("wpa_supplicant AP start failed")
    for _ in range(20):
        info = run(["iw", "dev", IFACE, "info"], timeout=10)
        if "type AP" in info.stdout:
            log(f"AP up: {ssid}")
            return
        time.sleep(1)
    log("warning: interface never reported type AP; continuing anyway")


def ap_down() -> None:
    # try_join() already tears the AP down before applying the client config.
    # A second teardown after a successful join would flush the new DHCP lease.
    if not AP_MAY_BE_ACTIVE.is_set():
        return
    errors: list[str] = []
    try:
        run(["pkill", "-F", WPA_PID], timeout=10)
    except Exception:
        errors.append("stopping the hotspot supplicant")
    for transient in (WPA_CONF, WPA_PID, NETWORKD_PATH):
        try:
            os.remove(transient)
        except FileNotFoundError:
            pass
        except OSError:
            errors.append(f"removing {os.path.basename(transient)}")
    for command, operation in (
        (["networkctl", "reload"], "reloading networkd"),
        (["ip", "addr", "flush", "dev", IFACE], "clearing the hotspot address"),
    ):
        try:
            _require_ok(run(command, timeout=20), operation)
        except Exception:
            errors.append(operation)
    if errors:
        raise RuntimeError("hotspot cleanup failed: " + ", ".join(errors))
    AP_MAY_BE_ACTIVE.clear()


def try_join(ssid: str, psk: str, sae: bool = False) -> bool:
    log(f"joining {ssid!r} (sae={sae})")
    content = emit_netplan(ssid, psk, sae)
    _write_private(NETPLAN_PATH, content, private_parent=False)
    ap_down()
    applied = run(["netplan", "apply"], timeout=60)
    if applied.returncode != 0:
        log("netplan rejected the submitted WiFi configuration")
        try:
            os.remove(NETPLAN_PATH)
        except FileNotFoundError:
            pass
        _require_ok(run(["netplan", "apply"], timeout=60),
                    "restoring networking after a rejected WiFi configuration")
        return False
    if wait_route(JOIN_WAIT_S):
        _ensure_private_dir(STATE_DIR)
        _write_private(STATE_PATH, json.dumps({
            "ssid": ssid, "joined_at": time.time(), "result": "ok",
        }))
        log("joined; provisioning complete")
        return True
    log("no route after join; reverting to hotspot")
    try:
        os.remove(NETPLAN_PATH)
    except FileNotFoundError:
        pass
    _require_ok(run(["netplan", "apply"], timeout=60),
                "restoring networking after a failed WiFi join")
    return False


# ---------------------------------------------------------- broker boundary

class BrokerRequestError(ValueError):
    """The unprivileged frontend supplied an invalid protocol message."""


class BrokerWindowClosed(RuntimeError):
    """The one-shot setup authorization is not currently active."""


def validate_broker_request(value) -> dict:
    """Validate the complete fixed-operation protocol without side effects."""
    if not isinstance(value, dict):
        raise BrokerRequestError("request must be an object")
    version = value.get("version")
    operation = value.get("operation")
    if (
        isinstance(version, bool)
        or version != BROKER_PROTOCOL_VERSION
        or not isinstance(operation, str)
        or operation not in BROKER_OPERATIONS
    ):
        raise BrokerRequestError("unsupported broker request")
    expected = {
        "open": {"version", "operation"},
        "scan": {"version", "operation"},
        "close": {"version", "operation"},
        "join": {"version", "operation", "ssid", "psk", "sae"},
    }[operation]
    if set(value) != expected:
        raise BrokerRequestError("unexpected broker request fields")
    if operation == "join":
        if not valid_ssid(value["ssid"]) or not valid_psk(value["psk"]):
            raise BrokerRequestError("invalid WiFi credentials")
        if not isinstance(value["sae"], bool):
            raise BrokerRequestError("invalid WiFi security selection")
    return dict(value)


def decode_broker_request(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BROKER_REQUEST_BYTES:
        raise BrokerRequestError("invalid broker frame length")
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1 or raw[:-1].strip() != raw[:-1]:
        raise BrokerRequestError("invalid broker frame")
    try:
        value = json.loads(
            raw[:-1].decode("utf-8"),
            object_pairs_hook=_json_object_without_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise BrokerRequestError("invalid broker JSON") from exc
    return validate_broker_request(value)


def _bounded_networks(value) -> list[dict]:
    if not isinstance(value, list):
        raise RuntimeError("network scan returned an invalid result")
    result = []
    for entry in value[:64]:
        if not isinstance(entry, dict):
            continue
        ssid = entry.get("ssid")
        signal_value = entry.get("signal")
        suites = entry.get("akm", [])
        if not valid_ssid(ssid):
            continue
        if (
            isinstance(signal_value, bool)
            or not isinstance(signal_value, (int, float))
            or not math.isfinite(float(signal_value))
        ):
            continue
        if not isinstance(suites, list):
            continue
        clean_suites = [
            suite
            for suite in suites[:16]
            if isinstance(suite, str)
            and len(suite) <= 32
            and re.fullmatch(r"[A-Z0-9_-]+", suite)
        ]
        result.append(
            {
                "ssid": ssid,
                "signal": max(-200.0, min(100.0, float(signal_value))),
                "akm": clean_suites,
            }
        )
    return result


class BrokerState:
    """Own the authorization deadline and serialize all privileged effects."""

    def __init__(self):
        self._state_lock = threading.RLock()
        self._network_lock = threading.Lock()
        self._stop = threading.Event()
        self._active = False
        self._generation = 0
        self._deadline = 0.0
        self._setup_ssid = ""
        self._setup_psk = ""
        self._networks: list[dict] = []

    def _current(self, generation: int, *, now: float | None = None) -> bool:
        checked_at = time.monotonic() if now is None else now
        with self._state_lock:
            return (
                self._active
                and self._generation == generation
                and checked_at < self._deadline
            )

    def _snapshot(self) -> tuple[int, str, str, float]:
        with self._state_lock:
            now = time.monotonic()
            if not self._active or now >= self._deadline:
                raise BrokerWindowClosed("setup authorization is closed")
            return (
                self._generation,
                self._setup_ssid,
                self._setup_psk,
                self._deadline - now,
            )

    def _mark_closed(self, generation: int | None = None) -> bool:
        with self._state_lock:
            if generation is not None and generation != self._generation:
                return False
            was_active = self._active
            self._active = False
            self._generation += 1
            self._deadline = 0.0
            self._setup_ssid = ""
            self._setup_psk = ""
            self._networks = []
            return was_active

    def _restore_ap(self, setup_ssid: str, setup_psk: str) -> None:
        if AP_MAY_BE_ACTIVE.is_set():
            ap_down()
        ap_up(setup_ssid, setup_psk)

    def _watch_window(self, generation: int) -> None:
        while not self._stop.is_set():
            with self._state_lock:
                if not self._active or self._generation != generation:
                    return
                remaining = self._deadline - time.monotonic()
            if remaining > 0:
                if self._stop.wait(remaining):
                    return
                continue
            self._mark_closed(generation)
            with self._network_lock:
                try:
                    ap_down()
                except Exception:
                    log("FATAL: broker deadline cleanup failed")
            return

    def open_window(self) -> dict:
        try:
            generation, setup_ssid, _setup_psk, remaining = self._snapshot()
        except BrokerWindowClosed:
            generation = 0
        else:
            with self._state_lock:
                networks = list(self._networks)
            return {
                "ok": True,
                "authorized": True,
                "ssid": setup_ssid,
                "remaining_s": min(float(PORTAL_LIFETIME_S), remaining),
                "networks": networks,
            }

        # Retry any incomplete cleanup before considering persisted authority.
        with self._network_lock:
            if AP_MAY_BE_ACTIVE.is_set():
                ap_down()
        _require_durable_data_mount()
        if not consume_ap_authorization(SETUP_IDENTITY_PATH):
            return {"ok": True, "authorized": False}
        deadline = time.monotonic() + PORTAL_LIFETIME_S
        identity = load_setup_identity(SETUP_IDENTITY_PATH)
        setup_ssid = identity["setup_ssid"]
        setup_psk = identity["active_psk"]
        if wlan_mac() is None:
            raise RuntimeError("WiFi interface is unavailable")

        with self._state_lock:
            self._generation += 1
            generation = self._generation
            self._active = True
            self._deadline = deadline
            self._setup_ssid = setup_ssid
            self._setup_psk = setup_psk
            self._networks = []
        threading.Thread(
            target=self._watch_window,
            args=(generation,),
            daemon=True,
        ).start()

        try:
            with self._network_lock:
                if not self._current(generation):
                    raise BrokerWindowClosed("setup authorization expired")
                networks = _bounded_networks(scan_networks())
                with self._state_lock:
                    if self._active and self._generation == generation:
                        self._networks = networks
                ap_up(setup_ssid, setup_psk)
                if not self._current(generation):
                    ap_down()
                    raise BrokerWindowClosed("setup authorization expired")
        except Exception:
            self._mark_closed(generation)
            with self._network_lock:
                if AP_MAY_BE_ACTIVE.is_set():
                    try:
                        ap_down()
                    except Exception:
                        log("FATAL: broker startup cleanup failed")
            raise
        finally:
            setup_psk = ""

        _, setup_ssid, _, remaining = self._snapshot()
        return {
            "ok": True,
            "authorized": True,
            "ssid": setup_ssid,
            "remaining_s": min(float(PORTAL_LIFETIME_S), remaining),
            "networks": networks,
        }

    def scan(self) -> list[dict]:
        generation, _ssid, _psk, _remaining = self._snapshot()
        with self._network_lock:
            if not self._current(generation):
                raise BrokerWindowClosed("setup authorization expired")
            networks = _bounded_networks(scan_networks())
            with self._state_lock:
                if not self._active or self._generation != generation:
                    raise BrokerWindowClosed("setup authorization expired")
                self._networks = networks
            return networks

    def join(self, ssid: str, psk: str, sae: bool) -> bool:
        generation, setup_ssid, setup_psk, _remaining = self._snapshot()
        try:
            with self._network_lock:
                if not self._current(generation):
                    raise BrokerWindowClosed("setup authorization expired")
                try:
                    joined = try_join(ssid, psk, sae)
                except Exception:
                    if self._current(generation):
                        self._restore_ap(setup_ssid, setup_psk)
                    raise
                if joined:
                    self._mark_closed(generation)
                    return True
                if not self._current(generation):
                    raise BrokerWindowClosed("setup authorization expired")
                self._restore_ap(setup_ssid, setup_psk)
                return False
        finally:
            setup_psk = ""
            psk = ""

    def close(self) -> None:
        self._mark_closed()
        with self._network_lock:
            ap_down()

    def stop(self) -> None:
        self._stop.set()
        self.close()


def dispatch_broker_request(state: BrokerState, request: dict) -> dict:
    checked = validate_broker_request(request)
    operation = checked["operation"]
    if operation == "open":
        return state.open_window()
    if operation == "scan":
        return {"ok": True, "networks": state.scan()}
    if operation == "join":
        return {
            "ok": True,
            "joined": state.join(checked["ssid"], checked["psk"], checked["sae"]),
        }
    if operation == "close":
        state.close()
        return {"ok": True, "closed": True}
    raise BrokerRequestError("unsupported broker operation")


def broker_peer_is_authorized(peer_uid, expected_uid) -> bool:
    return (
        isinstance(peer_uid, int)
        and not isinstance(peer_uid, bool)
        and isinstance(expected_uid, int)
        and not isinstance(expected_uid, bool)
        and peer_uid == expected_uid
    )


def _peer_uid(connection: socket.socket) -> int:
    if os.name != "posix" or not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("peer credentials are unavailable")
    import struct

    raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    _pid, uid, _gid = struct.unpack("3i", raw)
    return uid


def _read_broker_frame(connection: socket.socket) -> bytes:
    chunks = []
    total = 0
    while True:
        block = connection.recv(4096)
        if not block:
            break
        total += len(block)
        if total > MAX_BROKER_REQUEST_BYTES:
            raise BrokerRequestError("broker frame is too large")
        chunks.append(block)
    return b"".join(chunks)


def _encode_broker_response(response: dict) -> bytes:
    encoded = (json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > MAX_BROKER_RESPONSE_BYTES:
        raise RuntimeError("broker response is too large")
    return encoded


def _systemd_listener() -> socket.socket:
    try:
        listen_pid = int(os.environ.get("LISTEN_PID", ""))
        listen_fds = int(os.environ.get("LISTEN_FDS", ""))
    except ValueError as exc:
        raise RuntimeError("invalid systemd socket activation state") from exc
    if listen_pid != os.getpid() or listen_fds != 1:
        raise RuntimeError("broker requires exactly one systemd-activated socket")
    descriptor_names = os.environ.get("LISTEN_FDNAMES")
    if descriptor_names not in (None, "provision-broker"):
        raise RuntimeError("unexpected systemd socket descriptor")
    listener = socket.socket(fileno=3)
    if (
        listener.family != socket.AF_UNIX
        or listener.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
        or listener.getsockname() != BROKER_SOCKET
    ):
        listener.close()
        raise RuntimeError("unexpected broker listener")
    for name in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        os.environ.pop(name, None)
    return listener


def serve_broker(
    listener: socket.socket,
    state: BrokerState,
    *,
    expected_uid: int,
    stop_event: threading.Event,
) -> int:
    listener.settimeout(1.0)
    while not stop_event.is_set():
        try:
            connection, _address = listener.accept()
        except socket.timeout:
            continue
        with connection:
            connection.settimeout(5.0)
            try:
                if not broker_peer_is_authorized(_peer_uid(connection), expected_uid):
                    raise BrokerRequestError("unauthorized broker peer")
                request = decode_broker_request(_read_broker_frame(connection))
                response = dispatch_broker_request(state, request)
            except BrokerRequestError:
                response = {"ok": False, "error": "invalid_request"}
            except BrokerWindowClosed:
                response = {"ok": False, "error": "window_closed"}
            except Exception:
                log("broker operation failed")
                response = {"ok": False, "error": "operation_failed"}
            try:
                connection.sendall(_encode_broker_response(response))
            except OSError:
                pass
    return 0


def main_broker() -> int:
    try:
        _require_durable_data_mount()
        import pwd

        expected_uid = pwd.getpwnam(BROKER_FRONTEND_USER).pw_uid
        listener = _systemd_listener()
    except Exception as exc:
        log(f"FATAL: broker startup failed: {exc}")
        return 1

    stop_event = threading.Event()
    state = BrokerState()
    old_handlers = {}
    result = 0
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, lambda _signum, _frame: stop_event.set())
        result = serve_broker(
            listener,
            state,
            expected_uid=expected_uid,
            stop_event=stop_event,
        )
    except Exception as exc:
        log(f"FATAL: broker stopped unexpectedly: {exc}")
        result = 1
    finally:
        try:
            state.stop()
        except Exception as exc:
            log(f"FATAL: broker cleanup failed: {exc}")
            result = 1
        listener.close()
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
    return result


def _read_boot_id() -> str:
    with open(BOOT_ID_PATH, encoding="ascii") as stream:
        boot_id = stream.read(129).strip()
    if not _valid_boot_id(boot_id):
        raise ValueError("kernel boot identifier is invalid")
    return boot_id


def _read_uptime() -> float:
    with open(PROC_UPTIME_PATH, encoding="ascii") as stream:
        raw = stream.read(128).split()[0]
    uptime = float(raw)
    if not math.isfinite(uptime) or uptime < 0:
        raise ValueError("kernel uptime is invalid")
    return uptime


def main_record_boot() -> int:
    """Early-boot helper used only by astrodeck-recovery-record.service."""
    try:
        _require_durable_data_mount()
        uptime = _read_uptime()
        if uptime > SHORT_BOOT_MAX_UPTIME_S:
            log("boot recorder started too late; recovery progress not changed")
            return 1
        recovered = record_short_boot(
            SETUP_IDENTITY_PATH,
            boot_id=_read_boot_id(),
            now=time.time(),
        )
        log("physical WiFi recovery armed" if recovered else "short boot recorded")
        return 0
    except Exception as exc:
        log(f"FATAL: could not record physical boot: {exc}")
        return 1


def main_clear_boots() -> int:
    """Healthy-boot helper used only by astrodeck-recovery-clear.service."""
    try:
        _require_durable_data_mount()
        clear_short_boot_counter(SETUP_IDENTITY_PATH)
        log("short boot recovery counter cleared")
        return 0
    except Exception as exc:
        log(f"FATAL: could not clear physical boot state: {exc}")
        return 1


def main_selftest() -> int:
    assert derive_ssid("aa:bb:cc:dd:ee:ff") == "AstroDeck-EEFF"
    assert valid_psk("astrodeck") and valid_psk("") and not valid_psk("short")
    assert valid_ssid("home") and not valid_ssid("x" * 33)
    y = emit_netplan('Cafe "42"\\home', "pass word 8")
    assert '"Cafe \\"42\\"\\\\home"' in y and "password:" in y
    assert emit_netplan("open-net", "").strip().endswith("{}")
    assert "key-management: sae" in emit_netplan("w3", "12345678", sae=True)
    scan = parse_scan(
        "BSS aa:bb(on wlan0)\n\tsignal: -40.0 dBm\n\tSSID: W3Net\n"
        "\tRSN:\n\t\t * Authentication suites: SAE\n"
        "BSS cc:dd(on wlan0)\n\tsignal: -50.0 dBm\n\tSSID: Mixed\n"
        "\tRSN:\n\t\t * Authentication suites: PSK SAE\n")
    assert needs_sae("W3Net", scan) and not needs_sae("Mixed", scan)
    generated = generate_setup_password()
    assert SETUP_PASSWORD_RE.fullmatch(generated)
    c = emit_wpa_ap_conf("AstroDeck-BEEF", generated)
    assert "mode=2" in c and f"frequency={AP_FREQ}" in c
    n = emit_networkd_ap()
    assert "DHCPServer=yes" in n and AP_CIDR in n
    print("self-test OK")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "mode",
        nargs="?",
        default="self-test",
        choices=[
            "broker",
            "self-test",
            "commission",
            "rotate",
            "record-boot",
            "clear-boots",
        ],
    )
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--ssid", help="non-secret setup SSID used during commissioning")
    ap.add_argument("--identity-path", default=SETUP_IDENTITY_PATH)
    args = ap.parse_args()
    if args.self_test or args.mode == "self-test":
        sys.exit(main_selftest())
    if args.mode == "broker":
        if args.identity_path != SETUP_IDENTITY_PATH or args.ssid is not None:
            ap.error("broker mode does not accept paths or setup values")
        sys.exit(main_broker())
    if args.mode == "commission":
        if args.identity_path == SETUP_IDENTITY_PATH:
            _require_durable_data_mount()
        setup_ssid = args.ssid
        if setup_ssid is None:
            mac = wlan_mac()
            if mac is None:
                ap.error("commission requires --ssid when the WiFi interface is absent")
            setup_ssid = derive_ssid(mac)
        commissioned = commission_setup_identity(
            args.identity_path,
            ssid=setup_ssid,
        )
        # This dedicated factory-console output is intentionally the only place
        # the printable credential is revealed. It never enters argv or logs.
        print(json.dumps({
            "setup_ssid": commissioned["setup_ssid"],
            "setup_password": commissioned["factory_psk"],
            "generation": commissioned["generation"],
        }))
        sys.exit(0)
    if args.mode == "rotate":
        if args.identity_path == SETUP_IDENTITY_PATH:
            _require_durable_data_mount()
        print(json.dumps({
            "setup_password": rotate_setup_password(args.identity_path),
        }))
        sys.exit(0)
    if args.identity_path != SETUP_IDENTITY_PATH:
        ap.error("--identity-path is only supported for commission and rotate")
    if args.mode == "record-boot":
        sys.exit(main_record_boot())
    if args.mode == "clear-boots":
        sys.exit(main_clear_boots())
    ap.error("unsupported broker mode")
