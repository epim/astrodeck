"""POST /api/flows/wizard — the route that was missing under the sheet.

`flows/wizard.py` shipped complete: `generate()`, `generate_record()` and
`flow_name()`, with `tests/test_flows_wizard.py` enumerating every kind against
every subset of the six automation chips and requiring the doctor to return
nothing above `note`. No route reached any of it, so the sheet's GENERATE FLOW
button was an honest-disabled control saying so, and the only way out of the
wizard was START BLANK.

The alternative was re-implementing `genWizard()` in TypeScript, which would
make three transcriptions of one rule set: the prototype, wizard.py, and the
client. wizard.py's own header names that drift as the thing the project keeps
re-finding. So the rules stay on the server and the client asks.

What this file pins is the ROUTE, not the generator:

  * the three answers reach the generator intact, including a comma list
  * the record is PERSISTED, so the sheet can open it immediately
  * it lands in My flows and is editable, never a read-only fixture
  * the generated graph passes the doctor, through the route as well as in
    the unit tests -- an endpoint that returns a warned graph would teach a
    first-time user that the doctor is decoration
  * an unknown kind or chip is refused rather than silently generating the
    default night
"""
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.flows import wizard
from astrodeck.flows.doctor import check as flow_doctor
from astrodeck.flows.models import MY_FLOWS_FOLDER
from astrodeck.flows.store import flow_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(flow_store, "_dir", tmp_path, raising=False)
    with TestClient(app_module.create_app()) as c:
        yield c


def _post(c, **body):
    return c.post("/api/flows/wizard", json=body)


def test_the_route_exists_and_generates(client):
    r = _post(client, kind=wizard.KIND_DEEP_SKY, options=["Guiding"], target="M16")
    assert r.status_code == 200, f"the generator is still unreachable: {r.text[:300]}"
    rec = r.json()
    assert rec.get("id"), "a generated flow with no id cannot be opened"
    assert rec["graph"]["nodes"], "generated an empty canvas"


def test_it_persists_so_the_sheet_can_open_it(client):
    rec = _post(client, kind=wizard.KIND_DEEP_SKY, target="M16").json()
    got = client.get(f"/api/flows/{rec['id']}")
    assert got.status_code == 200, "generated but not saved -- the sheet would 404"
    assert got.json()["graph"]["nodes"], "saved an empty graph"


def test_it_lands_in_my_flows_and_stays_editable(client):
    rec = _post(client, kind=wizard.KIND_POOL,
                target="M16, M17, M8").json()
    assert rec["folder"] == MY_FLOWS_FOLDER
    assert rec["readonly"] is False, (
        "a generated flow is the operator's from the moment it appears; a "
        "read-only one cannot be edited and the wizard would be a dead end")


@pytest.mark.parametrize("kind", wizard.KINDS)
def test_every_kind_passes_the_doctor_through_the_route(client, kind):
    """The stated hard requirement, checked on what the ROUTE returns.

    The unit tests check `generate()`. This checks the graph that actually
    reaches the client after serialisation and persistence, which is the one a
    first-time user sees light up -- or not.
    """
    r = _post(client, kind=kind, options=list(wizard.AUTOMATION_OPTIONS),
              target="M16, M17")
    assert r.status_code == 200, r.text[:300]
    from astrodeck.flows.models import FlowGraph
    graph = FlowGraph.model_validate(r.json()["graph"])
    issues = flow_doctor(graph)
    bad = [i for i in issues if getattr(i, "level", "note") != "note"]
    assert not bad, f"{kind} generated a graph the doctor faults: {bad}"


def test_the_answers_actually_reach_the_generator(client):
    """A route that ignored its body would still pass every test above."""
    lone = _post(client, kind=wizard.KIND_DEEP_SKY, options=[], target="M16").json()
    everything = _post(client, kind=wizard.KIND_DEEP_SKY,
                       options=list(wizard.AUTOMATION_OPTIONS),
                       target="M16").json()
    assert len(everything["graph"]["nodes"]) > len(lone["graph"]["nodes"]), (
        "six automation chips produced no more nodes than none of them -- the "
        "body is being dropped")

    named = _post(client, kind=wizard.KIND_DEEP_SKY, target="NGC 7129").json()
    assert "7129" in named["name"], (
        f"the target never reached flow_name(): {named['name']!r}")


def test_an_unknown_kind_is_refused(client):
    r = _post(client, kind="Planetary lucky imaging", target="Jupiter")
    assert r.status_code == 422, (
        "an unrecognised kind must not quietly generate the default deep-sky "
        f"night; got {r.status_code}")


def test_an_unknown_automation_chip_is_refused(client):
    r = _post(client, kind=wizard.KIND_DEEP_SKY,
              options=["Guiding", "Autoguide the dome cat"], target="M16")
    assert r.status_code == 422, (
        f"an unrecognised chip must be refused, not dropped; got {r.status_code}")
