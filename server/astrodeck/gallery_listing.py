"""Bounded, temporary listing snapshots for pagination and bulk selection.

Rows live in a temporary file, not a second full library in RAM. A page reads
only its own rows; expiry is explicit rather than silently changing selection.
"""
from __future__ import annotations

from array import array
from collections import OrderedDict
import json
import secrets
import tempfile
import threading
import time

from . import gallery, capture_geometry

TTL_SECONDS = 15 * 60
MAX_BYTES = 256 * 1024 * 1024
MAX_LISTINGS = 32
_listings = OrderedDict()
_lock = threading.RLock()
_build_lock = threading.Lock()


class ListingExpired(ValueError):
    pass


def _scope(root, q, night_from, night_to):
    return (str(root.resolve()), q.strip().lower(), night_from.strip(), night_to.strip())


class Listing:
    def __init__(self, root, scope, rows, truncated):
        self.root = root
        self.scope = scope
        self.token = secrets.token_hex(16)
        self.touched = time.monotonic()
        self.file = tempfile.TemporaryFile()
        self.offsets = array("Q")
        self.lock = threading.Lock()
        self.total = len(rows)
        self.bytes = sum(row["bytes"] for row in rows)
        self.truncated = truncated
        groups = capture_geometry.geometry_groups(rows)
        self.groups = groups[:1000]
        self.geometry_truncated = len(groups) > 1000
        try:
            for row in rows:
                self.offsets.append(self.file.tell())
                self.file.write(json.dumps(row, allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n")
                if self.file.tell() > MAX_BYTES:
                    raise ValueError("This listing is too large. Narrow the gallery search and try again.")
            self.size = self.file.tell()
        except BaseException:
            self.file.close()
            raise

    def rows(self, offset=0, limit=None):
        end = min(self.total, offset + limit) if limit is not None else self.total
        if offset >= end:
            return []
        with self.lock:
            self.file.seek(self.offsets[offset])
            return [json.loads(self.file.readline()) for _ in range(offset, end)]

    def __del__(self):
        file = getattr(self, "file", None)
        if file is not None:
            file.close()


def _prune():
    now = time.monotonic()
    for token, entry in list(_listings.items()):
        if now - entry.touched > TTL_SECONDS:
            del _listings[token]
    # Dropping an entry closes its file when active readers release their
    # references. Never close a file underneath an in-flight page/download.
    while len(_listings) > MAX_LISTINGS or sum(item.size for item in _listings.values()) > MAX_BYTES:
        _listings.popitem(last=False)


def create(q="", night_from="", night_to=""):
    with _build_lock:
        return _create(q, night_from, night_to)


def _create(q, night_from, night_to):
    root = gallery.capture_root()
    rows, truncated = gallery.scan(root)
    rows = gallery.filter_rows(rows, q=q, night_from=night_from, night_to=night_to)
    entry = Listing(root, _scope(root, q, night_from, night_to), rows, truncated)
    with _lock:
        _listings[entry.token] = entry
        _prune()
    return entry


def get(token, q="", night_from="", night_to=""):
    if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        raise ValueError("Invalid gallery listing.")
    with _lock:
        _prune()
        entry = _listings.get(token)
        if entry is None or entry.scope != _scope(gallery.capture_root(), q, night_from, night_to):
            raise ListingExpired("This gallery listing expired or its filter changed. Refresh the gallery and select the frames again.")
        entry.touched = time.monotonic()
        _listings.move_to_end(token)
        return entry


def page(*, q="", night_from="", night_to="", offset=0, limit=200, cursor=""):
    if cursor:
        if len(cursor) > 64:
            raise ValueError("Invalid gallery cursor.")
        token, sep, position = cursor.partition(":")
        if not sep or not position.isascii() or not position.isdecimal():
            raise ValueError("Invalid gallery cursor.")
        offset = int(position)
        entry = get(token, q, night_from, night_to)
        if offset > entry.total:
            raise ValueError("Invalid gallery cursor position.")
    else:
        entry = create(q, night_from, night_to)
    frames = entry.rows(offset, limit)
    end = offset + len(frames)
    return dict(frames=frames, total=entry.total, bytes=entry.bytes, offset=offset, limit=limit,
                truncated=entry.truncated, geometry_groups=entry.groups,
                geometry_truncated=entry.geometry_truncated, snapshot=entry.token,
                next_cursor=f"{entry.token}:{end}" if end < entry.total else None)


def selected(token, q="", night_from="", night_to="", paths=None):
    """Validate the frozen selection before a download or destructive action.

    New files never join it. Changed/deleted files require a refreshed selection;
    don't silently download or delete a replacement at an old path.
    """
    entry = get(token, q, night_from, night_to)
    rows = entry.rows()
    if paths:
        wanted = set(paths)
        rows = [r for r in rows if r["path"] in wanted]
        if len(rows) != len(wanted):
            raise ListingExpired("The selection is not in this gallery listing. Refresh the gallery and select the frames again.")
    for row in rows:
        try:
            path = gallery.safe_subpath(entry.root, row["path"])
            if gallery.signature(path.stat()) == row["file_version"]:
                continue
        except (KeyError, OSError):
            pass
        raise ListingExpired("Selected files changed or were removed. Refresh the gallery and select the frames again.")
    return rows, entry.truncated
