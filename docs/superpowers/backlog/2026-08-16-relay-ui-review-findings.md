# What the UI said, and what was true (2026-08-16, over the relay)

Four findings from using AstroDeck as an operator does - signed in at
`https://astrodeck-relay.fly.dev/h/home-1/` in a browser, not through the API.
Recorded because all four are invisible from the API, and three of them are the
same defect class: **a screen that states something the system knows to be
false.**

Context: session `18572134` (NGC 7129) was DORMANT, armed, owing 58 frames, and
being held by the weather veto - 100% forecast cloud against a 50% threshold.
Everything below was observed in that state.

---

## 1. Monitor does not mention the armed run

**What the screen says.** `IDLE` / **NO RUN ACTIVE** - "Plan a session to start
capturing. Live guide, thermal and preview still work below if devices are
connected." Countdowns: "no run - flip not scheduled". Last frame: "NO FRAME
YET".

**What was true.** A session was armed and would start by itself the moment the
sky cleared, with 58 frames still owed on a campaign.

**Why it matters.** "Plan a session to start capturing" is an instruction to do
something, and doing it is the wrong move: starting a fresh run is how you
strand the armed one (the singleton in `engine.start` disarms every other
session). The one screen an operator checks at 22:00 tells them to take the
action that loses the night's remaining frames.

A dialog DID explain the hold on page load, and it was the best-written thing
on screen:

> HIGH CLOUD FORECAST TONIGHT - Forecast peak 100% total cloud (high layer
> dominant) between 21:00 and 05:15 - at/above your 50% threshold. Auto-resume
> will hold unless "ignore weather tonight" is set.

Cause, threshold, and the escape hatch by name. But it is a modal: dismiss it
and the screen goes back to claiming nothing is planned. **The honest sentence
exists and is thrown away after one reading.**

---

## 2. Every flow says NEVER RUN

**What the screen says.** All 9 cards in the Flows library carry a `NEVER RUN`
badge - including the two under MY FLOWS whose own descriptions read "First
flow run on the real rig".

**What is in the ledger.** Sessions named exactly `NGC 6946 SHO with a cloud
dodge` and `NGC 6946 Ha with a cloud dodge`, carrying the flow fingerprint:
`instructions` present, and `park_when_done` + `warm_cooler_when_done` both
true, which only `to_plan.plan_extras` forces.

**Status: to be confirmed** - either run-tracking was never wired to the ledger,
or those nights were started some other way. Not asserted as a defect until the
code says which. Either way an operator cannot tell from the library which of
their flows has ever actually run, which is the first question anyone asks of a
saved automation.

---

## 3. A session does not record where it came from

Asked "what is the status of the flow in progress", the UI cannot answer -
because nothing on a Session says whether it was built in the Plan editor or
compiled from a Flow. The answer had to be reconstructed by fingerprinting the
frozen plan (`instructions` + forced park/warm), which is inference, not a
record.

Tonight's pending work is a PLAN session, not a flow. That is a perfectly good
answer and the product cannot give it.

---

## 4. First-run furniture is still up on a rig that has been imaging for weeks

Two affordances still displayed, and both intercept pointer events (they
swallowed automated nav clicks, which is how they were noticed):

* a coach overlay: "Pick a target 3/6 - Open Atlas ... Next unlocks once a
  target is in your plan";
* an Equipment banner: "6 devices are connected but not assigned here - this rig
  was started somewhere else (the one-tap simulator, a boot profile, or Profiles
  > Activate)."

The banner is worth a second look on its own terms: the devices ARE connected
and working (Player One camera, ZWO AM5 native serial, EAF), and have been for
weeks. If the condition is "connected but not assigned to the active profile",
say that; if the rig is in a state the product considers unfinished after months
of successful nights, the condition is probably wrong rather than the rig.

---

## The pattern

Findings 1, 2 and 3 are one defect wearing three hats: **the system knows, and
the screen does not say.** ResumeArm knows a run is armed and why it is holding;
the ledger knows which flows have run; the engine knows whether a plan was
compiled from a graph. In all three cases the information exists server-side and
dies before it reaches a pixel.

That is the same shape as #252, where the engine knew a night was 58 frames
short and recorded the word "complete".
