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
