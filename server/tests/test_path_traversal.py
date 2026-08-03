"""Path-containment contract for client-controllable paths.

The live defect this file was written for: the SPA catch-all
(`@app.get("/{path:path}")`) did `target = UI_DIST / path` with no containment
check. `{path:path}` captures separators, starlette percent-decodes before the
handler sees the value (so `..%2f` arrives as `../`, past any client-side
normalizer that would have collapsed a literal `../`), and pathlib joins `..`
literally. On the deployed rig this served, with **no credential at all**:

    GET /..%2f..%2f..%2f..%2f..%2f..%2fWindows%2fwin.ini            -> 200
    GET /..%2f..%2f..%2f..%2f..%2f..%2fUsers%2f<u>%2f.ssh%2fknown_hosts -> 200

The route is anonymous by necessity (it serves the sign-in shell), so no login
was needed, and the relay exposes it off-LAN.

Two layers are tested: `safe_subpath` (the primitive) against an attack corpus,
and the route itself against a real file planted outside the served root. The
corpus is one table so a new file-serving surface can be added to the endpoint
test without re-deriving the vectors.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore
from astrodeck.persist import safe_id_path, safe_subpath

INDEX_SENTINEL = "<!doctype html><title>astrodeck-spa-test</title>"
CANARY = "SECRET-CANARY-9f3a"

# Every string here must be REFUSED. Kept as one table so a new file-serving
# surface gets the whole corpus by adding a case, not by re-deriving vectors.
# The percent-encoded forms are not listed: starlette decodes them before the
# handler runs, so `..%2fx` and `../x` are the same input by the time either
# layer sees it. That decoding is exactly why the literal forms must be refused.
TRAVERSAL = [
    "../x",
    "..\\x",                    # the other OS's separator, refused on both
    "a/../../x",
    "a/./../../x",
    "....//x",                  # collapses to ../ under a naive strip
    "/etc/passwd",              # absolute (leading empty segment)
    "C:/Windows/win.ini",       # Windows drive prefix
    "C:\\Windows\\win.ini",
    "\\\\server\\share\\x",     # UNC
    "x\x00.png",                # NUL truncation
    "index.html.",              # Windows strips the trailing dot -> index.html
    "index.html ",              # ...and the trailing space
    "CON",                      # reserved device name, any directory
    "com1.txt",                 # reserved even with an extension
    "x:stream",                 # NTFS alternate data stream
    "",
    ".",
    "..",
]

LEGITIMATE = [
    "index.html",
    "assets/app.js",
    "favicon.ico",
    "a/b/c/deep.js",
    ".well-known/thing.txt",    # a leading dot is a normal filename
]


# --------------------------------------------------------------- the primitive

@pytest.mark.parametrize("candidate", TRAVERSAL)
def test_safe_subpath_refuses(tmp_path, candidate):
    with pytest.raises(KeyError):
        safe_subpath(tmp_path, candidate)


# The two guards share `_refuse_component`, so the SAME corpus must bind both.
# They drifted the day safe_subpath was written: it refused `:` anywhere,
# trailing dot/space and reserved device names, and safe_id_path refused none of
# the three, so `CON`, `COM3`, `NUL`, `x ` and `x.` were accepted by one and
# rejected by the other. Not exploitable through the callers of the day — all
# pass a non-empty suffix — but `x.` and `x ` already aliased two ids onto one
# file on Windows, and one `suffix=""` call would have reopened the rest.
SINGLE_COMPONENT = [c for c in TRAVERSAL if c not in ("", "/etc/passwd")]


@pytest.mark.parametrize("candidate", SINGLE_COMPONENT)
def test_safe_id_path_refuses_the_same_corpus(tmp_path, candidate):
    with pytest.raises(KeyError):
        safe_id_path(tmp_path, candidate, ".json")


@pytest.mark.parametrize("candidate", ["", "/etc/passwd"])
def test_safe_id_path_refuses_empty_and_absolute(tmp_path, candidate):
    """Split out only because these two are refused for a different reason —
    empty-string and separator, not component shape."""
    with pytest.raises(KeyError):
        safe_id_path(tmp_path, candidate, ".json")


@pytest.mark.parametrize("ident", ["plan-1", "Rig1", "a_b.c", ".hidden", "M42 flat"])
def test_safe_id_path_still_allows_real_ids(tmp_path, ident):
    """The guards must not start refusing the ids the stores actually mint."""
    resolved = safe_id_path(tmp_path, ident, ".json")
    assert resolved.parent == tmp_path.resolve()


@pytest.mark.parametrize("candidate", LEGITIMATE)
def test_safe_subpath_allows_contained_paths(tmp_path, candidate):
    resolved = safe_subpath(tmp_path, candidate)
    assert resolved.is_relative_to(tmp_path.resolve())


def test_safe_subpath_refuses_symlink_escape(tmp_path):
    """The one vector no string check can see: a legal-looking name inside the
    root that is a link out of it. Caught by the resolve() backstop."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text(CANARY, encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")
    with pytest.raises(KeyError):
        safe_subpath(root, "link/secret.txt")


# ------------------------------------------------------------------- the route

def _spa_client(tmp_path, monkeypatch):
    """Isolated app with a synthetic ui/dist so the catch-all is mounted, and a
    canary file planted one level ABOVE it (standing in for any file on the box
    the server process can read)."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)

    dist = tmp_path / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_SENTINEL, encoding="utf-8")
    (dist / "assets" / "app.js").write_text("// built asset", encoding="utf-8")
    (tmp_path / "web" / "secret.txt").write_text(CANARY, encoding="utf-8")
    monkeypatch.setattr(app_module, "UI_DIST", dist)
    return app_module.create_app()


@pytest.mark.parametrize("attack", [
    "/../secret.txt",
    "/..%2fsecret.txt",
    "/%2e%2e%2fsecret.txt",
    "/assets/../../secret.txt",
    "/....//secret.txt",
])
def test_spa_catch_all_never_serves_outside_ui_dist(tmp_path, monkeypatch, attack):
    """No credential is sent — this route is anonymous, which is the whole
    problem. A refused path must fall through to the SPA shell, not 403: an
    unknown path IS a client-side route, and a distinct refusal would confirm
    which targets exist."""
    app = _spa_client(tmp_path, monkeypatch)
    with TestClient(app) as c:
        r = c.get(attack, follow_redirects=False)
        assert CANARY not in r.text, f"{attack} leaked a file outside UI_DIST"
        if r.status_code == 200:
            assert r.text == INDEX_SENTINEL


def test_spa_catch_all_still_serves_real_assets(tmp_path, monkeypatch):
    """The fix must not break the thing the route exists for."""
    app = _spa_client(tmp_path, monkeypatch)
    with TestClient(app) as c:
        assert c.get("/assets/app.js").text == "// built asset"
        assert c.get("/").text == INDEX_SENTINEL
        # An unknown client-side route still gets the shell (SPA deep link).
        assert c.get("/capture").text == INDEX_SENTINEL
