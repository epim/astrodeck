# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A compose build of the controller image carries the native engine or fails (#799, #1123).

Before this, docker-compose.yml and deploy/reverse-proxy/docker-compose.yml built
the root Dockerfile with no `native` build context, so every compose user got a
development image with no native guiding or autofocus and a stderr banner that
Docker's collapsed progress output hides. The compose files now hand the Dockerfile
the `native` context and REQUIRE_NATIVE=1, so a build with no wheel stops with the
reason.

No Docker daemon is needed. The Dockerfile's native RUN is plain POSIX sh, so it is
executed under `sh` with `uname`, `pip` and `python` stubbed, fed by what the
compose files say: the REQUIRE_NATIVE value they pass and the directory they name
as the `native` context.

Named mutant COMPOSE-BUILDS-WITHOUT-THE-WHEEL (REQUIRE_NATIVE: "1" changed to "0" in
docker-compose.yml, so a build with no wheel succeeds silently):
  AssertionError: 1 != 0 : docker compose build with no wheel must fail the build (docker-compose.yml:astrodeck)
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("wp18_build_contexts", ROOT / "deploy/reverse-proxy/check_build_contexts.py")
gate = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = gate
_SPEC.loader.exec_module(gate)

SH = shutil.which("sh")
MOUNT = "--mount=type=bind,from=native,target=/native"
COMPOSE_FILES = (ROOT / "docker-compose.yml", ROOT / "deploy/reverse-proxy/docker-compose.yml")
WHEEL = "astrodeck_native-0.1.0-cp311-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
DEFAULT_DIR = re.compile(r"^\$\{ASTRODECK_NATIVE_DIR:-([^}]+)\}$")


def image_builds():
    """(compose file, service name, build mapping) for every service that builds the root Dockerfile."""
    found = []
    for path in COMPOSE_FILES:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, service in document["services"].items():
            build = service.get("build")
            if build is None:
                continue
            build = {"context": build} if isinstance(build, str) else build
            context = (path.parent / build.get("context", ".")).resolve()
            if context == ROOT and build.get("dockerfile", "Dockerfile") == "Dockerfile":
                found.append((path, name, build))
    return found


def build_args(build):
    args = build.get("args", {})
    if isinstance(args, list):
        args = dict(item.split("=", 1) for item in args)
    return {key: str(value) for key, value in args.items()}


def native_directory(path, build):
    """The directory a compose file hands the Dockerfile as the `native` context, with its default resolved."""
    value = build["additional_contexts"]["native"]
    match = DEFAULT_DIR.match(value)
    assert match, "the native context must be ${ASTRODECK_NATIVE_DIR:-<default directory>}: " + value
    return (path.parent / match.group(1)).resolve()


def install_body() -> str:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    runs = [i for i in gate.instructions(text) if i.startswith("RUN ") and MOUNT in i]
    assert len(runs) == 1, "exactly one RUN consumes the native build context"
    return runs[0].split(MOUNT, 1)[1].strip()


def run_install(native: Path, require: str, arch: str = "x86_64"):
    """Run the Dockerfile's native RUN against `native`, as compose would have it run."""
    body = install_body().replace("/native/", native.as_posix() + "/")
    with tempfile.TemporaryDirectory(prefix="wp18-compose-native-") as scratch:
        scratch = Path(scratch)
        calls = scratch / "calls.txt"
        script = (
            'uname() { printf "%s\\n" "$FAKE_ARCH"; }\n'
            'pip() { printf "pip %s\\n" "$*" >> "$CALLS"; }\n'
            'python() { printf "python %s\\n" "$*" >> "$CALLS"; }\n'
            + body + "\n"
        )
        (scratch / "run.sh").write_text(script, encoding="utf-8", newline="\n")
        env = dict(os.environ, REQUIRE_NATIVE=require, FAKE_ARCH=arch, CALLS=calls.as_posix())
        proc = subprocess.run([SH, (scratch / "run.sh").as_posix()], env=env, capture_output=True, text=True, timeout=120)
        recorded = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return proc, recorded


class ComposeShape(unittest.TestCase):
    def test_every_compose_service_that_builds_the_image_exists(self):
        names = sorted(path.relative_to(ROOT).as_posix() + ":" + name for path, name, _ in image_builds())
        self.assertEqual(["deploy/reverse-proxy/docker-compose.yml:astrodeck", "docker-compose.yml:astrodeck"], names)

    def test_every_image_build_names_the_native_context_and_requires_the_engine(self):
        for path, name, build in image_builds():
            label = path.relative_to(ROOT).as_posix() + ":" + name
            self.assertEqual("1", build_args(build).get("REQUIRE_NATIVE"), label + " must require the engine")
            self.assertEqual(["native"], list(build.get("additional_contexts", {})), label + " must hand the Dockerfile its `native` stage")

    def test_the_native_directory_is_overridable_and_defaults_to_a_directory_in_the_checkout(self):
        for path, name, build in image_builds():
            directory = native_directory(path, build)
            self.assertEqual(ROOT / "native-wheels", directory, path.name)
            self.assertTrue(directory.is_dir(), "the default directory exists, so the Dockerfile's reason is what a build prints")

    def test_the_default_directory_holds_no_wheel_in_the_checkout(self):
        wheels = sorted(p.name for p in (ROOT / "native-wheels").glob("*.whl"))
        self.assertEqual([], wheels, "a release wheel is downloaded per build, never committed")

    def test_the_compose_override_for_usb_does_not_undo_it(self):
        usb = yaml.safe_load((ROOT / "docker-compose.usb.yml").read_text(encoding="utf-8"))
        self.assertNotIn("build", usb["services"]["astrodeck"])


@unittest.skipUnless(SH, "needs a POSIX sh to execute the Dockerfile RUN text")
class ComposeBuildBehaviour(unittest.TestCase):
    def test_compose_build_with_no_wheel_fails_with_the_reason(self):
        """Mutant COMPOSE-BUILDS-WITHOUT-THE-WHEEL (REQUIRE_NATIVE: "1" changed to "0" in docker-compose.yml):
          AssertionError: 1 != 0 : docker compose build with no wheel must fail the build (docker-compose.yml:astrodeck)
        """
        for path, name, build in image_builds():
            label = path.relative_to(ROOT).as_posix() + ":" + name
            proc, calls = run_install(native_directory(path, build), build_args(build)["REQUIRE_NATIVE"])
            self.assertEqual(1, proc.returncode, "docker compose build with no wheel must fail the build (" + label + ")")
            self.assertEqual([], calls, "nothing is installed and nothing is probed")
            self.assertIn("REQUIRE_NATIVE=1", proc.stderr)
            self.assertIn("astrodeck_native wheel for x86_64", proc.stderr, "the message names what is missing and for which architecture")
            self.assertIn("native-wheels", proc.stderr, "the message names where compose looks for the wheel")
            self.assertNotIn("DEVELOPMENT IMAGE", proc.stderr, "a compose build never reports itself as a development image")

    def test_compose_build_with_the_wheel_installs_it_and_probes_the_engine(self):
        for path, name, build in image_builds():
            with tempfile.TemporaryDirectory(prefix="wp18-compose-wheel-") as scratch:
                native = Path(scratch)
                (native / WHEEL).write_bytes(b"")
                proc, calls = run_install(native, build_args(build)["REQUIRE_NATIVE"])
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual(2, len(calls), calls)
            self.assertTrue(calls[0].startswith("pip install "), calls)
            self.assertEqual("python /opt/native_probe.py", calls[1], "the probe prints the engine version in the same RUN")


if __name__ == "__main__":
    unittest.main()
