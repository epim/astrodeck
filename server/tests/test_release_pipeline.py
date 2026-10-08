# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Release pipeline regressions inspect executed steps and publication ordering."""
from pathlib import Path
import shlex
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[2]
WORKFLOW=ROOT/".github/workflows/release.yml"

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
    def setUp(self):
        self.text=WORKFLOW.read_text(encoding="utf-8")
        self.jobs=yaml.safe_load(self.text)["jobs"]

    def test_publication_waits_for_all_artifacts(self):
        publish=self.jobs["publish"]
        self.assertTrue(set(publish["needs"]) == {"release","binaries"}, "publication must wait for source and every binary target")
        self.assertNotIn("if",publish)
        self.assertFalse(publish.get("continue-on-error",False))
        publishers=[name for name,job in self.jobs.items() if any(step.get("uses","").startswith("softprops/action-gh-release@") for step in job.get("steps",[]))]
        self.assertEqual(["publish"],publishers)

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
        download=next(s for s in job["steps"] if s.get("uses","").startswith("actions/download-artifact@"))
        self.assertEqual("accepted-*",download["with"]["pattern"])
        self.assertNotIn("if",job)
        publish=next(s for s in job["steps"] if s.get("uses","").startswith("softprops/action-gh-release@"))
        self.assertNotIn(".probe",publish["with"]["files"])
        self.assertIn("dist/*.whl",publish["with"]["files"])

    # --- the container image (#655, WP-655A) ---------------------------------
    def image_steps(self):
        job=self.jobs["image"]
        needs=job["needs"]
        self.assertIn("publish",[needs] if isinstance(needs,str) else needs,"the image is built only after every artifact gate and publication")
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

if __name__=="__main__":
    unittest.main()
