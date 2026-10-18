# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1071 round 2: ``captures/guide`` is a reserved directory, like
``captures/logs``.

The flight recorder and the pulse probe write JSON under
``captures/guide/<night>/``. That is diagnostic history, never frames, so:

* the gallery walk does not descend into it (``gallery.SKIP_TOP_DIRS``), so a
  stray frame-suffixed file there is not listed or counted, and a target
  folder that happens to be called ``guide`` deeper in the tree still is;
* a factory reset keeps it even when "delete captures" is ticked
  (``factory_reset.PRESERVED_CAPTURE_ENTRIES``), like the night logs.

Each test names the mutant of the production code it was shown RED under, from
a byte backup restored afterwards (sha256 checked).
"""
from __future__ import annotations

from astrodeck import gallery
from astrodeck.factory_reset import PRESERVED_CAPTURE_ENTRIES, factory_reset

from test_factory_reset import _dirty, env as reset_env  # noqa: F401
from test_gallery import _local, _write, cap  # noqa: F401


def test_the_gallery_does_not_walk_the_guide_directory(cap):
    """A frame-suffixed file under ``guide/`` is not a frame in the library.

    MUTANT R-a (``"guide"`` removed from ``SKIP_TOP_DIRS``): RED, two rows."""
    _write(cap, "M42/real.fits", ts=_local(2026, 6, 15, 22, 0))
    _write(cap, "guide/2026-06-15/stray.fits", ts=_local(2026, 6, 15, 22, 0))
    rows, _ = gallery.scan()
    assert [r["path"] for r in rows] == ["M42/real.fits"]
    assert "guide" in gallery.SKIP_TOP_DIRS


def test_a_target_folder_named_guide_deeper_in_the_tree_is_still_listed(cap):
    """Pruned by first component only: someone imaging into ``M42/guide`` does
    not lose their frames.

    MUTANT R-b (the prune made by bare name at any depth): RED."""
    _write(cap, "M42/guide/Light_0001.fits", ts=_local(2026, 6, 15, 22, 0))
    rows, _ = gallery.scan()
    assert [r["path"] for r in rows] == ["M42/guide/Light_0001.fits"]


def test_a_factory_reset_keeps_the_guide_history_even_when_captures_go(
        reset_env):
    """"Delete captures" clears the tester's frames and leaves the preserved
    entries, now including ``guide``.

    MUTANT R-c (``"guide"`` removed from ``PRESERVED_CAPTURE_ENTRIES``): RED,
    the night's flight-recorder files are gone."""
    _c, store, cfg_dir, cap_dir = reset_env
    _dirty(store, cfg_dir, cap_dir)
    night = cap_dir / "guide" / "2026-06-15"
    night.mkdir(parents=True)
    (night / "223000-pulse-probe.json").write_text("{}", encoding="utf-8")

    factory_reset(store, cfg_dir, cap_dir, delete_captures=True)

    assert "guide" in PRESERVED_CAPTURE_ENTRIES
    assert (night / "223000-pulse-probe.json").read_text(encoding="utf-8") == "{}"
    assert not (cap_dir / "M31").exists()           # the frames did go
