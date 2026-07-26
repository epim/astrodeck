# Sessions & multi-night imaging

A hard target can take more clear nights than you have. A **Session** is how
AstroDeck accumulates frames on a target across as many nights (and reboots) as
it takes, keeps a per-frame accept/reject ledger, and can wake itself up at dusk
to keep going.

Sessions surface as a **Sessions** panel on the **Plan** view. Cards appear once
a session exists (run a plan and one is created for it).

---

## What a session is

Each session holds a **frozen snapshot of the plan** it runs (with stable ids so
progress survives edits) and a **ledger** of every frame captured under it. A
session has one of four states, shown as a status chip on the card:

| State | Means | What moves it here | What you can do |
|---|---|---|---|
| **active** | running right now | you started/resumed it and the sequence engine currently owns it | Pause/Abort from [Plan & sequences](plan-and-sequences.md) or [Monitor](monitor.md) |
| **dormant** | paused between nights, ready to resume | the run finished a night without completing all quotas, **or** the server booted and found this session still marked active (see below) | **resume** (manual, `control.mount`), arm **auto-resume at dusk**, **update from plan**, **abandon**, delete |
| **complete** | every step met its count | the engine finishes the last target's last step | review/regrade frames, **abandon**, delete |
| **abandoned** | retired but kept on disk — hidden from the panel, ledger intact | pressing **Abandon** on a dormant or complete card (`control.mount`; a plain confirm dialog, refused by the server while the session is active) — issues `PATCH /api/sessions/{id}` with `status="abandoned"` | nothing in the panel (it's filtered out — the same as a completed row you've stopped caring about, minus the disk footprint). **Abandon is deliberately soft**: the ledger and thumbnails stay on disk, and there's no UI path back to dormant/complete, but the row itself is just hidden, not destroyed. Contrast the card's **Delete** button, a different and harder action — it *removes* the session's ledger and thumbnails outright (FITS frames untouched, cannot be undone; see [Deleting](#what-survives-a-reboot) below). Abandon retires; Delete erases. |

The card shows the name, the status, `accepted / total` frames, and a
per-target progress bar, plus the number of nights it has spanned.

---

## Accepted-frame quotas (count modes)

By default a plan runs in **attempts** mode: each step's `count` is a number of
frames to *shoot*, good or bad. A session can instead count **accepted** frames.

On the **Plan** view's automation section, the **count = accepted frames**
toggle switches the plan to accepted-quota mode (`count_mode`). Now each step's
count is a quota of *accepted* frames: rejected frames don't count, and the step
keeps shooting — across nights if needed — until the quota is met.

Frames are auto-graded against the plan's quality gates:

- **min stars per frame** (`min_stars`, 0 = off) — a star-count floor.
- **max guide RMS (arcsec)** (`max_guide_rms`, 0 = off) — a guiding ceiling.
- **flag HFR spikes** (`hfr_reject_factor`, 0 = off) — flags a frame whose HFR
  exceeds this factor times the running median.

Because an accepted-quota step could loop forever if the sky never cooperates,
two guards bound it (both accepted-mode only):

- **skip step after N rejects** (`max_consecutive_rejects`, default 10, per
  step).
- **end night after N rejects** (`max_consecutive_rejects_night`, default 20,
  crossing targets).

If you turn *both* guards off **and** any non-calibration target has no stop
boundary, the plan is "unbounded" — auto-resume refuses to start it (see below)
because it could run forever under persistent rejects.

---

## Reviewing and regrading frames

Every session card has a **review** button. It opens the **Session review**
drawer (docked on desktop, a bottom sheet on phones) showing a grid of frame
thumbnails with their metrics: **HFR**, star count (★), and guide **RMS**, plus
the verdict — **accepted**, **rejected**, or **overridden**.

You can filter by **target**, **night**, and **verdict**, select frames, and
bulk **mark accepted** / **mark rejected**. Your override wins over the automatic
verdict, and the header updates the *remaining* count live as you regrade. If a
frame was auto-rejected for poor HFR but you know it's fine (or vice versa), this
is where you override it.

Regrades are read-only while the session is actively running — you'll be told
*"Session is running — regrades are read-only until it finishes."* Thumbnails
and the ledger are what the review shows; the saved FITS files themselves are
never touched by regrading.

> The review drawer opens for every role (browsing frames is a read), but
> the **mark accepted** / **mark rejected** buttons are `disabled` for
> anyone lacking `control.mount` — by default that's **viewer** only,
> since regrading uses the same capability as a manual resume, and operators
> are meant to run sequences (so operator gets enabled verdict buttons too).
> A lock note under the buttons spells out why: *"Read-only — regrading
> frames needs operator or admin access."* No role sees an enabled verdict
> button that then fails to save.
> See [remote-access-and-roles.md](remote-access-and-roles.md#screen-by-screen-what-each-role-actually-sees).

### Update from Plan

A dormant session's card has **update from plan**: it replaces the session's
frozen plan with whatever is in the current **Plan** panel, id-safely. You're
shown exactly what happens — how many steps **keep** their recorded progress,
how many are **new** (start at zero), and how many are **dropped** (their frames
stay in the ledger but stop counting toward any quota).

---

## Resuming — manual and auto-at-dusk

### Manual resume

A dormant session card shows a **resume** button (for users who can drive the
mount). It restarts the run from where the ledger left off. Manual resume needs
`control.mount`.

### Auto-resume at dusk

A dormant card also has an **auto-resume at dusk** toggle. Arm it, and a
background service (checking every minute) will resume the session on its own
when tonight's observing window for one of its targets opens — running the exact
same code path as a manual resume, so every safety gate still applies.

Only **one** session can be armed at a time. If a resume attempt is refused
(rig not ready, unbounded plan, weather veto), it alerts and retries every 10
minutes; if dawn arrives first it gives up for the night and waits for the next.

**The no-safety-monitor warning.** If you arm auto-resume with **no safety
monitor connected**, AstroDeck makes you confirm — *"No safety monitor is
connected — the rig may start unattended in bad weather"* — and keeps a
persistent warning chip on the card while armed. Auto-resume can start the rig
while you're asleep; without a safety monitor nothing will stop it if the
weather turns.

**Weather veto.** If [weather](weather.md) is enabled, auto-resume also checks
the cloud forecast and **holds** if it's too cloudy in the next hour. This is
fail-open (advisory only) — the safety monitor remains the real guard. The card
surfaces *"high cloud tonight — auto-resume will hold unless overridden"* and,
when applicable, an **ignore weather tonight** toggle to proceed anyway.

---

## Getting your frames out (the stacking bundle)

When a run ends, the Plan view offers **View session report →**; the report is
also reachable any time from **More → Reports** on a phone, and its own picker
browses every past report. The report's **Stacking bundle** panel is how you
hand a night to your stacking software.

It lists what it found, one line per group — target, filter, exposure, gain,
binning, how many lights (and how many were accepted), and whether a matching
master **dark / flat / bias** was found. **Download bundle.zip** is the whole
novice path: one click, no options needed.

**What's in the .zip.** Not your photos. The bundle is a *description* of how
to lay them out: `manifest.json` (every sub with its metrics and weight),
`weights.csv` (one row per sub, ready for PixInsight's SubframeSelector or
Siril), a `README.txt`, and `build.sh` / `build.ps1`. Run the build script from
an empty folder on the machine holding your captures and it copies them into a
clean tree with the masters in place.

One column deserves a warning, and the README repeats it: **`fwhm_est` is an
estimate**, computed as 2 × HFR. AstroDeck measures HFR, not a fitted PSF FWHM,
and the real factor depends on your optics and sampling. Use `hfr` as the
primary sharpness column and treat `fwhm_est` as a convenience for tools that
want an FWHM-ish number.

### Advanced bundle options

The panel's **Advanced (layout, weighting, materialize)** disclosure holds:

- **Folder layout** — **Grouped** (the default: lights under each group, one
  shared `masters/` folder), **Siril** (`lights/ darks/ flats/ biases/` side by
  side inside each group), or **APP** (`Light/ Dark/ Flat/ Bias/` inside each
  group). The choice only changes the folder names the build script writes.
- **Weight subs by altitude** — off by default. Each sub's weight is otherwise
  sharpness (HFR), roundness, and guide RMS; turning this on folds in a
  sin(altitude) transparency term, so subs taken higher score higher.
- **Flag the weakest subs** — set a **weight cutoff** between 0 and 1 and any
  sub scoring below it is marked `keep=false` in the manifest and
  `weights.csv`. Weights are normalised within each group, so the best sub is
  always 1.0. **This is advisory only**: nothing is deleted, and every sub is
  still in the download and still laid out by the build script. Filter on the
  column in your stacker if you want the tail gone.
- **Make a folder of tonight's photos here** — does the sorting for you, on the
  capture machine, under `captures/exports/`. **The files it puts there are the
  same files as your originals**, not copies: deleting or editing one in the
  export folder deletes or edits your capture. Stack from that folder, don't
  tidy up inside it. (Where the filesystem can't share a file that way, a real
  copy is made instead, and the result line tells you how many.) This one
  writes to disk, so it needs `control.capture`, and it's offered only when the
  FITS are actually on this machine — if they live on your NINA host you're
  told to download the .zip and run `build.sh` over there instead.

A **Download frames.csv** button at the bottom of the report gives you the
per-frame table on its own.

---

## What survives a reboot

**Closing your browser tab, losing Wi-Fi, or your sign-in session expiring
does nothing to a running session.** The sequence engine runs entirely on the
server — your browser is a viewer/controller, not the thing doing the work.
Walk away, close the laptop lid, or get logged out (see
[remote-access-and-roles.md](remote-access-and-roles.md)) and the active
session keeps shooting; reopen the app later and it's exactly where you left
it. The distinction that actually matters is a **server/host reboot** —
sessions are built to survive those, specifically:

- Every session is one JSON file on disk, rewritten as frames land.
- At boot, any session still marked **active** (the process can't be running at
  boot) is swept to **dormant**, so a mid-run power cut leaves you a resumable
  session rather than a stuck one.
- Progress is counted from the ledger, so a resumed loop restarts at the exact
  frame counts it had — never re-shooting completed frames or overrunning a
  quota.
- The store keeps up to 200 sessions, pruning the oldest **complete/abandoned**
  ones first; **dormant** and **active** sessions are never pruned.

**Abandoning** a session (the **abandon** button on a dormant or complete
card, with a plain confirm) just hides it from the panel — the ledger and
thumbnails stay on disk untouched. It's the softer of the two exit actions:
there's no UI path back from it, but nothing is destroyed either (see the
state table above). An abandoned session still counts against the
200-session cap above and is the first kind pruned once you're over it.

**Deleting** a session (from a non-active card, with a confirm) removes its
ledger and thumbnails only — **your saved FITS frames are not deleted**, and it
cannot be undone.

---

## Related

- [Plan & sequences](plan-and-sequences.md) — building the plan a session runs.
- [Monitor](monitor.md) — the recover banner and live progress for a resumed run.
- [Weather](weather.md) — the auto-resume weather veto and overrides.
- [Safety & automation](safety-and-automation.md) — the safety monitor that
  guards unattended resumes.
- [Remote access & roles](remote-access-and-roles.md) — exactly who can
  resume, arm auto-resume, or regrade.
