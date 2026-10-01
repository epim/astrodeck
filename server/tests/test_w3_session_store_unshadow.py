"""The ``session_store`` singleton's instance dict is unshadowed between
tests (#522, backlog WP-27b).

THE MECHANISM (quoted in the issue): a plain ``monkeypatch.setattr(
session_store, "save", ...)`` reads the OLD value with ``getattr`` before
patching, and that old value -- nothing having been set on the INSTANCE
before -- is the bound method the class descriptor hands out. The
``monkeypatch`` FIXTURE's own undo, at ITS OWN teardown, writes that bound
method back onto the instance with ``setattr``, where there was nothing
before, and it stays there for the rest of this worker process.
Instance-``__dict__`` lookup wins over the class for a plain attribute, so a
LATER test that does the correct thing -- ``monkeypatch.setattr(SessionStore,
"save", ...)``, patching the class to spy on or fault-inject the singleton --
patches something the instance never reaches again.

THE TWO TESTS BELOW USE THE ``monkeypatch`` FIXTURE, NOT ``pytest.
MonkeyPatch.context()``, on purpose: every production test #522 lists
(``test_resume_crash_loop.py`` and friends) patches through the fixture, and
the fixture's undo runs at ITS OWN TEARDOWN -- the same moment every autouse
conftest fixture's teardown runs, in an order this file's premise depends on
(dir()-alphabetical setup, reverse teardown; see conftest's
``_a_session_store_instance_dict_is_unshadowed`` docstring, which names the
exact wrong-order failure this shape caught during WP-27). A context-manager
``with pytest.MonkeyPatch.context():`` would undo INSIDE the test body,
before any fixture teardown runs at all, and so would never exercise that
ordering -- it was tried first here and passed even with the unshadow
fixture named wrong, which is why it was replaced.

The two tests are order-dependent BY DESIGN, and that is the point: pytest
collects one module's tests in definition order, and this suite runs with no
randomization plugin (see conftest.py's docstring note and #443's own repro,
``-p no:randomly``), so under ``-n0`` the second always runs immediately
after the first. Test A leaves the shadow exactly the way a real test's
``monkeypatch.setattr(session_store, "save", ...)`` would, without cleaning
it up itself; test B then proves a class-level patch still reaches the
instance, which only holds if something between them (conftest's
``_a_session_store_instance_dict_is_unshadowed``) removed the leftover.
"""
from __future__ import annotations

import pytest

from astrodeck.sequence.session import SessionStore, session_store


def test_a_monkeypatch_fixture_undo_leaves_a_bound_method_shadow(monkeypatch):
    """The real shape #522 names. No cleanup here on purpose -- not even the
    ``monkeypatch`` fixture gets a chance to undo this before the test body
    returns -- the autouse conftest fixture is what must remove the shadow
    monkeypatch's OWN teardown leaves, and it must do so AFTER that
    teardown, not before."""
    monkeypatch.setattr(session_store, "save", lambda session: None)


def test_b_a_class_level_save_patch_reaches_the_singleton():
    """A class-level patch -- the pattern ``test_resume_crash_loop.py`` and
    the S7-LEDGER cost tests use to count or fault-inject saves -- must
    reach the singleton. It would not if the test above's shadow were still
    sitting in ``vars(session_store)``: instance lookup would win and this
    patch would never be seen.

    RED under mutant (``_a_session_store_instance_dict_is_unshadowed``
    deleted from conftest.py), observed: the shadow left by the test above's
    monkeypatch fixture undo wins the instance lookup, so this call reaches
    the REAL, unpatched ``save`` instead of ``fake_save`` -- which then
    fails on the sentinel for a reason of its own, proving the patch above
    never ran:

        >           session.updated_ts = time.time()
        E           AttributeError: 'object' object has no attribute
        'updated_ts'

    RED a second way under mutant (the fixture merely renamed back to
    ``_unshadow_session_store_instance_dict``, sorting AFTER the fixtures
    that pull ``monkeypatch`` in): same failure, because the fixture's own
    teardown then runs BEFORE monkeypatch's undo plants the shadow, so it
    finds nothing to remove.
    """
    calls: list[object] = []

    def fake_save(self, session):
        calls.append(session)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(SessionStore, "save", fake_save)
        sentinel = object()
        session_store.save(sentinel)
        assert calls == [sentinel]
