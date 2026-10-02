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

if __name__=="__main__":
    unittest.main()
