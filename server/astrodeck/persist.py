"""Small JSON persistence helpers.

A single place for the load-bearing "write a dict to disk without losing the old
copy on a crash or an antivirus lock" logic. Used by ``config``/``profiles``/
``plans`` and re-exported for the Sky-Atlas surface (which persists framing
sessions the same way).

Design notes:
- **Atomic write (Windows-safe):** serialize to an unpredictable same-directory
  staging file, ``os.fsync`` it (and the parent dir) for power-loss durability,
  then ``os.replace`` it over the target. ``os.replace`` is atomic on POSIX and
  Windows. On Windows an antivirus scanner can briefly lock a freshly-created
  file, so the replace is wrapped in a short retry (``PermissionError``).
- **Best-effort backup:** before overwriting an existing file we **copy** it to
  ``<path>.bak`` (``shutil.copy2`` — a real copy, so the primary is never
  momentarily absent) so a corrupt write or a later parse failure has a
  recoverable previous version.
- These functions are deliberately dependency-free (stdlib only) and synchronous;
  callers hold their own in-memory state and only touch disk on mutation.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

# AV scanners on Windows can hold a transient lock on a just-created file;
# retry the rename a few times before giving up.
_REPLACE_RETRIES = 3
_REPLACE_BACKOFF_S = 0.12
_PRIVATE_FILE_MODE = 0o600
_PRIVATE_DIR_MODE = 0o700


class PrivatePermissionsError(RuntimeError):
    """Private state cannot be secured without falling back to broad access."""


def ensure_dir(path: Path) -> None:
    """Create ``path`` (a directory) and parents if missing. No-op if present."""
    path.mkdir(parents=True, exist_ok=True)


def ensure_private_dir(path: Path) -> None:
    """Create/repair a directory used for secrets and private configuration.

    Windows uses an exact protected DACL through a no-follow Win32 handle.  On
    POSIX the directory is owner-only and symlinks/special files are refused.
    Unlike :func:`ensure_dir`, this function is deliberately fail-closed.
    """
    path = Path(path)
    if os.name == "nt":
        from .windows_acl import ensure_private_directory

        ensure_private_directory(path)
        return

    try:
        path.mkdir(mode=_PRIVATE_DIR_MODE, parents=True, exist_ok=True)
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise PrivatePermissionsError(
                f"private directory is not a real directory: {path}"
            )
        os.chmod(path, _PRIVATE_DIR_MODE, follow_symlinks=False)
        if stat.S_IMODE(path.lstat().st_mode) != _PRIVATE_DIR_MODE:
            raise PrivatePermissionsError(
                f"private directory mode verification failed: {path}"
            )
    except PrivatePermissionsError:
        raise
    except (OSError, NotImplementedError, ValueError) as exc:
        raise PrivatePermissionsError(
            f"cannot secure private directory {path}: {exc}"
        ) from exc


def harden_private_file(path: Path) -> None:
    """Apply and verify private permissions on an existing regular file.

    A missing file is an ordinary state used by first boot and is left for the
    caller to handle.  Every other inability to secure the path is fatal.
    """
    path = Path(path)
    # ``lexists`` sees broken links.  It is used only for missing-file
    # semantics; the Windows security operation itself reopens the object with
    # OPEN_REPARSE_POINT and fails if this result raced.
    if not os.path.lexists(path):
        return
    if os.name == "nt":
        from .windows_acl import harden_private_path

        harden_private_path(path, directory=False)
        return
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise PrivatePermissionsError(
                f"private file is not a regular file: {path}"
            )
        os.chmod(path, _PRIVATE_FILE_MODE, follow_symlinks=False)
        if stat.S_IMODE(path.lstat().st_mode) != _PRIVATE_FILE_MODE:
            raise PrivatePermissionsError(
                f"private file mode verification failed: {path}"
            )
    except PrivatePermissionsError:
        raise
    except (NotImplementedError, OSError, ValueError) as exc:
        raise PrivatePermissionsError(
            f"cannot secure private file {path}: {exc}"
        ) from exc


def secure_private_tree(root: Path) -> None:
    """Secure a config root and every existing descendant without links.

    This is the startup migration for state created by older releases.  The
    root is secured before names below it are enumerated.  Every descendant is
    independently opened/hardened and any link, junction, special file, or
    traversal error aborts startup.
    """
    root = Path(root)
    if os.name == "nt":
        from .windows_acl import (harden_private_path,
                                  require_acl_capable_filesystem)

        require_acl_capable_filesystem(root)
        ensure_private_dir(root)
        pending = [root]
        while pending:
            directory = pending.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError as exc:
                raise PrivatePermissionsError(
                    f"cannot enumerate private directory {directory}: {exc}"
                ) from exc
            for entry in entries:
                child = Path(entry.path)
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise PrivatePermissionsError(
                        f"cannot inspect private path {child}: {exc}"
                    ) from exc
                is_directory = stat.S_ISDIR(info.st_mode)
                if not is_directory and not stat.S_ISREG(info.st_mode):
                    # The Win32 helper still gets first refusal so reparse
                    # points consistently raise PrivateAclError.
                    harden_private_path(child, directory=None)
                    raise PrivatePermissionsError(
                        f"unsupported object in private tree: {child}"
                    )
                harden_private_path(child, directory=is_directory)
                if is_directory:
                    pending.append(child)
        return

    ensure_private_dir(root)
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise PrivatePermissionsError(
                f"cannot enumerate private directory {directory}: {exc}"
            ) from exc
        for entry in entries:
            child = Path(entry.path)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise PrivatePermissionsError(
                    f"cannot inspect private path {child}: {exc}"
                ) from exc
            if stat.S_ISLNK(info.st_mode):
                raise PrivatePermissionsError(
                    f"refusing link in private tree: {child}"
                )
            if stat.S_ISDIR(info.st_mode):
                ensure_private_dir(child)
                pending.append(child)
            elif stat.S_ISREG(info.st_mode):
                harden_private_file(child)
            else:
                raise PrivatePermissionsError(
                    f"unsupported object in private tree: {child}"
                )


# Windows treats these as device names in EVERY directory, with or without an
# extension ("CON.txt" is the console). Refused uniformly on both platforms for
# the same reason both guards below check both separators — see the note in
# ``safe_id_path``.
_WIN_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def _refuse_component(name: str) -> bool:
    """True when ``name`` must not be used as ONE path component.

    Shared by both guards so they cannot drift apart again. They did drift, the
    day ``safe_subpath`` was written (2026-08-03): the new one refused ``:``
    anywhere, trailing dot/space, and reserved device names, and the older
    ``safe_id_path`` refused none of the three. Not exploitable through today's
    callers — every one passes a non-empty suffix, and a device name WITH an
    extension does not resolve to the device — but ``x.`` and ``x `` already
    alias two ids onto one file on Windows, and the first
    ``safe_id_path(base, ident, suffix="")`` call would reopen the rest."""
    return (name in (".", "..")
            or ":" in name                      # drive prefix AND NTFS ADS
            or name != name.rstrip(" .")        # Windows strips these silently
            or name.split(".")[0].upper() in _WIN_RESERVED)


def safe_id_path(base: Path, ident: str, suffix: str = ".json") -> Path:
    """Resolve ``base/<ident><suffix>`` for a client-controllable ``ident``,
    raising ``KeyError`` for anything that is not a single contained filename
    component on *any* platform (callers/routes map ``KeyError`` → 404).

    The rejection is platform-UNIFORM and does NOT depend on ``os.sep``: an id
    holding a separator of *either* OS (``/`` or ``\\``), a NUL, a Windows drive
    prefix (``X:``), or a ``.``/``..`` ref is refused BEFORE touching the
    filesystem. This matters because a bare ``Path.resolve()`` + parent check
    only catches the *running* OS's separators — so on Linux ``"..\\victim"`` and
    ``"C:\\Windows\\..."`` are treated as literal filenames and silently created
    inside ``base`` (no escape, but not the refusal the security contract and its
    tests require), while on Windows they escape. Checking both separator sets up
    front makes a Windows-authored store and a Linux host agree, and keeps the
    guard's behavior independent of where the server runs. The ``resolve()`` +
    parent check remains as a belt-and-suspenders backstop (symlinks / odd
    normalization) once the string-level vectors are excluded."""
    if (not ident
            or "/" in ident
            or "\\" in ident
            or "\x00" in ident
            or _refuse_component(ident)):
        raise KeyError(ident)
    resolved = (base / f"{ident}{suffix}").resolve()
    if resolved.parent != base.resolve():
        raise KeyError(ident)
    return resolved


def safe_subpath(base: Path, relpath: str) -> Path:
    """Resolve a client-controllable MULTI-segment relative path under ``base``,
    raising ``KeyError`` for anything not contained (callers map ``KeyError`` →
    404 / fall back).

    The multi-segment sibling of ``safe_id_path``: use that one when the client
    supplies a bare id, this one when it supplies a path with directories in it
    (static assets, gallery frames). Same platform-uniform stance — every check
    is a string check made BEFORE the filesystem is touched, and both OSes'
    separators count as separators regardless of where the server runs, so a
    Windows-authored store and a Linux host agree.

    Refused: absolute paths, ``.``/``..`` in any component, either separator's
    escape, NUL, a ``:`` anywhere (Windows drive prefix AND NTFS alternate data
    streams), a component with a trailing dot or space (Windows silently strips
    them, so ``evil.txt.`` and ``evil.txt`` name the same file), and reserved
    device names. The ``resolve()`` + containment check stays as the backstop
    for symlinks pointing out of ``base`` — the one vector no string check can
    see."""
    if not relpath or "\x00" in relpath:
        raise KeyError(relpath)
    # Absolute and UNC forms are REFUSED, not silently reinterpreted. Dropping
    # the empty leading segment would quietly turn "/etc/passwd" into a
    # contained "etc/passwd" and "\\\\server\\share\\x" into "server/share/x" —
    # harmless here (neither file exists under the root) but a coercion the
    # caller never asked for, and the next caller may join the result to a
    # different base. Refusing keeps "what I passed is what got checked".
    if relpath[0] in ("/", "\\"):
        raise KeyError(relpath)
    parts = [p for p in relpath.replace("\\", "/").split("/") if p != ""]
    if not parts:
        raise KeyError(relpath)
    for p in parts:
        if _refuse_component(p):
            raise KeyError(relpath)
    resolved = base.joinpath(*parts).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise KeyError(relpath)
    return resolved


def _replace_with_retry(src: Path, dst: Path) -> None:
    """``os.replace(src, dst)`` with a short retry on Windows PermissionError.

    Exactly ``_REPLACE_RETRIES`` attempts: each failed attempt backs off, and
    the final ``PermissionError`` is re-raised (no redundant extra replace).
    """
    last: PermissionError | None = None
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:  # AV / indexer briefly holding the file
            last = e
            time.sleep(_REPLACE_BACKOFF_S * (attempt + 1))
    # All attempts exhausted — surface the real error.
    if last is not None:
        raise last


def write_json_atomic(path: Path, data: Any, *, backup: bool = True) -> None:
    """Serialize ``data`` to ``path`` atomically, keeping a ``.bak`` of the old file.

    Durability (power-loss safe within the limits of the filesystem):

    - The ``.bak`` is created by **COPY** (``shutil.copy2``), never by moving the
      primary, so the live file is never momentarily absent — a crash between the
      backup and the replace still leaves a complete primary on disk.
    - The new bytes are written to an unpredictable same-directory staging file
      and ``os.fsync``'d (and the parent directory fsync'd) *before*
      ``os.replace`` so the rename can never expose a half-written file.

    ``data`` must be JSON-serializable. The parent directory is created if needed.
    """
    path = Path(path)
    ensure_private_dir(path.parent)
    harden_private_file(path)
    if backup and path.exists():
        backup_path = path.with_suffix(path.suffix + ".bak")
        harden_private_file(backup_path)
        try:
            shutil.copy2(path, backup_path)
        except OSError:
            # Backups remain best-effort for ordinary copy errors.  Never leave
            # behind a possibly partial/broad destination from the failed copy.
            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            try:
                harden_private_file(backup_path)
            except RuntimeError as security_error:
                # A backup that could not be made private is worse than no
                # backup.  Remove it and propagate the security failure before
                # the primary is changed.
                try:
                    backup_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    # Do not let an ordinary OSError mask the security failure:
                    # callers intentionally catch I/O errors for recoverable
                    # state, whereas private-permission failures must abort.
                    raise PrivatePermissionsError(
                        "cannot remove backup after private-permission failure "
                        f"at {backup_path}: {cleanup_error}"
                    ) from security_error
                raise
    text = json.dumps(data, indent=2, ensure_ascii=False)
    _write_private_atomic(path, text)


def _write_private_atomic(path: Path, text: str) -> None:
    """Write already-serialized text through a hardened staging file."""
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        if os.name == "posix":
            os.fchmod(fd, _PRIVATE_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = -1  # ownership transferred to the file object
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # Windows mkstemp inherits the private parent DACL, but verification is
        # explicit so a filesystem/API failure cannot be hidden by inheritance.
        harden_private_file(tmp)
        _replace_with_retry(tmp, path)
        harden_private_file(path)
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    _fsync_dir(path.parent)


def write_private_text_atomic(path: Path, text: str) -> None:
    """Atomically write owner-only UTF-8 text without a plaintext backup.

    Used for singleton secrets such as the session-signing key.  ``mkstemp``
    avoids the predictable ``<name>.tmp`` symlink/race surface and creates the
    file with mode 0600 independently of the process umask.
    """
    path = Path(path)
    ensure_private_dir(path.parent)
    harden_private_file(path)
    _write_private_atomic(path, text)


def _fsync_dir(directory: Path) -> None:
    """Best-effort fsync of a directory so a rename is durable. No-op where the
    platform can't open a directory fd (e.g. Windows raises ``PermissionError``/
    ``OSError`` on ``os.open`` of a dir)."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except (OSError, ValueError):
        return
    try:
        os.fsync(fd)
    except (OSError, ValueError):
        pass
    finally:
        os.close(fd)


def read_json(path: Path) -> Any:
    """Read + parse a JSON file. Raises ``FileNotFoundError`` / ``ValueError``."""
    path = Path(path)
    # Repair files created by older releases on first read, not only after the
    # next settings mutation (which may never happen on an unattended rig).
    harden_private_file(path)
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_or(path: Path, default: Any = None) -> Any:
    """Read JSON, returning ``default`` on a missing or unparseable file."""
    try:
        return read_json(path)
    except (FileNotFoundError, ValueError, OSError):
        return default


def list_json(directory: Path) -> list[Path]:
    """Sorted list of ``*.json`` files in ``directory`` (empty if missing)."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())
