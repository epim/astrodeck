"""Small JSON persistence helpers.

A single place for the load-bearing "write a dict to disk without losing the old
copy on a crash or an antivirus lock" logic. Used by ``config``/``profiles``/
``plans`` and re-exported for the Sky-Atlas surface (which persists framing
sessions the same way).

Design notes:
- **Atomic write (Windows-safe):** serialize to ``<path>.tmp``, ``os.fsync`` it
  (and the parent dir) for power-loss durability, then ``os.replace`` it over the
  target. ``os.replace`` is atomic on POSIX and Windows. On Windows an antivirus
  scanner can briefly lock a freshly-created file, so the replace is wrapped in a
  short retry (``PermissionError``).
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
import time
from pathlib import Path
from typing import Any

# AV scanners on Windows can hold a transient lock on a just-created file;
# retry the rename a few times before giving up.
_REPLACE_RETRIES = 3
_REPLACE_BACKOFF_S = 0.12


def ensure_dir(path: Path) -> None:
    """Create ``path`` (a directory) and parents if missing. No-op if present."""
    path.mkdir(parents=True, exist_ok=True)


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
            or ident in (".", "..")
            or "/" in ident
            or "\\" in ident
            or "\x00" in ident
            or (len(ident) >= 2 and ident[1] == ":")):   # X: — Windows drive
        raise KeyError(ident)
    resolved = (base / f"{ident}{suffix}").resolve()
    if resolved.parent != base.resolve():
        raise KeyError(ident)
    return resolved


# Windows treats these as device names in EVERY directory, with or without an
# extension ("CON.txt" is the console). Refused uniformly on both platforms for
# the same reason ``safe_id_path`` checks both separators — see its docstring.
_WIN_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


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
        if (p in (".", "..")
                or ":" in p
                or p != p.rstrip(" .")
                or p.split(".")[0].upper() in _WIN_RESERVED):
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
    - The new bytes are written to ``<path>.tmp`` and ``os.fsync``'d (and the
      parent directory fsync'd) *before* ``os.replace`` so the rename can never
      expose a half-written file.

    ``data`` must be JSON-serializable. The parent directory is created if needed.
    """
    path = Path(path)
    ensure_dir(path.parent)
    if backup and path.exists():
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except OSError:
            pass  # backup is best-effort; never block the write on it
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, indent=2, ensure_ascii=False)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    _replace_with_retry(tmp, path)
    _fsync_dir(path.parent)


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
    return json.loads(Path(path).read_text(encoding="utf-8"))


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
