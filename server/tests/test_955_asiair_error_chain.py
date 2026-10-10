# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#955: an ASIAIR ``DeviceError`` chains nothing from libasi.

#927 stopped the box's own words reaching a ``DeviceError`` message and
``last_error``, but ``_Link.call`` and ``_Link.connect`` still raised
``from exc``, so libasi's exception (whose text IS the box's ``error`` string,
and can quote a mount position) sat on ``__cause__``. Any printed traceback of
the ``DeviceError`` re-quoted it: asyncio's "Task exception was never
retrieved", uvicorn's default handler, a ``logging.exception``. At home a
position is a site oracle (#140, #166).

``AsiairTelescope.sync`` read that cause to tell a refusal from a dead link
from an unclear answer, so the cause could not simply be dropped. The failure
is now classified where it is caught (``AsiairCallError.cause_kind``:
``busy`` / ``link`` / ``other``), the error is raised outside the handler and
``from None``, and ``sync`` reads the attribute.

These cases grade what a person reading a printed traceback would see
(``traceback.format_exception``), through the real ``_Link`` and the real
ASIAIR telescope over the repo's fake libasi client. The made-up position is
injected into the libasi text only. Every figure below is made up.
"""
from __future__ import annotations

import traceback

import pytest

import astrodeck.devices.backends.asiair_backend as ab
import astrodeck.hub as hub_module
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sync_verify import (SYNC_REPLY_BUSY,
                                           SYNC_UNVERIFIED_LINK_BEFORE,
                                           SYNC_UNVERIFIED_LINK_DURING,
                                           SYNC_UNVERIFIED_UNCLEAR,
                                           SyncRefused, SyncUnverified)

from test_906_907_driver_error_texts import BODY, RA_HMS, TELLS
from test_926_927_driver_error_texts import FAILURES, ASIAIRError
from test_asiair_backend import BusyError, _conn, _open, fake  # noqa: F401 (fixture)


def _printed(exc: BaseException) -> str:
    """Everything a printed traceback of ``exc`` would say."""
    return "".join(traceback.format_exception(exc))


def _assert_nothing_quoted(exc: BaseException) -> None:
    printed = _printed(exc)
    for tell in TELLS:
        assert tell not in printed, f"{tell!r} in {printed!r}"
    assert BODY not in printed, printed
    # The libasi exception is on neither link of the chain, not merely
    # hidden from the printer.
    assert exc.__cause__ is None and exc.__context__ is None


@pytest.mark.parametrize("make, named", FAILURES)
async def test_a_failed_call_prints_no_libasi_text(fake, make, named):
    """The traceback of a failed call names the class and the call and carries
    none of the box's text.

    MUTANT C1 "the cause chained back" (the failure built in the handler of
    ``_Link.call`` raised there as ``raise AsiairCallError(...) from exc``,
    the old shape): RED, the made-up position is in the printed traceback.
    MUTANT C5 "raised inside the handler" (the same raise as ``... from
    None``): RED on the ``__context__`` assertion of ``_assert_nothing_quoted``,
    although the printer already hides it."""
    injected = make()

    session = await _open()
    tel = await session.get_device("telescope", _conn())

    def info():
        raise injected

    fake.mount.info = info      # after connect, which reads the mount too
    try:
        with pytest.raises(DeviceError) as exc:
            await tel.get_position()
        _assert_nothing_quoted(exc.value)
        assert named in _printed(exc.value), _printed(exc.value)
        assert exc.value.cause_class == type(injected).__name__
    finally:
        await session.close()


@pytest.mark.parametrize("make, named", FAILURES)
async def test_a_failed_connect_prints_no_libasi_text(fake, make, named):
    """The same for ``_Link.connect``.

    MUTANT C2 "the cause chained back on connect" (the failure built in the
    handler of ``_Link.connect`` raised there as ``raise AsiairCallError(...)
    from exc``): RED."""
    injected = make()

    def connect(heartbeat=True):
        raise injected

    fake.connect = connect
    link = ab._Link(fake, "asiair.invalid")
    with pytest.raises(DeviceError) as exc:
        await link.connect()
    _assert_nothing_quoted(exc.value)
    assert named in _printed(exc.value), _printed(exc.value)
    assert exc.value.cause_class == type(injected).__name__


async def test_a_busy_refusal_prints_no_libasi_text(fake):
    """A ``BusyError`` takes its own arm of ``_Link.call``; the libasi text
    (the request it was refused, here carrying the made-up position) is as
    absent from the printed traceback as on the other arm."""
    session = await _open()
    tel = await session.get_device("telescope", _conn())

    def info():
        raise BusyError("guiding", BODY)

    fake.mount.info = info
    try:
        with pytest.raises(DeviceError) as exc:
            await tel.get_position()
        assert exc.value.cause_kind == "busy"
        assert "guiding" in str(exc.value)
        _assert_nothing_quoted(exc.value)
    finally:
        await session.close()


async def test_a_device_connect_that_wraps_the_link_error_prints_no_libasi_text(
        fake):
    """The camera's ``connect`` re-wraps the link's error (``from exc``) with
    its own words. What it chains is the link's error, which chains nothing,
    so the whole printed chain is still free of the box's text."""
    def boom():
        raise ASIAIRError(BODY, 253, "get_camera_info")

    fake.camera.info = boom
    session = await _open()
    try:
        with pytest.raises(DeviceError) as exc:
            await session.get_device("camera", _conn())
        printed = _printed(exc.value)
        assert "no main camera is open on the box" in printed
        for tell in TELLS:
            assert tell not in printed, f"{tell!r} in {printed!r}"
        assert BODY not in printed
        inner = exc.value.__cause__
        assert isinstance(inner, ab.AsiairCallError)
        assert inner.__cause__ is None and inner.__context__ is None
    finally:
        await session.close()


@pytest.mark.parametrize("make, kind", [
    pytest.param(lambda: BusyError("guiding", "a sync"), "busy", id="busy"),
    pytest.param(lambda: ConnectionResetError("reset"), "link", id="reset"),
    pytest.param(lambda: TimeoutError("timed out"), "link", id="timeout"),
    pytest.param(lambda: OSError(BODY), "link", id="os-error"),
    pytest.param(lambda: ASIAIRError(BODY, 253, "scope_sync"), "other",
                 id="box-error"),
    pytest.param(lambda: RuntimeError(BODY), "other", id="runtime"),
    pytest.param(lambda: ValueError(f"bad token {RA_HMS!r}"), "other",
                 id="parse-error"),
])
async def test_a_failed_call_carries_its_kind_and_class(make, kind):
    """The kind ``sync`` classifies on, and the class name, are attributes of
    the error itself, decided at the raise site.

    MUTANT C3 "everything is other" (``_cause_kind`` returning ``"other"``
    for every exception): RED on the busy and link cases (and on ``sync``'s,
    below)."""
    injected = make()
    link = ab._Link(client=None, host="asiair.invalid")

    def fn():
        raise injected

    with pytest.raises(DeviceError) as exc:
        await link.call(fn, what="read")
    assert isinstance(exc.value, ab.AsiairCallError)
    assert exc.value.cause_kind == kind
    assert exc.value.cause_class == type(injected).__name__
    _assert_nothing_quoted(exc.value)


# ------------------------------------------------------- sync's classification

def _stub_precession(monkeypatch) -> None:
    """Keep the refusal's residual read off astropy and IERS data."""
    monkeypatch.setattr(hub_module, "precess_j2000_to_jnow",
                        lambda ra, dec, when=None: ((ra + 0.25) % 24.0,
                                                    dec + 1.0))
    monkeypatch.setattr(hub_module, "precess_jnow_to_j2000",
                        lambda ra, dec, when=None: ((ra - 0.25) % 24.0,
                                                    dec - 1.0))


def _busy():
    return BusyError("guiding", BODY)


#: (where the failure is injected, what libasi raises, what sync must raise,
#: and the reason it must give). Each libasi text carries the made-up position.
SYNC_CASES = [
    pytest.param("check_idle", _busy, SyncRefused, ab.ASIAIR_BUSY_REASON,
                 id="idle-check-busy"),
    pytest.param("check_idle", lambda: OSError(BODY), SyncUnverified,
                 SYNC_UNVERIFIED_LINK_BEFORE, id="idle-check-link"),
    pytest.param("check_idle", lambda: ASIAIRError(BODY, 253, "get_app_state"),
                 SyncUnverified, SYNC_UNVERIFIED_LINK_BEFORE,
                 id="idle-check-other"),
    pytest.param("mount.sync", _busy, SyncRefused, ab.ASIAIR_BUSY_REASON,
                 id="sync-busy"),
    pytest.param("mount.sync", lambda: OSError(BODY), SyncUnverified,
                 SYNC_UNVERIFIED_LINK_DURING, id="sync-link"),
    pytest.param("mount.sync", lambda: TimeoutError("timed out"),
                 SyncUnverified, SYNC_UNVERIFIED_LINK_DURING,
                 id="sync-timeout"),
    pytest.param("mount.sync", lambda: ASIAIRError(BODY, 253, "scope_sync"),
                 SyncUnverified, SYNC_UNVERIFIED_UNCLEAR, id="sync-other"),
    pytest.param("mount.sync", lambda: RuntimeError(BODY), SyncUnverified,
                 SYNC_UNVERIFIED_UNCLEAR, id="sync-runtime"),
]


@pytest.mark.parametrize("where, make, raised, reason", SYNC_CASES)
async def test_sync_still_tells_busy_link_and_other_apart(
        fake, monkeypatch, where, make, raised, reason):
    """``sync`` classifies on the error's own ``cause_kind`` now, so it still
    tells a refusal from a dead link from an unclear answer, and what it
    raises (and prints) carries none of libasi's text.

    MUTANT C4 "sync ignores the kind" (the two ``getattr(e, "cause_kind",
    ...)`` reads in ``AsiairTelescope.sync`` made ``"other"``): RED on the
    busy cases (a refusal read as unverified) and on the link cases
    (unverified, but not "during")."""
    _stub_precession(monkeypatch)
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    injected = make()

    def failing(*args, **kwargs):
        raise injected

    if where == "check_idle":
        fake.check_idle = failing
    else:
        fake.mount.sync = failing
    try:
        with pytest.raises(DeviceError) as exc:
            await tel.sync(5.9, 32.5)
        e = exc.value
        assert type(e) is raised, type(e)
        assert e.reason == reason, e.reason
        if raised is SyncRefused:
            assert e.code == SYNC_REPLY_BUSY
        _assert_nothing_quoted(e)
    finally:
        await session.close()
