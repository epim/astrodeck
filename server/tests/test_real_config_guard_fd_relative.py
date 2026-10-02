# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#659: the real-config guard misread Linux's dir_fd-relative paths as
CWD-relative ones.

THE BUG, as CI reported it (run 36937456993, five tests in
test_rig_precheck_says_whether_the_rig_is_watched.py, Linux only)::

    AssertionError: ... reached the developer's real config: open of ./ (the
    real config directory), os.rmdir of ./ (the real config directory).

Each of those tests builds ``tmp_path / "config"``. When pytest deleted
that tmp_path (wave 5's WP-68 had set ``tmp_path_retention_policy =
"failed"``, which deletes a PASSED test's tmp_path live, inside this
guard's window), ``shutil.rmtree`` on Linux walked it through its fd-based
APIs (traced in ``shutil.py``'s ``_rmtree_safe_fd``): it opens the top
directory once, then descends into and removes each CHILD by its BARE NAME
relative to that already-open parent fd -- ``os.open("config", ...,
dir_fd=<tmp dir's fd>)``, later ``os.rmdir("config", dir_fd=<that fd>)``.
The guard's old code resolved every audited path with
``os.path.abspath(text)``, i.e. against the process CWD (``server/`` under
this suite's own run instructions), regardless of whether the call that
raised the event had actually used ``dir_fd``. ``os.path.abspath("config")``
from ``server/`` is exactly ``server/config``, the real config directory --
so a tmp dir that happened to contain a child literally named "config" was
misread as the developer's own config, with nothing in the event itself to
say it came from somewhere else entirely. Windows was never affected:
``shutil.rmtree`` there walks full paths, and dir_fd is not implemented for
the calls this walk makes at all (confirmed below).

THE FIX (conftest.py): events that carry a ``dir_fd`` in their own audit
arguments (``os.mkdir``, ``os.rmdir``, ``os.remove``, ``os.rename``,
``shutil.rmtree``) are skipped, never resolved against the CWD, for
whichever path argument that call's ``dir_fd`` was real
(``_dir_fd_relative_positions``). ``open``'s own audit arguments never carry
a dir_fd at all, so it is told apart by the call stack instead
(``_open_is_fd_relative``): a relative ``open`` raised while a frame on the
stack is shutil's fd-based delete walk (``_rmtree_safe_fd``, reached either
directly or through pytest's own tmp-dir cleanup, which calls
``shutil.rmtree`` the same way) is not a reach.

WHY THIS FILE CANNOT DRIVE THE REAL SYSCALLS: dir_fd is not implemented on
Windows for the calls ``_rmtree_safe_fd`` makes -- confirmed live, not
assumed:

    >>> import os
    >>> os.open in os.supports_dir_fd, os.rmdir in os.supports_dir_fd
    (False, False)
    >>> hasattr(os, "O_NONBLOCK")
    False

The last line matters even more than the first two: ``_rmtree_safe_fd``
reads ``os.O_NONBLOCK`` as a plain module constant on its way to the first
dir_fd call, REGARDLESS of whether a real dir_fd is ever passed, and that
attribute does not exist on this platform's ``os`` module at all. So the
real function cannot be executed here even for its harmless, no-dir_fd,
top-level case -- not with monkeypatched ``os.open``/``os.rmdir`` standing
in for the unsupported syscalls, because the AttributeError fires before
either is ever called. This file instead drives the guard with the exact
event shapes Linux raises (the orchestrator's own framing): real audit
dispatch (``sys.audit``) for the dir_fd-carrying events, which is
indistinguishable from the real thing because the guard never sees more
than ``(event, args)`` either way; and a constructed frame for ``open``'s
stack check, which cannot be produced by any means on this platform (above)
but is exactly the two attributes (``f_code.co_filename``/``co_name``,
``f_back``) the real check reads.

CONTROLS, in the same tests: an otherwise-identical event with NO real
dir_fd -- a genuine CWD-relative reach, exactly what a careless test or a
bug in product code would raise -- still fails. The fix does not widen into
"a relative path is never a reach"."""
from __future__ import annotations

import os
import shutil
import sys

import pytest

from conftest import (
    _RealConfig,
    _SERVER_DIR,
    _dir_fd_given,
    _dir_fd_relative_positions,
    _open_is_fd_relative,
)


class _FakeCode:
    """The two ``code`` attributes ``_open_is_fd_relative`` reads."""

    def __init__(self, filename: str, name: str) -> None:
        self.co_filename = filename
        self.co_name = name


class _FakeFrame:
    """A minimal stand-in for a Python frame object: the attributes
    ``_open_is_fd_relative`` reads and nothing else. See the module
    docstring for why a real frame inside ``shutil.py`` cannot be produced
    on this platform."""

    def __init__(self, filename: str, name: str, back: "_FakeFrame | None" = None):
        self.f_code = _FakeCode(filename, name)
        self.f_back = back


# ------------------------------------------------------- _dir_fd_given


def test_dir_fd_given_tells_a_real_fd_from_both_of_cpythons_unset_sentinels():
    """CPython spells "no dir_fd" two different ways (module docstring): -1
    from the C-implemented os.* functions, None from shutil.rmtree's own
    pure-Python audit call. A real fd is always a non-negative int.

    MUTANT "sentinel widened to also accept -1" (``value >= 0`` changed to
    ``value >= -1``): RED on the third assertion, ``_dir_fd_given(-1)``
    reading True."""
    assert _dir_fd_given(7) is True
    assert _dir_fd_given(0) is True        # fd 0 is a real, valid descriptor
    assert _dir_fd_given(-1) is False       # the os.* "unset" sentinel
    assert _dir_fd_given(None) is False     # shutil.rmtree's "unset"
    assert _dir_fd_given(True) is False     # bool is an int subclass, not a fd


# ----------------------------------------------- _dir_fd_relative_positions


@pytest.mark.parametrize("event,args,want_positions", [
    ("os.rmdir", ("config", 7), {0}),
    ("os.rmdir", ("config", -1), set()),
    ("os.remove", ("config", 9), {0}),
    ("os.remove", ("config", None), set()),
    ("os.mkdir", ("config", 0o777, 11), {0}),
    ("os.mkdir", ("config", 0o777, -1), set()),
    ("shutil.rmtree", ("config", 13), {0}),
    ("shutil.rmtree", ("config", None), set()),
    ("os.rename", ("config", "other", 17, -1), {0}),
    ("os.rename", ("config", "other", -1, 19), {1}),
    ("os.rename", ("config", "other", -1, -1), set()),
    # No dir_fd slot in this event's own arguments at all (_DIR_FD_ARG_INDEX
    # has no entry for it): always unaffected, not even a position to skip.
    ("os.listdir", ("config",), set()),
])
def test_dir_fd_relative_positions_names_only_the_path_whose_call_used_one(
        event, args, want_positions):
    """RED under mutant "os.rename's second dir_fd ignored"
    (``_DIR_FD_ARG_INDEX["os.rename"]`` changed from ``(2, 3)`` to ``(2,)``):
    the ``("config", "other", -1, 19)`` case reads ``set()`` instead of
    ``{1}``, so ``dst`` (a dir_fd-relative rename target) would still be
    wrongly resolved against the CWD."""
    assert _dir_fd_relative_positions(event, args) == frozenset(want_positions)


# --------------------------------------------------------- _open_is_fd_relative


def test_open_is_fd_relative_true_inside_shutils_real_delete_walk():
    """The one frame signature that marks an ``open`` audit event as having
    come from an already-open directory fd, not the CWD: shutil's own
    ``_rmtree_safe_fd``.

    MUTANT "function name check dropped" (``_FD_RELATIVE_OPEN_FRAME``'s
    second element changed from ``"_rmtree_safe_fd"`` to ``""``, so the
    comparison no longer matches): RED, this assertion reading False."""
    frame = _FakeFrame(shutil.__file__, "_rmtree_safe_fd")
    assert _open_is_fd_relative(frame) is True


def test_open_is_fd_relative_matches_through_intermediate_frames():
    """The real shape: pytest's own tmp-dir cleanup calls shutil.rmtree,
    which calls ``_rmtree_safe_fd`` from its own driver loop -- so the
    matching frame is not always the immediate caller. The one check
    (shutil's ``_rmtree_safe_fd``) covers both halves of the issue's fix
    shape ("raised from inside shutil.rmtree or pytest's tmpdir cleanup")
    because pytest's cleanup is simply a caller of shutil.rmtree; no
    separate pytest-specific frame signature is needed."""
    shutil_frame = _FakeFrame(shutil.__file__, "_rmtree_safe_fd")
    pytest_cleanup_frame = _FakeFrame(
        "_pytest/pathlib.py", "rm_rf", back=shutil_frame)
    assert _open_is_fd_relative(pytest_cleanup_frame) is True


def test_open_is_fd_relative_false_for_an_unrelated_caller():
    """The control for the frame check alone: an ``open`` raised from
    ordinary code -- not inside shutil's delete walk -- is never
    suppressed, whatever its path looks like.

    MUTANT "frame walk stops looking and just assumes fd-relative"
    (``_open_is_fd_relative`` changed to ``return True``
    unconditionally): RED, this assertion reading True and, more to the
    point, ``test_open_of_a_bare_name_cwd_relative_is_still_a_reach``
    below going red too (a genuine reach would stop being caught)."""
    frame = _FakeFrame(__file__, "some_ordinary_test_function")
    assert _open_is_fd_relative(frame) is False


def _call_from_a_frame_that_reports_itself_as_shutils_rmtree_safe_fd(fn):
    """Call ``fn()`` from inside a REAL Python frame whose code object
    genuinely reports ``co_filename == shutil.__file__`` and ``co_name ==
    "_rmtree_safe_fd"`` -- not shutil's real function (which cannot be
    executed on this platform at all; see the module docstring), but a
    frame compiled fresh with its exact two identifying attributes, so the
    REAL ``conftest.audited`` dispatch (not just ``_open_is_fd_relative`` in
    isolation) sees, via its own ``sys._getframe(1)``, exactly what it
    would walking a genuine stack on Linux."""
    code = compile(
        "def _rmtree_safe_fd(_fn):\n    return _fn()\n",
        shutil.__file__, "exec")
    ns: dict = {}
    exec(code, ns)
    return ns["_rmtree_safe_fd"](fn)


# --------------------------------------------- end to end, via sys.audit


def test_rmdir_of_a_bare_name_relative_to_an_open_dir_fd_is_not_a_reach(
        monkeypatch):
    """The exact false positive from #659, reproduced through the real,
    already-installed session audit hook (the autouse
    ``_never_touch_the_real_config`` fixture arms it for the whole run):
    the event shape Linux's ``shutil.rmtree`` raises when it removes a
    child directory named "config" relative to an already-open parent --
    ``os.rmdir("config", dir_fd=<real fd>)`` -- must not be read as a reach
    just because ``os.path.abspath("config")`` against this process's CWD
    (server/) happens to equal the real config directory.

    The control, same test: an otherwise-identical event with NO dir_fd (a
    genuine CWD-relative ``os.rmdir("config")``) still fails, with the exact
    line the CI failure quoted.

    MUTANT (the dir_fd check removed from ``conftest.audited``, i.e. the
    guard back to always resolving ``args[:1]`` against the CWD): RED on
    the first assertion, ``_RealConfig.reads`` gaining "os.rmdir of ./ (the
    real config directory)" for the dir_fd-relative call too."""
    monkeypatch.chdir(_SERVER_DIR)
    with monkeypatch.context() as m:
        m.setattr(_RealConfig, "reads", [])

        sys.audit("os.rmdir", "config", 7)
        assert _RealConfig.reads == [], _RealConfig.reads

        sys.audit("os.rmdir", "config", -1)
        assert _RealConfig.reads == [
            "os.rmdir of ./ (the real config directory)"], _RealConfig.reads


def test_open_raised_from_a_real_shutil_frame_is_not_a_reach(monkeypatch):
    """The other half of #659's reported failure text, end to end: the
    exact ``open`` event shutil's fd-based walk raises while descending into
    a child named "config", dispatched through the REAL, already-installed
    ``conftest.audited`` (not just ``_open_is_fd_relative`` called directly)
    from a REAL frame that reports itself as shutil's ``_rmtree_safe_fd``
    (see ``_call_from_a_frame_that_reports_itself_as_shutils_rmtree_safe_fd``
    above for why that frame is compiled rather than produced by the real
    function, which cannot run on this platform at all).

    MUTANT (the open-event frame check removed, ``conftest.audited``
    treating ``open`` exactly like any other single-path event, always
    resolved against the CWD): RED, ``_RealConfig.reads`` gaining "open of
    ./ (the real config directory)" even though this call is fd-relative."""
    monkeypatch.chdir(_SERVER_DIR)
    with monkeypatch.context() as m:
        m.setattr(_RealConfig, "reads", [])

        _call_from_a_frame_that_reports_itself_as_shutils_rmtree_safe_fd(
            lambda: sys.audit("open", "config", None, os.O_RDONLY))
        assert _RealConfig.reads == [], _RealConfig.reads


def test_open_of_a_bare_name_cwd_relative_is_still_a_reach(monkeypatch):
    """The control: a GENUINE CWD-relative open of the real config
    directory -- ordinary code, not shutil's delete walk -- still fails.
    ``sys.audit`` called directly from this test function is not shutil's
    ``_rmtree_safe_fd``, so ``_open_is_fd_relative`` reads False here and
    the existing resolve-against-CWD path is unchanged.

    MUTANT "frame walk stops looking and just assumes fd-relative"
    (``_open_is_fd_relative`` changed to ``return True`` unconditionally):
    RED, ``_RealConfig.reads`` staying empty instead of naming the reach --
    a genuine bug would then slip past the guard entirely."""
    monkeypatch.chdir(_SERVER_DIR)
    with monkeypatch.context() as m:
        m.setattr(_RealConfig, "reads", [])

        sys.audit("open", "config", None, os.O_RDONLY)
        assert _RealConfig.reads == [
            "open of ./ (the real config directory)"], _RealConfig.reads
