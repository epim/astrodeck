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
| **dormant** | paused between nights, ready to resume | the run finished a night without completing all quotas, **or** the server booted and found this session still marked active (see below) | **resume** (manual, `control.mount`), arm **auto-resume at dusk**, **update from plan**, delete |
| **complete** | every step met its count | the engine finishes the last target's last step | review/regrade frames, delete |
| **abandoned** | retired but kept on disk — hidden from the panel, ledger intact | **no UI path reaches this state today** — it's API-only (`PATCH /api/sessions/{id}` with `status="abandoned"`, refused while active); no button in the app issues it | nothing in the panel (it's filtered out). Note: the card's **Delete** button is a different, harder action — it *removes* the session's ledger and thumbnails outright (FITS frames untouched, cannot be undone; see [Deleting](#what-survives-a-reboot) below), it does not mark it abandoned |

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
> anyone lacking `control.mount` — by default that's viewer *and* operator,
> since regrading uses the same capability as a manual resume. A lock note
> under the buttons spells out why: *"Read-only — regrading frames needs
> operator or admin access."* No role sees an enabled verdict button that
> then fails to save.
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
