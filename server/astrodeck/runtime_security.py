"""Fail-closed operating-system privilege checks for server startup.

The web process is intentionally not a privileged helper.  Running it as root,
with an elevated Windows token, or as LocalSystem would turn any future server
or driver vulnerability into an immediate host compromise.  Keep this module
stdlib-only so the check can run before application configuration is imported.
"""

from __future__ import annotations

import ipaddress
import os


class PrivilegeDetectionError(RuntimeError):
    """Raised when the process privilege level cannot be established safely."""


def parse_forwarded_allow_ips(raw: str | None) -> str:
    """Canonicalize an exact Uvicorn trusted-proxy allow-list.

    Only literal IP addresses and CIDR networks are accepted.  In particular,
    wildcard and all-address networks are rejected because either would let a
    directly connected client forge forwarding metadata.
    """

    if raw is None or not raw.strip():
        return ""
    values: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        value = item.strip()
        if not value or value == "*":
            raise ValueError(
                "trusted proxy entries must be non-empty IP literals or CIDRs"
            )
        try:
            parsed = (
                ipaddress.ip_network(value, strict=False)
                if "/" in value
                else ipaddress.ip_address(value)
            )
        except ValueError as exc:
            raise ValueError(f"invalid trusted proxy address: {value!r}") from exc
        canonical = str(parsed)
        if canonical in {"0.0.0.0/0", "::/0"}:
            raise ValueError("all-address trusted proxy networks are forbidden")
        if canonical in seen:
            raise ValueError(f"duplicate trusted proxy address: {canonical}")
        seen.add(canonical)
        values.append(canonical)
    return ",".join(values)


def _posix_is_elevated() -> bool:
    get_effective_uid = getattr(os, "geteuid", None)
    if not callable(get_effective_uid):
        raise PrivilegeDetectionError("effective UID query is unavailable")
    try:
        effective_uid = get_effective_uid()
    except Exception as exc:  # noqa: BLE001 - an unknown result must fail closed
        raise PrivilegeDetectionError("effective UID query failed") from exc
    if not isinstance(effective_uid, int) or effective_uid < 0:
        raise PrivilegeDetectionError("effective UID query returned an invalid value")
    return effective_uid == 0


def _windows_is_elevated() -> bool:
    # Imports stay local so POSIX startup does not depend on Windows-only ctypes
    # declarations and unit tests can import this module on every target.
    try:
        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception as exc:  # noqa: BLE001 - platform/API uncertainty is fatal
        raise PrivilegeDetectionError("Windows token APIs are unavailable") from exc

    token_query = 0x0008
    token_user_information = 1
    token_elevation_information = 20
    win_local_system_sid = 22
    error_insufficient_buffer = 122

    class TokenElevation(ctypes.Structure):
        _fields_ = [("TokenIsElevated", wintypes.DWORD)]

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]

    class TokenUser(ctypes.Structure):
        _fields_ = [("User", SidAndAttributes)]

    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.IsValidSid.argtypes = [wintypes.LPVOID]
    advapi32.IsValidSid.restype = wintypes.BOOL
    advapi32.IsWellKnownSid.argtypes = [wintypes.LPVOID, ctypes.c_int]
    advapi32.IsWellKnownSid.restype = wintypes.BOOL

    def fail(operation: str) -> PrivilegeDetectionError:
        return PrivilegeDetectionError(
            f"{operation} failed with Windows error {ctypes.get_last_error()}"
        )

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)
    ):
        raise fail("OpenProcessToken")

    result: bool | None = None
    pending_error: BaseException | None = None
    try:
        elevation = TokenElevation()
        returned = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token,
            token_elevation_information,
            ctypes.byref(elevation),
            ctypes.sizeof(elevation),
            ctypes.byref(returned),
        ):
            raise fail("GetTokenInformation(TokenElevation)")
        if returned.value < ctypes.sizeof(elevation):
            raise PrivilegeDetectionError(
                "GetTokenInformation(TokenElevation) returned a short result"
            )
        if elevation.TokenIsElevated:
            result = True
        else:
            required = wintypes.DWORD()
            ctypes.set_last_error(0)
            first_query = advapi32.GetTokenInformation(
                token,
                token_user_information,
                None,
                0,
                ctypes.byref(required),
            )
            error = ctypes.get_last_error()
            if first_query or error != error_insufficient_buffer or not required.value:
                raise PrivilegeDetectionError(
                    "GetTokenInformation(TokenUser) did not report a usable size"
                )
            buffer = ctypes.create_string_buffer(required.value)
            if not advapi32.GetTokenInformation(
                token,
                token_user_information,
                buffer,
                required.value,
                ctypes.byref(required),
            ):
                raise fail("GetTokenInformation(TokenUser)")
            sid = ctypes.cast(buffer, ctypes.POINTER(TokenUser)).contents.User.Sid
            if not sid or not advapi32.IsValidSid(sid):
                raise PrivilegeDetectionError("process token contains an invalid SID")
            result = bool(advapi32.IsWellKnownSid(sid, win_local_system_sid))
    except BaseException as exc:  # preserve the detection failure across cleanup
        pending_error = exc
    finally:
        if not kernel32.CloseHandle(token) and pending_error is None:
            pending_error = fail("CloseHandle(process token)")

    if pending_error is not None:
        if isinstance(pending_error, PrivilegeDetectionError):
            raise pending_error
        raise PrivilegeDetectionError("Windows privilege detection failed") from pending_error
    if result is None:
        raise PrivilegeDetectionError("Windows privilege detection returned no result")
    return result


def is_elevated_runtime() -> bool:
    """Return whether this process has host-administrator privileges.

    Unsupported platforms and failed OS queries raise instead of guessing that
    the process is safe.
    """

    if os.name == "posix":
        return _posix_is_elevated()
    if os.name == "nt":
        return _windows_is_elevated()
    raise PrivilegeDetectionError(f"unsupported operating-system family: {os.name!r}")


def require_unprivileged_runtime(component: str) -> None:
    """Refuse to run a network component with host-administrator privileges."""

    if is_elevated_runtime():
        label = component.strip() if isinstance(component, str) else ""
        label = label or "network service"
        raise RuntimeError(
            f"{label} refuses to run as root, LocalSystem, or with an elevated "
            "Administrator token; use a dedicated unprivileged service account"
        )
