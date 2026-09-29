"""A plan file that no longer validates is listed, and asking for it says
why (#378; the flow library's #153 and the session store's #242 rule).

THE FAULT (#378). ``PlanLibrary.list()`` skipped a file that did not
validate as a ``SequencePlan`` (``except Exception: continue``), so the
operator saw one plan fewer and nothing said why; ``GET /api/plans/{id}``
and its export let pydantic's ``ValidationError`` out, a 500. #334 made a
step's ``frame_type`` one of Light, Dark, Bias or Flat, so a plan saved
before it with any other word (a hand edit, a direct API call) stopped
loading and vanished. Deterministic.

THE RULE. ``_read_plan`` is the one judgment of which files are plans. A
file that is there and is not one raises ``PlanUnreadable`` with a reason:
``list`` makes it a row, ``{id, name when the file gives one, status:
"unreadable", unreadable: <reason>, mtime}``, and ``GET`` and the export
answer 422 ``unreadable`` with the same sentence. For a plan that fails
validation the reason names the first error by its path in the plan and the
value the file holds there, and counts the rest. A file the OS will not open
right now is left out, as before, and a missing one is a 404.

``fixtures/plan_list_unreadable.json`` records the routes' real answers for
a library of one good plan and one unreadable one, for S5-PLANS-UI; the last
class grades it byte for byte. To record it again after a DELIBERATE change:

    cd server && ASTRODECK_REWRITE_PLAN_LIST_FIXTURE=1 .venv/Scripts/python.exe -m pytest -q -p no:randomly -n0 tests/test_plan_library_unreadable.py

Every named mutation was run in a private copy of ``server/`` under the
session scratchpad (``S5-ROUTES-mut``), from byte copies, never in the
shared tree (#254); the failure each produced is quoted where it went red.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.plans as plans_mod
from astrodeck.plans import (NO_PLAN, NOT_JSON, PlanLibrary, PlanUnreadable,
                             migrate_plan_policy_fields)
from astrodeck.sequence import SequencePlan

FIXTURE = Path(__file__).parent / "fixtures" / "plan_list_unreadable.json"
REWRITE = os.environ.get("ASTRODECK_REWRITE_PLAN_LIST_FIXTURE") == "1"

#: The ids the two plans are saved under: fixed, so the recording is the
#: same bytes on every run, and uuid-shaped, as the library mints them.
GOOD_ID = "6f1c0d2e4b5a49c8a1e2f3d4c5b6a7e8"
BAD_ID = "0b9e8d7c6a5f4e3d2c1b0a9f8e7d6c5b"

#: The two files' modification times: the good plan the newer, so the
#: list's newest-first order puts it first, and fixed for the recording.
GOOD_MTIME = 1790600000.0
BAD_MTIME = 1790500000.0

#: #378's probe: a plan whose first step's frame type is a word #334 no
#: longer takes, and what the row and the 422 say about it.
SNAPSHOT_REASON = ("fails validation: targets.0.steps.0.frame_type: frame "
                   "type 'Snapshot' is not one of Light, Dark, Bias, Flat "
                   "(in any case)")


def _plan(name: str, target: str = "M42", **step) -> SequencePlan:
    return SequencePlan.model_validate({"name": name, "targets": [{
        "name": target, "ra_hours": 5.588, "dec_deg": -5.391,
        "steps": [{"filter": "L", "exposure_s": 60, "count": 30, **step},
                  {"filter": "R", "exposure_s": 60, "count": 10}]}]})


def _edit(lib: PlanLibrary, plan_id: str, change) -> Path:
    """Hand-edit a stored plan's file: ``change(envelope)`` in place."""
    path = lib._dir / f"{plan_id}.json"
    env = json.loads(path.read_text(encoding="utf-8"))
    change(env)
    path.write_text(json.dumps(env), encoding="utf-8")
    return path


def _snapshot(env: dict) -> None:
    env["plan"]["targets"][0]["steps"][0]["frame_type"] = "Snapshot"


@pytest.fixture
def lib(tmp_path) -> PlanLibrary:
    return PlanLibrary(tmp_path / "plans")


def _library(lib: PlanLibrary) -> None:
    """#378's library: one good plan, and one saved good and then
    hand-edited to a frame type #334 refuses, each at its fixed mtime."""
    lib.save(_plan("M42 LRGB"), GOOD_ID)
    lib.save(_plan("NGC 7000 Ha", target="NGC 7000"), BAD_ID)
    _edit(lib, BAD_ID, _snapshot)
    os.utime(lib._dir / f"{GOOD_ID}.json", (GOOD_MTIME, GOOD_MTIME))
    os.utime(lib._dir / f"{BAD_ID}.json", (BAD_MTIME, BAD_MTIME))


@pytest.fixture
def client(isolated_config, lib, monkeypatch):
    monkeypatch.setattr(app_module, "plan_library", lib)
    app = app_module.create_app()
    isolated_config.sweep()
    with TestClient(app) as c:
        yield c


# ======================================================= the fault itself

class TestAPlanThatNoLongerValidates:
    def test_it_is_listed_with_the_first_error(self, lib):
        """#378's probe: two plans saved, one hand-edited to ``"frame_type":
        "Snapshot"``. The list holds both: the good row as it always was,
        and the other as unreadable, naming the field and its value.

        RED under mutant "continue restored" (``list`` skipping a file that
        is not a plan, as before), the damaged plan gone, observed:

            E   AssertionError: assert ['6f1c0d2e4b5...f3d4c5b6a7e8'] == ['6f1c0d2e4b5...0a9f8e7d6c5b']
            E     Right contains one more item: '0b9e8d7c6a5f4e3d2c1b0a9f8e7d6c5b'

        The same mutant turns 19 of this file's 24 cases red; every case
        that expects a row finds none.
        """
        _library(lib)
        rows = lib.list()
        assert [r["id"] for r in rows] == [GOOD_ID, BAD_ID]
        assert rows[1] == {"id": BAD_ID, "name": "NGC 7000 Ha",
                           "status": "unreadable",
                           "unreadable": SNAPSHOT_REASON, "mtime": BAD_MTIME}

    def test_get_and_export_answer_422_with_that_sentence(self, client, lib):
        """``GET /api/plans/{id}`` and ``/export`` answer 422 ``unreadable``
        with the list row's sentence, word for word.

        RED under mutant "ValidationError reaches a 500" (``_read_plan``
        without its ``except ValidationError``, so pydantic's error leaves
        the library as it did before), the list route raising it first,
        observed:

            E   pydantic_core._pydantic_core.ValidationError: 1 validation error for SequencePlan
            E   targets.0.steps.0.frame_type
            E     Value error, frame type 'Snapshot' is not one of Light, Dark, Bias, Flat (in any case) [type=value_error, input_value='Snapshot', input_type=str]

        and under its route half, "the route lets PlanUnreadable out"
        (``get_plan``'s ``except PlanUnreadable`` arm removed), the refusal
        leaving ``GET`` as a 500 (its cause, the pydantic error above, is
        printed first), observed:

            E   astrodeck.plans.PlanUnreadable: fails validation: targets.0.steps.0.frame_type: frame type 'Snapshot' is not one of Light, Dark, Bias, Flat (in any case)

        and the same line under "the export lets PlanUnreadable out" (the
        export's arm removed).
        """
        _library(lib)
        row = next(r for r in client.get("/api/plans").json()
                   if r["id"] == BAD_ID)
        for url in (f"/api/plans/{BAD_ID}", f"/api/plans/{BAD_ID}/export"):
            r = client.get(url)
            assert r.status_code == 422, (url, r.text)
            assert r.json()["detail"] == {"detail": row["unreadable"],
                                          "code": "unreadable"}, url

    @pytest.mark.parametrize("step,reason", [
        ({"exposure_s": 5000},
         "targets.0.steps.0.exposure_s 5000: input should be less than or "
         "equal to 3600"),
        ({"filter": 12345},
         "targets.0.steps.0.filter 12345: input should be a valid string"),
        ({"count": None},
         "targets.0.steps.0.count None: input should be a valid integer"),
        ({"gain": 1.5, "count": 0},
         "targets.0.steps.0.gain 1.5: input should be a valid integer, got "
         "a number with a fractional part (and 1 more error)"),
    ], ids=["a-bound", "a-type", "a-null", "two-errors"])
    def test_any_field_is_named_by_its_path_and_value(self, lib, step,
                                                      reason):
        """Any field, not only the frame type: its path, the value the file
        holds there and pydantic's words for the rule, with further errors
        counted.

        RED under mutant "the path alone" (the value never quoted), on
        every case, e.g. on [a-type]:

            E   AssertionError: assert 'fails valida... valid string' == 'fails valida... valid string'
            E     - fails validation: targets.0.steps.0.filter 12345: input should be a valid string
            E     ?                                           ------
            E     + fails validation: targets.0.steps.0.filter: input should be a valid string

        and under "the other errors go unsaid" (the count of the rest not
        added) on [two-errors]:

            E   AssertionError: assert 'fails valida...actional part' == 'fails valida...1 more error)'
            E     Skipping 103 identical leading characters in diff, use -v to show
            E     - tional part (and 1 more error)
            E     + tional part
        """
        lib.save(_plan("bad"), BAD_ID)
        _edit(lib, BAD_ID, lambda env: env["plan"]["targets"][0]["steps"][0]
              .update(step))
        (row,) = lib.list()
        assert row["unreadable"] == f"fails validation: {reason}"

    def test_a_missing_field_is_said_to_be_missing(self, lib):
        """A field the file leaves out is missing, not quoted as None."""
        lib.save(_plan("bad"), BAD_ID)
        _edit(lib, BAD_ID, lambda env: env["plan"]["targets"][0]["steps"][0]
              .pop("count"))
        (row,) = lib.list()
        assert row["unreadable"] == ("fails validation: "
                                     "targets.0.steps.0.count is missing")

    def test_a_list_or_object_is_named_by_its_path_alone(self, lib):
        """A value that is not one plain value is not quoted: only a
        field's own plain value is, and never pydantic's rendering of the
        error, which quotes its input from wherever it sits.

        RED under mutant "every value quoted" (the plain-value test
        dropped), the object written into the row, observed:

            E   AssertionError: assert 'fails valida... a valid list' == 'fails valida... a valid list'
            E     - fails validation: targets.0.steps: input should be a valid list
            E     + fails validation: targets.0.steps {'a': 1}: input should be a valid list
            E     ?                                  +++++ ++++

        (and red on the next class's [its-plan-not-an-object], which gains
        ``the plan [1]``.)
        """
        lib.save(_plan("bad"), BAD_ID)
        _edit(lib, BAD_ID, lambda env: env["plan"]["targets"][0]
              .update(steps={"a": 1}))
        (row,) = lib.list()
        assert row["unreadable"] == ("fails validation: targets.0.steps: "
                                     "input should be a valid list")

    def test_a_long_value_is_cut(self, lib):
        """#334's rule quotes the value whole, and the value is whatever the
        file holds: the reason is a list line, so its rule is cut.

        RED under mutant "the rule uncut" (``_RULE_MAX`` not applied), the
        whole 5000-character value in the row, observed:

            E   AssertionError: 5113
            E   assert 5113 <= (48 + 160)
            E    +  and   48 = len('fails validation: targets.0.steps.0.frame_type: ')
            E    +  and   160 = plans_mod._RULE_MAX
        """
        lib.save(_plan("bad"), BAD_ID)
        _edit(lib, BAD_ID, lambda env: env["plan"]["targets"][0]["steps"][0]
              .update(frame_type="X" * 5000))
        (row,) = lib.list()
        reason = row["unreadable"]
        head = "fails validation: targets.0.steps.0.frame_type: "
        assert reason.startswith(head + "frame type 'XXXX")
        assert len(reason) <= len(head) + plans_mod._RULE_MAX, len(reason)
        assert reason.endswith("...")


class TestAFileThatIsNotAPlanAtAll:
    @pytest.mark.parametrize("content,reason", [
        (b"{not json", NOT_JSON),
        (b"\xff\xfe\x00garbage", NOT_JSON),
        # Nested past the parser's depth: ``json.loads`` raises
        # RecursionError, not a ValueError (#363's lesson).
        (b"[" * 200000, NOT_JSON),
        (b"[1, 2]", NO_PLAN),
        (json.dumps({"id": BAD_ID, "plan": [1]}).encode(),
         "fails validation: the plan: input should be a valid dictionary "
         "or instance of SequencePlan"),
    ], ids=["not-json", "not-utf8", "nested-past-the-parsers-depth",
            "not-an-object", "its-plan-not-an-object"])
    def test_it_is_listed_and_get_says_why(self, client, lib, content,
                                           reason):
        """A file that does not parse, or holds no envelope, or an envelope
        whose plan is not an object, is a row too, with no name (it gives
        none), and ``GET`` and the export answer 422 with its reason rather
        than a 404 for a file that is on disk.

        RED under mutant "continue restored" on every case (the row
        missing), e.g. on [not-json]:

            E   AssertionError: assert [] == [{'id': '0b9e... valid JSON'}]
            E     Right contains one more item: {'id': '0b9e8d7c6a5f4e3d2c1b0a9f8e7d6c5b', 'mtime': 1790500000.0, 'status': 'unreadable', 'unreadable': 'not valid JSON'}

        under "a deep file escapes" (``RecursionError`` taken out of
        ``_read_plan``'s parse arm) on [nested-past-the-parsers-depth], the
        list itself raising, observed:

            E   RecursionError: maximum recursion depth exceeded while decoding a JSON array from a unicode string

        and under "the export reads the bytes first" (the export route's old
        order, its bytes read before the plan) on the four cases whose file
        does not parse or holds no envelope, e.g.:

            E   AssertionError: /api/plans/0b9e8d7c6a5f4e3d2c1b0a9f8e7d6c5b/export
            E   assert (404, 'plan not found') == (422, {'code'... valid JSON'})
        """
        lib._dir.mkdir(parents=True)
        path = lib._dir / f"{BAD_ID}.json"
        path.write_bytes(content)
        os.utime(path, (BAD_MTIME, BAD_MTIME))
        assert client.get("/api/plans").json() == [
            {"id": BAD_ID, "status": "unreadable", "unreadable": reason,
             "mtime": BAD_MTIME}]
        for url in (f"/api/plans/{BAD_ID}", f"/api/plans/{BAD_ID}/export"):
            r = client.get(url)
            assert (r.status_code, r.json()["detail"]) == (
                422, {"detail": reason, "code": "unreadable"}), url


class TestTheName:
    @pytest.mark.parametrize("change,name", [
        (lambda env: None, "NGC 7000 Ha"),
        (lambda env: env.pop("name"), "NGC 7000 Ha"),
        (lambda env: env.update(name="   "), "NGC 7000 Ha"),
        (lambda env: (env.pop("name"), env["plan"].pop("name")), None),
    ], ids=["the-envelopes", "the-plans-own", "a-blank-one-is-none",
            "neither"])
    def test_the_name_is_the_files_when_it_gives_one(self, lib, change,
                                                     name):
        """The envelope's name, else the plan's own, and no ``name`` key at
        all when the file gives neither: a row never invents one.

        RED under mutant "the stem as the name" (the row taking the file's
        stem when the file gives no name, as the session store's row does),
        on [neither], observed:

            E   AssertionError: assert '0b9e8d7c6a5f4e3d2c1b0a9f8e7d6c5b' == None

        (and on the previous class's five cases, each row gaining a name.)
        """
        lib.save(_plan("NGC 7000 Ha"), BAD_ID)
        _edit(lib, BAD_ID, lambda env: (_snapshot(env), change(env)))
        (row,) = lib.list()
        assert row.get("name") == name
        assert ("name" in row) == (name is not None)


# ================================================ what stays as it was

class TestWhatStaysAsItWas:
    def test_control_a_library_of_plans_lists_as_before(self, lib):
        """Control: every row of a healthy library has the shape it always
        had, and no ``status`` key, so no client reading plan rows sees a
        new field on a plan."""
        lib.save(_plan("a"), GOOD_ID)
        lib.save(_plan("b"), BAD_ID)
        for row in lib.list():
            assert set(row) == {"id", "name", "frames", "integration_min",
                                "shutter_min", "targets", "mtime"}, row

    def test_control_a_missing_or_traversal_id_is_a_404(self, client, lib):
        """Control: no file is still "not found", on GET and export."""
        _library(lib)
        for url in ("/api/plans/nope", "/api/plans/nope/export",
                    "/api/plans/..%5Castrodeck"):
            assert client.get(url).status_code == 404, url

    def test_a_file_the_os_will_not_open_is_left_out(self, client, lib,
                                                     monkeypatch):
        """A sharing violation says nothing about what a file holds, so the
        list leaves it out for now, as it always did, rather than offer a
        good plan for deletion as unreadable; ``GET`` answers 404 as before.

        RED under mutant "an OSError is damage" (``_read_plan`` catching
        ``PermissionError`` with the parse errors), the good plan listed
        as unreadable, observed:

            E   AssertionError: assert ['6f1c0d2e4b5...0a9f8e7d6c5b'] == ['6f1c0d2e4b5...f3d4c5b6a7e8']
            E     Left contains one more item: '0b9e8d7c6a5f4e3d2c1b0a9f8e7d6c5b'
        """
        _library(lib)
        real = plans_mod.read_json

        def fake(path):
            if (sys._getframe(1).f_code.co_name == "_read_plan"
                    and Path(path).stem == BAD_ID):
                raise PermissionError(13, "sharing violation")
            return real(path)

        monkeypatch.setattr(plans_mod, "read_json", fake)
        assert [r["id"] for r in client.get("/api/plans").json()] == [GOOD_ID]
        assert client.get(f"/api/plans/{BAD_ID}").status_code == 404

    def test_the_migration_leaves_an_unreadable_plan_as_it_is(self, lib):
        """The boot migration nulls a policy field that holds its old
        built-in default. It reads the list, which now holds an unreadable
        row, and it leaves that file byte for byte as it is: nothing about a
        file this build cannot read is "a default nobody chose". The good
        plan beside it is migrated (control).

        RED under mutant "the migration takes every row" (its skip of an
        unreadable row removed), the damaged file rewritten, observed:

            E   AssertionError: the unreadable plan was rewritten
            E   assert 2 == 1
        """
        lib.save(_plan("good"), GOOD_ID)
        lib.save(_plan("bad"), BAD_ID)
        for pid in (GOOD_ID, BAD_ID):
            _edit(lib, pid, lambda env: env["plan"].update(dither_pixels=3.0))
        bad = _edit(lib, BAD_ID, _snapshot)
        before = bad.read_bytes()
        changed = migrate_plan_policy_fields(lib)
        assert changed == 1, "the unreadable plan was rewritten"
        assert bad.read_bytes() == before
        good = json.loads((lib._dir / f"{GOOD_ID}.json").read_text("utf-8"))
        assert good["plan"]["dither_pixels"] is None

    def test_get_raises_plan_unreadable_not_key_error(self, lib):
        """``PlanUnreadable`` is a ``ValueError`` and never a ``KeyError``:
        every plan route answers ``KeyError`` with 404, and "not found" is
        false for a plan on disk."""
        _library(lib)
        with pytest.raises(PlanUnreadable) as info:
            lib.get(BAD_ID)
        assert not isinstance(info.value, KeyError)
        assert info.value.reason == SNAPSHOT_REASON


# ================================================== the recorded answer

ABOUT = ("GET /api/plans for a library of two plan files: one good plan, "
         "and one saved good and then hand-edited so that its first step's "
         "frame_type is 'Snapshot', which #334 refuses; and GET "
         "/api/plans/{id} and /export for that one, which answer 422. The "
         "ids and the two files' mtimes are fixed. Recorded and graded byte "
         "for byte by server/tests/test_plan_library_unreadable.py "
         "(ASTRODECK_REWRITE_PLAN_LIST_FIXTURE=1 re-records it); read by "
         "S5-PLANS-UI (#378). Never edit it by hand.")


def _recording(client) -> str:
    """The fixture's text as the routes answer now."""
    listed = client.get("/api/plans")
    assert listed.status_code == 200, listed.text
    got = client.get(f"/api/plans/{BAD_ID}")
    exported = client.get(f"/api/plans/{BAD_ID}/export")
    doc = {"about": ABOUT, "list": listed.json(),
           "get": {"url": f"/api/plans/{BAD_ID}", "status": got.status_code,
                   "body": got.json()},
           "export": {"url": f"/api/plans/{BAD_ID}/export",
                      "status": exported.status_code,
                      "body": exported.json()}}
    return json.dumps(doc, indent=1, ensure_ascii=True) + "\n"


class TestTheRecordedAnswer:
    def test_the_file_is_the_routes_answer_byte_for_byte(self, client, lib):
        """The fixture's bytes are the recording the routes make now, one
        good row and one unreadable row, and the file holds what the fault
        needs a UI to draw: a row with no counts, and the sentence both the
        row and the 422 carry.

        Line endings are the checkout's, not the recording's (#445): the
        file is written with LF and ``core.autocrlf`` may check it out with
        CRLF, so CRLF is read as LF and every other byte is graded.

        RED under mutant "fixture hand-edited" (the list row's reason's
        'Snapshot' changed to 'Snapshots' in the copy's file), observed:

            E   AssertionError: the recorded answer is not the routes'; re-record it only after a deliberate change
            E   assert b'{\\n "about"...n  }\\n }\\n}\\n' == b'{\\n "about"...n  }\\n }\\n}\\n'
            E     At index 877 diff: b's' != b"'"

        and the file as recorded with the code mutated, under "continue
        restored" (the unreadable row gone from the answer):

            E     At index 687 diff: b',' != b'\\n'
        """
        _library(lib)
        text = _recording(client)
        if REWRITE:
            FIXTURE.write_bytes(text.encode("utf-8"))
        on_disk = FIXTURE.read_bytes().replace(b"\r\n", b"\n")
        assert on_disk == text.encode("utf-8"), (
            "the recorded answer is not the routes'; re-record it only "
            "after a deliberate change")
        doc = json.loads(on_disk)
        good, bad = doc["list"]
        assert "status" not in good and good["frames"] == 40
        assert bad == {"id": BAD_ID, "name": "NGC 7000 Ha",
                       "status": "unreadable", "unreadable": SNAPSHOT_REASON,
                       "mtime": BAD_MTIME}
        for answer in (doc["get"], doc["export"]):
            assert answer["status"] == 422
            assert answer["body"] == {"detail": {
                "detail": SNAPSHOT_REASON, "code": "unreadable"}}
