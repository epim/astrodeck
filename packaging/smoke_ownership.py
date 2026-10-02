# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Build-only process ownership for frozen-server smoke tests.

PID, creation time, command, executable and observed ancestry must agree before
any listener response is read or process is stopped. There is no image-name or
port-based kill fallback. psutil is a build dependency, not a server dependency.
"""
from __future__ import annotations
import os
from pathlib import Path
import time


class OwnershipSetupError(RuntimeError):
    def __init__(self, cleanup_complete=False):
        self.cleanup_complete = cleanup_complete
        status = "proven processes stopped" if cleanup_complete else "cleanup incomplete; private smoke state preserved"
        super().__init__("smoke launch identity could not be established; " + status)


class ListenerNotReady(TimeoutError):
    pass


def process_api():
    import psutil
    return psutil


def same_path(first, second):
    return os.path.normcase(str(Path(first).resolve())) == os.path.normcase(str(Path(second).resolve()))


class OwnedSmoke:
    def __init__(self, pid: int, launched_at: float, executable: Path, port: int,
                 wrapper: Path | None = None, api=None):
        self.api = api or process_api()
        self.root_pid, self.executable, self.port = pid, executable.resolve(), port
        self.wrapper = wrapper.resolve() if wrapper else None
        self.known = {}
        try:
            process = self.api.Process(pid)
            created = process.create_time()
            if created < launched_at - 1 or not self.command_matches(process, root=True):
                raise RuntimeError("smoke launch identity does not match")
            self.known[pid] = created
            self.root_created = created
            self.refresh()
        except BaseException:
            complete = False
            if self.known:
                try:
                    self.stop()
                    complete = True
                except BaseException:
                    pass
            raise OwnershipSetupError(complete) from None

    def command_matches(self, process, root=False):
        command = process.cmdline()
        if root and self.wrapper is not None:
            return (same_path(process.exe(), os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")) and len(command) == 3
                    and command[1].lower() == "/c"
                    and same_path(command[2].strip('"'), self.wrapper))
        return (same_path(process.exe(), self.executable)
                and len(command) == 6 and same_path(command[0], self.executable)
                and command[1:] == ["run", "--host", "127.0.0.1", "--port", str(self.port)])

    def matches(self, process):
        return (self.known.get(process.pid) == process.create_time()
                and self.command_matches(process, root=process.pid == self.root_pid))

    def refresh(self):
        try:
            root = self.api.Process(self.root_pid)
            if not self.matches(root):
                raise RuntimeError("smoke root PID identity changed")
            for process in root.children(recursive=True):
                if process.create_time() < self.root_created or not self.command_matches(process):
                    continue
                # Re-read the ancestry instead of trusting a stale PID list.
                if any(parent.pid == self.root_pid and parent.create_time() == self.root_created
                       for parent in process.parents()):
                    self.known[process.pid] = process.create_time()
        except self.api.NoSuchProcess:
            # Previously observed children keep their creation-time identity.
            pass

    def verify_listener(self):
        self.refresh()
        listeners = []
        for pid in self.known:
            try:
                process = self.api.Process(pid)
                if not self.matches(process):
                    raise RuntimeError("smoke listener process identity changed")
                # macOS denies system-wide socket enumeration to ordinary users.
                # Read only already-proven processes; never fall back to a port
                # lookup that could identify an unrelated server.
                sockets = process.net_connections(kind="tcp")
                if not self.matches(process):
                    raise RuntimeError("smoke listener process identity changed")
                listeners.extend((pid, item) for item in sockets
                                 if item.status == self.api.CONN_LISTEN
                                 and item.laddr.port == self.port)
            except self.api.NoSuchProcess:
                continue
        if not listeners:
            raise ListenerNotReady("owned smoke listener is not ready")
        if len(listeners) != 1 or listeners[0][1].laddr.ip != "127.0.0.1":
            raise RuntimeError("smoke port is not an exclusive loopback listener")
        return listeners[0][0]

    def stop(self):
        self.refresh()
        stopped = []
        for pid in reversed(list(self.known)):
            try:
                process = self.api.Process(pid)
                if not self.matches(process):
                    raise RuntimeError("refusing to stop a changed smoke PID")
                process.terminate()
                stopped.append(process)
            except self.api.NoSuchProcess:
                pass
        _, alive = self.api.wait_procs(stopped, timeout=10)
        for process in alive:
            if not self.matches(process):
                raise RuntimeError("refusing to kill a changed smoke PID")
            process.kill()
        _, alive = self.api.wait_procs(alive, timeout=5)
        if alive:
            raise RuntimeError("owned smoke cleanup did not finish")
