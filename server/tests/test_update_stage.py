"""Safe extraction + version-dir flattening for staging a release."""
import io
import tarfile

import pytest

from astrodeck.update import stage as S


def _bundle(tmp_path, version="0.2.0"):
    top = f"astrodeck-{version}"
    tb = tmp_path / f"astrodeck-{version}.tar.gz"
    with tarfile.open(tb, "w:gz") as tf:
        def add(name, content):
            data = content.encode()
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        add("manifest.json", '{"version":"0.2.0"}')
        add("server/astrodeck/__init__.py", '__version__ = "0.2.0"')
        add("ui/dist/index.html", "<html></html>")
    return tb


def test_stage_flattens_top_dir(tmp_path):
    tb = _bundle(tmp_path)
    releases = tmp_path / "releases"
    out = S.stage_release(tb, releases, "0.2.0")
    assert out == releases / "0.2.0"
    assert (out / "manifest.json").exists()
    assert (out / "server" / "astrodeck" / "__init__.py").exists()
    assert (out / "ui" / "dist" / "index.html").exists()


def test_stage_replaces_existing(tmp_path):
    tb = _bundle(tmp_path)
    releases = tmp_path / "releases"
    (releases / "0.2.0").mkdir(parents=True)
    (releases / "0.2.0" / "stale.txt").write_text("old")
    out = S.stage_release(tb, releases, "0.2.0")
    assert not (out / "stale.txt").exists()  # prior staging removed


def test_safe_extract_rejects_path_traversal(tmp_path):
    tb = tmp_path / "evil.tar.gz"
    with tarfile.open(tb, "w:gz") as tf:
        data = b"pwn"
        info = tarfile.TarInfo("../escape.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError):
        S.safe_extract(tb, tmp_path / "dest")
    assert not (tmp_path / "escape.txt").exists()


def test_safe_extract_rejects_symlink(tmp_path):
    tb = tmp_path / "link.tar.gz"
    with tarfile.open(tb, "w:gz") as tf:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    with pytest.raises(ValueError):
        S.safe_extract(tb, tmp_path / "dest2")


def test_reconcile_deps_skips_without_pyproject(tmp_path):
    rel = tmp_path / "rel"
    (rel / "server").mkdir(parents=True)
    ok, reason = S.reconcile_deps(rel, "python")
    assert ok is True and "skipped" in reason
