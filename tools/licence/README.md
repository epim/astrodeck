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

Review artifact-registry.json when a binary, dataset, font or raster changes. It
records exact input hashes, and what each application source file is, so a new
ASCII table in a Python file cannot be silently treated as original code; whether
a changed source file was reviewed is the ledger's question, below. Do not
automatically regenerate the registry just to turn the gate green. The two PWA icons are attributed to their project commits; the
nebula artwork is owner-made and registered as Apache-2.0 on the owner's
ruling of 2026-10-20 (#637).

### The reviewed-inputs ledger

`release-reviewed-inputs.json` is the one place a changed file is recorded as
reviewed. The release gate reads nothing else for that question, and
`artifact-registry.json` `reviewed_sources` only says what a file is (its
component and credit); its recorded hash approves nothing. The ledger names its
`base`, a commit of this history whose files count as reviewed. A file is
approved when its row's digest equals the file, or, with no row, when it equals
its copy at `base`. A row that contradicts the file approves nothing, even when
the file equals the base, so a stale record fails instead of hiding. A new
application module needs a ledger row and nothing else.

The digest is the SHA-256 of the file's text with a BOM removed and CRLF folded
to LF. A CRLF checkout and `git show HEAD:path` give the same digest.

Three commands keep it, all in `release_evidence.py`:

- `status` reads and reports, and exits 1 while work remains. It lists the
  changed files that have no row, the rows whose file is back to the base and
  can go, and the rows whose file changed since they were written.

      python tools/licence/release_evidence.py status

  Add `--json` for a list of paths, and `--ui-inventory` and
  `--frozen-provenance` to check the paths a UI build and a frozen build read.

- `sync` removes the rows that say nothing the base does not. It never adds a
  row and never changes a digest.

      python tools/licence/release_evidence.py sync

- `approve` records a digest for the changed bytes of the files named on its
  command line, with a review sentence.

      python tools/licence/release_evidence.py approve --path server/astrodeck/hub.py --review "Read the whole diff from the base: one new route, no dependency or data."

  `approve` is a statement by a person that they read the changed bytes of
  that file. It refuses without a named path (no patterns, no "all"), with a
  review under 40 characters, for a file equal to the base, and for a changed
  file when the review repeats the old row's text. Do not run it to turn the
  gate green.

A release commit changes the version line in two reviewed sources,
server/astrodeck/__init__.py and server/pyproject.toml. Run
`python tools/licence/version_bump_evidence.py X.Y.Z` in that commit. It writes
their ledger rows, and only when each file is HEAD's approved copy with its
single version line changed and nothing else; any other difference is refused
and needs `approve`. It then runs `sync` and prints `status`. It also checks
that README.md, tools/docs/setup-claims.json, ui/package.json and
ui/src/credits.generated.json carry X.Y.Z, naming the fix for any that does
not, and exits 1 if one lags or work remains.

`tools/licence/test_release_gate.py` runs in the release workflow's selftest
job, not on pull requests, so the ledger may trail the tree between releases;
it has to be current at the tag.

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
