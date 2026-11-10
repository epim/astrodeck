# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 3.8 (#871): an ASIAIR abort does not wait behind a FITS download.

``_Link.call`` keeps the link lock until an abandoned libasi thread returns
(#862), so a cancelled FITS download (a full frame over Wi-Fi) would hold the
lock for the whole transfer and the abort's ``stop_exposure`` would queue
behind it. libasi downloads on its own one-shot connection (port 4801), not on
the command socket the lock protects, so the camera now runs the download
outside the lock, as a task it can cancel, and ``abort_exposure`` cancels that
task before it sends ``stop_exposure``.

The fake download blocks on an event, so "slow" is a state and not a duration.
Every wait below carries a short, explicit bound: a regression shows as a
timeout, not as a long hang.

MUTANT "the abort does not cancel the download" (the ``download.cancel()``
lines deleted from ``AsiairCamera.abort_exposure``): RED, the exposure task
never ends and ``test_an_abort_during_a_download_cancels_the_download`` times
out waiting for it.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

import astrodeck.devices.backends.asiair_backend as ab
from astrodeck.devices.base import DeviceError

from test_asiair_backend import _conn, _open, fake  # noqa: F401 (fixture import)

BOUND_S = 3.0


class _SlowDownload:
    """Replaces ``fake.images.fetch_fits`` with a download stuck on an event."""

    def __init__(self, fake):
        self.inside = threading.Event()
        self.release = threading.Event()
        real = fake.images.fetch_fits

        def slow(path):
            data = real(path)
            self.inside.set()
            self.release.wait(10.0)
            return data

        fake.images.fetch_fits = slow

    async def wait_inside(self) -> None:
        assert await asyncio.to_thread(self.inside.wait, BOUND_S)


async def _camera_mid_download(fake, monkeypatch):
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    download = _SlowDownload(fake)
    session = await _open()
    cam = await session.get_device("camera", _conn())
    task = asyncio.create_task(cam.expose(0.01, 100, 10))
    await download.wait_inside()
    return session, cam, task, download


async def test_an_abort_during_a_download_cancels_the_download(fake, monkeypatch):
    session, cam, task, download = await _camera_mid_download(fake, monkeypatch)
    try:
        await asyncio.wait_for(cam.abort_exposure(), BOUND_S)
        assert "camera.stop_exposure" in fake.labels
        # The download is cancelled: the exposure ends although its libasi
        # thread is still blocked inside the transfer.
        with pytest.raises(DeviceError) as info:
            await asyncio.wait_for(task, BOUND_S)
        assert "abort" in str(info.value)
        assert download.release.is_set() is False
        # The preview delete runs in its own task and holds the lock for its
        # one call; once it has ended nothing is left holding the lock.
        await asyncio.wait_for(asyncio.gather(*cam._cleanups), BOUND_S)
        assert not cam._link._lock.locked()
    finally:
        download.release.set()
        await session.close()


async def test_cancelling_an_exposure_in_a_download_aborts_at_once(fake,
                                                                    monkeypatch):
    session, cam, task, download = await _camera_mid_download(fake, monkeypatch)
    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, BOUND_S)
        assert "camera.stop_exposure" in fake.labels
        assert download.release.is_set() is False
        # The preview delete runs in its own task and holds the lock for its
        # one call; once it has ended nothing is left holding the lock.
        await asyncio.wait_for(asyncio.gather(*cam._cleanups), BOUND_S)
        assert not cam._link._lock.locked()
    finally:
        download.release.set()
        await session.close()


async def test_a_download_does_not_hold_the_command_link(fake, monkeypatch):
    session, cam, task, download = await _camera_mid_download(fake, monkeypatch)
    try:
        assert not cam._link._lock.locked()
        activity = await asyncio.wait_for(cam._link.activity(), BOUND_S)
        assert activity == {}
        assert not task.done()
    finally:
        download.release.set()
        await asyncio.wait_for(task, BOUND_S)
        await session.close()


async def test_a_download_that_fails_names_the_download(fake, monkeypatch):
    monkeypatch.setattr(ab, "POLL_S", 0.001)

    def broken(path):
        raise OSError("fake socket reset")

    fake.images.fetch_fits = broken
    session = await _open()
    cam = await session.get_device("camera", _conn())
    try:
        with pytest.raises(DeviceError) as info:
            await asyncio.wait_for(cam.expose(0.01, 100, 10), BOUND_S)
        assert "download FITS failed" in str(info.value)
        assert info.value.__cause__ is None
        assert info.value.__context__ is None
    finally:
        await session.close()


# ------------------------------------------------------------------- #1193
# A preview frame's file on the box is removed on EVERY ending after the save.

async def _deleted_within_bound(fake) -> list[str]:
    async def _poll():
        while not fake.deleted:
            await asyncio.sleep(0.005)
    await asyncio.wait_for(_poll(), BOUND_S)
    return list(fake.deleted)


async def test_an_abort_during_a_download_deletes_the_preview(fake, monkeypatch):
    session, cam, task, download = await _camera_mid_download(fake, monkeypatch)
    try:
        await asyncio.wait_for(cam.abort_exposure(), BOUND_S)
        with pytest.raises(DeviceError):
            await asyncio.wait_for(task, BOUND_S)
        assert await _deleted_within_bound(fake) == ["Preview/Light_0001.fit"]
    finally:
        download.release.set()
        await session.close()


async def test_a_cancelled_exposure_deletes_the_preview(fake, monkeypatch):
    session, cam, task, download = await _camera_mid_download(fake, monkeypatch)
    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, BOUND_S)
        assert await _deleted_within_bound(fake) == ["Preview/Light_0001.fit"]
    finally:
        download.release.set()
        await session.close()


async def test_a_failed_download_deletes_the_preview(fake, monkeypatch):
    monkeypatch.setattr(ab, "POLL_S", 0.001)

    def broken(path):
        raise OSError("fake socket reset")

    fake.images.fetch_fits = broken
    session = await _open()
    cam = await session.get_device("camera", _conn())
    try:
        with pytest.raises(DeviceError):
            await asyncio.wait_for(cam.expose(0.01, 100, 10), BOUND_S)
        assert await _deleted_within_bound(fake) == ["Preview/Light_0001.fit"]
    finally:
        await session.close()


async def test_a_failed_decode_deletes_the_preview(fake, monkeypatch):
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    fake.fits_bytes = b"this is not a FITS file"
    session = await _open()
    cam = await session.get_device("camera", _conn())
    try:
        with pytest.raises(Exception):
            await asyncio.wait_for(cam.expose(0.01, 100, 10), BOUND_S)
        assert await _deleted_within_bound(fake) == ["Preview/Light_0001.fit"]
    finally:
        await session.close()


async def test_a_kept_light_is_never_deleted_on_a_cancel(fake, monkeypatch):
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    download = _SlowDownload(fake)
    session = await _open()
    cam = await session.get_device("camera", _conn())
    task = asyncio.create_task(cam.expose(0.01, 100, 10, save=True))
    try:
        await download.wait_inside()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, BOUND_S)
        await asyncio.sleep(0.05)
        assert fake.deleted == []
    finally:
        download.release.set()
        await session.close()


async def test_a_failed_delete_is_logged_once_and_does_not_fail_the_capture(
        fake, monkeypatch, caplog):
    monkeypatch.setattr(ab, "POLL_S", 0.001)

    def refuse(path):
        raise OSError("fake delete refused")

    fake.images.delete = refuse
    session = await _open()
    cam = await session.get_device("camera", _conn())
    try:
        with caplog.at_level("WARNING", logger=ab._log.name):
            frame = await asyncio.wait_for(cam.expose(0.01, 100, 10), BOUND_S)
        assert frame.data is not None
        warned = [r for r in caplog.records if r.levelname == "WARNING"
                  and "preview" in r.getMessage()]
        assert len(warned) == 1
        assert "Light_0001" not in warned[0].getMessage()
    finally:
        await session.close()


# ------------------------------------------------------------------- #1194
# An abort or a halt does not queue behind a cancelled command RPC.

class _BlockingMountInfo:
    """Makes the main client's ``mount.info`` block on an event: a command RPC
    the box has not answered yet."""

    def __init__(self, fake):
        self.inside = threading.Event()
        self.release = threading.Event()

        def blocked():
            fake._rec("mount.info")
            self.inside.set()
            self.release.wait(10.0)
            raise OSError("fake late reply")

        fake.mount.info = blocked


@pytest.fixture
def two_clients(monkeypatch):
    """A main client and a separate abort client, as the backend now opens."""
    from test_asiair_backend import FakeAsiair, _fits_bytes
    main, abort = FakeAsiair(), FakeAsiair()
    main.fits_bytes = abort.fits_bytes = _fits_bytes()
    clients = iter([main, abort])
    monkeypatch.setattr(ab, "make_client", lambda host, timeout=10.0: next(clients))
    return main, abort


async def _stalled_link(two_clients):
    """A session with every device open, then a command RPC cancelled while the
    box has not answered it: the link lock is held by the orphaned call."""
    main, abort = two_clients
    session = await _open()
    devices = {role: await session.get_device(role, _conn())
               for role in ("telescope", "camera", "focuser")}
    stall = _BlockingMountInfo(main)
    poll = asyncio.create_task(devices["telescope"]._info())
    assert await asyncio.to_thread(stall.inside.wait, BOUND_S)
    poll.cancel()
    with pytest.raises(asyncio.CancelledError):
        await poll
    assert session.link._lock.locked(), "the orphaned call must hold the lock"
    return session, devices, stall, main, abort


async def test_a_mount_stop_does_not_wait_behind_a_cancelled_rpc(two_clients):
    session, devices, stall, main, abort = await _stalled_link(two_clients)
    try:
        mount = devices["telescope"]
        loop = asyncio.get_running_loop()
        began = loop.time()
        await asyncio.wait_for(mount.stop(), 0.5)
        assert loop.time() - began < 0.5
        assert "mount.stop" in abort.labels
        assert "mount.stop" not in main.labels
        assert stall.release.is_set() is False
    finally:
        stall.release.set()
        await session.close()


async def test_a_camera_abort_does_not_wait_behind_a_cancelled_rpc(two_clients):
    session, devices, stall, main, abort = await _stalled_link(two_clients)
    try:
        cam = devices["camera"]
        await asyncio.wait_for(cam.abort_exposure(), 0.5)
        assert "camera.stop_exposure" in abort.labels
        assert "camera.stop_exposure" not in main.labels
    finally:
        stall.release.set()
        await session.close()


async def test_a_focuser_halt_does_not_wait_behind_a_cancelled_rpc(two_clients):
    session, devices, stall, main, abort = await _stalled_link(two_clients)
    try:
        focuser = devices["focuser"]
        await asyncio.wait_for(focuser.halt(), 0.5)
        assert "focuser.stop" in abort.labels
        assert "focuser.stop" not in main.labels
    finally:
        stall.release.set()
        await session.close()


async def test_a_command_rpc_still_waits_behind_a_cancelled_rpc(two_clients):
    """The #862 guard is unchanged: ordinary commands still queue, so a late
    reply cannot be taken by the next command."""
    session, devices, stall, main, abort = await _stalled_link(two_clients)
    try:
        mount = devices["telescope"]
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(mount.set_tracking(True), 0.2)
    finally:
        stall.release.set()
        await session.close()


async def test_an_abort_falls_back_to_the_main_client_when_the_second_cannot_connect(
        fake, monkeypatch):
    clients = iter([fake, None])

    def make(host, timeout=10.0):
        client = next(clients)
        if client is None:
            raise DeviceError("no second connection")
        return client

    monkeypatch.setattr(ab, "make_client", make)
    session = await _open()
    try:
        mount = await session.get_device("telescope", _conn())
        await asyncio.wait_for(mount.stop(), BOUND_S)
        assert "mount.stop" in fake.labels
    finally:
        await session.close()
