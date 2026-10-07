# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""`platform_tag` never asks WMI on Windows, and asks the OS what machine this
is at most once, and never from two threads at the same moment, anywhere else
(#699).

THE DEFECT WAS NOT WHAT ITS ISSUE SUSPECTED. #699 read the faulthandler dumps
("Windows fatal exception: code 0x8007000e", stacks through `platform.py`
`_wmi_query`) as `platform.machine()` querying WMI on EVERY call. Measured on
CPython 3.12.10 / Windows 11: it does not. `platform.uname()` caches its answer
after the first call that completes, and the first call costs about 140 ms and
two WMI queries. What is wrong is the FIRST call, and that nothing serialises
it: `platform_tag` is reached from `find_astap` on every solve resolve, from
worker threads, so several threads can be inside that first call at once, and
six racing threads issued twelve concurrent WMI queries (the dumps, and once in
a hundred under load an `OSError: [Errno 9]` at pytest's faulthandler
unconfigure, with a UTF-16 WMI string at the head of the output).

MEASURED UNDER 40 CPU BURNERS (24 cores), eight threads racing the first read,
25 runs each, process exit status: the stdlib's `platform.machine()` alone, 22
segmentation faults; behind a lock and cache, 3; with the first call on the
main thread, 0; `platform_tag` as it now is (the environment on Windows, no
WMI), 0. A lock is not the cure, it is what is left for a machine that has no
`PROCESSOR_ARCHITECTURE`, and for every other platform.

So `sdk_paths._machine` reads `PROCESSOR_ARCHITEW6432` / `PROCESSOR_ARCHITECTURE`
on Windows (what CPython itself falls back on when WMI fails), and elsewhere
caches the answer AND holds a lock around the first read; a cache alone would
not do, since `functools.lru_cache` does not stop two threads computing the
same missing key at once.

The cache is keyed on the CALLABLE it read the answer from (`platform.machine`
itself). The machine does not change under a running process, but this
module's own tests (`test_sdk_paths.py`) and `test_astap_bundle.py` replace
`platform.machine` to ask what the tag would be on another box, and they must
keep getting a fresh answer without knowing a cache exists.

Mutants, each applied from a byte backup and restored (sha256 compared):

MUTANT "the answer is never kept" (``_machine_cache = (probe, answer)``
replaced by ``_machine_cache = None`` in `_machine`, so every call asks):
`test_the_machine_is_asked_once_however_often_the_tag_is_read`,
`test_racing_threads_ask_the_machine_once`,
`test_the_solver_search_does_not_ask_again` and
`test_windows_without_the_variables_asks_once` are RED (observed):
    E   AssertionError: platform.machine() was called 40 times for 40 reads
    of the tag; it is a fact about the process
    E   assert 40 == 1
    E   AssertionError: 8 threads reading the tag at once made 8 calls to
    platform.machine(); the first read must be serialised
    E   assert 8 == 1
    E   assert 10 == 1
MUTANT "a cache without the lock" (``with _MACHINE_LOCK:`` replaced by
``if True:``): only `test_racing_threads_ask_the_machine_once` is RED
(observed):
    E   AssertionError: 8 threads reading the tag at once made 8 calls to
    platform.machine(); the first read must be serialised
    E   assert 8 == 1
MUTANT "Windows asks WMI" (``if native:`` replaced by ``if False:`` in
`_machine`, so the environment is read and ignored): the four
`test_windows_never_asks_wmi` cases are RED (observed):
    E   AssertionError: platform.machine() was asked on Windows, where it is
    a WMI query
"""
from __future__ import annotations

import threading
import time

import pytest

from astrodeck.devices import sdk_paths
from astrodeck.solve import astap


@pytest.fixture
def counting_machine(monkeypatch):
    """A stand-in for ``platform.machine`` that counts its calls, answers for
    a Linux x86-64 box, and takes long enough that a second thread arriving
    during the first call is certain to overlap it."""
    monkeypatch.setattr(sdk_paths.sys, "platform", "linux")
    calls: list[str] = []

    def machine() -> str:
        calls.append(threading.current_thread().name)
        time.sleep(0.05)
        return "X86_64"
    monkeypatch.setattr(sdk_paths.platform, "machine", machine)
    return calls


def test_the_machine_is_asked_once_however_often_the_tag_is_read(
        counting_machine):
    tags = {sdk_paths.platform_tag() for _ in range(40)}
    assert tags == {"linux-x86_64"}, tags
    assert len(counting_machine) == 1, (
        f"platform.machine() was called {len(counting_machine)} times for 40 "
        f"reads of the tag; it is a fact about the process")


def test_racing_threads_ask_the_machine_once(counting_machine):
    """The first read is the expensive and the unsafe one, and in the server
    it is made by whichever solve-resolving worker thread gets there first."""
    n = 8
    arrive = threading.Barrier(n)
    tags: list[str] = []

    def read() -> None:
        arrive.wait()
        tags.append(sdk_paths.platform_tag())
    threads = [threading.Thread(target=read, name=f"reader-{i}")
               for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a reader never returned"
    assert tags == ["linux-x86_64"] * n, tags
    assert len(counting_machine) == 1, (
        f"{n} threads reading the tag at once made {len(counting_machine)} "
        f"calls to platform.machine(); the first read must be serialised")


def test_the_solver_search_does_not_ask_again(counting_machine):
    """The path #699 was seen on: every solve resolve calls ``find_astap``,
    which builds the bundled-binary candidates from the tag. Through the real
    consumer, so a future caller that stops going through the cache shows."""
    for _ in range(10):
        astap._bundled_candidates()
    assert len(counting_machine) == 1, counting_machine


def test_a_replaced_probe_is_asked_afresh(monkeypatch):
    """What lets ``test_sdk_paths.py`` fake other machines: a different
    ``platform.machine`` is a different question, never answered from the
    cache of the last one."""
    monkeypatch.setattr(sdk_paths.sys, "platform", "linux")
    monkeypatch.setattr(sdk_paths.platform, "machine", lambda: "aarch64")
    assert sdk_paths.platform_tag() == "linux-arm64"
    monkeypatch.setattr(sdk_paths.platform, "machine", lambda: "x86_64")
    assert sdk_paths.platform_tag() == "linux-x86_64"
    # and the platform itself is read on every call, not remembered
    monkeypatch.setattr(sdk_paths.sys, "platform", "darwin")
    assert sdk_paths.platform_tag() == "macos"


# ---------------------------------------------------------------- Windows

@pytest.fixture
def windows(monkeypatch):
    """A Windows process whose ``platform.machine`` is WMI: asking it fails
    the test, and the environment names the machine."""
    monkeypatch.setattr(sdk_paths.sys, "platform", "win32")
    monkeypatch.delenv("PROCESSOR_ARCHITEW6432", raising=False)
    monkeypatch.delenv("PROCESSOR_ARCHITECTURE", raising=False)

    def wmi() -> str:
        raise AssertionError(
            "platform.machine() was asked on Windows, where it is a WMI query")
    monkeypatch.setattr(sdk_paths.platform, "machine", wmi)
    return monkeypatch


@pytest.mark.parametrize("architecture,native,expected", [
    ("AMD64", None, "win-x64"),
    ("x86", None, "win-x86"),
    ("ARM64", None, "win-x64"),
    # A 32-bit process on 64-bit Windows: the variable it sees masks the
    # machine, and the native one names it.
    ("x86", "AMD64", "win-x64"),
])
def test_windows_never_asks_wmi(windows, architecture, native, expected):
    windows.setenv("PROCESSOR_ARCHITECTURE", architecture)
    if native:
        windows.setenv("PROCESSOR_ARCHITEW6432", native)
    assert [sdk_paths.platform_tag() for _ in range(3)] == [expected] * 3


def test_windows_without_the_variables_asks_once(windows):
    """A service context with no ``PROCESSOR_*`` still gets an answer, from
    the one place left, once."""
    calls: list[int] = []

    def machine() -> str:
        calls.append(1)
        return "AMD64"
    windows.setattr(sdk_paths.platform, "machine", machine)
    assert [sdk_paths.platform_tag() for _ in range(5)] == ["win-x64"] * 5
    assert len(calls) == 1, calls
