"""The reconciliation core: what a side HAS, and what the difference is.

THE ONE DESIGN DECISION, and everything else follows from it: **sync state is
DERIVED, never remembered.** There is no "sent" flag, no transfer queue, no
per-file bookkeeping that a crash can desynchronise from reality. Both sides
describe what they currently hold; :func:`diff` subtracts one from the other;
the answer IS the work list. Re-run it and you get the same answer, minus
whatever landed.

That is not stylistic. A remembered flag is the defect shape this codebase has
paid for repeatedly — a mount that reported ``connected`` from memory rather
than measurement, a sequence that reported "complete: 150 frames" over a dead
link. A sync queue is the same trap with 9 GB a night riding on it: mark a file
sent, have the write actually fail, and the frame is gone from the work list
forever while the UI says the night is safe. Derivation cannot produce that
state. A destination file that is missing, truncated, or corrupt simply differs
from the source and comes back in the next diff, forever, until it matches.

TWO HAZARDS THIS MODULE EXISTS TO SURVIVE, both found by reading the capture
path rather than by being bitten:

1. **The FITS writer is not atomic.** ``imaging.fitsio.save_fits`` ends in
   ``hdu.writeto(path, overwrite=True)``, which streams into the FINAL filename.
   For the ~0.3-1 s that a 50 MB frame takes to land, a file exists at its real
   name holding partial data. Anything that triggers on "a file appeared" — an
   OS directory watcher, ``robocopy``, Syncthing, rclone on a timer — will
   eventually copy a truncated frame and record it as complete. So an entry is
   only offered for transfer once it is ``final`` (see ``settle_s``), and the
   hash is over the bytes actually on disk, so a torn copy can never match.

2. **A saved frame can still be rewritten.** When ``solve_saved_lights`` is on,
   a background worker reopens the file ``mode="update"`` and merges plate-solve
   WCS into the header in place. "Saved" therefore does not mean "final bytes",
   and — because ``write_wcs`` swallows every exception — a sync process holding
   the file at the wrong moment makes that stamp fail SILENTLY and for good.
   Hashing the content rather than trusting an event means a header rewrite
   simply shows up as a changed file and is re-sent; nothing has to predict it.

``diff`` is pure and does no I/O at all. That is what makes it testable against
real manifests instead of through a transport double — a fake transport that
reimplemented the comparison would happily agree with itself while the real one
was wrong, which is exactly how a sabotage came back MISSED once already.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

#: How long a file must have been untouched before it is considered ``final``.
#: This is the guard for hazard 1 above, and it is a MEASUREMENT rather than a
#: promise: the manifest builder sees a filesystem, not the writer, so age is
#: the only evidence available to it that ``writeto`` has returned. 15 s is
#: ~15-50x the observed write time for a 50 MB frame on this rig's disk, which
#: buys a wide margin for a slower disk or a busier machine while still putting
#: a frame in the work list long before the next one arrives (~114 s cadence
#: measured on the NGC 6946 run).
DEFAULT_SETTLE_S = 15.0

#: Read granularity for hashing. 1 MiB keeps a 50 MB frame off the heap while
#: still doing few enough syscalls that hashing is disk-bound, not loop-bound.
_HASH_CHUNK = 1024 * 1024

#: sha256 and not something faster on purpose. The verifier is whatever the
#: operator points at the destination — a shell one-liner, another AstroDeck, a
#: script on a machine we will never see — and every one of those has sha256 in
#: its standard library. A 50 MB frame hashes in ~0.12 s, which is noise beside
#: a 60 s exposure. Speed was never the binding constraint; being checkable
#: without installing anything is.
HASH_ALGO = "sha256"


@dataclass(frozen=True)
class FileFacts:
    """What the SOURCE OF TRUTH knows about one file before it is hashed.

    Deliberately not a gallery row and not a ``os.DirEntry``: this module is
    fed by the gallery scan on the rig today and by a directory walk on the
    destination side, and neither should have to imitate the other's shape.
    ``night`` in particular is passed IN rather than derived here — the gallery
    computes it from the frame's DATE-OBS through ``events.night_key`` with a
    noon rollover, precisely so a 23:50 and a 00:10 frame agree on which night
    they belong to. Re-deriving it from a filename here would split every real
    session at midnight and disagree with every other surface in the product.
    """
    relpath: str            # POSIX, relative to the capture root
    size: int
    mtime_ns: int
    night: str = ""
    kind: str = "frame"     # frame | log | plan | other


@dataclass(frozen=True)
class Entry:
    """One file, as a side reports it. ``sha256`` is the identity that matters;
    ``size``/``mtime_ns`` are metadata and cache keys, never the comparison."""
    relpath: str
    size: int
    sha256: str
    mtime_ns: int = 0
    night: str = ""
    kind: str = "frame"
    final: bool = True

    def to_json(self) -> dict:
        return {"relpath": self.relpath, "size": self.size,
                "sha256": self.sha256, "mtime_ns": self.mtime_ns,
                "night": self.night, "kind": self.kind, "final": self.final}

    @staticmethod
    def from_json(d: Mapping) -> "Entry":
        return Entry(relpath=str(d["relpath"]), size=int(d.get("size", 0)),
                     sha256=str(d.get("sha256", "")),
                     mtime_ns=int(d.get("mtime_ns", 0)),
                     night=str(d.get("night", "")),
                     kind=str(d.get("kind", "frame")),
                     final=bool(d.get("final", True)))


@dataclass
class Manifest:
    """A side's whole answer to "what do you have?"."""
    entries: dict[str, Entry] = field(default_factory=dict)
    #: Files seen but deliberately NOT offered, because they are still settling.
    #: Reported rather than hidden: "3 frames not yet final" is a true and
    #: useful thing for a client to show, whereas silently omitting them makes
    #: an in-progress night look finished.
    unsettled: list[str] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        return sum(e.size for e in self.entries.values())

    def to_json(self) -> dict:
        return {"algo": HASH_ALGO,
                "entries": [e.to_json() for e in self.entries.values()],
                "unsettled": list(self.unsettled)}

    @staticmethod
    def from_json(d: Mapping) -> "Manifest":
        got = str(d.get("algo", HASH_ALGO))
        if got != HASH_ALGO:
            # Refuse rather than compare hashes from two different algorithms,
            # which would make every file look "stale" and re-send the night.
            raise ValueError(f"manifest uses {got!r}, this build speaks {HASH_ALGO!r}")
        entries = [Entry.from_json(e) for e in d.get("entries", [])]
        return Manifest(entries={e.relpath: e for e in entries},
                        unsettled=list(d.get("unsettled", [])))


@dataclass
class Diff:
    """The work list. ``extra`` is REPORTED AND NEVER ACTED ON — deleting at the
    destination is not sync's job. The destination is where a human keeps
    processed masters, crops and experiments beside the raw data, and a mirror
    that "helpfully" removes what the source lacks would delete a night's work
    to make two directory listings agree."""
    missing: list[Entry] = field(default_factory=list)   # destination lacks it
    stale: list[Entry] = field(default_factory=list)     # present, wrong bytes
    extra: list[str] = field(default_factory=list)       # only at destination
    identical: int = 0

    @property
    def transfers(self) -> list[Entry]:
        """What actually has to move, oldest first.

        Oldest-first is not arbitrary: a stacker wants a CONTIGUOUS run of a
        filter, so completing early frames first makes a subset usable while the
        night is still going. Newest-first would leave every filter one frame
        short until the very end.
        """
        return sorted(self.missing + self.stale, key=lambda e: (e.mtime_ns, e.relpath))

    @property
    def bytes(self) -> int:
        return sum(e.size for e in self.transfers)

    def to_json(self) -> dict:
        return {"missing": [e.to_json() for e in self.missing],
                "stale": [e.to_json() for e in self.stale],
                "extra": list(self.extra), "identical": self.identical,
                "transfer_count": len(self.transfers),
                "transfer_bytes": self.bytes}


def hash_file(path: Path, *, cache: dict | None = None,
              size: int | None = None, mtime_ns: int | None = None) -> str:
    """Content hash, with an optional (size, mtime_ns)-keyed cache.

    The cache is what keeps reconciliation cheap: without it every poll would
    re-read the whole night — 9 GB — to answer a question whose answer changed
    for at most one file. With it, a frame is hashed once, when it first settles.

    The key deliberately includes BOTH size and mtime: a WCS header rewrite (see
    the module docstring) keeps the size identical on a 2880-byte-block boundary
    and only moves mtime, while a truncated copy moves size. Keying on either
    alone misses one of those, and missing it means serving a stale hash for a
    file whose bytes have changed — the one failure this whole design exists to
    make impossible.
    """
    if size is None or mtime_ns is None:
        st = path.stat()
        size = st.st_size if size is None else size
        mtime_ns = st.st_mtime_ns if mtime_ns is None else mtime_ns
    key = (str(path), int(size), int(mtime_ns))
    if cache is not None and key in cache:
        return cache[key]
    h = hashlib.new(HASH_ALGO)
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    digest = h.hexdigest()
    if cache is not None:
        cache[key] = digest
    return digest


def is_final(mtime_ns: int, now: float, settle_s: float = DEFAULT_SETTLE_S) -> bool:
    """Has this file been still long enough to be worth copying?

    A file from the FUTURE (clock skew between the rig and whatever set the
    mtime, or a filesystem with coarse timestamps) is treated as NOT final
    rather than as infinitely old. Sync waiting one extra poll costs nothing;
    copying a frame mid-write costs a frame that looks fine and is not.
    """
    age = now - (mtime_ns / 1e9)
    return age >= settle_s


def build(facts: Iterable[FileFacts], *, root: Path | None = None,
          now: float, settle_s: float = DEFAULT_SETTLE_S,
          cache: dict | None = None,
          hasher=None) -> Manifest:
    """Turn raw file facts into a manifest, hashing only what has settled.

    ``hasher`` is injected for tests and for a destination that can answer with
    a stored hash instead of re-reading; it defaults to hashing ``root/relpath``.
    An unsettled file is listed in ``unsettled`` and NOT hashed — hashing a file
    that is still growing burns I/O to produce a value guaranteed to be wrong.

    A file that vanishes between the walk and the hash (rotated, trashed,
    purged) is dropped silently: it is not an error for a night to change while
    it is being described, and a manifest that raised would make the whole poll
    fail because one frame moved to trash.
    """
    man = Manifest()
    for f in facts:
        if not is_final(f.mtime_ns, now, settle_s):
            man.unsettled.append(f.relpath)
            continue
        try:
            if hasher is not None:
                digest = hasher(f)
            else:
                if root is None:
                    raise ValueError("build() needs root= or hasher=")
                digest = hash_file(root / f.relpath, cache=cache,
                                   size=f.size, mtime_ns=f.mtime_ns)
        except (FileNotFoundError, PermissionError, OSError):
            continue
        man.entries[f.relpath] = Entry(
            relpath=f.relpath, size=f.size, sha256=digest,
            mtime_ns=f.mtime_ns, night=f.night, kind=f.kind, final=True)
    return man


def diff(source: Manifest, dest: Manifest) -> Diff:
    """What must move from ``source`` to ``dest``. Pure; no I/O; no clock.

    Comparison is on the HASH ALONE. Not size (a torn 50 MB copy can land on the
    right length), not mtime (every transport rewrites it, and the two machines'
    clocks are unrelated), not "is it there" (a half-written destination file is
    there and is wrong). Hash is the only predicate that answers the actual
    question, which is whether the destination holds these bytes.
    """
    out = Diff()
    for relpath, src in source.entries.items():
        have = dest.entries.get(relpath)
        if have is None:
            out.missing.append(src)
        elif have.sha256 != src.sha256:
            out.stale.append(src)
        else:
            out.identical += 1
    out.extra = sorted(set(dest.entries) - set(source.entries))
    return out


#: Content hashes shared by EVERY rig-side consumer, keyed (path, size,
#: mtime_ns). Module scope so it survives across requests and across the push
#: runner's passes — the whole point is that a night is read once, not once per
#: poll. A pull agent polling every 30 s for six hours and a push runner sweeping
#: every 15 minutes are otherwise two independent reasons to re-read 9 GB.
#:
#: ONE cache for both directions, not one each, because they hash the same files
#: with the same function; two caches would halve the hit rate for no benefit.
#: Unbounded is deliberate and safe: one entry is ~120 bytes and the library is
#: bounded by ``gallery.SCAN_MAX_FILES``, so the worst case is a few MB.
SHARED_HASH_CACHE: dict = {}


def rig_facts(rows: Iterable[dict]) -> list[FileFacts]:
    """Gallery rows -> ``FileFacts``. **The rig's only source of file facts.**

    Both directions go through here: ``/api/sync/manifest`` answers a pull agent
    with it, and the push runner decides what to send with it. That is the point
    — the two directions must agree about what the library contains, and the
    surest way to keep them agreeing is to give them one function rather than
    two correct-looking loops.

    Using the gallery scan rather than a directory walk is what keeps "that
    night" meaning the same thing here as it does on screen (noon rollover
    included), and what keeps the trash bin, the thumbnail cache, the session
    records and the logs out of a sync — none of them are frames, and none of
    them appear in a gallery row.
    """
    return [
        FileFacts(
            relpath=r["path"], size=int(r["bytes"]),
            # gallery reports mtime as float seconds; the hash cache wants an
            # integer key. Derived the same way on every call, so the key is
            # stable even though the precision is not the stat's.
            mtime_ns=int(float(r["mtime"]) * 1e9),
            night=r.get("night", ""),
            kind=(r.get("frame_type") or "frame").lower())
        for r in rows
    ]


def walk_facts(root: Path, *, suffixes: frozenset[str] | None = None,
               skip_top: frozenset[str] | None = None) -> list[FileFacts]:
    """Describe a destination directory. Used by the PULL AGENT, not the rig.

    The rig builds its facts from ``gallery.scan`` so that ``night`` matches
    every other surface; a destination has no gallery and no FITS headers worth
    opening, so it walks. ``night``/``kind`` are intentionally left blank here:
    they are the SOURCE's classification, and a destination inventing its own
    would give ``diff`` two files that disagree about metadata while agreeing
    about bytes. Only ``relpath`` and ``sha256`` decide anything.
    """
    facts: list[FileFacts] = []
    root = Path(root)
    if not root.is_dir():
        return facts
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        top = rel_dir.parts[0] if rel_dir.parts else ""
        if skip_top and top in skip_top:
            dirnames[:] = []
            continue
        for name in filenames:
            p = Path(dirpath) / name
            if suffixes is not None and p.suffix.lower() not in suffixes:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            facts.append(FileFacts(
                relpath=(rel_dir / name).as_posix() if rel_dir.parts else name,
                size=st.st_size, mtime_ns=st.st_mtime_ns))
    return facts
