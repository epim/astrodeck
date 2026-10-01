# Tooling review

Reviewed 2026-10-01 by the site-design agent, independently of the root-authored tooling. Scope: `tools/site/check_site.py`, `test_site_checks.py`, `mutate_checks.py`, `capture_sim.py`, `screenshot-provenance.json`, and `.github/workflows/pages.yml`. No production source was edited during review. Findings describe the initial reviewed versions; the parent is remediating them separately.

## Remediation verification

The original findings below are retained as historical evidence. Independent retest of the revised implementation produced these dispositions:

| Finding | Disposition | Verification |
| --- | --- | --- |
| Workflow JavaScript exit status | Resolved | The exact replacement Bash loop exits 1 and reports SyntaxError for the disposable invalid file. |
| Entity-encoded privacy value | Resolved for the reported case | The synthetic HTML entity fixture now produces a forbidden-site-value finding. Both raw and decoded source are scanned. |
| Raw-reference diagnostics | Resolved | The missing-reference fixture remains rejected, without the synthetic forbidden value in diagnostics. Privacy runs before structural checks when requested and before validation/testing in the workflow. |
| CSS/SVG dependency forms | Resolved | Every original CSS uppercase/import-comment, style element/attribute, standalone SVG and xlink fixture is rejected. Canonical-only pages still fail missing-stylesheet validation. |
| Existing/stale capture target | Resolved in the new launch-only design | Negative PIDs, existing config directories and existing capture directories are rejected. The helper now creates its own isolated server and verifies listener ownership before capture. |

The final revised regression suite independently passed **34 tests**. All **13 named mutants** were independently killed in a temporary copy; the copied Python and workflow bytes matched their pre-run hashes afterward. Only actual forks skip the unavailable-secret PR privacy check. All original findings and the subsequent cleanup finding are resolved in the reviewed scope.

### Resolved additional P2: cleanup of a partially failed launch

In the first revised capture helper, the nonzero `server_ctl start` check occurs before the `try/finally` that stops the server. The launcher can start a process and create a marker, then return nonzero when health or simulator connection times out. That leaves the owned probe process running.

A mocked subprocess test created a matching positive marker and PID file on `start`, then returned exit 3. `capture_sim.main()` raised the launch failure with **one start call and zero stop calls**. No real process was launched. Move launch into the cleanup scope, and on failure stop only a process whose fresh marker/PID/ownership can be verified. The correction was independently verified: a failed launcher with a valid newly owned process now causes exactly one stop call, while capture is never invoked. The final suite and FAILED-LAUNCH-CLEANUP mutant also protect this behavior.

## Findings

### P1: JavaScript syntax failures do not fail the Pages job

The workflow runs `find site -name '*.js' -exec node --check {} \;`. A failed command makes that find expression false for the file; it does not make find itself fail. A temporary `bad.js` containing `const = ;` produced a Node `SyntaxError` while find returned **0**. The publish dependency therefore remains green when shipped JavaScript cannot parse.

Exact reproduction on a disposable directory:

```sh
printf 'const = ;\n' > bad.js
find . -name '*.js' -exec node --check {} \;
echo "$?"
# Node prints SyntaxError; status is 0.
```

The actual review used the installed GNU find and Node executables through Python subprocess, with a temporary directory. Fix by running an explicit failing loop, a Python subprocess loop with `check=True`, or a find form whose exit status propagates command failure. Add a regression that runs the actual workflow check command against an invalid file.

### P1: The privacy gate misses rendered entity-encoded values

`privacy()` scans raw file text. With the synthetic needle `private-test-label`, an HTML paragraph containing `private&#45;test&#45;label` passes both `check()` and `privacy(site, True)`. The browser renders the forbidden string. This is a concrete bypass of the site-specific publication check, even though the existing raw-SVG privacy test passes.

Fix by scanning both raw input and decoded HTML/SVG text and attributes. Include numeric/named HTML entity cases, escaped attributes and encoded URL values. Preserve the existing raw-source scan for comments and non-rendered content. All diagnostic output must continue to omit the matched value.

### P1: Link diagnostics print source values before the privacy check

A fixture anchor `href="private-test-label.html"` produces `index.html: missing local reference private-test-label.html`. The site checker promises not to print private values, but interpolates raw references in missing-file and missing-fragment diagnostics. CSS diagnostics do the same. The workflow runs ordinary checks before its privacy step, so a forbidden label or coordinate embedded in a reference can reach logs first.

Fix by removing raw URL/reference strings from diagnostics and reporting only a safe file identifier plus the error class. Perform the privacy scan before other operations that could render source-derived values. Test with a synthetic forbidden value in href, src, a fragment and CSS URLs, asserting the value is absent from captured stdout/stderr.

### P2: Legal CSS and SVG resource forms bypass dependency validation

All of the following returned an empty findings list in a valid temporary HTML fixture with an existing stylesheet:

| Input | Missed outcome |
| --- | --- |
| CSS `body { background: URL(https://example.invalid/pixel.png); }` | External image resource |
| CSS `@import/**/"https://example.invalid/style.css";` | External stylesheet |
| HTML `<style>@import "https://example.invalid/style.css";</style>` | External stylesheet |
| HTML `<p style="background:url(missing.png)">Sample</p>` | Missing local image |
| Referenced SVG with `<image href="https://example.invalid/image.png"/>` | External image inside a local asset |
| Referenced SVG with a style element importing an external stylesheet | External stylesheet inside a local asset |
| Inline SVG `<image xlink:href="missing.png"/>` with the xlink namespace | Missing local image |

The stylesheet regex is case-sensitive, does not parse comments, and is only applied to `.css` files. Standalone SVG resources and namespaced attributes are not traversed. Use a CSS parser for external stylesheets, inline styles and SVG styles, and inspect standalone SVG href/xlink:href references relative to their source files. Add one independent regression for each legal form above.

A canonical-only page **does fail correctly** with `missing stylesheet`. That suspected bypass was not reproduced.

### P2: Capture preflight does not bind the HTTP server to the accepted probe directory

`checked_config()` accepts a marker with matching path/port and `pid: -1`. It does not inspect the marker PID, `server.pid`, the process owning the port, or whether the running process uses that configuration. The subsequent HTTP proof is only `status.mode == "sim"`, after which the helper mutates flows and starts a capture.

The aggregate mode is not a complete hardware proof: `hub.py` describes it as the legacy primary-backend scalar, and `apply_connect_result()` can retain explicitly assigned roles from different backends. A stale probe marker plus an unrelated simulator-mode or mixed-backend server on the same port is not excluded.

Read-only inspection of the authorized synthetic server at **127.0.0.1:8876 only** found:

- `connected[role]` includes `name`, `kind`, `connected`, `host`, `port`, `dev_type`, `dev_num`, `role`, and `backend`.
- The legacy simulator reports an empty backend string for every connected role.
- `backend_links` is empty for that legacy connection path. When populated, its implementation exposes role/ok/error/attempted/connected, without backend identifiers.
- `Device.hardware` exists internally but is not emitted by `describe()`.

Therefore, accepting a blank backend or relying on `backend_links` does not add the desired proof. Bind the marker, PID file and port owner to the expected server process; verify a fresh/default probe configuration before issuing mutations. Check `status.site.is_default` without logging any site values. A stronger reusable contract would have the launched probe return an unambiguous per-device simulation identity or an instance nonce bound to the launcher. Do not query or mutate an unrelated endpoint to test this.

The original screenshots appeared safe. This historical finding concerned the helper's claimed isolation on later runs and is resolved by the launch-only process ownership design described above.

## Reproducing checker findings without touching the site

Run with the existing Python environment, `-B`, and `PYTHONPATH` containing `.probe/site-check-deps` and `tools/site`. The following creates only a disposable directory and uses a synthetic privacy label:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import check_site

base = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Fixture</title><link rel="stylesheet" href="style.css">HEAD</head><body><main><h1>Fixture</h1>BODY</main></body></html>'
with TemporaryDirectory() as directory:
    site = Path(directory)
    def probe(body='', css='body{color:black}', head=''):
        (site/'style.css').write_text(css, encoding='utf-8')
        (site/'index.html').write_text(base.replace('BODY', body).replace('HEAD', head), encoding='utf-8')
        return check_site.check(site)
    assert probe(css='body{background:URL(https://example.invalid/pixel.png)}') == []
    assert probe(css='@import/**/"https://example.invalid/style.css";') == []
    assert probe(head='<style>@import "https://example.invalid/style.css";</style>') == []
    assert probe(body='<p style="background:url(missing.png)">Sample</p>') == []
    (site/'icon.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.invalid/image.png"/><style>@import "https://example.invalid/style.css";</style></svg>', encoding='utf-8')
    assert probe(body='<img src="icon.svg" alt="Diagram">') == []
    assert probe(body='<svg xmlns:xlink="http://www.w3.org/1999/xlink"><image xlink:href="missing.png"/></svg>') == []
    assert any('private-test-label' in e for e in probe(body='<a href="private-test-label.html">Link</a>'))
    assert probe(body='<p>private&#45;test&#45;label</p>') == []
    with patch.dict(os.environ, {'ASTRODECK_PRIVACY_NEEDLES':'private-test-label'}):
        assert check_site.privacy(site, True) == []
```

Capture boundary reproduction is similarly local: create a temporary `.probe/config` directory, write `.astrodeck-probe` containing its resolved `dir`, port `8876`, and PID `-1`; patch `capture_sim.ROOT` to the temporary root; `checked_config(config, 8876)` returns the path. No server is needed to reproduce the missing ownership check.

## Checks that passed

The existing unittest command passed **19 tests**. The six existing mutants were independently rerun from copies under a temporary `tools/site` tree, with a copied privacy-scanner dependency. All six were killed by the expected assertion; the clean suite passed afterward. SHA-256 comparisons of the four copied source files before/after showed exact byte restoration. The live worktree was not mutated by this run. This supports the six named checks, but does not cover the bypasses above. `mutate_checks.py` hardcodes the final test count, so update that evidence if the suite grows.

The initial four PNG files were inspected visually at their full dimensions. None shows an observing-site label or latitude/longitude, numerical mount altitude/azimuth, or an Atlas survey view. The Equipment images visibly show simulator devices in day and red night modes. Flows shows an unrun mosaic editor with real warnings, and Monitor shows an idle session with a simulated last frame and weather monitoring off. No invented success/progress state was observed. All four file hashes matched `screenshot-provenance.json`. Its `visual_review` field still says pending and should be updated after final review; recapture invalidates this inspection and requires hash/review renewal.

## Final capture and process-boundary verification

The final capture ledger is dated `2026-10-01T17:34:41.780752+00:00`, uses `.probe/site-sim-owned` on port 8877, and records the helper-owned fresh launch/stop lifecycle. Each of the four regenerated images was reopened at original resolution. Visual findings are unchanged: no observing-site label or coordinates, no numerical mount altitude/azimuth, no Atlas survey view, and no fabricated running-session or successful-plan state. The mosaic editor retains its warnings; Monitor is idle with weather monitoring off. Day/red-night captions match the pixels.

The exact final hashes and image dimensions were independently compared with the ledger:

| Image | Dimensions | SHA-256 |
| --- | --- | --- |
| `flows-desktop-dark.png` | 1440 x 1000 | `7f772c122463d3aed3883df163c9bf8e82f8124a1a7db3b1b2736360d0581a87` |
| `equipment-phone-light.png` | 390 x 844 | `b6baddad09840c2a9ed1a56eda61cfd967128963f3993b781d4b135af8e4095d` |
| `equipment-phone-night.png` | 390 x 844 | `83535da759d31632eb7d8273586a4957c7555cee3acc76b17ab4c14c0a3ca2b7` |
| `monitor-desktop-light.png` | 1440 x 1000 | `7d0c5803122bf6660b95c0d0490967c3e3298dc3a5c9417a47e5f1dfce8c5838` |

The capture helper now binds the fresh marker/PID to the expected Python executable, exact AstroDeck command/port and launch creation time. Loopback listener ownership may pass through the Windows venv child process, but every parent must be live and no younger than its child. Independent mocked checks accepted the direct owner and a valid venv child, and rejected an unrelated process, missing parent, reused parent PID and parent cycle. The revised failure-cleanup test also passed. This follows the [Microsoft Win32_Process guidance](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-process#parentprocessid) that parent PIDs can be reused and creation times must be considered.

No open finding remains in the reviewed tooling scope. The parent may update the provenance `visual_review` field to record this completed review. Replacing an image or changing its hash requires a fresh review. GitHub action inputs/permissions were validated against official metadata; no live deployment was performed by this reviewer.

## Actions API and permission review

The selected major versions exist in official action metadata: [checkout v7](https://github.com/actions/checkout/blob/v7/action.yml), [setup-python v7](https://github.com/actions/setup-python/blob/v7/action.yml), [configure-pages v6](https://raw.githubusercontent.com/actions/configure-pages/v6/action.yml), [upload-pages-artifact v5](https://raw.githubusercontent.com/actions/upload-pages-artifact/v5/action.yml), and [deploy-pages v5](https://raw.githubusercontent.com/actions/deploy-pages/v5/action.yml). The JavaScript actions use Node 24; the hosted Ubuntu runner is an appropriate execution target.

The workflow's `path: site` input is supported by upload-pages-artifact, and its default artifact name matches deploy-pages. `enablement: false` is a supported configure-pages input, so no Pages-enablement permission is being implicitly requested. The deploy job grants the documented `pages: write` and `id-token: write` permissions and uses the `github-pages` environment. [Official deploy-pages documentation](https://github.com/actions/deploy-pages/tree/v5) supports this split-job deployment shape.

The PR path has read-only repository permission and does not upload or deploy a Pages artifact. Missing privacy secrets skip the PR-only privacy step, while publication requires a configured secret. Deployment is restricted to main. No additional blocker was found in action inputs or permission placement. This was source/metadata validation, not a live GitHub Actions run or deployment.