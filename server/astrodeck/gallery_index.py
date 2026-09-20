"""Disposable on-disk FITS metadata cache. Never stores image pixels or site cards."""
from __future__ import annotations

import json
import math
import os
import sqlite3

from .persist import safe_subpath

DIRECTORY = "_gallery_index"
MAX_ENTRIES = 200_000


def signature(st):
    # Nanoseconds and file identity also catch replacements preserving mtime/size.
    # Windows DirEntry.stat reports inode 0, unlike Path.stat. Creation time
    # still distinguishes ordinary replacements there; use inode on POSIX.
    inode = 0 if os.name == "nt" else st.st_ino
    return f"{st.st_mtime_ns}:{st.st_ctime_ns}:{st.st_size}:{inode}"


class MetadataIndex:
    """One scan transaction, serialized by gallery's scan lock.

    An unavailable cache must not hide files. Callers can always read headers
    directly, including on read-only storage or while another process writes.
    """

    def __init__(self, root):
        self.connection = None
        self.entries = {}
        self.pending = []
        self.seen = set()
        try:
            path = safe_subpath(root, f"{DIRECTORY}/metadata-v1.sqlite3")
            path.parent.mkdir(parents=True, exist_ok=True)
            for attempt in range(2):
                try:
                    self.connection = sqlite3.connect(path, timeout=.1)
                    self.connection.execute("CREATE TABLE IF NOT EXISTS metadata (path TEXT PRIMARY KEY, stamp TEXT NOT NULL, payload TEXT NOT NULL)")
                    self.entries = {rel: (stamp, payload) for rel, stamp, payload in
                                    self.connection.execute("SELECT path, stamp, payload FROM metadata LIMIT ?", (MAX_ENTRIES + 1,))}
                    break
                except sqlite3.Error as error:
                    if self.connection is not None:
                        self.connection.close()
                        self.connection = None
                    if attempt == 0 and getattr(error, "sqlite_errorcode", None) in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                        # Only this disposable cache, already containment-checked.
                        path.unlink()
                        continue
                    raise
        except (OSError, KeyError, sqlite3.Error):
            if self.connection is not None:
                self.connection.close()
                self.connection = None

    def get(self, rel, stamp):
        self.seen.add(rel)
        entry = self.entries.get(rel)
        if entry and entry[0] == stamp:
            try:
                value = json.loads(entry[1])
                if (isinstance(value, dict) and value.get("_header_ok") is True
                        and all(isinstance(value.get(f), str) for f in ("target", "filter", "frame_type"))
                        and all(value.get(f) is None or isinstance(value[f], (int, float)) and math.isfinite(value[f])
                                for f in ("width", "height", "bin_x", "bin_y", "exposure_s", "ts"))):
                    return value
            except (ValueError, TypeError):
                pass
        return None

    def put(self, rel, stamp, meta):
        if self.connection is not None:
            self.pending.append((rel, stamp, json.dumps(meta, allow_nan=False, separators=(",", ":"))))

    def close(self, *, complete):
        if self.connection is None:
            return
        try:
            if self.pending:
                self.connection.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?, ?)", self.pending)
            # A bounded scan does not know which unseen files were removed.
            if complete:
                self.connection.executemany("DELETE FROM metadata WHERE path = ?",
                                            ((rel,) for rel in self.entries.keys() - self.seen))
            if len(self.entries) + len(self.pending) > MAX_ENTRIES:
                self.connection.execute("DELETE FROM metadata WHERE rowid NOT IN (SELECT rowid FROM metadata ORDER BY rowid DESC LIMIT ?)", (MAX_ENTRIES,))
            self.connection.commit()
        except sqlite3.Error:
            self.connection.rollback()
        finally:
            self.connection.close()
