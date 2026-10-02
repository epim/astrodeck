# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""No real process or network access: synthetic ownership graph fixtures."""
from pathlib import Path
import sys
import tempfile
import types
from contextlib import ExitStack
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT / "packaging"))
import smoke_ownership as smoke
import build_binary as binary


class MissingProcess(Exception):
    pass


class Process:
    def __init__(self, pid, created=100, parent=None, command=None):
        self.pid,self.created,self.parent=pid,created,parent
        self.command=command or [str(Path("synthetic.exe").resolve()),"run","--host","127.0.0.1","--port","8899"]
        self.children_rows=[]
        self.terminated=False
        self.killed=False
    def create_time(self): return self.created
    def exe(self): return self.command[0]
    def cmdline(self): return self.command
    def children(self,recursive=False): return self.children_rows
    def parents(self): return [self.parent] if self.parent else []
    def terminate(self): self.terminated=True
    def kill(self): self.killed=True


class SmokeOwnership(unittest.TestCase):
    def setUp(self):
        self.parent=Process(10)
        self.child=Process(11,101,self.parent)
        self.stranger=Process(12,101)
        self.parent.children_rows=[self.child]
        self.processes={p.pid:p for p in [self.parent,self.child,self.stranger]}
        self.connections=[types.SimpleNamespace(status="LISTEN",laddr=types.SimpleNamespace(ip="127.0.0.1",port=8899),pid=11)]
        def process(pid):
            if pid not in self.processes: raise MissingProcess()
            return self.processes[pid]
        self.api=types.SimpleNamespace(Process=process,NoSuchProcess=MissingProcess,CONN_LISTEN="LISTEN",net_connections=lambda **kw:self.connections,wait_procs=lambda ps,timeout:(ps,[]))
        for item in self.processes.values():
            item.net_connections=lambda pid=item.pid,**kw:[c for c in self.connections if c.pid == pid]
        self.owned=smoke.OwnedSmoke(10,100,Path("synthetic.exe"),8899,api=self.api)

    def test_valid_descendant_listener(self):
        self.assertEqual(11,self.owned.verify_listener())

    def test_global_socket_access_is_unnecessary(self):
        def denied(**kw): raise PermissionError("global enumeration denied")
        self.api.net_connections=denied
        self.assertEqual(11,self.owned.verify_listener())

    def test_unproven_ancestry_is_not_adopted(self):
        self.parent.children_rows.append(self.stranger)
        self.connections[0].pid=12
        with self.assertRaises(smoke.ListenerNotReady):
            self.owned.verify_listener()
        self.assertNotIn(12,self.owned.known)

    def test_stranger_listener_is_refused(self):
        self.connections[0].pid=12
        with self.assertRaises(smoke.ListenerNotReady):
            self.owned.verify_listener()

    def test_child_pid_reuse_is_refused(self):
        self.child.created=200
        self.child.parent=None
        with self.assertRaisesRegex(RuntimeError,"identity changed"):
            self.owned.verify_listener()

    def test_wrong_command_is_not_owned(self):
        self.child.command[-1]="9999"
        with self.assertRaisesRegex(RuntimeError,"identity changed"):
            self.owned.verify_listener()

    def test_wildcard_listener_is_refused(self):
        self.connections[0].laddr.ip="0.0.0.0"
        with self.assertRaisesRegex(RuntimeError,"exclusive loopback"):
            self.owned.verify_listener()

    def test_missing_listener_is_not_readiness(self):
        self.connections=[]
        with self.assertRaises(smoke.ListenerNotReady):
            self.owned.verify_listener()

    def test_stop_never_touches_stranger_image(self):
        self.owned.stop()
        self.assertTrue(self.parent.terminated)
        self.assertTrue(self.child.terminated)
        self.assertFalse(self.stranger.terminated)
        self.assertFalse(self.stranger.killed)

    def test_reused_pid_is_never_stopped(self):
        self.parent.children_rows=[]
        self.child.created=999
        with self.assertRaisesRegex(RuntimeError,"changed smoke PID"):
            self.owned.stop()
        self.assertFalse(self.child.terminated)

    def test_preexisting_launch_pid_is_refused(self):
        with self.assertRaisesRegex(RuntimeError,"launch identity"):
            smoke.OwnedSmoke(10,500,Path("synthetic.exe"),8899,api=self.api)

    def test_only_observed_orphans_can_be_stopped(self):
        del self.processes[10]
        self.child.parent=None
        self.owned.stop()
        self.assertTrue(self.child.terminated)
        self.assertFalse(self.stranger.terminated)

    def test_missing_ownership_never_kills_by_pid_or_name(self):
        with patch.object(binary,"_run_text") as run:
            process=binary.SmokeProcess(8899,pid=10)
            with self.assertRaisesRegex(RuntimeError,"no process stopped"):
                process.stop()
            run.assert_not_called()

    def test_constructor_failure_cleans_already_proven_identity(self):
        with patch.object(smoke.OwnedSmoke,"refresh",side_effect=[PermissionError("transient"),None]):
            with self.assertRaises(smoke.OwnershipSetupError) as caught:
                smoke.OwnedSmoke(10,100,Path("synthetic.exe"),8899,api=self.api)
        self.assertTrue(caught.exception.cleanup_complete)
        self.assertTrue(self.parent.terminated)
        self.assertFalse(self.stranger.terminated)

    def test_constructor_unproven_identity_is_never_stopped(self):
        self.parent.command[-1]="9999"
        with self.assertRaises(smoke.OwnershipSetupError) as caught:
            smoke.OwnedSmoke(10,100,Path("synthetic.exe"),8899,api=self.api)
        self.assertFalse(caught.exception.cleanup_complete)
        self.assertIn("private smoke state preserved",str(caught.exception))
        self.assertFalse(self.parent.terminated)

    def test_creation_change_during_socket_read_is_refused(self):
        def changed(**kw):
            self.child.created=900
            return self.connections
        self.child.net_connections=changed
        with self.assertRaisesRegex(RuntimeError,"identity changed"):
            self.owned.verify_listener()

    def test_failed_stop_can_be_retried(self):
        with patch.object(self.owned,"stop",side_effect=[RuntimeError("transient"),None]) as stop:
            process=binary.SmokeProcess(8899,pid=10,ownership=self.owned)
            with self.assertRaises(RuntimeError): process.stop()
            process.stop()
            self.assertEqual(2,stop.call_count)

    def test_plain_launch_ownership_failure_is_explicit(self):
        popen=types.SimpleNamespace(pid=10)
        with patch.object(binary,"process_api"),patch.object(binary,"_is_elevated",return_value=False),patch.object(binary.subprocess,"Popen",return_value=popen),patch.object(binary,"OwnedSmoke",side_effect=PermissionError("private failure")):
            with self.assertRaises(smoke.OwnershipSetupError) as caught:
                binary._launch_smoke(Path("synthetic.exe"),8899,{})
        self.assertFalse(caught.exception.cleanup_complete)
        self.assertNotIn("private failure",str(caught.exception))

    def exercise_early_failure(self, *, occupied=False, launch_error=None):
        scratch=ROOT / ".probe/release"
        with tempfile.TemporaryDirectory(prefix="smoke-early-test-",dir=scratch) as name:
            root=Path(name)
            sock=types.SimpleNamespace(bind=lambda addr:None,getsockname=lambda:("127.0.0.1",8899))
            with ExitStack() as stack:
                stack.enter_context(patch.object(binary,"ROOT",root))
                stack.enter_context(patch.object(binary,"_port_in_use",return_value=occupied))
                stack.enter_context(patch.object(binary,"_source_version",return_value="0"))
                stack.enter_context(patch.object(binary,"_launch_smoke",side_effect=launch_error))
                socket=stack.enter_context(patch("socket.socket"))
                socket.return_value.__enter__.return_value=sock
                with self.assertRaises((SystemExit,RuntimeError,OSError)):
                    binary.smoke(Path("synthetic.exe"))
            return list((root / ".probe/release").glob("smoke-*"))

    def test_occupied_port_leaves_no_test_state(self):
        self.assertEqual([],self.exercise_early_failure(occupied=True))

    def test_launch_failure_leaves_no_test_state(self):
        self.assertEqual([],self.exercise_early_failure(launch_error=OSError("launch refused")))

    def test_unverified_launch_preserves_private_test_state(self):
        self.assertEqual(1,len(self.exercise_early_failure(launch_error=smoke.OwnershipSetupError())))

    def test_verified_failed_launch_cleans_test_state(self):
        self.assertEqual([],self.exercise_early_failure(launch_error=smoke.OwnershipSetupError(True)))

    def test_no_raw_log_read(self):
        process=binary.SmokeProcess(8899,log_path=Path("private.log"))
        with patch.object(Path,"read_text",side_effect=AssertionError("raw log read")):
            self.assertEqual("",process.output_tail())

    def test_smoke_uses_new_private_directories(self):
        scratch=ROOT / ".probe/release"
        scratch.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="smoke-state-test-",dir=scratch) as name:
            root=Path(name)
            old=root / "build/smoke-state"
            old.mkdir(parents=True)
            sentinel=old / "keep.txt"
            sentinel.write_text("unrelated existing state",encoding="utf-8")
            observed=[]
            fake=types.SimpleNamespace(poll=lambda:1,stop=lambda:None)
            def launch(exe,port,env):
                observed.append((env["ASTRODECK_CONFIG_DIR"],env["ASTRODECK_CAPTURE_DIR"]))
                return fake
            # Socket allocation is mocked too: the fixture never binds a port.
            sock=types.SimpleNamespace(bind=lambda addr:None,getsockname=lambda:("127.0.0.1",8899))
            context=types.SimpleNamespace(__enter__=lambda self:sock,__exit__=lambda *a:None)
            with patch.object(binary,"ROOT",root),patch.object(binary,"_port_in_use",return_value=False),patch.object(binary,"_source_version",return_value="0"),patch.object(binary,"_launch_smoke",side_effect=launch),patch("socket.socket") as socket:
                socket.return_value.__enter__.return_value=sock
                for _ in range(2):
                    with self.assertRaises(SystemExit): binary.smoke(Path("synthetic.exe"))
            self.assertNotEqual(observed[0][0],observed[1][0])
            self.assertTrue(all(Path(config).is_relative_to(root / ".probe/release") for config,captures in observed))
            self.assertEqual("unrelated existing state",sentinel.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
