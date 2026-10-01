# Artifact licence checks

These tools inspect local artifacts and dependency graphs. They do not grant
permission or certify artifacts that were not inspected. The October audit's
known rights and packaging gaps intentionally produce failures.

## Reproduce the checks

Use an existing Python environment with the project's test dependencies, the
locked npm dependencies, and the Cargo registry cache. Use a private environment
for PyInstaller; do not install or upgrade packages in the shared development
environment. Build outputs and downloaded dependencies belong in ignored
.probe directories in this checkout.

~~~text
python tools/gen_credits.py --check
python -m pytest -n 0 -q server/tests/test_credits.py server/tests/test_licensing.py server/tests/test_licence_artifact.py server/tests/test_licence_frozen.py server/tests/test_licence_dependencies.py
node tools/licence/build_ui_inventory.mjs
python tools/licence/audit_artifact.py --build
python tools/licence/audit_dependencies.py
<private-pyinstaller-python> tools/licence/audit_frozen.py <owned-executable>
python tools/licence/mutate_gates.py
~~~

The UI inventory uses the normal Vite configuration with the runner loader.
It records each output hash and actual chunk inputs, including compiler helpers.
It also records the input credits hash. It does not install npm packages.

The tarball gate invokes the real scripts/build_release.py packager in strict
mode with ASTAP explicitly waived. It reads the result without extracting it.
An existing archive can instead be passed with --archive; use --ui-inventory
to identify the corresponding build evidence. The report defaults to
.probe/licence/artifact-gate-report.json.

The dependency gate runs cargo metadata --locked --offline and reads every npm
lock entry, including build dependencies. Its policy result is deliberately
separate from artifact clearance. An allowed metadata licence cannot dispose
of an unknown native component inside a wheel. The reviewed BlueOak-1.0.0
entry follows the [publisher's text-or-link rule](https://blueoakcouncil.org/license/1.0.0.html).
The distinct BSD-3-Clause-Open-MPI notice requirement follows
[the published licence](https://opensource.org/license/bsd-3-clause-open-mpi).

The frozen gate reads CArchive members using PyInstaller's reader without
running the executable. The current Windows baseline contains unresolved
findings; matching its bytes never produces release clearance. A new platform
or changed executable requires a new evidence review, not a copied baseline
hash. Native wheels, container images and appliance images need their own
inspection; the existing reports state which were and were not available.

## Updating evidence

Review artifact-registry.json when a binary, dataset, font, raster or application
source changes. It records exact input hashes and a normalized UTF-8 source
manifest so a new ASCII table in a Python file cannot be silently treated as
original code. Do not automatically regenerate this manifest just to turn the
gate green. The two PWA icons are attributed to their project commits; the
unverified nebula artwork stays blocked.

A registered credit must match the reviewed licence and resolve to intact full
texts where required. Remote survey data are recorded separately from bundled
data. ODbL database grants do not replace original-image rights. Owner-required
policy exceptions remain failures at the artifact gate.

bundled-components.json records the exact observed Windows interpreter,
native-library and retained setuptools-vendor versions, source references,
artifact hashes and canonical full texts. It is a manual supplement grounded
in artifact evidence, not an automatic inventory of every future wheel.

The generated credits include input fingerprints and the current project
version. Tests compare those public outputs against the current files without
calling the generator. The old dependency-coverage tests missed a stale project
version even though all dependency entries/texts matched; see
[the baseline diagnosis](credits-baseline-drift.json) and issue #639.

## Reports and mutation evidence

- [Service and data review](service-data-audit.md): primary current terms,
  quoted wording, separate readings, rights layers and independent review.
- [Artifact and dependency review](dependency-artifact-audit.md): exact files,
  hashes, native components and platform limits.
- [Final gate results](final-gate-results.json): artifact hashes, member counts,
  expected failures and separate metadata-policy result.
- [Mutation evidence](mutation-evidence.md): selected failing assertions and
  exact-byte restoration for every named mutant.
- [Completion report](completion-report.md): changes, validation and owner work.

mutate_gates.py temporarily modifies owned tool/generated files, runs the
selected regression and restores original bytes in a finally block. Run it
only in an isolated worktree without another writer. It requires a green
baseline and green restored suite. One mutant allows a foreign vendor DLL
inserted before the real tarball packager runs; the regression must kill it.
