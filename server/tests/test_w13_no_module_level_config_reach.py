# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#675 (continued, integration follow-up to WP-H2, backlog wave 13,
owner-approved plan 2026-09-30): a static sweep for the ROOT CAUSE class
WP-H2 could only route around, not fix -- a module-level statement in a test
file that reaches ``astrodeck.config.config_store`` (or
``astrodeck.sequence.schedule.observing_night``, or constructs a bare
``ConfigStore()``) AT IMPORT, before any fixture -- even a session-scoped,
autouse one -- gets a chance to run.

test_w1_cooling_restore_daylight.py's `NIGHT_TS = _night_midpoint()` was the
TRACED instance (conftest.py's ``_arm_real_config_guard_before_collection``
docstring has the full mechanism and the observed xdist race); that file's
own fix is a lazily-cached ``_night_ts()`` function plus a module-scoped
autouse fixture that runs the old module-level self-check asserts at first
test setup instead of at import. This file is the SWEEP that proves it was
the only instance in the suite, and the GUARD that catches a future one
appearing anywhere else, including this reach through an intervening
LOCALLY-DEFINED helper function (the actual shape of the bug: the
module-level statement called ``_night_midpoint()``, not
``schedule.observing_night`` directly -- a textual grep for the latter would
have missed it).

WHY AN AST SWEEP RATHER THAN THE CONFTEST WATCHER: the watcher
(``_RealConfig``/``_watch_the_real_config``) only fires when something
reaches the REAL config location -- and after WP-H2's redirect, nothing at
module level can do that any more regardless of whether this specific class
of statement exists, because ``config_store`` is already pointed at a
throwaway file before any test module imports. So a future regression of
this shape would land on the throwaway store, pass every existing guard, and
still be exactly the fragile pattern #675 was about: a value computed from
shared global state at an uncontrolled time, impossible to override per-test,
and (on the day someone removes WP-H2's redirect, believing it no longer
necessary) a straight reversion to the original bug. This sweep catches the
PATTERN itself, independent of whether today's redirect happens to make it
harmless.

CANNOT PASS VACUOUSLY: ``test_sweep_finds_the_known_bad_shape`` below runs
the exact same scanner against a literal reproduction of the old
`NIGHT_TS = _night_midpoint()` shape and asserts it is flagged, so a scanner
that silently matches nothing (an empty glob, a broken binding lookup, a
mis-indented walk that never descends) fails loudly here rather than
reporting a clean sweep by accident.
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent

#: Reads that must never happen at import: an attribute access resolving to
#: one of these names on an object bound (via ``collect_bindings``) to the
#: config singleton, or a bare call to a name bound to ``observing_night`` or
#: ``ConfigStore``.
_STORE_ATTRS = ("cfg", "_load", "_save")


def _collect_bindings(tree: ast.Module) -> dict[str, str]:
    """Map a name this module binds, at its own top level, to what it is --
    ``"config_store"``, ``"ConfigStore"``, ``"observing_night"`` or
    ``"schedule_module"`` -- or leave it out of the map entirely. Only the
    import SHAPES this codebase actually uses are recognised; a new import
    shape that evades this is a gap to extend, not a reason to distrust the
    ones already covered (the vacuity test below pins the shape that was
    actually observed)."""
    bindings: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                name = alias.asname or alias.name
                if mod == "astrodeck.config":
                    if alias.name == "config_store":
                        bindings[name] = "config_store"
                    elif alias.name == "ConfigStore":
                        bindings[name] = "ConfigStore"
                elif mod == "astrodeck.sequence.schedule":
                    if alias.name == "observing_night":
                        bindings[name] = "observing_night"
                elif mod == "astrodeck.sequence":
                    if alias.name == "schedule":
                        bindings[name] = "schedule_module"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "astrodeck.config":
                    bindings[alias.asname or "astrodeck"] = "config_module_root"
    return bindings


class _DirectCallCollector(ast.NodeVisitor):
    """Every ``Call`` within a statement's own execution, WITHOUT crossing
    into a nested function/async-function/lambda/class body -- those don't
    run now; a call made to them is a separate, later event, and is caught
    at ITS OWN call site instead, not here."""

    def __init__(self) -> None:
        self.calls: list[ast.Call] = []

    def visit_FunctionDef(self, node):  # noqa: N802 - ast visitor name
        pass

    def visit_AsyncFunctionDef(self, node):  # noqa: N802
        pass

    def visit_Lambda(self, node):  # noqa: N802
        pass

    def visit_ClassDef(self, node):  # noqa: N802
        pass

    def visit_Call(self, node):  # noqa: N802
        self.calls.append(node)
        self.generic_visit(node)


def _direct_calls(node: ast.AST) -> list[ast.Call]:
    c = _DirectCallCollector()
    c.visit(node)
    return c.calls


def _direct_calls_in_body(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    """Same as ``_direct_calls``, but for a function/async-function's own
    BODY rather than the def node itself -- ``_direct_calls(func)`` would
    dispatch straight to ``_DirectCallCollector.visit_FunctionDef``, which is
    a deliberate no-op (so a call made TO such a def, from elsewhere, is not
    also counted as a call made BY it), and so would never descend into the
    body at all."""
    calls: list[ast.Call] = []
    for stmt in func.body:
        calls.extend(_direct_calls(stmt))
    return calls


def _call_target(call: ast.Call):
    """A tuple describing what ``call.func`` is, shallow enough to match
    against ``bindings`` without needing a full name-resolution pass:
    ``("name", id)`` for ``f()``, ``("attr", base_id, attr)`` for
    ``base.attr()``, ``("attr2", base_id, mid_attr, attr)`` for
    ``base.mid.attr()`` (e.g. ``config_mod.config_store.cfg()``)."""
    func = call.func
    if isinstance(func, ast.Name):
        return ("name", func.id)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return ("attr", func.value.id, func.attr)
    if (isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and isinstance(func.value.value, ast.Name)):
        return ("attr2", func.value.value.id, func.value.attr, func.attr)
    return None


def _direct_reach(call: ast.Call, bindings: dict[str, str]) -> str | None:
    """Non-``None`` (a human-readable description) iff this ONE call is
    itself a reach -- never mind what it's nested inside."""
    t = _call_target(call)
    if t is None:
        return None
    if t[0] == "name":
        kind = bindings.get(t[1])
        if kind in ("observing_night", "ConfigStore"):
            return f"{t[1]}()"
    elif t[0] == "attr":
        kind = bindings.get(t[1])
        if kind == "config_store" and t[2] in _STORE_ATTRS:
            return f"{t[1]}.{t[2]}()"
        if kind == "schedule_module" and t[2] == "observing_night":
            return f"{t[1]}.{t[2]}()"
    elif t[0] == "attr2":
        kind = bindings.get(t[1])
        if kind == "config_module_root" and t[2] == "config_store" and t[3] in _STORE_ATTRS:
            return f"{t[1]}.config_store.{t[3]}()"
    return None


def _local_call_name(call: ast.Call) -> str | None:
    t = _call_target(call)
    return t[1] if t and t[0] == "name" else None


def _reach_closure(tree: ast.Module, bindings: dict[str, str]) -> dict[str, str]:
    """For every top-level ``def`` in this module, whether CALLING it
    reaches a tracked seam -- directly, or transitively through another
    top-level function it calls (arbitrary depth, e.g. ``NIGHT_TS =
    _night_midpoint()`` where ``_night_midpoint`` calls
    ``schedule.observing_night`` -- two hops, neither of them the bound name
    a plain text search for ``config_store`` or ``observing_night`` would
    have to be told to also look for). Maps a reaching function's name to a
    one-line reason."""
    funcs = {n.name: n for n in tree.body
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    reasons: dict[str, str] = {}

    def resolve(name: str, seen: frozenset[str]) -> str | None:
        if name in reasons:
            return reasons[name]
        if name not in funcs or name in seen:
            return None
        node = funcs[name]
        for call in _direct_calls_in_body(node):
            hit = _direct_reach(call, bindings)
            if hit:
                reasons[name] = f"calls {hit}"
                return reasons[name]
            called = _local_call_name(call)
            if called:
                sub = resolve(called, seen | {name})
                if sub:
                    reasons[name] = f"calls {called}() which {sub}"
                    return reasons[name]
        return None

    for name in list(funcs):
        resolve(name, frozenset())
    return reasons


def _module_level_statements(tree: ast.Module):
    """Every statement that RUNS at import: the module's own top-level body,
    plus -- recursively -- the top-level body of any class defined there (a
    class body executes when the class object is created, i.e. at import;
    only the METHODS inside it wait to be called)."""
    def walk(body):
        for stmt in body:
            yield stmt
            if isinstance(stmt, ast.ClassDef):
                yield from walk(stmt.body)
    yield from walk(tree.body)


def find_violations(source: str, filename: str = "<string>") -> list[tuple[int, str]]:
    """Every ``(lineno, description)`` of a module-level (or class-body) call
    graph that reaches ``config_store.cfg``/``_load``/``_save``,
    ``ConfigStore()``, or ``schedule.observing_night`` -- directly, through a
    locally-defined helper (any depth), through a decorator call, or through
    a function/method's own default-argument value (all three run at
    import/class-creation time, same as a plain top-level statement)."""
    tree = ast.parse(source, filename=filename)
    bindings = _collect_bindings(tree)
    if not bindings:
        return []
    reach_reason = _reach_closure(tree, bindings)

    def scan(targets: list[ast.AST], lineno: int, out: list[tuple[int, str]]) -> None:
        for t in targets:
            for call in _direct_calls(t):
                hit = _direct_reach(call, bindings)
                if hit:
                    out.append((lineno, hit))
                    continue
                called = _local_call_name(call)
                if called and called in reach_reason:
                    out.append((lineno, f"{called}() -> {reach_reason[called]}"))

    violations: list[tuple[int, str]] = []
    for stmt in _module_level_statements(tree):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            targets = (list(stmt.decorator_list) + list(stmt.args.defaults)
                       + [d for d in stmt.args.kw_defaults if d is not None])
            scan(targets, stmt.lineno, violations)
        elif isinstance(stmt, ast.ClassDef):
            targets = (list(stmt.decorator_list) + list(stmt.bases)
                       + [kw.value for kw in stmt.keywords])
            scan(targets, stmt.lineno, violations)
        else:
            scan([stmt], stmt.lineno, violations)
    return violations


def test_sweep_finds_the_known_bad_shape():
    """Proves the scanner is not vacuous: a literal reproduction of the OLD
    `NIGHT_TS = _night_midpoint()` shape (module-level assignment calling a
    LOCAL helper that calls ``schedule.observing_night``, plus the old
    module-level self-check that called ``config_store.cfg()`` through
    ``_threshold()``) must be flagged, with the two-hop chain named."""
    src = textwrap.dedent("""
        from astrodeck.config import config_store
        from astrodeck.sequence import schedule

        def _threshold():
            return config_store.cfg().foo

        def _night_midpoint():
            dusk, dawn = schedule.observing_night({}, None, 0.0)
            return (dusk + dawn) / 2

        NIGHT_TS = _night_midpoint()
        assert NIGHT_TS < _threshold()
    """)
    violations = find_violations(src, filename="<known-bad>")
    joined = "\n".join(f"{ln}: {desc}" for ln, desc in violations)
    assert any("_night_midpoint" in desc and "observing_night" in desc
               for _ln, desc in violations), (
        "scanner did not flag the known-bad NIGHT_TS shape:\n" + joined)
    assert any("_threshold" in desc and "cfg()" in desc
               for _ln, desc in violations), (
        "scanner did not flag the known-bad self-check shape:\n" + joined)


def test_sweep_does_not_flag_a_lazy_equivalent():
    """The fixed shape -- the same two helpers, but called only from inside
    a function/fixture, never at module level -- must NOT be flagged (a
    scanner that flags everything is as useless as one that flags nothing)."""
    src = textwrap.dedent("""
        import pytest
        from astrodeck.config import config_store
        from astrodeck.sequence import schedule

        def _threshold():
            return config_store.cfg().foo

        def _night_midpoint():
            dusk, dawn = schedule.observing_night({}, None, 0.0)
            return (dusk + dawn) / 2

        _CACHE = None

        def _night_ts():
            global _CACHE
            if _CACHE is None:
                _CACHE = _night_midpoint()
            return _CACHE

        @pytest.fixture(scope="module", autouse=True)
        def _self_check():
            assert _night_ts() < _threshold()
            yield

        def test_something():
            assert _night_ts() is not None
    """)
    assert find_violations(src, filename="<lazy-ok>") == []


def test_no_server_test_module_reaches_config_at_import():
    """The actual sweep: every ``server/tests/*.py`` file, scanned for the
    pattern pinned above. A hit here means a NEW module-level statement
    reaches the shared config singleton (or constructs a bare
    ``ConfigStore()``, or calls ``observing_night``) at import -- make it
    lazy instead, the same way this wave's own fix did for
    test_w1_cooling_restore_daylight.py (a cached function called from a
    fixture or the tests that need it, never a module-level statement).

    Named mutant: test_w1_cooling_restore_daylight.py's `NIGHT_TS =
    _night_midpoint()` plus its module-level self-check asserts restored (the
    eager shape this wave replaced) -- see that file's own history; this
    test is expected to go RED again if that ever happens, naming the exact
    file and the two-hop chain."""
    violations: list[tuple[Path, int, str]] = []
    for path in sorted(_TESTS_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for lineno, desc in find_violations(source, filename=str(path)):
            violations.append((path, lineno, desc))
    assert violations == [], "module-level config reach(es) found:\n" + "\n".join(
        f"{p.name}:{ln}: {desc}" for p, ln, desc in violations)
