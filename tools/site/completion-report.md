# Site overhaul completion record

Job 1, reviewed 2026-10-01. Branch: codex/site-overhaul. Baseline:
bf3eadddcac9fb74bd802309b57ab6536c15ee36.

## Delivered

Rebuilt the overview, features, flows, hardware and weather pages. Added
getting-started and releases pages. The seven pages share a system-font design,
light/dark/system selection, keyboard mobile navigation, visible focus styles,
skip links and readable phone gutters. Pages remain static HTML/CSS/JavaScript
with no runtime build step, remote fonts, analytics or third-party scripts.

Four real local simulator captures show the mosaic editor, equipment in day
and red night modes, and an idle monitor after one simulated exposure. The
simulator used new private directories, a dedicated loopback port and a verified
fresh process tree; capture stopped its own server. The provenance ledger
records the source revision, dimensions, state, hashes and independent review.

Pages CI validates HTML5, CSS/HTML/SVG resource references, internal links,
house style, privacy and JavaScript syntax. Pull requests do not deploy.
Publishing remains main-only and requires the repository owner's existing
Pages setup; automatic enablement is off. No deployment was attempted.

## Evidence and review

- content-audit.md inventories 121 grouped old claim/node rows and their
  keep, qualify, stale, false or cut disposition.
- claims-ledger.md defines 55 claim IDs and file:line evidence. Factual page
  sections carry those IDs; content.json keeps the editorial copy.
- render-review.md records independent review of all seven pages at 320,
  390 and 1440 pixels in light/dark: 42 matrix cases. Another 41 checks cover
  keyboard, themes, storage denial, no JavaScript and 200-percent text.
- tooling-review.md preserves initial findings, reproductions and final
  dispositions. All reported findings were resolved.
- mutation-evidence.md records all 13 killed mutants, exact assertions and
  byte-for-byte restoration; the unmutated suite passed 34 tests afterward.
  The independent tooling reviewer repeated this in a temporary copy.
- Site HTML/link/style/privacy checks passed for all seven pages; tooling
  privacy, JavaScript syntax, UTF-8/BOM and diff whitespace checks passed.

## Corrections and limits

Native guiding/autofocus depend on astrodeck_native, which published releases
do not include yet (#630). ASTAP and its database are separate prerequisites.
DSS2 is not bundled: Atlas is schematic offline until the operator downloads
tiles for personal use. The stale workflow fetch is tracked as #631.

Hardware verification is limited to recorded checks. Other implemented paths
are labelled Supported; no full-night certification is implied. Rain can veto
automatic re-arming; cloud forecast does not. Backend role lists now match the
implemented adapters. The release page uses explicit release commits/deployment
records, without claiming that each version has public binary assets.

Cuts include old node/device/sky-size marketing counts, guaranteed unattended
operation, a two-minute installation promise, universal driver support and
unverified hardware claims. The ASIAIR backend remains explicitly optional
and experimental. Existing NINA, ASIAIR and standalone paths have equal space.

The independent render review found enlarged-text overflow, a broken hardware
status word and a stale release-nav label; all were corrected. Tooling review
found CSS/SVG reference gaps, encoded privacy bypass, raw-reference diagnostics,
a JavaScript exit-status bug and screenshot ownership/cleanup gaps; focused
regressions and mutations now cover those corrections.

Changes stay within site/, tools/site/ and .github/workflows/pages.yml. No app
or packaging source, README, existing docs, real configuration or GitHub state
was changed. #630 and #631 were reported to Claude for separate work. Browser
review used Chromium, not a claim of full assistive-technology certification.
