# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The container image installs the release's native wheel and probes it (#655, WP-655A).

The wheel arrives through a named build context (`native`) that replaces an empty
stage of the same name. The RUN that consumes it is plain POSIX sh, so these tests
execute that very text under `sh` with `uname`, `pip` and `python` stubbed as shell
functions: the REQUIRE_NATIVE hard failure, the architecture selection and the
"a failed install or probe fails the build" paths are exercised as behaviour, not
as strings. A real `docker build` of the same Dockerfile (plain, and with
REQUIRE_NATIVE=1 and a wheel) is what only a runner with a daemon and the release's
wheels can prove; the release workflow's image job does on a tag, and WP-655C's
ci.yml job will on a branch.

Named mutants are recorded in the docstring of the test that kills each. Every one was
applied from a byte backup of the file, the suite run, the file restored byte-identically
(sha256 compared) and the mutant text grepped out.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("image_native_contexts", ROOT / "deploy/reverse-proxy/check_build_contexts.py")
gate = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = gate
_SPEC.loader.exec_module(gate)

SH = shutil.which("sh")
BASH = shutil.which("bash")
MOUNT = "--mount=type=bind,from=native,target=/native"

X86 = "astrodeck_native-0.1.0-cp311-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
ARM = "astrodeck_native-0.1.0-cp311-abi3-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
# maturin falls back to the plain platform tag when a binary is not manylinux clean.
PLAIN_X86 = "astrodeck_native-0.1.0-cp311-abi3-linux_x86_64.whl"
# The release's artifact folder can hold every platform's wheel; none of these may
# ever be chosen on a linux image, even though two of them name x86_64 or arm64.
WINDOWS = "astrodeck_native-0.1.0-cp311-abi3-win_amd64.whl"
MAC_ARM = "astrodeck_native-0.1.0-cp311-abi3-macosx_11_0_arm64.whl"
MAC_X86 = "astrodeck_native-0.1.0-cp311-abi3-macosx_10_12_x86_64.whl"


def dockerfile_instructions() -> list[str]:
    return list(gate.instructions((ROOT / "Dockerfile").read_text(encoding="utf-8")))


def install_body() -> str:
    """The shell text of the RUN that installs and probes the native wheel."""
    runs = [i for i in dockerfile_instructions() if i.startswith("RUN ") and MOUNT in i]
    assert len(runs) == 1, "exactly one RUN consumes the native build context"
    return runs[0].split(MOUNT, 1)[1].strip()


class DockerfileShape(unittest.TestCase):
    def test_native_stage_is_empty_so_a_plain_build_still_works(self):
        """Mutant DOCKER-NATIVE-STAGE-REMOVED (the `FROM scratch AS native` line deleted):
          AssertionError: 1 != 0 : one stage named native
        """
        stages = [i for i in dockerfile_instructions() if i.startswith("FROM ")]
        index = [i for i, s in enumerate(stages) if re.search(r"\sAS\s+native$", s, re.I)]
        self.assertEqual(1, len(index), "one stage named native")
        self.assertEqual("FROM scratch AS native", stages[index[0]])
        self.assertLess(index[0], len(stages) - 1, "declared before the runtime stage that mounts it")

    def test_require_native_defaults_off_in_the_dockerfile(self):
        """Mutant DOCKER-DEFAULT-REQUIRED (ARG REQUIRE_NATIVE=0 changed to 1):
          AssertionError: 'ARG REQUIRE_NATIVE=0' not found in [...] : a plain docker build / compose build must keep working
        """
        self.assertIn("ARG REQUIRE_NATIVE=0", dockerfile_instructions(), "a plain docker build / compose build must keep working")

    def test_require_native_is_declared_in_the_runtime_stage_before_the_run_reads_it(self):
        """Mutants, each restored (found by the WP-128 verifier: the presence test alone passed both):
        DOCKER-ARG-AFTER-RUN (the ARG moved below the RUN that reads it):
          AssertionError: 25 not less than 24 : the ARG must precede the RUN that reads it, or `set -u` fails every build
        DOCKER-ARG-GLOBAL-SCOPE (the ARG moved above every FROM):
          AssertionError: 17 not less than 0 : an ARG above the runtime FROM is not visible inside that stage
        """
        instructions = dockerfile_instructions()
        arg = instructions.index("ARG REQUIRE_NATIVE=0")
        run = next(i for i, ins in enumerate(instructions) if MOUNT in ins)
        runtime = next(i for i, ins in enumerate(instructions) if ins.startswith("FROM ") and re.search(r"\sAS\s+runtime$", ins, re.I))
        self.assertLess(runtime, arg, "an ARG above the runtime FROM is not visible inside that stage")
        self.assertLess(arg, run, "the ARG must precede the RUN that reads it, or `set -u` fails every build")

    def test_probe_is_copied_into_the_image_before_it_runs(self):
        """Mutant DOCKER-PROBE-COPY-REMOVED (the COPY of native_probe.py deleted):
          AssertionError: 'COPY packaging/native_probe.py /opt/native_probe.py' not found in [...] : the probe must be copied into the image
        """
        instructions = dockerfile_instructions()
        self.assertIn("COPY packaging/native_probe.py /opt/native_probe.py", instructions, "the probe must be copied into the image")
        copy = instructions.index("COPY packaging/native_probe.py /opt/native_probe.py")
        run = next(i for i, ins in enumerate(instructions) if MOUNT in ins)
        self.assertLess(copy, run)
        self.assertIn("python /opt/native_probe.py", install_body())

    def test_install_never_forces_what_the_probe_exists_to_catch(self):
        """Mutants, each restored:
        DOCKER-PIP-FORCE (--force-reinstall added to the pip install)
          AssertionError: '--force' unexpectedly found in 'set -eu; say() { printf ... --no-deps --force-reinstall "$1" ...
        DOCKER-PROBE-DROPPED (the final python /opt/native_probe.py replaced by true)
          AssertionError: False is not true : the probe is the last command of the RUN, so its status is the RUN's status
        """
        body = install_body()
        self.assertIn("pip install", body)
        self.assertIn("--no-deps", body)
        for flag in ("--force", "--force-reinstall", "--ignore-installed", "--break-system-packages", "--ignore-requires-python"):
            self.assertNotIn(flag, body)
        # The probe is the LAST command, so its status is the RUN's status, and it
        # follows the install (the development banner also names the probe).
        self.assertTrue(body.endswith("python /opt/native_probe.py"), "the probe is the last command of the RUN, so its status is the RUN's status")
        self.assertLess(body.index("pip install"), body.rindex("native_probe.py"))

    def test_base_images_stay_digest_pinned(self):
        names = set()
        for ins in dockerfile_instructions():
            if not ins.startswith("FROM "):
                continue
            words = [w for w in ins.split()[1:] if not w.startswith("--")]
            ref = words[0]
            if ref != "scratch" and ref not in names:
                self.assertIn("@sha256:", ref, "external base image must carry its digest")
            if "AS" in [w.upper() for w in words]:
                names.add(words[-1])

    def test_dockerignore_keeps_the_probe_and_the_wheels(self):
        patterns = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        # Mutant DOCKERIGNORE-EXCLUDES-PROBE (a `packaging` line added to .dockerignore):
        #   AssertionError: True is not false : packaging/native_probe.py is a build input
        for relative in ("packaging/native_probe.py", "packaging", "native-wheels", "native-wheels/" + X86):
            self.assertFalse(gate.ignored(relative, patterns), relative + " is a build input")

    def test_the_context_checker_accepts_the_dockerfile(self):
        for report in gate.check(ROOT):
            self.assertEqual([], report["findings"], report["build"])


@unittest.skipUnless(SH, "needs a POSIX sh to execute the Dockerfile RUN text")
class InstallScript(unittest.TestCase):
    def run_install(self, *, arch="x86_64", wheels=(), require="0", pip_status=0, probe_status=0):
        with tempfile.TemporaryDirectory(prefix="w16-image-native-") as scratch:
            scratch = Path(scratch)
            native = scratch / "native"
            native.mkdir()
            for name in wheels:
                (native / name).write_bytes(b"")
            calls = scratch / "calls.txt"
            body = install_body().replace("/native/", native.as_posix() + "/")
            self.assertNotIn("/native/", body.replace(native.as_posix(), ""), "every wheel path must come from the mounted directory")
            script = (
                'uname() { printf "%s\\n" "$FAKE_ARCH"; }\n'
                'pip() { printf "pip %s\\n" "$*" >> "$CALLS"; return "$PIP_STATUS"; }\n'
                'python() { printf "python %s\\n" "$*" >> "$CALLS"; return "$PROBE_STATUS"; }\n'
                + body + "\n"
            )
            (scratch / "run.sh").write_text(script, encoding="utf-8", newline="\n")
            env = dict(os.environ, REQUIRE_NATIVE=require, FAKE_ARCH=arch, CALLS=calls.as_posix(),
                       PIP_STATUS=str(pip_status), PROBE_STATUS=str(probe_status))
            proc = subprocess.run([SH, (scratch / "run.sh").as_posix()], env=env, capture_output=True, text=True, timeout=120)
            recorded = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
            return proc, recorded, native

    def test_wheel_for_this_architecture_is_installed_then_probed(self):
        """Mutant DOCKER-PROBE-DROPPED (the probe command replaced by true):
          AssertionError: 2 != 1 : ['pip install --no-cache-dir --disable-pip-version-check --no-deps .../native/astrodeck_native-0.1.0-cp311-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl']
        """
        for arch, wheel in (("x86_64", X86), ("aarch64", ARM), ("x86_64", PLAIN_X86)):
            proc, calls, native = self.run_install(arch=arch, wheels=[wheel], require="1")
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual(2, len(calls), calls)
            self.assertTrue(calls[0].startswith("pip install "), calls)
            self.assertIn(" --no-deps ", calls[0])
            self.assertTrue(calls[0].endswith((native / wheel).as_posix()), calls[0])
            self.assertEqual("python /opt/native_probe.py", calls[1], "the probe runs after the install, in the same RUN")

    def test_only_this_architectures_wheel_is_chosen_from_a_mixed_folder(self):
        """Mutants DOCKER-GLOB-ANY-ARCH (the arch dropped from the wheel glob) and DOCKER-GLOB-ANY-OS (the `linux` dropped):
          AssertionError: 0 != 1 : ERROR: several astrodeck_native wheels match this architecture; supply exactly one:
        """
        folder = [X86, ARM, WINDOWS, MAC_ARM, MAC_X86]
        for arch, wheel in (("x86_64", X86), ("aarch64", ARM)):
            proc, calls, native = self.run_install(arch=arch, wheels=folder, require="1")
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual(2, len(calls), calls)
            self.assertTrue(calls[0].endswith((native / wheel).as_posix()), calls[0])

    def test_a_foreign_wheel_is_no_wheel_at_all(self):
        """Mutants DOCKER-GLOB-ANY-ARCH and DOCKER-GLOB-ANY-OS (the arch, then the `linux`, dropped from the glob):
          AssertionError: 0 == 0 : REQUIRE_NATIVE=1 refuses an image with no wheel for x86_64
        Mutant DOCKER-BANNER-SILENT (the DEVELOPMENT IMAGE banner reworded):
          AssertionError: 'DEVELOPMENT IMAGE' not found in '... note: no native engine ...'
        """
        for arch, foreign in (("x86_64", [ARM, WINDOWS, MAC_ARM, MAC_X86]), ("aarch64", [X86, WINDOWS, MAC_ARM, MAC_X86]), ("armv7l", [X86, ARM])):
            hard, calls, _ = self.run_install(arch=arch, wheels=foreign, require="1")
            self.assertNotEqual(0, hard.returncode, "REQUIRE_NATIVE=1 refuses an image with no wheel for " + arch)
            self.assertEqual([], calls, "nothing foreign is installed")
            soft, calls, _ = self.run_install(arch=arch, wheels=foreign, require="0")
            self.assertEqual(0, soft.returncode, soft.stderr)
            self.assertEqual([], calls)
            self.assertIn("DEVELOPMENT IMAGE", soft.stderr)

    def test_plain_build_without_a_wheel_is_a_loud_development_image(self):
        """Mutant DOCKER-BANNER-SILENT (WARNING: DEVELOPMENT IMAGE... reworded to `note: no native engine`):
          AssertionError: 'DEVELOPMENT IMAGE' not found in '... note: no native engine ...'
        """
        proc, calls, _ = self.run_install(wheels=[], require="0")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual([], calls, "no install and no probe: there is no engine to probe")
        self.assertIn("DEVELOPMENT IMAGE", proc.stderr)
        self.assertIn("native_probe.py", proc.stderr, "the message says how to confirm the engine is absent")
        self.assertEqual("", proc.stdout.strip(), "the warning goes to stderr")

    def test_require_native_fails_the_build_without_a_wheel(self):
        """Mutant DOCKER-REQUIRE-NATIVE-SOFT (the REQUIRE_NATIVE=1 branch exits 0 instead of 1):
          AssertionError: 1 != 0 : REQUIRE_NATIVE=1 without a wheel must fail the build with status 1
        """
        proc, calls, _ = self.run_install(wheels=[], require="1")
        self.assertEqual(1, proc.returncode, "REQUIRE_NATIVE=1 without a wheel must fail the build with status 1")
        self.assertEqual([], calls)
        self.assertIn("REQUIRE_NATIVE", proc.stderr)
        self.assertNotIn("DEVELOPMENT IMAGE", proc.stderr, "a required build must not also announce itself as a development image")

    def test_ambiguous_wheels_fail_instead_of_guessing(self):
        """Mutant DOCKER-AMBIGUOUS-PICKS-FIRST (the exactly-one guard weakened from -ne 1 to -lt 1):
          AssertionError: 0 == 0 : two wheels for one architecture is not a development image
        """
        proc, calls, _ = self.run_install(wheels=[X86, PLAIN_X86], require="0")
        self.assertNotEqual(0, proc.returncode, "two wheels for one architecture is not a development image")
        self.assertEqual([], calls)

    def test_a_failed_install_fails_the_build(self):
        """Mutant DOCKER-PIP-FAILURE-SWALLOWED (the pip install given || true):
          AssertionError: 0 == 0 : a wheel pip refuses (for example a glibc newer than the image) must stop the build
        """
        proc, calls, _ = self.run_install(wheels=[X86], require="0", pip_status=1)
        self.assertNotEqual(0, proc.returncode, "a wheel pip refuses (for example a glibc newer than the image) must stop the build")
        self.assertEqual(1, len(calls), "and the probe must not run after a failed install")

    def test_a_failed_probe_fails_the_build_even_when_native_is_optional(self):
        """Mutant DOCKER-PROBE-DROPPED (the probe command replaced by true):
          AssertionError: 0 == 0 : an installed engine that does not work is never a development image
        """
        proc, calls, _ = self.run_install(wheels=[X86], require="0", probe_status=1)
        self.assertNotEqual(0, proc.returncode, "an installed engine that does not work is never a development image")
        self.assertEqual(2, len(calls))

    def test_require_native_accepts_only_zero_or_one(self):
        """Mutant DOCKER-REQUIRE-VALUE-UNCHECKED (the value check widened to `0|1|*`):
          AssertionError: 0 == 0 : yes
        """
        for value in ("yes", "true", "2", "01"):
            proc, calls, _ = self.run_install(wheels=[X86], require=value)
            self.assertNotEqual(0, proc.returncode, value)
            self.assertEqual([], calls, value)
        proc, calls, _ = self.run_install(wheels=[X86], require="")
        self.assertNotEqual(0, proc.returncode, "an empty value is a mistake to report, not a default to guess")


VERDICT_OK = '{"native_available": true, "native_version": "0.1.0"}'
VERDICT_BAD = '{"native_available": false, "error_type": "ImportError"}'
FAKE_IMAGE = "ghcr.io/example/astrodeck@sha256:" + "ab" * 32


def image_step_run(marker: str) -> str:
    """The shell text of the one release.yml image-job step whose run mentions `marker`."""
    jobs = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))["jobs"]
    found = [s["run"] for s in jobs["image"]["steps"] if marker in s.get("run", "")]
    assert len(found) == 1, "exactly one image step mentions " + marker
    return found[0]


@unittest.skipUnless(BASH, "needs bash to execute the release workflow's step text")
class ReleaseImageSteps(unittest.TestCase):
    """The probe and retag steps run as GitHub runs them (`bash -eo pipefail`) against a stubbed docker."""

    DOCKER_STUB = (
        'docker() {\n'
        '  printf "%s\\n" "$*" >> "$CALLS"\n'
        '  if [ "$1" != run ]; then return 0; fi\n'
        '  platform=none\n'
        '  while [ "$#" -gt 0 ]; do\n'
        '    if [ "$1" = --platform ]; then platform=$2; fi\n'
        '    shift\n'
        '  done\n'
        '  case "$platform" in\n'
        '    linux/amd64) printf "%s\\n" "$AMD64_OUT"; return "$AMD64_STATUS" ;;\n'
        '    linux/arm64) printf "%s\\n" "$ARM64_OUT"; return "$ARM64_STATUS" ;;\n'
        '  esac\n'
        '  return 97\n'
        '}\n'
    )

    def run_step(self, run, **env_extra):
        with tempfile.TemporaryDirectory(prefix="w16-image-step-") as scratch:
            scratch = Path(scratch)
            calls = scratch / "calls.txt"
            (scratch / "step.sh").write_text(self.DOCKER_STUB + run + "\n", encoding="utf-8", newline="\n")
            env = dict(os.environ, CALLS=calls.as_posix(), **env_extra)
            proc = subprocess.run([BASH, "--noprofile", "--norc", "-eo", "pipefail", (scratch / "step.sh").as_posix()],
                                  env=env, capture_output=True, text=True, timeout=120)
            recorded = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
            return proc, recorded

    def probe(self, amd=(VERDICT_OK, 0), arm=(VERDICT_OK, 0)):
        return self.run_step(image_step_run("native_probe.py"), IMAGE=FAKE_IMAGE,
                             AMD64_OUT=amd[0], AMD64_STATUS=str(amd[1]), ARM64_OUT=arm[0], ARM64_STATUS=str(arm[1]))

    def test_probe_runs_the_pushed_image_on_each_platform(self):
        """Mutant IMAGE-PROBE-PLATFORM-UNUSED (the --platform "$platform" argument deleted from the docker run;
        the structural test could not see it, both platform names still appear in the loop header):
          AssertionError: 0 != 97 :   (97 is the stub's refusal of a docker run that names no platform)
        """
        proc, calls = self.probe()
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(["run --rm --platform linux/amd64 %s python /opt/native_probe.py" % FAKE_IMAGE,
                          "run --rm --platform linux/arm64 %s python /opt/native_probe.py" % FAKE_IMAGE], calls)

    def test_a_probe_that_fails_stops_the_step_and_says_why(self):
        """Mutant IMAGE-PROBE-FAILURE-UNEXPLAINED (`| tee "$verdict"` changed to `> "$verdict"`, which has the effect of
        the original `out=$(docker run ...)` under `bash -e`: the verdict is discarded together with the step):
          AssertionError: 'ImportError' not found in 'native probe on linux/amd64\\n' : the failing verdict is shown in the log
        """
        for amd, arm in (((VERDICT_BAD, 1), (VERDICT_OK, 0)), ((VERDICT_OK, 0), (VERDICT_BAD, 1))):
            proc, calls = self.probe(amd=amd, arm=arm)
            self.assertNotEqual(0, proc.returncode, "a failing probe on either architecture fails the step")
            self.assertIn("ImportError", proc.stdout + proc.stderr, "the failing verdict is shown in the log")

    def test_a_printed_verdict_of_false_fails_even_when_the_status_is_zero(self):
        """Mutant IMAGE-PROBE-NOT-ASSERTED (the grep for the printed verdict deleted):
          AssertionError: 0 == 0 : the printed verdict is what is judged, not only the exit status
        """
        for amd, arm in (((VERDICT_BAD, 0), (VERDICT_OK, 0)), ((VERDICT_OK, 0), (VERDICT_BAD, 0)), (("", 0), (VERDICT_OK, 0))):
            proc, _ = self.probe(amd=amd, arm=arm)
            self.assertNotEqual(0, proc.returncode, "the printed verdict is what is judged, not only the exit status")

    def test_a_failing_docker_run_fails_even_when_it_printed_a_good_verdict(self):
        """Mutant IMAGE-PROBE-FAILURE-IGNORED (the docker run in the pipeline given || true):
          AssertionError: 0 == 0 : the container's own status counts
        """
        proc, _ = self.probe(amd=(VERDICT_OK, 1))
        self.assertNotEqual(0, proc.returncode, "the container's own status counts")

    def test_the_probed_digest_is_given_both_release_names_and_nothing_is_rebuilt(self):
        """Mutant IMAGE-RETAG-WRONG-NAME (--tag "$IMAGE_REPO:$REF_NAME" changed to --tag "$IMAGE_REPO:$IMAGE"):
          AssertionError: Lists differ: ['bui[49 chars]deck:v9.9.9 --tag ghcr.io/example/astrodeck:la[99 chars]bab'] != ['bui[49 chars]deck:ghcr.io/example/astrodeck@sha256:abababab[190 chars]bab']
        """
        proc, calls = self.run_step(image_step_run("imagetools create"), IMAGE_REPO="ghcr.io/example/astrodeck",
                                    IMAGE=FAKE_IMAGE, REF_NAME="v9.9.9")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(["buildx imagetools create --tag ghcr.io/example/astrodeck:v9.9.9 --tag ghcr.io/example/astrodeck:latest " + FAKE_IMAGE], calls)


if __name__ == "__main__":
    unittest.main()
