# AstroDeck UI/UX Review Protocol

How to run a review that finds what a human holding a phone in a dark field would find.

This document exists because a previous review did not. Four persona reviewers took 130
screenshots, filed 100 findings, and produced real value — WCAG contrast failures, controls
with no accessible name, content clipped past the viewport, a filter-identity bug feeding the
wrong flats to a stacker. Then the project owner picked up an actual phone and, in about ten
minutes, found six problems none of them could have found:

- the setup wizard is *named and shaped* like a wizard but *behaves* like a checklist
- it covers roughly half a phone screen — while instructing you to operate the screen underneath
- at "Pick a target" it covers the Atlas it just told you to open
- once a finger touches the sky map, every swipe pans the sky, so the page can no longer be
  scrolled and you are stranded on that view
- "cool the camera" requires scrolling to the bottom of a long view, and nothing says so
- a button wraps onto its own line at the bottom of the cooler panel

The post-mortem matters more than the list, because every miss had a *mechanical* cause:

| Miss | Why it was structurally impossible to find |
|---|---|
| Scroll trap | The harness launched a desktop browser with a mouse. `click()` never swipes, so the defect **could not be triggered** |
| Wizard covering half the screen | Occlusion was never measured; and at review time the wizard rendered off-screen, so nobody ever saw it |
| Wizard covering the Atlas | Nobody was asked to *obey* the guidance — they audited screens, not sequences |
| Wizard vs checklist | No category existed for "this violates what its name promises" |
| Button wrapping | Below the fold, and screenshots were viewport-only |
| "Tell me to scroll" | Reviewers teleported between views by clicking nav labels; they never had to *find* anything |

**The deepest cause: the instrument chose the findings.** Reviewers were handed a contrast
meter, a rect measurer and a name checker, and returned contrast, rect and naming defects.
Nobody measured "can I actually do this," because nothing measured that.

This protocol is built to prevent that specific failure. Its bias is toward **embodied use**.

---

## 1. What you are reviewing

AstroDeck controls a telescope. It runs on a small computer (often Raspberry-Pi class) at the
scope and is driven from a browser. The operating conditions are not incidental — they are the
design constraints:

- **It is dark.** Night mode collapses the palette toward red. Anything encoded by hue alone
  stops working. White light ruins dark adaptation, so a bright surface is a defect, not a
  style choice.
- **It is cold, and often late.** Users are tired, wearing gloves, standing up, one-handed.
- **The phone is the first screen.** Many users meet this app on a phone. The tablet is what
  gets propped up beside the scope. The desktop is the morning-after machine.
- **Sessions are long and get interrupted.** Phones lock. WiFi drops. People go inside.
- **Mistakes are expensive.** A wrong setting can waste a whole clear night, and clear nights
  are scarce. A "success" that hides a partial failure is the worst defect class in the product.

---

## 2. The apparatus

Harness: `scratchpad/ux/uxkit.py`. Run it with the **system** `python` — Playwright is not
installed in `server/.venv`.

Each reviewer gets their **own server instance and their own config directory**, so nobody
inherits another reviewer's state and a cold-start reviewer gets a genuine cold start:

```
ASTRODECK_CONFIG_DIR=<fresh dir> server/.venv/Scripts/python.exe -m astrodeck run --port <YOUR_PORT>
```

```python
import asyncio, os, sys
sys.path.insert(0, r"<path to>/scratchpad/ux")
os.environ["UX_OUT"] = r"<your own output dir>"
import uxkit

async def main():
    async with uxkit.session(port=PORT, persona="me", viewport="s25ultra") as ux:
        await ux.tap("Equipment")                 # REAL touch tap
        await ux.swipe(200, 700, 200, 300)        # finger drag
        print(await ux.can_scroll_from(200, 500)) # the scroll-trap test
        print(await ux.occlusion())               # % of screen eaten by chrome
        await ux.screenfuls("capture")            # capture the WHOLE view, screen by screen
        await ux.rotate()                         # landscape
        await ux.set_night(True)
        p = await ux.shot("capture-night")        # then READ the png
asyncio.run(main())
```

Viewports are real devices, not round numbers — width is what breaks layouts, and these span
390→440, a spread wide enough to straddle a breakpoint:

| name | size | what it is |
|---|---|---|
| `phone` | 390×844 | iPhone 14/15/16 base — the narrow floor |
| `s25ultra` | 412×915 | Galaxy S25 Ultra |
| `iphone-pro-max` | 440×956 | iPhone 16 Pro Max — the wide end |
| `tablet` | 820×1180 | the field device, propped by the scope |
| `desktop` | 1440×900 | the morning-after machine |

Phone viewports launch with `has_touch=True, is_mobile=True`. **This is not cosmetic.** A
desktop browser resized to 412px is not a phone; it has a mouse, and a mouse cannot find a
scroll trap.

`POST /api/connect/sim` brings up a simulated rig (11 devices, ~3 s). **The rig is simulated —
press everything.** Nothing you tap can hurt real hardware.

---

## 3. The protocol

### Pass 1 — unaided (do this first, and do not skip it)

**No `audit()`. No `occlusion()`. No measurements at all.**

Pick up the app as your persona and try to accomplish your goal. Narrate as you go, in the
first person, in the order it happened. Write down:

- what you expected to happen, *before* you touched anything
- what actually happened
- every moment you had to hunt, guess, backtrack, or re-read something
- every moment you felt stupid, annoyed, or unsure whether it had worked
- the exact point at which you would have given up if this weren't your job

This pass is the primary artifact. Findings are extracted *from* it, not the reverse. The
sharpest report in the project's history is a paragraph of a human saying "I muddle through,
and I'm as far as 'Pick a target'. I loaded the atlas, but I can't see it because the modal is
in the way." No schema produced that. Narrative did.

### Pass 2 — instrumented

Now go back with the tools and measure what you felt. Turn "the wizard is huge" into
`occludedPct: 46.6`. Turn "the text is hard to read" into a contrast ratio. Turn "I got stuck"
into `can_scroll_from() -> {scrolled: False}`.

Measurements are **evidence for a judgement you already made**, not a substitute for making one.

---

## 4. Rules that are not negotiable

**1. Touch, don't click.** For at least one full pass on every phone viewport, operate the app
with `ux.tap()` and `ux.swipe()` only. `locator.click()` is a mouse and will hide entire defect
classes. (It also silently fails on honest-disabled controls: Playwright treats
`aria-disabled="true"` as non-actionable, so `locator.tap()` times out. `ux.tap()` resolves the
box and taps the coordinates, which is what a finger does — and is the only way to check that a
blocked control *explains itself* when pressed.)

**2. No teleporting.** You may not use `ux.goto()` to reach a destination the app told you to
visit. Get there the way a user must — by finding and tapping the affordance. Log every time
you had to hunt. `goto()` is for setting up a scenario, never for completing a flow.

**3. Obey the guidance.** If the app offers a wizard, checklist or coach mark, **follow it
literally, in order, doing only what it says.** Report every instruction you could not carry
out, and every case where the thing giving the instruction made the instruction harder to
follow.

**4. Predict, then act.** Before using an unfamiliar control, write one line: *what do you
expect this to do?* Then do it. **Every mismatch is a finding, even when the behaviour is
technically correct.** This is the only reliable way to catch "it's called a wizard but it's a
checklist" — a defect invisible to anyone who already knows the answer.

**5. Look at the pixels.** After every `shot()`, **Read the PNG**. Judge alignment, crowding,
overlap, hierarchy and whether it looks finished, with your own eyes. Never infer the interface
from source code. Report how many screenshots you actually read; a low number makes the review
non-credible.

**6. See below the fold.** A viewport screenshot is what the user can see *now*, not what
exists. Use `ux.screenfuls()` on every long view. A button wrapping badly at the bottom of a
panel is still a defect.

**7. Escape must always be possible.** After landing on any view containing a map, canvas or
chart, run `ux.can_scroll_from()` with the finger starting **on** that element. Read
`trapped`, not `scrolled`: a page that does not move because the view *fits* is fine; a page
that does not move while there is room below is a **blocker** — the user is stranded and does
not know a trick to get out.

> This test was itself broken on first release, and the failure is instructive. `swipe()`
> drove `page.mouse`, and this Chromium build does not synthesise mouse into touch even with
> `has_touch` — so a drag moved nothing on a plainly scrollable view and `can_scroll_from()`
> reported "stranded" **everywhere**, manufacturing the exact blocker it exists to catch. It
> now dispatches CDP `Input.dispatchTouchEvent`. **Always run a control first:** a view you
> know scrolls must come back `scrolled: True`. An instrument that cannot fail its own control
> is not evidence.

**8. Rotate.** Phones and tablets rotate. A layout tested only in portrait is half-tested.

**9. Both themes.** Audit day and night. In night mode check specifically for meaning carried
by hue alone — two things that must be told apart need a second channel (shape, dash pattern,
fill, a word).

**10. Every string must earn its place.** For each label, subtitle, caption and hint, ask what
the reader learns from it that the pixels do not already tell them. A heading subtitle reading
"one row per device", above a list with one row per device, is slop even though every word in
it is ordinary. Delete it. Note that this is invisible to a vocabulary scan for AI writing,
which is why it needs its own pass. The opposite case is copy that states a reason the screen
cannot show ("a sequence owns the camera", "Next unlocks once a target is in your plan") --
that is the most valuable text in the app, so do not cull it by the same rule.

**11. Measure the work area.** For any persistent bar, sheet or overlay, run `ux.occlusion()`.
On a phone, **anything over ~25% while the user is expected to operate the screen underneath is
a finding.** Report the number.

---

## 5. The flows

Review **flows, not screens.** A screen-by-screen audit cannot find a wizard that covers the
view it just told you to open, because that defect exists only in the sequence.

### Flow A — First light *(novice · phone · start here, it is the highest-value flow)*

A complete beginner, first clear night, one-shot-colour camera, no idea what to photograph.
Cold start: no location, no equipment, no profile. Get from opening the app to seeing a picture
of something on screen.

Watch for: jargon assumed; dead ends; where the next action stops being obvious; whether it
tells you *where* to go, not just what to do; anything that lets you make an expensive mistake
it could have prevented; anything that frightens you.

### Flow B — The interrupted night *(any persona · phone)*

Start something long. Lock the phone. Wait. Come back. Rotate the device. Drop the network and
restore it (`ux.page.context.set_offline(True)`). Reload mid-run.

Watch for: lost work; lost place; stale readings presented as live; a reconnect that lies about
what happened while you were away.

### Flow C — The multi-night project *(mono convert · tablet)*

Set up a filter wheel, build a real three-filter narrowband plan with dithering, autofocus
cadence and a meridian flip, run part of it, then come back "the next night" and resume.

Watch for: how many taps a three-filter, three-night project costs; whether filters feel
first-class or bolted onto a colour-camera app; whether the app knows what it still owes you.

### Flow D — The unattended night *(professional · desktop, then phone)*

Configure the rig as if you cannot physically reach it. Then **make it fail** — trip the safety
monitor, lose guiding, force a rejected frame. Watch it degrade, recover, and report.

Watch for: anything that would cost a night or a client; a status that reads OK when it is not;
a "success" hiding a partial failure; whether the morning-after report tells the truth about
what happened at 2am.

### Flow E — The 3am check *(professional · phone · one-handed)*

You are in bed. You pick up the phone with one hand, half asleep, dark-adapted. **Within five
seconds: is the rig fine or not?**

Watch for: whether the answer is on the first screen; whether the primary actions are in thumb
reach on a 6.9" phone; whether anything is bright enough to ruin your night vision; whether
anything important hides under the notch or home indicator.

### Flow F — Craft sweep *(designer · all five viewports · both themes)*

Every view, systematically. Alignment to a grid, spacing rhythm, type scale discipline, colour
as a system, iconography consistency, and the states that get forgotten — empty, loading,
error, disabled, first-run.

Test with **real data**: type a long target name, a long filter name, a big number. Long values
break layouts, and empty sim data hides that.

---

### Flow G — The rig changes *(professional · desktop and tablet)*

Working astrophotographers reconfigure constantly. This flow is about what the software does
when the hardware underneath it moves, and it is the flow most likely to expose a lie: state
that survives when it should not, or a setting that quietly keeps applying to gear that is no
longer there.

**G1. Swap mono + filter wheel for a one-shot-colour camera.** Pull the mono camera and the
wheel off; put an OSC on. Then look at everything the wheel used to touch. Does the filter UI
disappear cleanly, or leave orphaned controls? What happens to a saved plan whose steps name
Ha/OIII/SII? To a multi-night session mid-project? To the per-filter focus offsets and the
profile that referenced them: are they destroyed, or kept for when the wheel comes back? Does
the optics config follow the new sensor (pixel size, dimensions, Bayer pattern), or keep
reporting the old camera's geometry? A plan that cannot run must SAY which step is impossible
and why, not fail at 2am on the first exposure.

**G2. Install a different brand of filters in the wheel.** Same wheel, new glass, so every
slot name is wrong and every focus offset is stale. Rename the slots. Re-run the learn-offsets
routine. The questions: does anything warn that the stored offsets no longer describe the
filters in the wheel, or do they keep silently applying? How many interactions does renaming
seven slots cost? If a plan or a calibration library references the OLD filter names, what
happens to that history? Stale offsets are the dangerous case here, because a wrong offset
does not error, it just makes every frame slightly soft.

**G3. Shoot a calibration set: flats, then bias, then darks.** Run the actual acquisition, not
just the settings screens. Per-filter flats if a wheel is fitted. Watch for: whether the flat
exposure solver reaches the target ADU or saturates; whether calibration frames are correctly
excluded from an integration total; whether each frame is stored with the filter, gain,
binning and temperature needed to match it to lights later; and whether the library then
matches those masters to the right lights.

Weight G3 heavily. A previous review found the stacking bundle grouping frames under
`NoFilter` while the FITS header said otherwise, which silently hands a stacker the wrong
flats and puts gradients into a finished image. It also found flat steps defaulting to no
filter, 120 s (saturated against a panel) and a target ADU of 0. Both were fixed; this flow is
how we find out whether they stayed fixed, and whether the same class exists elsewhere.

**Also check the empty-slot case if you can arrange it.** A wheel connected with no filter
physically installed in a slot is different from having no wheel at all, and the simulator
cannot represent it. A slot that reports a name while empty images through nothing and reports
success.

---

## 6. Reporting

**The journey is the deliverable.** Lead with the narrative from Pass 1. Then findings, each
carrying:

- **severity** — `blocker` (cannot complete the task, or a control is broken) · `major` (costs
  real time, causes a mistake, loses data or a night) · `minor` (friction) · `polish` (craft)
- **category** — including **`mental-model`** for "this violates what its name or shape
  promises". That category exists because its absence hid a real defect.
- **surface** — view, panel, control
- **device + orientation + theme**
- **evidence** — REQUIRED. The screenshot path you read, and/or the measured number. "It looks
  wrong" without evidence is not a finding.
- **impact** — what it costs *this persona*, concretely
- **suggested fix**

Also report **what genuinely worked**. A review that is all complaints is as useless as one
that is all praise, and it costs you credibility on the findings that matter.

Flag **`NEEDS-REPRO`** on anything you inferred rather than observed. A previous reviewer
reported "device assignments are lost on reload"; it was later measured and found false — they
had compared two browser sessions, which start with empty storage. Say what you saw and what
you concluded from it.

---

## 7. Known measurement traps

Do not file these; they are artifacts of the instrument.

- `shot()` defaults to a **viewport** capture on purpose. In a `full_page=True` capture,
  `position: fixed` elements (modals, wizard, toasts, bottom nav) render at the scroll offset
  and look clipped or misplaced.
- The `skip-link` sits at negative `y` until focused. Correct.
- **Wrapped inline elements** (an inline `<span class="mono">` inside a paragraph) get one
  bounding box spanning every line they touch, so two in the same paragraph appear to overlap
  while rendering perfectly. The harness filters these; if you write your own check, use
  `getClientRects().length > 1` as the wrap signal.
- **A static child of a fixed parent** is itself `position: static`. Naïve overlap checks count
  every bottom-nav label against whatever is scrolled underneath.
- `.panel` is the app's **generic card class**, not a dialog. Do not treat it as an overlay.
- Occlusion must ignore transparent, text-free, pointer-events-none hosts, or a full-screen
  wrapper reports as 100% coverage.

---

## 8. What a bad review looks like

So you can recognise yourself doing it:

- A per-screen table of contrast ratios with no attempt to complete a task
- Findings that restate a raw audit count without judging whether it hurts (a 10px axis tick is
  not the same defect as 10px body copy you must read to operate the mount)
- "Inconsistent spacing" with no rect, no screenshot, no location
- Reviewing source code instead of the running app
- Reaching every destination with `goto()` and then reporting that navigation is fine
- Reporting zero positives
- A high finding count and a low screenshots-read count

---

## 9. Process rules for whoever runs the review

- **Re-review anything you unblock.** A `blocker` means the surface behind it was never
  evaluated. Fixing it exposes untested territory — that is exactly how a half-screen wizard
  shipped after a review that had reported it as invisible.
- **Give reviewers disjoint file ownership** if they will also fix. Partition by *file*, not by
  finding, and require pathspec-limited commits — a bare `git commit` commits the whole index
  and will sweep a concurrent agent's staged files into your commit.
- **Personas need the right device.** Novice → phone. Mono convert → tablet. Professional →
  desktop plus phone, and a tablet for Flow G. Designer → all five. The professional carries
  three flows (D, E, G) because reconfiguring hardware is as much a part of that job as
  running a night, and it is the part software usually handles worst.
- **Instrumented review and embodied use are complements, not substitutes.** Ten minutes of a
  human with a phone found what 130 screenshots could not; the reverse is equally true, and the
  contrast and data-integrity defects would never have surfaced from casual use. Run both.
