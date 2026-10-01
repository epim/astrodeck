"""No test in this suite runs the relay client's system link probes (#521, H4;
filed as #571).

Since H4 the relay client checks the rig's own network after every drop: it
reads the route table (``route print`` on Windows, ``/proc/net/route`` on a
board), pings the default gateway, and looks the relay's host up. A client
built directly is handed no probes and runs no check. The app's lifespan is
the other way in: ``run_relay_client`` hands the client
``SYSTEM_LINK_PROBES``, read at call time, so a test that enables remote and
enters ``TestClient(app)`` starts the production client, whose dial fails
and whose drop runs the real probes. Two cases in ``test_remote_status.py``
did exactly that on every run (H4-RELAY's verifier instrumented the probes
and recorded one call from each), from a module whose docstring says "no
network".

The conftest's autouse ``_no_test_probes_the_real_network`` swaps the name
for probes that answer "could not tell". This case walks the same path those
two did, with every OS step the system probes take spied and made inert
here as well (``_default_gateway`` answers "cannot read the route table", so
no ping follows, and a lookup of this case's relay host is refused before it
leaves the process), so even a mutant that drops the fixture touches no
network: it only shows up in the two lists.

MUTATION RECORD, each run in a private copy of ``server/`` (session
scratchpad ``H4-INTEG-mut``), from a byte backup restored with its sha256
checked, never in the shared tree:

* "fixture removed" (conftest.py: the ``@pytest.fixture(autouse=True)`` line
  above ``_no_test_probes_the_real_network`` deleted, so nothing replaces the
  name). Observed:

      AssertionError: the lifespan client ran the system link probes: the
      route table was read 1 time(s) and the relay host looked up 1 time(s)
      (test_remote_status.py's lifespan cases reach the same code)
      assert ([1.5], [('re...alid', None)]) == ([], [])

* "the inert gateway calls through" (conftest.py: the fixture's
  ``gateway=_inert_gateway_probe`` made
  ``gateway=relay_client._system_gateway_probe``). Observed:

      AssertionError: the lifespan client ran the system link probes: the
      route table was read 1 time(s) and the relay host looked up 0 time(s)
      (test_remote_status.py's lifespan cases reach the same code)
      assert ([1.5], []) == ([], [])

* CONTROL "the lifespan client unprobed" (relay_client.py
  ``run_relay_client``: ``link_probes=SYSTEM_LINK_PROBES`` made
  ``link_probes=None``): the premise goes red, so the case cannot pass on a
  path that never reaches the probes. Observed:

      AssertionError: premise: the lifespan's relay client ran no link check
      within 10 s of its first drop, so this case cannot see which probes it
      runs (dials: 5)
      assert False
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.hub as hub_mod
import astrodeck.remote.relay_client as rc
from astrodeck.auth import reset_active_provider
from astrodeck.config import ConfigStore, RemoteConfig

#: A host under ``.invalid`` (RFC 2606), refused by the spy below before any
#: resolver is asked, whatever the probes are.
RELAY_HOST = "relay.h4-guard.invalid"


def test_the_lifespan_client_runs_no_system_probe(tmp_path, monkeypatch):
    route_reads: list[float] = []
    lookups: list[tuple] = []

    def default_gateway_spy(timeout_s):
        route_reads.append(timeout_s)
        return None      # "cannot read the route table": no ping follows

    monkeypatch.setattr(rc, "_default_gateway", default_gateway_spy)
    real_getaddrinfo = rc.socket.getaddrinfo

    def getaddrinfo_spy(host, port, *args, **kwargs):
        if host == RELAY_HOST:
            lookups.append((host, port))
            raise rc.socket.gaierror(11001, "not looked up in the test")
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(rc.socket, "getaddrinfo", getaddrinfo_spy)

    # The dial fails at once and dials nothing, as relay.example.test's does
    # in test_remote_status.py, only without asking a resolver first.
    dials: list[str] = []

    async def refuse(self, url):
        dials.append(url)
        raise ConnectionError("refused in the test; nothing was dialed")

    monkeypatch.setattr(rc.RelayClient, "_default_connect", refuse)

    # test_remote_status.py's harness: a throwaway store, remote enabled.
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    reset_active_provider()
    store.set_remote(RemoteConfig(
        enabled=True, relay_url=f"wss://{RELAY_HOST}/scope",
        device_token="t" * 43, home_id="h4-guard"))
    monkeypatch.setattr(rc, "_current_client", None)

    checked = False
    with TestClient(app_module.create_app()):
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            check = getattr(rc.current_client(), "_link_check", None)
            if check is not None and check.done():
                checked = True
                break
            time.sleep(0.02)

    assert checked, (
        f"premise: the lifespan's relay client ran no link check within 10 s "
        f"of its first drop, so this case cannot see which probes it runs "
        f"(dials: {len(dials)})")
    assert (route_reads, lookups) == ([], []), (
        f"the lifespan client ran the system link probes: the route table was "
        f"read {len(route_reads)} time(s) and the relay host looked up "
        f"{len(lookups)} time(s) (test_remote_status.py's lifespan cases "
        f"reach the same code)")
