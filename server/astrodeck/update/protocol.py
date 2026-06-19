"""Server side of the supervisor contract (MIRROR of ``supervisor/protocol.py``).

The server cannot import the standalone ``supervisor`` package -- that package
lives outside the installed app and must keep working when the server is broken
(mid-rollback). So the shared constants are mirrored here and
``tests/test_supervisor_protocol_parity.py`` asserts the two never drift.

The server's role in the contract: STAGE a release into ``releases/<version>/``,
write ``state/pending-update.json``, then exit with ``EXIT_APPLY_UPDATE`` so the
supervisor swaps ``current`` + relaunches. On boot it reads
``state/update-result.json`` to surface the outcome of the last attempt.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# ---- exit codes (mirror) -----------------------------------------------------
EXIT_STOP = 0
EXIT_APPLY_UPDATE = 92

# ---- file/dir names (mirror) -------------------------------------------------
CURRENT_POINTER = "current"
RELEASES_DIRNAME = "releases"
VENV_DIRNAME = "venv"
STATE_DIRNAME = "state"
LAST_GOOD_FILE = "last-good"
PENDING_FILE = "pending-update.json"
RESULT_FILE = "update-result.json"
FAILED_DIRNAME = "failed"


def _atomic_write_json(path: Path, data: dict) -> None:
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


class InstallLayout:
    """The subset of the install-root layout the server writes/reads."""

    def __init__(self, root: "str | Path") -> None:
        self.root = Path(root)

    @property
    def releases(self) -> Path:
        return self.root / RELEASES_DIRNAME

    def release(self, version: str) -> Path:
        return self.releases / version

    @property
    def state(self) -> Path:
        return self.root / STATE_DIRNAME

    @property
    def pending(self) -> Path:
        return self.state / PENDING_FILE

    @property
    def result(self) -> Path:
        return self.state / RESULT_FILE

    @property
    def current(self) -> Path:
        return self.root / CURRENT_POINTER

    def read_current(self) -> "str | None":
        try:
            v = self.current.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return v or None

    def write_pending(self, version: str, *, path: "str | None" = None,
                      ts: "float | None" = None) -> None:
        """Stage the apply request the supervisor will act on after exit-92."""
        _atomic_write_json(self.pending, {
            "version": version,
            "path": path or str(self.release(version)),
            "ts": ts,
        })

    def read_result(self) -> "dict | None":
        try:
            data = json.loads(self.result.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def clear_result(self) -> None:
        try:
            self.result.unlink()
        except OSError:
            pass


def supervised_install_root() -> "Path | None":
    """The install root when running UNDER the supervisor, else None.

    The supervisor (or the operator's launcher) exports ``ASTRODECK_INSTALL_ROOT``.
    When unset the server is running un-supervised (plain ``python -m astrodeck``),
    so self-update apply is unavailable (check-only)."""
    root = (os.environ.get("ASTRODECK_INSTALL_ROOT") or "").strip()
    return Path(root) if root else None
