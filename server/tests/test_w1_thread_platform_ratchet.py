# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A ratchet on the stdlib's WMI-backed `platform` calls (#757, #1122).

THE HAZARD. On Windows (CPython 3.12.10) `platform.machine()`, `uname()` and
the calls built on them ask WMI the first time they run, through `_wmi`. From
a worker thread that first call can end the process with a segmentation fault
AFTER the test or the night has printed its result: 22 of 25 processes with
eight threads racing it under 40 CPU burners, 3 of 25 behind a lock, 0 of 25
when the first call is made on the main thread. The root is the stdlib, not
this package, so the only defence is not to make the call from code a worker
thread can reach.

THE RATCHET. Every reference to one of those calls under `server/astrodeck`
is found by walking the syntax trees, and the set of places that make one
must equal `ALLOWED`: today only `devices/sdk_paths._machine`, which reads the
environment on Windows, and elsewhere takes the answer once under a lock
(test_w15_platform_tag_once.py holds its behaviour). A new caller fails here
with its file, line and name, and must either go through `sdk_paths` or be
added to `ALLOWED` by a change that says why its first call is safe. The scan
reads references, not only calls: `probe = platform.machine` followed by
`probe()` is the shape the one allowed site has.

`test_w15_platform_tag_once.py` pins the allowed site from the outside; the
threaded case below adds the claim #757 is about: a first read from a worker
thread on Windows never reaches `platform.machine`.

NAMED MUTANT "the scanner reads calls only" (the `ast.Attribute` branch of
`wmi_backed_references` skipped unless its parent is a call): the indirect
reference case is RED, observed:

    AssertionError: the scanner missed a platform reference that is not a
    call: {('new.py', 'asks', 5, 'uname')}
"""
from __future__ import annotations

import ast
import threading
from pathlib import Path


from astrodeck.devices import sdk_paths

PACKAGE = Path(__file__).resolve().parents[1] / "astrodeck"

#: The `platform` functions that reach `_wmi` on Windows, directly or through
#: `platform.uname()`.
WMI_BACKED = frozenset({
    "machine", "uname", "processor", "platform", "system", "node", "release",
    "version", "win32_ver", "win32_edition", "win32_is_iot",
})

#: ``(path under astrodeck, enclosing function)`` of every reference the
#: package may make. `sdk_paths._machine` reads the environment on Windows
#: and holds a lock around the one other first call.
ALLOWED = {("devices/sdk_paths.py", "_machine")}


def wmi_backed_references(source: str, rel: str) -> set[tuple[str, str, int, str]]:
    """``(rel, enclosing function, line, name)`` for each reference to a
    WMI-backed `platform` function in ``source``: `platform.machine`,
    `plat.uname` for `import platform as plat`, and `from platform import
    machine`. A function-valued reference counts as much as a call."""
    tree = ast.parse(source)
    modules = {"platform"}
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.asname for a in node.names
                        if a.name == "platform" and a.asname}
        elif isinstance(node, ast.ImportFrom) and node.module == "platform":
            for a in node.names:
                if a.name in WMI_BACKED:
                    names[a.asname or a.name] = a.name
    found: set[tuple[str, str, int, str]] = set()

    def walk(node: ast.AST, enclosing: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = (child.name if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)) else enclosing)
            if (isinstance(child, ast.Attribute) and child.attr in WMI_BACKED
                    and isinstance(child.value, ast.Name)
                    and child.value.id in modules):
                found.add((rel, enclosing, child.lineno, child.attr))
            elif isinstance(child, ast.Name) and child.id in names:
                found.add((rel, enclosing, child.lineno, names[child.id]))
            walk(child, inner)

    walk(tree, "<module>")
    return found


def test_the_package_makes_no_wmi_backed_platform_call_but_the_allowed_one():
    """The ratchet itself, over the real package."""
    seen: set[tuple[str, str, int, str]] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        seen |= wmi_backed_references(path.read_text(encoding="utf-8"), rel)
    stray = sorted(r for r in seen if (r[0], r[1]) not in ALLOWED)
    assert not stray, (
        "a WMI-backed platform call outside the allowed site; its first call "
        "from a worker thread can segfault the process on Windows (#757). Go "
        "through devices.sdk_paths, or add the site to ALLOWED with the "
        f"reason its first call is safe: {stray}")
    assert {(r[0], r[1]) for r in seen} == ALLOWED, (
        "the allowed site no longer makes the call; trim ALLOWED so the "
        f"ratchet keeps meaning something: {sorted(seen)}")


def test_the_scanner_finds_a_call_made_from_a_worker_thread():
    """A new `platform.machine()` on a thread's target is what #757 is about."""
    source = ("import platform\n"
              "import threading\n"
              "def work():\n"
              "    return platform.machine()\n"
              "threading.Thread(target=work).start()\n")
    assert wmi_backed_references(source, "new.py") == {
        ("new.py", "work", 4, "machine")}


def test_the_scanner_finds_a_reference_that_is_not_a_call():
    """The allowed site binds `probe = platform.machine` and calls `probe()`;
    a scanner that reads calls only would not see the same shape elsewhere.

    MUTATION: the scanner reads calls only. Observed: the assertion below
    fails: the `plat.machine` reference on line 4 is missing from the set.
    """
    source = ("import platform as plat\n"
              "from platform import uname as who\n"
              "def asks():\n"
              "    probe = plat.machine\n"
              "    return probe(), who\n")
    found = wmi_backed_references(source, "new.py")
    assert found == {("new.py", "asks", 4, "machine"),
                     ("new.py", "asks", 5, "uname")}, (
        f"the scanner missed a platform reference that is not a call: {found}")


def test_the_scanner_ignores_calls_that_do_not_reach_wmi():
    source = ("import platform\n"
              "def tag():\n"
              "    return platform.python_version(), platform.python_implementation()\n")
    assert wmi_backed_references(source, "new.py") == set()


def test_a_first_read_from_a_worker_thread_on_windows_never_asks_platform(monkeypatch):
    """The claim itself: on Windows the environment names the machine, so a
    worker thread that reads the tag first makes no `platform.machine()` call.

    MUTATION: in `sdk_paths._machine`, `if native:` becomes `if False:`.
    Observed: the worker thread's read reached the replaced
    `platform.machine` and the assertion below names the thread.
    """
    monkeypatch.setattr(sdk_paths.sys, "platform", "win32")
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")
    monkeypatch.delenv("PROCESSOR_ARCHITEW6432", raising=False)
    asked: list[str] = []

    def machine() -> str:
        asked.append(threading.current_thread().name)
        return "AMD64"

    monkeypatch.setattr(sdk_paths.platform, "machine", machine)
    monkeypatch.setattr(sdk_paths, "_machine_cache", None)
    tags: list[str] = []
    worker = threading.Thread(target=lambda: tags.append(sdk_paths.platform_tag()),
                              name="solve-resolver")
    worker.start()
    worker.join(timeout=30)
    assert not worker.is_alive(), "the worker never returned"
    assert tags == ["win-x64"], tags
    assert asked == [], f"platform.machine() was called from {asked}"
