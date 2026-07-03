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


def test_reconcile_deps_passes_pip_timeout(tmp_path, monkeypatch):
    # A hung PyPI must never wedge the caller forever: pip runs with a timeout.
    rel = tmp_path / "rel"
    (rel / "server").mkdir(parents=True)
    (rel / "server" / "pyproject.toml").write_text("[project]\nname='x'\n")
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)

        class R:
            pass
        return R()

    monkeypatch.setattr(S.subprocess, "run", fake_run)
    ok, _ = S.reconcile_deps(rel, "python", timeout_s=42.0)
    assert ok is True and seen.get("timeout") == 42.0


def test_reconcile_deps_surfaces_timeout_as_nonfatal(tmp_path, monkeypatch):
    rel = tmp_path / "rel"
    (rel / "server").mkdir(parents=True)
    (rel / "server" / "pyproject.toml").write_text("[project]\nname='x'\n")

    def boom(cmd, **kw):
        raise S.subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(S.subprocess, "run", boom)
    ok, reason = S.reconcile_deps(rel, "python", timeout_s=1.0)
    assert ok is False and "timed out" in reason


def test_snapshot_env_filters_to_restorable_pins(monkeypatch):
    # pip freeze can list the local package, editable installs, and URL/VCS refs;
    # none are installable from an index, so the rollback snapshot keeps only clean
    # name==version pins (and drops astrodeck itself).
    freeze = (
        "httpx==0.27.0\n"
        "pydantic==2.7.1\n"
        "astrodeck==0.2.0\n"            # local package: never published
        "-e git+https://x/y.git#egg=z\n"  # editable
        "somepkg @ file:///tmp/somepkg\n"  # local path
        "# a comment\n"
    )

    class R:
        stdout = freeze

    monkeypatch.setattr(S.subprocess, "run", lambda *a, **k: R())
    out = S.snapshot_env("python")
    lines = out.splitlines()
    assert "httpx==0.27.0" in lines
    assert "pydantic==2.7.1" in lines
    assert not any("astrodeck" in ln for ln in lines)
    assert not any("git+" in ln or "file://" in ln or ln.startswith("-e") for ln in lines)


def test_snapshot_env_returns_none_on_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("pip not found")

    monkeypatch.setattr(S.subprocess, "run", boom)
    assert S.snapshot_env("python") is None
