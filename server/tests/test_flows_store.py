"""Flow persistence + the five shipped Examples.

The Definition of Done requires the five examples to "load, validate, and run
end-to-end on the simulator". Loading and validating is this file; running is
milestone 1c. So these tests treat the fixtures as an ACCEPTANCE CORPUS rather
than as sample data — if a transcription slipped, the graph either fails to
validate or the doctor says something about it, and both are caught here.
"""
from __future__ import annotations

import pytest

import astrodeck.config as config_mod
from astrodeck.flows import check
from astrodeck.flows.examples import examples
from astrodeck.flows.models import EXAMPLES_FOLDER, FlowGraph, FlowNode, FlowRecord
from astrodeck.flows.store import FlowStore, FlowLibraryFull, ReadOnlyFlow


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    return FlowStore(tmp_path / "flows")


class TestTheShippedExamples:
    def test_there_are_five(self):
        assert len(examples()) == 5

    @pytest.mark.parametrize("ex", examples(), ids=lambda e: e.id)
    def test_each_one_is_structurally_valid(self, ex):
        """A transcription slip in a wire shows up here first."""
        assert ex.graph.validation_errors() == [], ex.name

    @pytest.mark.parametrize("ex", examples(), ids=lambda e: e.id)
    def test_each_one_is_readonly_and_lives_in_examples(self, ex):
        assert ex.readonly is True
        assert ex.folder == EXAMPLES_FOLDER

    def test_the_carefully_built_examples_have_a_quiet_doctor(self):
        """M31 / M16 / pool / NB are graphs a careful person built, so the
        doctor should find nothing WARN-level to say. If one of them starts
        complaining, either the transcription drifted or a rule is wrong — and
        both are worth being told about loudly."""
        for ex in examples():
            if ex.id == "example-eaa":
                continue
            warns = [i.text for i in check(ex.graph) if i.level in ("warn", "danger")]
            assert not warns, f"{ex.name}: {warns}"

    def test_the_eaa_example_has_exactly_one_open_check(self):
        """EAA starts at TARGET with no DUSK WINDOW above it, so its 'arm' input
        is unwired — IN THE PROTOTYPE TOO. That is not a transcription slip; it
        is a deliberately minimal graph, and the header chip showing "1 OPEN
        CHECK" on it is the honest render.

        Pinned as EXACTLY one, because the value of this example is that it
        exercises the open-checks path: if a future rule change made EAA noisy,
        the first place anyone would see it is the library's own card.

        The two rules it must NOT trip: 4 s subs are far below rule 2's 120 s
        guiding threshold and rule 3's 60 s focus threshold, so a short-sub
        visual run is never nagged about a guider it does not want."""
        eaa = next(e for e in examples() if e.id == "example-eaa")
        found = [i.text for i in check(eaa.graph) if i.level in ("warn", "danger")]
        assert found == ["▸ TARGET — 'arm' input unwired"], found
        assert not any("stars will trail" in t for t in found)
        assert not any("focus drift" in t for t in found)

    def test_m16_carries_every_wire_the_cloud_dodge_needs(self):
        """The DoD's headline case: hold → queue → panel → clean stop → resume.
        Each leg is one edge, and a missing one would only show up as a
        choreography that silently skips a step at 02:00."""
        m16 = next(e for e in examples() if e.id == "example-m16")
        edges = {(e.from_, e.fromPort, e.to, e.toPort) for e in m16.graph.edges}
        for leg in [("n13", "in", "n14", "pause"),      # clouds in  -> hold
                    ("n13", "in", "n15", "do"),         # clouds in  -> queue
                    ("n13", "clear", "n14", "resume"),  # clear      -> resume
                    ("n13", "clear", "n15", "stop"),    # clear      -> queue stop
                    ("n18", "ready", "n15", "panel")]:  # panel      -> flats unlocked
            assert leg in edges, f"missing cloud-dodge leg {leg}"

    def test_each_call_returns_fresh_copies(self):
        """The editor loads an example to 'save as'; a shared instance would let
        one session's edit leak into the next reader's Examples folder."""
        a, b = examples()[0], examples()[0]
        a.graph.nodes[0].params["offset"] = -999
        assert b.graph.nodes[0].params.get("offset") != -999


class TestStore:
    def test_a_fresh_store_still_lists_the_examples(self, store):
        assert len(store.load_all()) == 5
        assert all(r.readonly for r in store.load_all())

    def test_save_and_reload(self, store):
        r = FlowRecord(name="mine", graph=FlowGraph(nodes=[FlowNode(id="a", type="dusk")]))
        store.save(r)
        again = FlowStore(store.dir).get(r.id)
        assert again.name == "mine" and len(again.graph.nodes) == 1

    def test_an_example_cannot_be_overwritten(self, store):
        ex = examples()[0]
        with pytest.raises(ReadOnlyFlow):
            store.save(ex)

    def test_an_example_cannot_be_overwritten_by_borrowing_its_id(self, store):
        """The readonly flag is on the RECORD, but a client can send its own
        record with a fixture's id. The id check is what actually holds."""
        with pytest.raises(ReadOnlyFlow):
            store.save(FlowRecord(id="example-m16", name="sneaky", readonly=False))

    def test_an_example_cannot_be_deleted(self, store):
        with pytest.raises(ReadOnlyFlow):
            store.delete("example-m16")

    def test_a_structurally_broken_graph_is_refused_at_save(self, store):
        bad = FlowRecord(name="bad", graph=FlowGraph(
            nodes=[FlowNode(id="a", type="cloudwatch"), FlowNode(id="b", type="slew")],
            edges=[{"from": "a", "fromPort": "in", "to": "b", "toPort": "run"}]))
        with pytest.raises(ValueError, match="can't feed"):
            store.save(bad)

    def test_a_traversing_id_cannot_escape_the_directory(self, store):
        """Same defence as PlanLibrary, via the same call — the id is a path
        param as well as a body field."""
        with pytest.raises(Exception):
            store._path("../../astrodeck")

    def test_a_corrupt_file_does_not_brick_the_library(self, store):
        store.save(FlowRecord(name="good"))
        (store.dir / "junk.json").write_text("{not json", encoding="utf-8")
        assert any(r.name == "good" for r in store.load_all())

    def test_the_quota_refuses_a_new_flow_but_allows_an_update(self, store, monkeypatch):
        monkeypatch.setattr("astrodeck.flows.store.MAX_FLOWS", 2)
        a = store.save(FlowRecord(name="a"))
        store.save(FlowRecord(name="b"))
        with pytest.raises(FlowLibraryFull):
            store.save(FlowRecord(name="c"))
        store.save(a.model_copy(update={"name": "a2"}))     # update still fine

    def test_folders_lead_with_my_flows_and_end_with_examples(self, store):
        store.save(FlowRecord(name="x", folder="Winter"))
        names = [f["name"] for f in store.folders()]
        assert names[0] == "My flows" and names[-1] == "Examples"
        assert "Winter" in names

    def test_deleting_a_folder_reparents_rather_than_deleting_flows(self, store):
        r = store.save(FlowRecord(name="keeper", folder="Winter"))
        moved = store.delete_folder("Winter")
        assert moved == 1
        assert store.get(r.id).folder == "My flows", \
            "a flow was lost when its folder was tidied away"

    def test_the_examples_folder_is_fixed(self, store):
        with pytest.raises(ReadOnlyFlow):
            store.delete_folder(EXAMPLES_FOLDER)
        with pytest.raises(ReadOnlyFlow):
            store.rename_folder("My flows", EXAMPLES_FOLDER)


class TestAFlowCannotClaimARunItNeverHad:
    """Found by LOOKING at the rendered library, not by a failing assertion.

    Four of the five shipped fixtures carried ``last_result="ok"`` while their
    ``last_run`` was None, so every example card rendered "never run" on one
    line and a green "COMPLETED CLEAN" badge on the next. The card component was
    faithful - it maps last_result exactly - which is precisely why nothing
    caught it: the lie was in the DATA, and both halves of it were rendered
    correctly.

    That pairing is the broken-promise class in miniature: a green badge is the
    one thing an operator scanning a library actually reads, and it was saying a
    night had completed cleanly for a flow that had never been started.
    """

    def test_no_example_reports_a_result_without_a_run(self):
        from astrodeck.flows.examples import examples
        for ex in examples():
            if ex.last_run is None:
                assert ex.last_result == "", (
                    f"{ex.name!r} has never run (last_run is None) but claims "
                    f"last_result={ex.last_result!r} - the library card renders "
                    f"that as COMPLETED CLEAN")

    def test_the_pairing_holds_for_anything_the_store_returns(self):
        """The same invariant over load_all(), so a saved flow cannot be given
        one either - the route re-derives both fields from the stored record and
        this is what says the two must agree."""
        from astrodeck.flows.store import FlowStore
        for rec in FlowStore().load_all():
            if rec.last_run is None:
                assert rec.last_result == "", rec.name
