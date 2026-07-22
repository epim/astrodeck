"""Private-repo self-update: github asset API urls + github_token redaction
(2026-07-21). A PRIVATE releases source needs a token; the browser_download_url
can't be token-authed, so the service downloads via each asset's API url +
``Accept: application/octet-stream``. The token is a secret -> scrubbed everywhere."""
from astrodeck.config import AppConfig, UpdateConfig, redacted
from astrodeck.update import github


def _releases(assets):
    return [{"tag_name": "v0.3.0", "draft": False, "prerelease": False,
             "body": "notes", "assets": assets}]


def test_pick_release_captures_asset_api_urls():
    assets = [
        {"name": "astrodeck-0.3.0.tar.gz",
         "browser_download_url": "https://gh/dl/x.tar.gz",
         "url": "https://api.github.com/repos/o/r/releases/assets/1"},
        {"name": "astrodeck-0.3.0.tar.gz.sha256",
         "browser_download_url": "https://gh/dl/x.sha256",
         "url": "https://api.github.com/repos/o/r/releases/assets/2"},
        {"name": "astrodeck-0.3.0.tar.gz.sig",
         "browser_download_url": "https://gh/dl/x.sig",
         "url": "https://api.github.com/repos/o/r/releases/assets/3"},
    ]
    rel = github.pick_release(_releases(assets), channel="stable", current="0.2.0")
    assert rel is not None
    # browser urls still populated (public-repo path unchanged)
    assert rel.artifact_url.endswith("x.tar.gz")
    assert rel.sha256_url.endswith("x.sha256")
    assert rel.sig_url.endswith("x.sig")
    # NEW: asset API urls captured (private-repo download path)
    assert rel.artifact_api_url.endswith("/assets/1")
    assert rel.sha256_api_url.endswith("/assets/2")
    assert rel.sig_api_url.endswith("/assets/3")


def test_pick_release_api_urls_none_when_absent():
    # an old release JSON with no asset "url" field must not crash
    assets = [{"name": "astrodeck-0.3.0.tar.gz",
               "browser_download_url": "https://gh/dl/x.tar.gz"}]
    rel = github.pick_release(_releases(assets), channel="stable", current="0.2.0")
    assert rel is not None and rel.artifact_api_url is None


def test_redacted_scrubs_github_token():
    cfg = AppConfig()
    cfg.update = UpdateConfig(github_token="ghp_supersecret", repo="o/r")
    r = redacted(cfg)
    assert r["update"]["github_token"] == ""
    assert r["update"]["github_token_configured"] is True
    # repo/channel/signing_pubkey are public -> pass through
    assert r["update"]["repo"] == "o/r"
    # the token never appears anywhere in the serialized redacted config
    import json
    assert "ghp_supersecret" not in json.dumps(r)


def test_redacted_github_token_not_configured():
    r = redacted(AppConfig())
    assert r["update"]["github_token_configured"] is False
