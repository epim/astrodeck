"""GitHub release selection logic (pick_release)."""
from astrodeck.update import github as G


def _assets(v):
    base = f"https://example/dl/astrodeck-{v}.tar.gz"
    return [
        {"name": f"astrodeck-{v}.tar.gz", "browser_download_url": base},
        {"name": f"astrodeck-{v}.tar.gz.sha256", "browser_download_url": base + ".sha256"},
        {"name": f"astrodeck-{v}.tar.gz.sig", "browser_download_url": base + ".sig"},
    ]


def _rel(tag, *, prerelease=False, draft=False, body="notes", assets=None):
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft,
            "body": body, "assets": assets if assets is not None else _assets(tag.lstrip("v"))}


def test_picks_latest_stable_with_sidecars():
    rels = [_rel("v0.1.0"), _rel("v0.2.0"), _rel("v0.3.0-rc1", prerelease=True)]
    info = G.pick_release(rels, channel="stable", current="0.1.0")
    assert info.version == "0.2.0"
    assert info.artifact_url.endswith("astrodeck-0.2.0.tar.gz")
    assert info.sha256_url.endswith(".sha256")
    assert info.sig_url.endswith(".sig")
    assert info.notes_md == "notes"


def test_prerelease_channel_includes_rc():
    rels = [_rel("v0.2.0"), _rel("v0.3.0-rc1", prerelease=True)]
    info = G.pick_release(rels, channel="prerelease", current="0.2.0")
    assert info.version == "0.3.0-rc1" and info.prerelease is True


def test_none_when_already_current():
    rels = [_rel("v0.2.0")]
    assert G.pick_release(rels, channel="stable", current="0.2.0") is None


def test_release_without_tarball_is_skipped():
    rels = [_rel("v0.2.0", assets=[{"name": "notes.txt", "browser_download_url": "u"}])]
    assert G.pick_release(rels, channel="stable", current="0.1.0") is None


def test_drafts_ignored():
    rels = [_rel("v0.3.0", draft=True), _rel("v0.2.0")]
    info = G.pick_release(rels, channel="stable", current="0.1.0")
    assert info.version == "0.2.0"


def test_stable_channel_excludes_prerelease_even_if_newest():
    rels = [_rel("v0.2.0"), _rel("v0.3.0-rc1", prerelease=True)]
    info = G.pick_release(rels, channel="stable", current="0.1.0")
    assert info.version == "0.2.0"
