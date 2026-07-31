"""Phase-1 self-update endpoints: open /healthz and /api/version snapshot."""
import pytest
from fastapi.testclient import TestClient

from astrodeck import __version__
from astrodeck.api import create_app
from astrodeck.update.state import update_state


@pytest.fixture(autouse=True)
def _clean_update_state():
    # /api/version reflects the shared update_state singleton; reset so a prior
    # test file's "update available" can't leak into these assertions.
    update_state.current = __version__
    update_state.set_available(None, "")
    update_state.set_phase("idle")
    update_state.set_result(None)
    yield


def test_healthz_is_open_and_reports_version():
    with TestClient(create_app()) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "version": __version__}


def test_api_version_returns_update_snapshot():
    with TestClient(create_app()) as c:
        r = c.get("/api/version")
        assert r.status_code == 200
        body = r.json()
        assert body["current"] == __version__
        assert body["update_available"] is False
        # snapshot shape the UI/poller rely on
        for k in ("latest", "channel", "phase", "progress", "last_check_ts",
                  "last_result"):
            assert k in body


def test_the_reported_version_matches_the_packaged_one():
    """Two files carry the version and only one of them is what the app SAYS.

    On 2026-07-31 a release bumped pyproject.toml but not ``__version__``, so
    the deployed 0.2.26 introduced itself as 0.2.25 in the header and in
    /healthz -- which is the exact signal a deploy check reads to decide whether
    the new build is live. A version string that lies makes every later
    verification unreliable.
    """
    import pathlib
    import re

    pyproject = pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert m, f"no version in {pyproject}"
    assert m.group(1) == __version__, (
        f"pyproject.toml says {m.group(1)} but astrodeck.__version__ is "
        f"{__version__} — /healthz and the UI header report the latter")


def test_create_app_boot_rbac_assertion_still_passes():
    # create_app() runs assert_route_capabilities(); a mis-wired new route would
    # raise here. The two new GETs carry no cap (read-only) and must not trip it.
    assert create_app() is not None
