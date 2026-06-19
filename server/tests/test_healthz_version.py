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


def test_create_app_boot_rbac_assertion_still_passes():
    # create_app() runs assert_route_capabilities(); a mis-wired new route would
    # raise here. The two new GETs carry no cap (read-only) and must not trip it.
    assert create_app() is not None
