# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The test-strength ratchets (#1121; members #978 and #1019): no new silent
handler under ``astrodeck``, no new source-reading pin, no new ``ast.walk``
pin, and the scanners that count them do what they say.

The allowlists and the scanners are in ``_ratchet.py``; this file runs them
over the real tree and over synthetic ones. A ratchet that cannot fail is
worthless, so the scanners are graded on sources written for the purpose (what
counts, what does not) and the verdict function on counts that grew and
counts that shrank.

NAMED MUTANT "one silent handler added" (a function ending in ``try: pass``
``except Exception: pass`` appended to ``astrodeck/sky_angle.py``, run from a
byte backup and restored with its sha256 checked): ``test_no_new_silent_handler
_under_astrodeck`` is RED, observed:

    AssertionError: silent handlers under astrodeck: more than the ratchet
    allows. ...
      ('sky_angle.py', '_mutant', 'Exception'): +1
"""
from __future__ import annotations

import pytest

import _ratchet as r


# ------------------------------------------------------------- the real tree

def test_no_new_silent_handler_under_astrodeck():
    problems = r.ratchet_problems(
        "silent handlers under astrodeck", r.scan_silent_handlers(),
        r.SILENT_HANDLERS,
        remedy="Say what was caught (Hub.say_swallowed or bus.log, the type's "
               "name and never its text), or put the reason in a comment "
               "beside the pass.")
    assert not problems, problems


def test_no_new_inspect_getsource_pin_in_the_tests():
    problems = r.ratchet_problems(
        "inspect.getsource uses in the tests", r.scan_tests(r.getsource_uses),
        r.GETSOURCE_USES,
        remedy="A test that reads source grades where code sits, not what it "
               "does. Drive the behaviour and assert on its effect.")
    assert not problems, problems


def test_no_new_ast_walk_pin_in_the_tests():
    problems = r.ratchet_problems(
        "ast.walk uses in the tests", r.scan_tests(r.ast_walk_uses),
        r.AST_WALK_USES,
        remedy="A test that walks a syntax tree to find a call grades the "
               "layout. Drive the behaviour and assert on its effect.")
    assert not problems, problems


def test_the_tables_hold_only_positive_counts():
    for name in ("SILENT_HANDLERS", "GETSOURCE_USES", "AST_WALK_USES"):
        table = getattr(r, name)
        assert table, f"{name} is empty: the scan would pass on nothing"
        assert all(n > 0 for n in table.values()), name


# ---------------------------------------------------- the silent-handler scan

def _silent(source: str) -> dict[tuple[str, str], int]:
    return r.silent_handlers(source)


@pytest.mark.parametrize("handler,kind", [
    ("except Exception:\n        pass", "Exception"),
    ("except:\n        pass", "bare"),
    ("except BaseException:\n        pass", "BaseException"),
    ("except (OSError, Exception):\n        pass", "(OSError, Exception)"),
    ("except Exception as e:\n        pass", "Exception"),
    ("except Exception:  # noqa: BLE001\n        pass", "Exception"),
    ("except Exception:\n        pass  # ok", "Exception"),
])
def test_a_silent_broad_handler_is_counted(handler, kind):
    src = f"def f():\n    try:\n        g()\n    {handler}\n"
    assert _silent(src) == {("f", kind): 1}, handler


@pytest.mark.parametrize("handler", [
    "except OSError:\n        pass",
    "except (ValueError, KeyError):\n        pass",
    "except Exception:\n        log(1)",
    "except Exception:\n        pass\n        log(1)",
    "except Exception:\n        raise",
    # the reason, in the places the convention puts it
    "except Exception:  # best effort: the caller retries\n        pass",
    "except Exception:\n        pass  # best effort: the caller retries",
    "except Exception:\n        # best effort: the caller retries\n        pass",
    "# best effort: the caller retries\n    except Exception:\n        pass",
])
def test_a_typed_handler_or_one_with_a_reason_is_not(handler):
    src = f"def f():\n    try:\n        g()\n    {handler}\n"
    assert _silent(src) == {}, handler


def test_a_lint_code_alone_is_not_a_reason():
    """``noqa: BLE001`` tells the linter to look away; it tells the reader
    nothing about why swallowing is right."""
    src = ("def f():\n    try:\n        g()\n"
           "    except Exception:  # noqa: BLE001\n        pass\n")
    assert _silent(src) == {("f", "Exception"): 1}


def test_a_hash_inside_a_string_is_not_a_reason():
    src = ("def f():\n    try:\n        g()\n    except Exception:\n"
           "        pass\n    x = 'this is a long string # not a comment'\n")
    assert _silent(src) == {("f", "Exception"): 1}


def test_handlers_are_keyed_by_the_innermost_function_and_counted():
    src = ("def outer():\n"
           "    def inner():\n"
           "        try:\n            g()\n        except Exception:\n            pass\n"
           "    try:\n        g()\n    except Exception:\n        pass\n"
           "    try:\n        g()\n    except Exception:\n        pass\n"
           "try:\n    g()\nexcept:\n    pass\n")
    assert _silent(src) == {("inner", "Exception"): 1,
                            ("outer", "Exception"): 2,
                            ("<module>", "bare"): 1}


# ------------------------------------------------- the source-reading counts

def test_getsource_counts_calls_and_not_mentions():
    src = ('import inspect\n'
           'from inspect import getsource as gs\n'
           'def t():\n'
           '    a = inspect.getsource(f)\n'
           '    b = inspect.getsourcelines(f)\n'
           '    # inspect.getsource(f) in a comment\n'
           '    c = "inspect.getsource(f) in a string"\n'
           '    d = getsource(f)\n'
           '    e = other.getsource(f)\n'
           '    return inspect.getsourcefile(f)\n')
    assert r.getsource_uses(src) == 4


def test_ast_walk_counts_the_ast_module_only():
    src = ('import ast, os\n'
           'def t(tree):\n'
           '    a = list(ast.walk(tree))\n'
           '    b = [n for n in ast.walk(tree)]\n'
           '    c = list(os.walk("."))\n'
           '    d = list(ast.iter_child_nodes(tree))\n'
           '    # ast.walk(tree)\n')
    assert r.ast_walk_uses(src) == 2


# ------------------------------------------------------------ the verdict

def test_a_count_that_grew_fails_and_names_the_place():
    msg = r.ratchet_problems("things", {"a.py": 3, "b.py": 1}, {"a.py": 2, "b.py": 1},
                             remedy="fix it")
    assert "more than the ratchet allows" in msg and "fix it" in msg
    assert "a.py: +1" in msg and "b.py" not in msg


def test_a_new_place_fails_with_an_allowance_of_zero():
    msg = r.ratchet_problems("things", {"a.py": 1, "new.py": 1}, {"a.py": 1},
                             remedy="fix it")
    assert "new.py: +1" in msg


def test_a_count_that_shrank_fails_and_asks_for_the_table_to_follow():
    msg = r.ratchet_problems("things", {"a.py": 1}, {"a.py": 2, "gone.py": 1},
                             remedy="fix it")
    assert "fewer than the ratchet allows" in msg and "tests/_ratchet.py" in msg
    assert "a.py: -1" in msg and "gone.py: -1" in msg


def test_equal_counts_pass():
    assert r.ratchet_problems("things", {"a.py": 2}, {"a.py": 2}, remedy="x") == ""


# ---------------------------------------- one new handler in a package file

def _package(tmp_path, body: str):
    pkg = tmp_path / "astrodeck"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "sub" / "mod.py").write_text(body, encoding="utf-8")
    (pkg / "clean.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    return pkg


_ONE = ("def run():\n    try:\n        go()\n    except Exception:\n"
        "        pass\n")


def test_one_silent_handler_added_to_a_package_file_fails_the_ratchet(tmp_path):
    """THE MUTANT, without touching the real tree: a package whose ratchet was
    set with one handler, then a second lands in the same function."""
    pkg = _package(tmp_path, _ONE)
    allowed = {("sub/mod.py", "run", "Exception"): 1}
    assert r.ratchet_problems("h", r.scan_silent_handlers(pkg), allowed,
                              remedy="x") == ""
    (pkg / "sub" / "mod.py").write_text(
        _ONE + "    try:\n        go()\n    except Exception:\n        pass\n",
        encoding="utf-8")
    msg = r.ratchet_problems("h", r.scan_silent_handlers(pkg), allowed, remedy="x")
    assert "('sub/mod.py', 'run', 'Exception'): +1" in msg, msg


def test_a_handler_added_in_a_file_that_had_none_fails_the_ratchet(tmp_path):
    pkg = _package(tmp_path, _ONE)
    allowed = {("sub/mod.py", "run", "Exception"): 1}
    (pkg / "clean.py").write_text(_ONE.replace("run", "fresh"), encoding="utf-8")
    msg = r.ratchet_problems("h", r.scan_silent_handlers(pkg), allowed, remedy="x")
    assert "('clean.py', 'fresh', 'Exception'): +1" in msg, msg


def test_a_handler_given_a_reason_or_removed_asks_for_the_table_to_shrink(tmp_path):
    pkg = _package(tmp_path, _ONE)
    allowed = {("sub/mod.py", "run", "Exception"): 1}
    (pkg / "sub" / "mod.py").write_text(
        _ONE.replace("        pass\n", "        pass  # best effort: the caller retries\n"),
        encoding="utf-8")
    msg = r.ratchet_problems("h", r.scan_silent_handlers(pkg), allowed, remedy="x")
    assert "('sub/mod.py', 'run', 'Exception'): -1" in msg, msg
