# Site maintenance

The site is static HTML, CSS and JavaScript. It has no runtime packages, external
fonts, trackers or build step. Only `site/` is published.

## Local checks

Use Python 3.12 and install `tools/site/requirements.txt` in a dedicated environment.
The review run used a private `.probe/site-check-deps` target, not the shared server
environment. With that target on `PYTHONPATH`:

```text
python -B -m unittest discover -s tools/site -p "test_*.py"
python -B tools/site/mutate_checks.py
python -B tools/site/check_site.py --privacy --require-privacy
```

The privacy check imports the same scanner used by `tools/privacy_scan.py`.
Needles stay in its configured external file or environment variable. Reports
identify files without printing matches. HTML validation uses html5lib's HTML5
parser and tinycss2 tokenization plus project checks for landmarks, naming, links and resources. Rendered
browser review complements these checks; parsing alone is not a complete
accessibility assessment.

## Screenshots

Run the capture helper on Windows with a new config path under this worktree's
`.probe/`, a private UI build, and the existing server venv. It owns the fresh
`server_ctl.py` launch and stops its process tree when capture ends, including
a failed launch health check. It refuses existing config/capture directories
and port 8800. For example:

```text
python -B tools/site/capture_sim.py --config-dir .probe/site-sim-new --port 8876 --ui-dir .probe/site-ui --venv-python path/to/server/.venv/Scripts/python.exe
```

The helper verifies the new directory marker, process ownership of the selected
loopback listener and simulator mode. It saves a
synthetic M31 mosaic without running it, takes one one-second simulator exposure,
dismisses the normal first-time notice through its button and fits the flow graph
through the app's Fit control. No site settings are changed. It captures the
ordinary day display and red night display; the application's day display also
has a dark background. Filename light/dark labels describe capture settings.

The helper writes `screenshot-provenance.json` with a source revision, viewport,
config provenance and SHA-256 for each PNG. Every regeneration resets visual
review to pending. Inspect every image before approving it. Do not show site
settings, mount altitude/azimuth, or survey imagery. The flow editor's bundled
decorative nebula background is not an Atlas survey layer.

## Claims and reviews

`content.json` is the editorial source for page copy. The HTML remains directly
editable; keep the copy and `claims-ledger.md` aligned after substantive edits.
The ledger's claim IDs appear as `data-claims` on the corresponding HTML
sections. `content-audit.md` records removed or corrected old claims.

Review results and named-mutation evidence live beside the scripts. A Supported
hardware family means an implemented adapter or discovery path. A Verified row
names a specific recorded check; it is not an unattended-night certification.

## Publishing handoff

The Pages workflow checks pull requests without deployment. Publishing requires
a passing check job on `main` and a repository owner to have enabled GitHub Pages
with GitHub Actions. The workflow explicitly leaves automatic enablement off.
This change does not enable Pages or deploy anything.

Action majors were checked against the upstream repositories on 2026-10-01:
[checkout v7](https://github.com/actions/checkout/releases/tag/v7.0.1),
[setup-python v7](https://github.com/actions/setup-python/releases/tag/v7.0.0),
[configure-pages v6](https://github.com/actions/configure-pages/releases/tag/v6.0.0),
[upload-pages-artifact v5](https://github.com/actions/upload-pages-artifact/releases/tag/v5.0.0),
and [deploy-pages v5](https://github.com/actions/deploy-pages/releases/tag/v5.0.1).
The artifact and deployment actions use compatible Pages artifacts.

The owner still needs to review and merge this branch, resolve any release
packaging blockers independently, enable Pages, and verify the first deployment.
