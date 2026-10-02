# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The flow store and the user store follow ``config.CONFIG_DIR`` when it is
repointed (#436; #361's class, test isolation of module singletons).

THE FAULT (#436). Two comments stated a rule the code did not keep.
``FlowStore.dir`` said it was "Resolved LIVE, never bound at import: a test
monkeypatches CONFIG_DIR", while the module did ``from ..config import
CONFIG_DIR`` at import, so repointing ``config.CONFIG_DIR`` (what conftest's
``IsolatedConfig`` does) left ``flow_store`` on the directory it was
imported with. ``auth.users`` said its default path was "resolved lazily",
while ``user_store = UserStore()`` resolved it once, at import. The session
fixture's sweep moves both bindings, so the suite never read the real
config through them, but any code that repoints the directory, a test or a
maintenance path, got a store still reading the old one. Deterministic.

THE RULE, both comments made true rather than corrected: ``FlowStore.dir``
reads ``CONFIG_DIR`` through the config module on every call, and a
``UserStore`` built with no path resolves ``config.CONFIG_DIR / 'users.json'``
on every call. A user store's cache belongs to the file it was read from,
so a store whose default moved reads the new file even when the two files'
``(mtime_ns, size)`` stamps agree. A store built on an explicit path or
directory stays on it (the controls).

``flows.store.CONFIG_DIR`` was answered for a while through a module
``__getattr__``, only because conftest's sweep asserted it moved; the S7
integration dropped that known positive, which could not fail (conftest's
``_SWEEP_MUST_MOVE`` says how that was shown), and the name with it: the
flow store reads ``config.CONFIG_DIR`` and nothing reads the module's name.
Setting ``user_store._path`` is still graded (``test_auth_lockout.py`` pins
the process store that way): a set path pins, and setting back the file the
default names, which is what a monkeypatch's undo does, follows the default
again.

Every named mutation was run in a private copy of ``server/`` under the
session scratchpad (``S7-STORE-mut``), from byte copies, never in the shared
tree (#254); the failure each produced is quoted where it went red.
"""
from __future__ import annotations

import json
import os

import astrodeck.config as config_mod
from astrodeck.auth.users import UserStore, user_store
from astrodeck.flows.models import FlowRecord
from astrodeck.flows.store import FlowStore, flow_store


def _users_file(path, username: str) -> None:
    """A user store's file holding one viewer called ``username``, with a
    fixed id and creation time, so two such files differ only in the name
    and are the same size when the names are."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "users": [{
        "id": "0" * 32, "username": username, "email": username,
        "role": "viewer", "created": 1.0}]}), encoding="utf-8")


class TestTheFlowStoreFollows:
    def test_the_singleton_and_a_store_with_no_directory_follow(
            self, tmp_path, monkeypatch):
        """Repoint ``CONFIG_DIR`` and the process ``flow_store`` and a
        ``FlowStore()`` built with no directory both follow it; a flow saved
        under the first directory is not in the library under the second.

        RED under mutant "bound at import" (flows half: the module's
        ``from ..config import CONFIG_DIR`` put back and ``dir`` reading
        that name), the singleton still on the session's throwaway
        directory, where conftest's sweep had moved the binding (temporary
        directory names elided), observed:

            E   AssertionError: assert WindowsPath('C:/Users/bear/AppData/Local/Temp/astrodeck-test-config-.../flows') == (WindowsPath('C:/Users/bear/AppData/Local/Temp/pytest-of-bear/pytest-.../test_the_singleton_and_a_store0/first') / 'flows')
            E    +  where WindowsPath('C:/Users/bear/AppData/Local/Temp/astrodeck-test-config-.../flows') = <astrodeck.flows.store.FlowStore object at 0x...>.dir

        The same line under the mutant's two halves together.

        (S7-STORE recorded a third mutant here, "the CONFIG_DIR name
        removed", red only at conftest's known positive for that name. The
        S7 integration removed both the name and the known positive, which
        could not fail, so there is no such mutant left to run.)
        """
        first, second = tmp_path / "first", tmp_path / "second"
        monkeypatch.setattr(config_mod, "CONFIG_DIR", first)
        assert flow_store.dir == first / "flows"
        saved = flow_store.save(FlowRecord(name="saved under first"))
        assert (first / "flows" / f"{saved.id}.json").is_file()

        monkeypatch.setattr(config_mod, "CONFIG_DIR", second)
        assert flow_store.dir == second / "flows"
        assert FlowStore().dir == second / "flows"
        assert saved.id not in {r.id for r in flow_store.load_all()}

    def test_control_a_store_on_its_own_directory_stays(self, tmp_path,
                                                        monkeypatch):
        """Control: a store built on a directory keeps it whatever
        ``CONFIG_DIR`` says.

        It can fail: under "a given directory or path ignored" (``dir``
        answering ``CONFIG_DIR / "flows"`` always), observed (temporary
        directory names elided):

            E   AssertionError: assert WindowsPath('.../test_control_a_store_on_its_ow0/moved/flows') == (WindowsPath('.../test_control_a_store_on_its_ow0') / 'own')
        """
        own = FlowStore(tmp_path / "own")
        monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / "moved")
        assert own.dir == tmp_path / "own"


class TestTheUserStoreFollows:
    def test_the_singleton_and_a_store_with_no_path_follow(self, tmp_path,
                                                           monkeypatch):
        """Repoint ``CONFIG_DIR`` and the process ``user_store`` writes its
        accounts under it, and once it moves on, reads the new directory's
        file (none: no accounts) rather than serving the old one's; a
        ``UserStore()`` built with no path resolves there too.

        RED under mutant "bound at import" (users half: ``__init__``
        resolving the default path once, as before), the singleton still on
        the session's throwaway file (temporary directory names elided),
        observed:

            E   AssertionError: assert WindowsPath('C:/Users/bear/AppData/Local/Temp/astrodeck-test-config-.../users.json') == (WindowsPath('C:/Users/bear/AppData/Local/Temp/pytest-of-bear/pytest-.../test_the_singleton_and_a_store1/first') / 'users.json')
            E    +  where WindowsPath('C:/Users/bear/AppData/Local/Temp/astrodeck-test-config-.../users.json') = <astrodeck.auth.users.UserStore object at 0x...>._path

        The same line under the mutant's two halves together, and the next
        case red with it (``assert [] == ['aaaa@example.org']``: a store
        built before the move never looked at the first directory).
        """
        first, second = tmp_path / "first", tmp_path / "second"
        monkeypatch.setattr(config_mod, "CONFIG_DIR", first)
        assert user_store._path == first / "users.json"
        user_store.create(username="first@example.org", role="viewer")
        assert (first / "users.json").is_file()

        monkeypatch.setattr(config_mod, "CONFIG_DIR", second)
        assert user_store._path == second / "users.json"
        assert user_store.get_by_username("first@example.org") is None
        assert user_store.is_empty()
        assert UserStore()._path == second / "users.json"

    def test_a_moved_store_reads_the_new_file_when_the_stamps_agree(
            self, tmp_path, monkeypatch):
        """Two users files the same size with the same modification time,
        one account each: after the move the store lists the second file's
        account. The cache is the file's, not the stamp's.

        RED under mutant "cache keyed on the stamp alone" (``_cache``'s
        reload test without the ``path != self._source`` term), the first
        file's account served after the move, observed:

            E   AssertionError: assert ['aaaa@example.org'] == ['bbbb@example.org']
            E     At index 0 diff: 'aaaa@example.org' != 'bbbb@example.org'
        """
        first, second = tmp_path / "first", tmp_path / "second"
        _users_file(first / "users.json", "aaaa@example.org")
        _users_file(second / "users.json", "bbbb@example.org")
        stamp = 1_790_000_000_000_000_000
        for d in (first, second):
            os.utime(d / "users.json", ns=(stamp, stamp))
        a, b = (os.stat(d / "users.json") for d in (first, second))
        assert (a.st_mtime_ns, a.st_size) == (b.st_mtime_ns, b.st_size), \
            "premise: the two files' stamps agree"

        store = UserStore()
        monkeypatch.setattr(config_mod, "CONFIG_DIR", first)
        assert [u.username for u in store.list()] == ["aaaa@example.org"]
        monkeypatch.setattr(config_mod, "CONFIG_DIR", second)
        assert [u.username for u in store.list()] == ["bbbb@example.org"]

    def test_a_monkeypatched_path_is_put_back_to_following(self, tmp_path,
                                                           monkeypatch):
        """A test that pins the process store by setting ``_path`` (as
        ``test_auth_lockout.py`` does with ``monkeypatch.setattr``) gets it
        back following ``CONFIG_DIR`` when the patch is undone: the undo
        sets back the file the default named, which is not a pin.

        RED under mutant "a set path pins for good" (the setter keeping
        whatever it is given), the store left on the session's throwaway
        file after the undo (temporary directory names elided), observed:

            E   AssertionError: assert WindowsPath('C:/Users/bear/AppData/Local/Temp/astrodeck-test-config-.../users.json') == ((WindowsPath('C:/Users/bear/AppData/Local/Temp/pytest-of-bear/pytest-.../test_a_monkeypatched_path_is_p0') / 'after') / 'users.json')

        and under "no setter" (``_path`` read-only, as this task first
        built it), at the pin itself, observed, with
        ``test_auth_lockout.py::test_create_admin_also_makes_the_account_reachable``
        failing and erroring the same way (its pin, and its undo):

            E   AttributeError: property '_path' of 'UserStore' object has no setter

        (and under "a given directory or path ignored", at the pin, the
        store answering the throwaway file for ``pinned``.)
        """
        pinned = tmp_path / "pinned" / "users.json"
        with monkeypatch.context() as m:
            m.setattr(user_store, "_path", pinned)
            assert user_store._path == pinned
            m.setattr(config_mod, "CONFIG_DIR", tmp_path / "elsewhere")
            assert user_store._path == pinned, "a pin follows nothing"
        monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / "after")
        assert user_store._path == tmp_path / "after" / "users.json"

    def test_control_a_store_on_its_own_path_stays(self, tmp_path,
                                                   monkeypatch):
        """Control: a store built on a path keeps it and its accounts
        whatever ``CONFIG_DIR`` says.

        It can fail: under "a given directory or path ignored" (``_path``
        answering the default always), observed:

            E   AssertionError: assert [] == ['own@example.org']
            E     Right contains one more item: 'own@example.org'
        """
        _users_file(tmp_path / "own" / "users.json", "own@example.org")
        own = UserStore(tmp_path / "own" / "users.json")
        assert [u.username for u in own.list()] == ["own@example.org"]
        monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / "moved")
        assert own._path == tmp_path / "own" / "users.json"
        assert [u.username for u in own.list()] == ["own@example.org"]
