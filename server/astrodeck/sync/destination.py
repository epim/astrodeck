"""Where frames get pushed TO, and the one rule every destination obeys.

A destination knows two things and nothing else: what it currently holds, and
how to accept bytes. Every decision about WHAT to send lives in
:mod:`.manifest`, because the moment a transport owns a copy of the
reconciliation rules the directions start to drift and only one of them gets the
next fix. Phase 1's pull agent already works that way; this is the same contract
pointed the other direction.

THE ONE RULE: **a destination file appears at its final name only when it is
complete.** Write to a sibling ``.part`` and ``os.replace`` it into place.

That is not defensiveness, it is the fix for the hazard the source has and
cannot fix — ``save_fits`` ends in ``hdu.writeto(path, overwrite=True)``, which
streams into the real filename, so for the second or so a 50 MB frame takes to
land there IS a file at its final name holding partial data. The source guards
that with ``settle_s`` before offering an entry; the destination guards its own
side with ``os.replace``, which is atomic within a filesystem. A reader on the
far end (PixInsight watching a folder, another sync tool) therefore never sees a
half-written frame, no matter what either end crashes in the middle of.

WHAT A DESTINATION MUST NEVER DO: delete. ``Diff.extra`` is reported and never
acted on, here as in the pull direction. A rig is not the authority on what a
processing box has chosen to keep, and a sync that can delete is one bad
manifest away from removing a night's work.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Protocol

from . import manifest as _manifest
from .manifest import Manifest


class Destination(Protocol):
    """What a push target has to be able to do. Two methods, on purpose."""

    #: Human-readable, for logs and the status payload. Never a secret — a
    #: destination URL can carry credentials, so implementations expose a
    #: redacted label rather than their configuration.
    label: str

    def manifest(self) -> Manifest:
        """What this destination currently holds, hashed the same way the
        source hashes. Same function, not an equivalent one — see
        ``manifest.hash_file``."""
        ...

    def put(self, relpath: str, source: Path) -> int:
        """Copy ``source`` to ``relpath``, appearing atomically. Returns bytes
        written. Raises on failure; the caller counts and continues."""
        ...


class LocalDirDestination:
    """A directory this machine can write to — which on Windows covers the case
    that actually matters: a mapped drive or a UNC share (``\\\\nas\\astro``)
    exported by the box PixInsight runs on.

    SFTP and object-store drivers are the obvious next two, and they slot in
    behind the same Protocol without any of the logic above moving, because none
    of the logic above lives here.
    """

    def __init__(self, root: Path | str, *, label: str | None = None) -> None:
        self.root = Path(root)
        self.label = label or str(self.root)

    def manifest(self) -> Manifest:
        """Hash what is already there.

        Cost is real — hashing a 9 GB night is minutes of disk — and it is paid
        on purpose. The alternative is trusting size+mtime, which is exactly the
        remembered state this design refuses: a truncated file has a plausible
        size and a perfectly good mtime.
        """
        if not self.root.exists():
            return Manifest(entries={})
        # settle_s=0: nothing writes into this tree except us, and we write
        # via os.replace, so there is no partial-file window to wait out.
        return _manifest.build(_manifest.walk_facts(self.root),
                               root=self.root, now=time.time(), settle_s=0.0)

    def put(self, relpath: str, source: Path) -> int:
        target = self.root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        # The .part sits BESIDE the target rather than in a temp dir: os.replace
        # is only atomic within a filesystem, and a temp dir on another volume
        # would silently degrade it to a copy — reintroducing exactly the
        # partial-file window this exists to close.
        part = target.with_name(target.name + ".part")
        try:
            shutil.copyfile(source, part)
            os.replace(part, target)
        except BaseException:
            # Leave nothing half-named behind. The next diff re-offers the file
            # because the destination still does not have it.
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return target.stat().st_size

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"LocalDirDestination({self.label!r})"
