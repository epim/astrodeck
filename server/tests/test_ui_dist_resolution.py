"""Which built SPA the server actually serves.

`webui` is a build artifact (packaging/build_binary.py copies ui/dist into it)
and nothing refreshes it outside that script. When a release is staged from a
tree that once produced a binary, BOTH exist and the stale one used to win
unconditionally — the server came up, served a UI, and served the wrong one.
Silent, and indistinguishable from "my CSS change didn't work": it cost four
rounds of a UI fix being reported as ineffective on 2026-07-30.
"""
import os

import pytest

from astrodeck.api import app as app_module


def _bundle(path, marker: str):
    """A minimal built SPA at `path`, tagged so tests can tell them apart."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "index.html").write_text(f"<html><!--{marker}--></html>",
                                     encoding="utf-8")
    return path


@pytest.fixture()
def layout(tmp_path, monkeypatch):
    """A fake install: <root>/server/astrodeck/api/app.py beside <root>/ui/dist."""
    pkg = tmp_path / "server" / "astrodeck"
    (pkg / "api").mkdir(parents=True)
    fake_file = pkg / "api" / "app.py"
    fake_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_module, "__file__", str(fake_file))
    monkeypatch.delenv("ASTRODECK_UI_DIR", raising=False)
    return {"bundled": pkg / "webui", "repo": tmp_path / "ui" / "dist"}


def _mtime(path, when: float) -> None:
    os.utime(path / "index.html", (when, when))


def test_env_override_wins_over_everything(layout, tmp_path, monkeypatch):
    """The container sets this; it must not be second-guessed."""
    chosen = _bundle(tmp_path / "explicit", "env")
    _bundle(layout["bundled"], "bundled")
    monkeypatch.setenv("ASTRODECK_UI_DIR", str(chosen))
    assert app_module._resolve_ui_dist() == chosen.resolve()


def test_bundled_alone_is_used(layout):
    """A wheel or PyInstaller bundle carries the SPA inside the package and has
    no ui/ sibling at all."""
    b = _bundle(layout["bundled"], "bundled")
    assert app_module._resolve_ui_dist() == b


def test_repo_layout_alone_is_used(layout):
    """A git checkout: ui/dist beside server/, nothing bundled."""
    r = _bundle(layout["repo"], "repo")
    assert app_module._resolve_ui_dist() == r


def test_a_fresher_repo_build_beats_a_stale_bundled_one(layout):
    """THE regression. A leftover `webui` from an old binary build must not
    shadow the ui/dist the author just rebuilt."""
    b = _bundle(layout["bundled"], "stale")
    r = _bundle(layout["repo"], "fresh")
    _mtime(b, 1_000_000)
    _mtime(r, 2_000_000)
    assert app_module._resolve_ui_dist() == r


def test_a_fresher_bundled_build_still_wins(layout):
    """Symmetric: a release that deliberately refreshed `webui` keeps using it
    even when an older ui/dist is lying around."""
    b = _bundle(layout["bundled"], "fresh")
    r = _bundle(layout["repo"], "stale")
    _mtime(b, 2_000_000)
    _mtime(r, 1_000_000)
    assert app_module._resolve_ui_dist() == b


def test_equal_timestamps_keep_the_bundled_copy(layout):
    """A tie is not evidence of staleness — prefer the packaged copy, which is
    the one that travels with an install."""
    b = _bundle(layout["bundled"], "b")
    r = _bundle(layout["repo"], "r")
    _mtime(b, 1_500_000)
    _mtime(r, 1_500_000)
    assert app_module._resolve_ui_dist() == b


def test_a_directory_without_index_html_does_not_count(layout):
    """An empty or half-copied `webui` must not win — index.html is the proof
    that a build actually landed there."""
    layout["bundled"].mkdir(parents=True)
    r = _bundle(layout["repo"], "repo")
    assert app_module._resolve_ui_dist() == r
