"""Fail-closed operating-system privilege checks for relay startup.

This copy is intentionally component-local: the standalone relay image does not
contain the AstroDeck server package.  Keep it stdlib-only and call the guard
before reading relay secrets or importing the ASGI server.
"""

from __future__ import annotations

import os


class PrivilegeDetectionError(RuntimeError):
    """Raised when the process privilege level cannot be established safely."""


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
    except BaseException as exc:
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
    """Return whether this process has host-administrator privileges."""

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
