# Non-photosphere issue pass, 19 September 2026

## Scope

Eleven existing issues are addressed in the isolated branch
`codex/nonphotosphere-wave2`, based on the previous gallery fixes at `0c0955e7`.
The shared checkout and the active imaging rig were not changed. Nothing was
pushed or deployed.

| Issue | Result |
| --- | --- |
| #21 | Native cooler status reports power availability from the actual adapter reading. Zero percent is a valid reading; unavailable readings remain unknown. |
| #26 | The sky reason describes the debounced verdict, including votes needed to change it. A separate `latest_frame` names the latest measurement and its reason. |
| #29 | Cloud probes are unsaved exposures. They do not enter the FITS library, gallery, session totals, or science frame numbering. |
| #30 | Probes retain the interrupted science exposure, gain, offset, binning and restored filter, removing the artificial drop to ten seconds. |
| #31 | Each UI-probe route gets a fresh page in the same authenticated browser context. Late errors cannot leak between routes; startup errors are still recorded. |
| #32 | The router's literal NUL is written as an escape, preserving its runtime sentinel while making the file searchable as text. |
| #33 | Calibration duration uses the slower declared axis rate and respects a pinned calibration distance. Existing pulse caps and extended step budgets remain enforced. AM5 cap messages describe the correct axis rate. |
| #34 | Native guider status exposes the scale used for calibration and whether it is known, even before the first frame. Calibration startup logs an explicit warning when it assumes a scale. |
| #40 | Repository editing instructions explain safe UTF-8 handling on PowerShell. An automatically discovered UI test rejects NUL bytes, BOMs and the characteristic smart-punctuation mojibake. |
| #43 | Error transitions publish `end_reason: error`; they cannot inherit `unsafe` from a previous run. Genuine safety stops retain `unsafe`. |
| #60 | Operator stops publish `aborted`; new runs clear old causes synchronously, including Stop before the new task's first turn. Stops during terminal teardown preserve the already established cause. |

The current connection model already requires `primary` for #20, so that is
not counted as a fix in this pass. The broader guiding recovery loop in #72
and the hardware diagnosis in #14 remain separate work.

Testing also found and fixed #93: cancelling a new run before its first task
turn skipped report finalization and left the session active and armed.
`abort()` now performs idempotent finalization itself if the task never did,
so an immediate operator stop also disarms automatic resume. This brings the
batch to twelve product/tooling issues, excluding the draft-only error in #92.

## Validation

The new server regression suite covers real simulator capture through FITS
storage and gallery discovery, unchanged science numbering after a probe,
failure and cancellation, stale terminal causes, immediate Stop, asymmetric
rates, pinned settings, pulse caps and scale telemetry. Existing camera,
sequence, cloud-hold, guider and AM5 suites are also exercised.

The combined server regression run passed 186 tests. The final 22 tests in
the new batch suite pass, including the saved session's disarmed state after
an immediate abort. Existing operator-stop and resume-arm regressions also
pass after that change. These suites overlap; their counts are not additive.

The browser regression uses a temporary loopback fixture in headless Edge. It
starts delayed network and console failures on one route, checks that the next
route receives neither, and verifies that the next route still reports its own
startup errors. Cookies and local storage survive page replacement. The clean
third route passes, and each page is closed after its check.

The router tests and the new source-encoding guard pass. The Vite production
build passes. Full TypeScript checking still reports the five existing
photosphere/horizon errors tracked by #78; no photosphere files were changed.

An uncommitted patch matched the wrong guider context during implementation.
The import failure was caught before commit, corrected, and recorded in #92 as
required by the repository rules. It was never deployed.

## Operational limits

Longer science exposures also make cloud checks longer. The hold still uses
the exposure-relative capture timeout, cancellation, safety checks between
frames and the existing hold deadline. This change removes the exposure
mismatch; it does not claim that the cloud detector is calibrated for every
optical train or filter. Short science exposures can still yield weak evidence.

The guider still supports an assumed scale when optics are missing. The new
status and warning make that assumption visible; they do not measure the optics.
The slower-axis change does not establish the cause of the intermittent Dec
backlash or star-loss failures reported on the physical rig.

The public `sky.score` remains the latest frame's score for compatibility.
Consumers needing its matching verdict should use `sky.latest_frame`; the
top-level `cloudy` is the debounced decision and `reason` explains that decision.

No issue is closed remotely before the commits are integrated. Device-level
confirmation and deployment remain pending the end of the live imaging run.
