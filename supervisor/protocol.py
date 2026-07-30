"""Supervisor <-> server contract: exit codes, the install-root layout, and the
state files they exchange.

STANDALONE by design -- this module imports only the stdlib so the supervisor
keeps working even when the server package is broken (mid-rollback). The server
side mirrors the SAME constants in ``astrodeck.update.protocol``; a test asserts
the two never drift.

Install root layout (see spec 2026-06-19-self-update-design.md section 3):

    <root>/
      current                  pointer FILE: the active version string
      releases/<version>/      one dir per staged version (server/ + ui/dist + manifest)
      venv/                    shared Python venv (Python era)
      state/
        last-good              last version that passed health-check
        pending-update.json    written by the server, read by the supervisor
        update-result.json     written by the supervisor, read by the server on boot
        failed/<version>       marker: a version that failed health-check (never retried)
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

# ---- exit codes the SERVER returns to ask the supervisor to act --------------
EXIT_STOP = 0           # clean shutdown: supervisor stops too
EXIT_APPLY_UPDATE = 92  # apply the staged pending-update, then relaunch + probe

# ---- file/dir names ----------------------------------------------------------
CURRENT_POINTER = "current"
RELEASES_DIRNAME = "releases"
VENV_DIRNAME = "venv"
STATE_DIRNAME = "state"
LAST_GOOD_FILE = "last-good"
PENDING_FILE = "pending-update.json"
RESULT_FILE = "update-result.json"
FAILED_DIRNAME = "failed"

# A version string used as a path component must be filename-safe (defense against
# a crafted pending-update.json injecting ``..`` / separators).
_SAFE_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]*$")


def is_safe_version(v: str) -> bool:
    return bool(isinstance(v, str) and _SAFE_VERSION.match(v) and ".." not in v)


def write_json_atomic(path: Path, data: dict) -> None:
    """Write ``data`` as JSON via a temp file + atomic replace (no torn reads)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


class Layout:
    """All install-root paths in one place (both sides build paths from here)."""

    def __init__(self, root: "str | Path") -> None:
        self.root = Path(root)

    @property
    def current(self) -> Path:
        return self.root / CURRENT_POINTER

    @property
    def releases(self) -> Path:
        return self.root / RELEASES_DIRNAME

    def release(self, version: str) -> Path:
        return self.releases / version

    @property
    def venv(self) -> Path:
        return self.root / VENV_DIRNAME

    @property
    def state(self) -> Path:
        return self.root / STATE_DIRNAME

    @property
    def last_good(self) -> Path:
        return self.state / LAST_GOOD_FILE

    @property
    def pending(self) -> Path:
        return self.state / PENDING_FILE

    @property
    def result(self) -> Path:
        return self.state / RESULT_FILE

    @property
    def failed_dir(self) -> Path:
        return self.state / FAILED_DIRNAME

    def failed_marker(self, version: str) -> Path:
        return self.failed_dir / version

    def ensure(self) -> None:
        self.releases.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)

    # -- pointer + markers -----------------------------------------------------
    def _read_pointer(self, path: Path) -> "str | None":
        """Read a version pointer, tolerating a byte-order mark.

        ``encoding="utf-8"`` decodes a leading BOM to U+FEFF, and ``strip()``
        removes whitespace — which a BOM is not. The surviving character makes
        the version a name no release directory has, so ``PYTHONPATH`` points at
        nothing and the server silently imports whatever ``astrodeck`` happens to
        be in site-packages: an old build, running, healthy-looking, serving a
        different version than the pointer claims. That is a very quiet way to
        lose an install, and it takes one editor that writes a BOM (PowerShell's
        ``Set-Content -Encoding utf8``, Notepad) to trigger it.

        ``utf-8-sig`` consumes a BOM when present and is identical to utf-8 when
        it is not."""
        try:
            v = path.read_text(encoding="utf-8-sig").strip()
        except OSError:
            return None
        return v or None

    def read_current(self) -> "str | None":
        return self._read_pointer(self.current)

    def set_current(self, version: str) -> None:
        write_text_atomic(self.current, version.strip() + "\n")

    def read_last_good(self) -> "str | None":
        return self._read_pointer(self.last_good)

    def set_last_good(self, version: str) -> None:
        write_text_atomic(self.last_good, version.strip() + "\n")

    def is_failed(self, version: str) -> bool:
        return self.failed_marker(version).exists()

    def mark_failed(self, version: str, reason: str = "") -> None:
        write_text_atomic(self.failed_marker(version), reason + "\n")

    def read_pending(self) -> "dict | None":
        try:
            data = json.loads(self.pending.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def clear_pending(self) -> None:
        try:
            self.pending.unlink()
        except OSError:
            pass

    def write_result(self, result: dict) -> None:
        write_json_atomic(self.result, result)
