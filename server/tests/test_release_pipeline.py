# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Release pipeline regressions inspect executed steps and publication ordering."""
import hashlib
from pathlib import Path
import tempfile
import shlex
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[2]
WORKFLOW=ROOT/".github/workflows/release.yml"
# A tag push publishes; a workflow_dispatch publishes only when dry_run is false.
LIVE="github.event_name == 'push' || !inputs.dry_run"
# Source first (#1073): the source job passing is enough for the release to publish.
PUBLISH_IF="always() && needs.release.result == 'success' && ("+LIVE+")"
# The image needs the Linux wheels, so every binaries leg must have passed.
IMAGE_IF="needs.binaries.result == 'success' && ("+LIVE+")"

def commands(job):
    result=[]
    for index,step in enumerate(job["steps"]):
        for line in step.get("run", "").replace("\\\n", " ").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                result.append((index,shlex.split(line)))
    return result

def calls(job,script):
    return [(i,args) for i,args in commands(job) if len(args)>1 and args[0]=="python" and args[1]==script]

class ReleasePipeline(unittest.TestCase):
    def same(self,want,got,msg):
        self.assertTrue(want==got,msg)

    def setUp(self):
        self.text=WORKFLOW.read_text(encoding="utf-8")
        self.jobs=yaml.safe_load(self.text)["jobs"]

    def test_publication_waits_for_all_artifacts(self):
        publish=self.jobs["publish"]
        self.assertTrue(set(publish["needs"]) == {"release","binaries"}, "publication must wait for source and every binary target")
        self.same(PUBLISH_IF,publish.get("if"),"publication needs the source job and no dry run, not every binary")
        self.assertFalse(publish.get("continue-on-error",False))
        publishers=[name for name,job in self.jobs.items() if any(step.get("uses","").startswith("softprops/action-gh-release@") for step in job.get("steps",[]))]
        self.assertEqual(["publish"],publishers)

    def test_a_dispatch_is_a_dry_run_that_cannot_publish_or_push(self):
        """Named mutants (each run from a byte backup and restored): DRY-RUN-PUBLISHES (the publish job's `if` removed),
        DRY-RUN-PUSHES-IMAGE (the image job's `if` removed), DRY-RUN-DEFAULT-OFF (dry_run default true changed to false),
        TAG-CHECK-ALWAYS (the tag assertion step's `if` removed, so a dry run on a branch fails it)."""
        document=yaml.safe_load(self.text)
        triggers=document.get("on",document.get(True))
        dry_run=triggers["workflow_dispatch"]["inputs"]["dry_run"]
        self.assertEqual("boolean",dry_run["type"])
        self.assertIs(True,dry_run["default"],"a dispatch must be a dry run unless the operator opts out")
        self.assertEqual(["v*"],triggers["push"]["tags"])
        self.same(PUBLISH_IF,self.jobs["publish"].get("if"),"publish must be skipped on a dry run")
        self.same(IMAGE_IF,self.jobs["image"].get("if"),"image must be skipped on a dry run")
        # The only jobs that write outside the run: a release upload and a registry push.
        writers=[name for name,job in self.jobs.items() if job.get("permissions",{}).get("contents")=="write" or job.get("permissions",{}).get("packages")=="write"]
        self.assertEqual(["image","publish"],sorted(writers))
        steps=self.jobs["selftest"]["steps"]
        tag=[s for s in steps if s.get("name")=="Require a version tag matching the package"]
        plain=[s for s in steps if s.get("name")=="Require a plain package version"]
        self.assertEqual(1,len(tag));self.assertEqual(1,len(plain))
        self.same(LIVE,tag[0].get("if"),"a dry run on a branch has no version tag to match")
        self.assertNotIn("if",plain[0],"the package version is asserted plain X.Y.Z on every run")
        self.assertIn("X.Y.Z",plain[0]["run"])

    def test_artifact_names_use_the_package_version_not_the_ref(self):
        """Named mutant REF-NAME-IN-ARTIFACT (one `$RELEASE_VERSION` changed back to `${GITHUB_REF_NAME#v}`)."""
        self.assertNotIn("GITHUB_REF_NAME#v",self.text,"a dispatch ref is a branch: names come from pyproject")
        for name in ("release","publish"):
            steps=self.jobs[name]["steps"]
            resolve=[i for i,s in enumerate(steps) if "RELEASE_VERSION" in s.get("run","") and "GITHUB_ENV" in s.get("run","")]
            self.assertEqual(1,len(resolve),name+" resolves the version once")
            users=[i for i,s in enumerate(steps) if "$RELEASE_VERSION" in s.get("run","")]
            self.assertTrue(users,name+" uses the resolved version")
            self.assertTrue(all(resolve[0]<i for i in users),"the version is resolved before any step uses it")
            self.assertIn("X.Y.Z",steps[resolve[0]]["run"])

    def test_source_release_publishes_without_uncleared_binaries(self):
        """Named mutants (each run from a byte backup and restored): PUBLISH-NEEDS-BINARIES (the `always()` and
        source-result guard removed from publish's `if`), IMAGE-WITHOUT-BINARIES (the image's binaries-result clause
        removed), PUBLISH-UPLOADS-UNGATED (the download pattern widened from accepted-* so a gate report could ship),
        EXPECT-ALL-EIGHT (the script call replaced by an assertion that all eight payloads exist)."""
        publish=self.jobs["publish"]
        self.assertIn("always()",publish["if"],"a failed binaries leg must not skip the source release")
        self.assertIn("needs.release.result == 'success'",publish["if"],"the source release needs the source job's gates")
        self.assertNotIn("needs.binaries.result",publish["if"],"binaries do not gate the source release")
        self.assertIn("needs.binaries.result == 'success'",self.jobs["image"]["if"])
        # Only a platform's own accepted artifact can reach the release.
        binaries=self.jobs["binaries"]
        upload=[s for s in binaries["steps"] if s.get("with",{}).get("name")=="accepted-${{ matrix.platform }}"]
        self.assertEqual(1,len(upload))
        self.assertNotIn("if",upload[0],"accepted-<platform> exists only when the platform's gates passed")
        self.assertFalse(binaries["strategy"].get("fail-fast",True),"one platform failing must not discard another's cleared payloads")
        runs=[s.get("run","") for s in publish["steps"]]
        self.assertTrue(any("scripts/release_assets.py" in r for r in runs),"cleared payloads are checksummed by the tested script")
        self.assertFalse(any("expected four wheels" in r or "len(xs)==8" in r for r in runs),"the all-eight assertion is gone")
        gh=next(s for s in publish["steps"] if s.get("uses","").startswith("softprops/action-gh-release@"))
        self.assertTrue(gh["with"]["generate_release_notes"])
        self.assertIn("release-notes.md",gh["with"]["body_path"],"the withheld-platform note reaches the release body")
        order=[i for i,r in enumerate(runs) if "release_assets.py" in r]+[publish["steps"].index(gh)]
        self.assertEqual(sorted(order),order,"the note is written before the release is published")

    def test_header_describes_the_source_first_rule(self):
        head=self.text.split("\non:")[0]
        self.assertIn("No uncleared artifact is ever uploaded",head.replace("\n# "," "))
        self.assertNotIn("unless every platform",head)

    def test_source_gate_precedes_payload_upload(self):
        job=self.jobs["release"]
        gates=[i for i,args in calls(job,"tools/licence/audit_release.py") if args[args.index("--kind")+1]=="source-tar"]
        self.assertEqual(1,len(gates),"source archive must run one artifact gate")
        uploads=[(i,s) for i,s in enumerate(job["steps"]) if s.get("with",{}).get("name")=="accepted-source"]
        self.assertEqual(1,len(uploads))
        index,step=uploads[0]
        self.assertLess(gates[0],index)
        self.assertNotIn("if",step,"payload upload must require prior gate success")
        self.assertFalse(any(s.get("continue-on-error",False) for s in job["steps"]))

    def test_both_platform_artifacts_are_gated(self):
        job=self.jobs["binaries"]
        calls_=calls(job,"tools/licence/audit_release.py")
        kinds={args[args.index("--kind")+1] for _,args in calls_}
        self.assertTrue(kinds == {"native-wheel","frozen"}, "both native-wheel and frozen artifact gates must execute")
        upload=next(i for i,s in enumerate(job["steps"]) if s.get("with",{}).get("name")=="accepted-${{ matrix.platform }}")
        self.assertTrue(all(i<upload for i,_ in calls_))
        self.assertNotIn("if",job["steps"][upload])
        self.assertFalse(any(s.get("continue-on-error",False) for s in job["steps"]))
        self.assertIn("dist/*.whl",job["steps"][upload]["with"]["path"])

    def test_four_native_targets(self):
        rows=self.jobs["binaries"]["strategy"]["matrix"]["include"]
        self.assertEqual({"linux-x86_64","linux-arm64","windows-x86_64","macos-arm64"},{row["platform"] for row in rows})
        self.assertEqual(4,len(rows))
        job=self.jobs["binaries"]
        native=calls(job,"packaging/build_native.py")
        binary=calls(job,"packaging/build_binary.py")
        self.assertEqual(1,len(native));self.assertEqual(1,len(binary))
        self.assertLess(native[0][0],binary[0][0])
        self.assertIn("--native-wheel",binary[0][1])
        self.assertNotIn("--no-smoke",binary[0][1])

    def test_dss2_is_never_fetched_or_passed(self):
        runs="\n".join(" ".join(args) for job in self.jobs.values() for _,args in commands(job))
        self.assertNotIn("survey_pack fetch",runs)
        self.assertNotIn("--survey-pack",runs)
        source=calls(self.jobs["release"],"scripts/build_release.py")
        self.assertEqual(1,len(source));self.assertIn("--strict",source[0][1])
        self.assertNotIn("ui",source[0][1][source[0][1].index("--allow-missing")+1:])

    def test_each_build_records_fresh_credits_and_ui(self):
        for name in ("release","binaries"):
            job=self.jobs[name]
            self.assertEqual(1,len(calls(job,"tools/licence/build_credits.py")))
            self.assertTrue(any(len(args)>1 and args[:2]==["node","tools/licence/build_ui_inventory.mjs"] and "--credits" in args for _,args in commands(job)))
        self.assertEqual(1,len(calls(self.jobs["binaries"],"tools/licence/release_provenance.py")))

    def test_reviewed_git_base_is_available(self):
        for name in ("release","binaries"):
            checkout=next(step for step in self.jobs[name]["steps"] if step.get("uses","").startswith("actions/checkout@"))
            self.assertEqual(0,checkout["with"].get("fetch-depth"), "artifact source review needs the pinned historical Git base")

    def test_diagnostics_cannot_be_published_as_payload(self):
        job=self.jobs["publish"]
        self.same(PUBLISH_IF,job.get("if"),"the payload download is gated like the publication")
        download=next(s for s in job["steps"] if s.get("uses","").startswith("actions/download-artifact@"))
        self.assertEqual("accepted-*",download["with"]["pattern"])
        publish=next(s for s in job["steps"] if s.get("uses","").startswith("softprops/action-gh-release@"))
        self.assertNotIn(".probe",publish["with"]["files"])
        self.assertIn("dist/*.whl",publish["with"]["files"])

    # --- the container image (#655, WP-655A) ---------------------------------
    def image_steps(self):
        job=self.jobs["image"]
        needs=job["needs"]
        self.assertIn("publish",[needs] if isinstance(needs,str) else needs,"the image is built only after every artifact gate and publication")
        self.same(IMAGE_IF,job.get("if"),"a dry-run dispatch pushes no image, and a withheld binary stops it")
        self.assertEqual({"publish","binaries"},set(needs),"the image needs the source release and the Linux wheels")
        self.assertFalse(any(s.get("continue-on-error",False) for s in job["steps"]))
        def one(predicate,what):
            found=[(i,s) for i,s in enumerate(job["steps"]) if predicate(s)]
            self.assertEqual(1,len(found),what)
            return found[0]
        download=one(lambda s:s.get("uses","").startswith("actions/download-artifact@"),"exactly one artifact download")
        stage=one(lambda s:"astrodeck_native-" in s.get("run",""),"exactly one step stages the wheels")
        build=one(lambda s:s.get("uses","").startswith("docker/build-push-action@"),"exactly one image build")
        probe=one(lambda s:"native_probe.py" in s.get("run",""),"exactly one native probe step")
        retag=one(lambda s:"imagetools create" in s.get("run",""),"exactly one retag step")
        return job,download,stage,build,probe,retag

    def test_image_downloads_the_linux_native_wheels_it_installs(self):
        """Named mutants, each run from a byte backup and restored, and the failure each produced:
        IMAGE-DOWNLOAD-REMOVED (the actions/download-artifact step deleted)
          AssertionError: 1 != 0 : exactly one artifact download
        IMAGE-CONTEXT-IS-DOWNLOAD-DIR (build-contexts native= pointed at the download folder, which also holds the frozen binaries)
          AssertionError: '${{ runner.temp }}/accepted' == '${{ runner.temp }}/accepted' : the build context must not be the download folder
        """
        _,download,stage,build,_,_=self.image_steps()
        with_=download[1]["with"]
        self.assertEqual("accepted-linux-*",with_["pattern"],"only the linux payloads: the image needs neither the windows or macos wheels nor the source bundle")
        self.assertTrue(with_["merge-multiple"])
        self.assertLess(download[0],stage[0])
        self.assertLess(stage[0],build[0])
        # The download folder also receives the two frozen binaries (hundreds of
        # megabytes); only the wheels may reach the build, from a clean directory.
        contexts=dict((k.strip(),v.strip()) for k,_,v in (l.partition("=") for l in build[1]["with"]["build-contexts"].splitlines() if l.strip()))
        self.assertEqual(["native"],list(contexts),"the Dockerfile stage `native` is what the wheels replace")
        self.assertNotEqual(with_["path"],contexts["native"],"the build context must not be the download folder")
        self.assertNotIn("astrodeck-linux",stage[1]["run"],"a frozen binary must not enter the image build")

    def test_image_build_requires_the_native_engine(self):
        """Named mutant IMAGE-NATIVE-REQUIRED (build-args REQUIRE_NATIVE=1 changed to REQUIRE_NATIVE=0), run from a byte backup and restored:
          AssertionError: 'REQUIRE_NATIVE=1' not found in {'REQUIRE_NATIVE=0'} : a release image without the engine must fail the build, not ship as a development image
        """
        _,_,_,build,_,_=self.image_steps()
        args={l.strip() for l in build[1]["with"]["build-args"].splitlines() if l.strip()}
        self.assertIn("REQUIRE_NATIVE=1",args,"a release image without the engine must fail the build, not ship as a development image")
        self.assertEqual({"linux/amd64","linux/arm64"},set(build[1]["with"]["platforms"].split(",")))
        self.assertTrue(build[1]["with"]["push"])

    def test_nothing_user_visible_is_tagged_before_the_probe_passes(self):
        """Named mutants, each run from a byte backup and restored, and the failure each produced:
        IMAGE-PROBE-BEFORE-TAG (the imagetools retag step moved ahead of the probe step)
          AssertionError: 8 not less than 7 : the retag that creates :latest comes after the probe
        IMAGE-BUILD-TAGS-LATEST (the candidate build tagged :latest directly)
          AssertionError: False is not true : the build pushes a candidate name only
        IMAGE-PROBE-FAILURE-IGNORED (the docker run inside the probe given || true)
          AssertionError: '||' unexpectedly found in 'for platform in linux/amd64 linux/arm64; do ...
        IMAGE-RETAG-NOT-BY-DIGEST (the retag names the candidate tag, not the probed digest)
          AssertionError: 'steps.candidate.outputs.digest' not found in '${{ env.IMAGE_REPO }}:candidate'
        """
        job,_,_,build,probe,retag=self.image_steps()
        tags=[l.strip() for l in build[1]["with"]["tags"].splitlines() if l.strip()]
        self.assertTrue(tags and all("candidate" in t for t in tags),"the build pushes a candidate name only")
        for t in tags:
            self.assertFalse(t.endswith(":latest") or "ref_name" in t,"the build must not give the image a user-visible name")
        self.assertLess(build[0],probe[0])
        self.assertLess(probe[0],retag[0],"the retag that creates :latest comes after the probe")
        self.assertIn(":latest",retag[1]["run"])
        self.assertIn("REF_NAME",retag[1]["run"])
        for step in job["steps"]:
            if step is not retag[1]:
                self.assertNotIn(":latest",step.get("run","")+str(step.get("with","")),"only the retag step may create :latest")
        # What is probed is what is promoted: both name the build's own digest.
        digest="steps."+build[1]["id"]+".outputs.digest"
        for _,step in (probe,retag):
            self.assertIn(digest,step["env"]["IMAGE"])
            self.assertNotIn("if",step,"neither the probe nor the retag may be skipped")
            self.assertNotIn("||",step["run"],"a failed probe must stop the retag")

    def test_image_probe_runs_the_baked_probe_on_both_platforms(self):
        """Named mutants, each run from a byte backup and restored, and the failure each produced:
        IMAGE-PROBE-ONE-ARCH (the probe loop reduced to linux/amd64)
          AssertionError: 'linux/arm64' not found in 'for platform in linux/amd64; do ...
        IMAGE-PROBE-NOT-ASSERTED (the grep for the printed verdict deleted)
          AssertionError: '"native_available": true' not found in 'for platform in linux/amd64 linux/arm64; do ...
        """
        _,_,_,_,probe,_=self.image_steps()
        text=probe[1]["run"]
        for platform in ("linux/amd64","linux/arm64"):
            self.assertIn(platform,text)
        self.assertIn("docker run",text)
        self.assertIn("python /opt/native_probe.py",text,"the probe the Dockerfile copies into the image, run inside it")
        self.assertIn('"native_available": true',text,"the printed verdict is asserted, not only the exit status")

    def test_image_workflow_names_match_the_dockerfile(self):
        """Named mutants DOCKER-NATIVE-STAGE-REMOVED and DOCKER-PROBE-COPY-REMOVED (a Dockerfile line deleted), each restored:
          AssertionError: 'FROM scratch AS native' not found in [...] : the build context name `native` replaces this stage
          AssertionError: 'COPY packaging/native_probe.py /opt/native_probe.py' not found in [...] : the path the probe step runs
        """
        lines=(ROOT/"Dockerfile").read_text(encoding="utf-8").splitlines()
        self.assertIn("FROM scratch AS native",lines,"the build context name `native` replaces this stage")
        self.assertTrue(any(l.startswith("ARG REQUIRE_NATIVE=") for l in lines),"the build argument the workflow sets")
        self.assertIn("COPY packaging/native_probe.py /opt/native_probe.py",lines,"the path the probe step runs")

def load_release_assets():
    import importlib.util
    spec=importlib.util.spec_from_file_location("release_assets",ROOT/"scripts/release_assets.py")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

WHEELS={
    "linux-x86_64":"astrodeck_native-0.1.0-cp311-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
    "linux-arm64":"astrodeck_native-0.1.0-cp311-abi3-manylinux_2_17_aarch64.manylinux2014_aarch64.whl",
    "windows-x86_64":"astrodeck_native-0.1.0-cp311-abi3-win_amd64.whl",
    "macos-arm64":"astrodeck_native-0.1.0-cp311-abi3-macosx_11_0_arm64.whl",
}

class ReleaseAssets(unittest.TestCase):
    """scripts/release_assets.py: what the source-first publish job does with the accepted-* payloads.

    Named mutants (each run from a byte backup and restored): HALF-PLATFORM-ACCEPTED (the exactly-one-executable-and-one-wheel
    rule removed), WITHHELD-NOT-NAMED (the note returns an empty string), EXE-WITHOUT-CHECKSUM (executables skipped when
    checksumming), WINDOWS-WHEEL-MISATTRIBUTED (win_amd64 wheels attributed to linux-x86_64)."""
    def setUp(self):
        self.mod=load_release_assets()
        self.tmp=tempfile.TemporaryDirectory()
        self.dist=Path(self.tmp.name)/"dist"
        self.dist.mkdir()
        (self.dist/"astrodeck-0.3.45.tar.gz").write_bytes(b"source")
    def tearDown(self):
        self.tmp.cleanup()
    def put(self,*platforms,exe=True,wheel=True):
        for platform in platforms:
            if exe: (self.dist/self.mod.EXECUTABLES[platform]).write_bytes(b"exe-"+platform.encode())
            if wheel: (self.dist/WHEELS[platform]).write_bytes(b"whl-"+platform.encode())
    def run_script(self):
        notes=Path(self.tmp.name)/"notes.md"
        self.assertEqual(0,self.mod.main(["--dist",str(self.dist),"--notes",str(notes)]))
        return notes.read_text(encoding="utf-8")

    def test_source_alone_publishes_and_names_every_withheld_platform(self):
        note=self.run_script()
        for platform in self.mod.EXECUTABLES:
            self.assertIn(platform,note,"every withheld platform is named")
        self.assertIn("licence gates have not passed",note)
        self.assertIn("#1073",note)
        self.assertEqual([],sorted(p.name for p in self.dist.glob("*.sha256")),"no binary, so no binary checksum")

    def test_only_the_cleared_platform_is_checksummed_and_the_rest_are_named(self):
        self.put("linux-x86_64","linux-arm64")
        note=self.run_script()
        sums=sorted(p.name for p in self.dist.glob("*.sha256"))
        self.assertEqual(sorted(n+".sha256" for n in (self.mod.EXECUTABLES["linux-x86_64"],self.mod.EXECUTABLES["linux-arm64"],WHEELS["linux-x86_64"],WHEELS["linux-arm64"])),sums)
        self.assertNotIn("linux-x86_64",note);self.assertNotIn("linux-arm64",note)
        self.assertIn("windows-x86_64",note);self.assertIn("macos-arm64",note)
        digest=hashlib.sha256(b"exe-linux-arm64").hexdigest()
        self.assertEqual(digest+"  astrodeck-linux-arm64\n",(self.dist/"astrodeck-linux-arm64.sha256").read_text(encoding="utf-8"))

    def test_a_fully_cleared_release_has_an_empty_note(self):
        self.put(*self.mod.EXECUTABLES)
        self.assertEqual("",self.run_script())
        self.assertEqual(8,len(list(self.dist.glob("*.sha256"))))

    def test_a_platform_with_only_one_of_its_two_payloads_fails(self):
        for kwargs in ({"exe":True,"wheel":False},{"exe":False,"wheel":True}):
            with self.subTest(**kwargs):
                for f in self.dist.iterdir():
                    if f.name!="astrodeck-0.3.45.tar.gz": f.unlink()
                self.put("windows-x86_64",**kwargs)
                with self.assertRaises(ValueError):
                    self.mod.plan(self.dist)

    def test_each_wheel_is_attributed_to_its_own_platform(self):
        for platform,wheel in WHEELS.items():
            self.assertEqual(platform,self.mod.wheel_platform(wheel))
        with self.assertRaises(ValueError):
            (self.dist/"astrodeck_native-0.1.0-cp311-abi3-freebsd_14_x86_64.whl").write_bytes(b"x")
            self.mod.plan(self.dist)

if __name__=="__main__":
    unittest.main()
