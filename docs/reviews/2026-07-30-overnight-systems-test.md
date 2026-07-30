# Overnight systems test — 2026-07-30

Run unattended on the real rig between 02:07 and 03:40, driven through the UI in
a real browser rather than through the API. Mount parked at 03:38, 50 minutes
before astronomical dawn.

**The headline: autofocus on this rig cannot work, and now we know exactly why.**
The native star detector finds 2 stars in a frame where the Python one finds 200.
That single defect explains the entire lost session — see §1.

---

## 1. Root cause: the native detector is blinded by saturated pixels

Measured on a real frame taken tonight at best focus, on a rich Cygnus field
(8 s, gain 300, bin 1):

| input | native `detect_and_measure` |
|---|---|
| the frame as captured | **2 stars** |
| × 2, × 4, × 8, × 16 | **2 stars** — scaling changes nothing |
| **(frame − median) × 8** | **405 stars** |

Python's `detect_stars` finds **200** in the unmodified frame.

Frame statistics: median **735**, p99.9 **1229**, max **65535**. A handful of
saturated pixels sit roughly fifty times above every real star.

The tell is the last row. The same detector, on the same pixels, finds a full
field the moment the background is removed — so this is not sensitivity, it is
the threshold being computed from a statistic that a few extreme outliers
dominate. Python's detector does background-and-sigma thresholding and is
unaffected, which is why the two disagree by 100× on identical data.

**Consequence:** every autofocus sweep gets 0–3 stars at every position, cannot
fit a curve, and dies as `not_enough_spread` or `r_squared_below_threshold`.
There is no exposure that fixes it — a longer exposure makes the saturation
worse. Tracked as **#102**, and it should be the next thing fixed.

## 2. Both of last night's autofocus bugs are fixed, verified on hardware

Driven by clicking **FOCUS MY SCOPE** in a real browser:

```
verdict: refused-sparse
still says measuring: False
"couldn't focus. not enough stars to lock onto — check the sky is clear
 and roughly focused, then try again."
```

and in the log:

```
02:45:01  autofocus sweep: 2s at gain 120, bin 2, 9 points of 1500 steps around 18679
02:45:07  autofocus: 1 stars at the starting position
02:45:08  autofocus not attempted: only 1 stars ... Try a longer exposure than 2s,
          bin 1 instead of 2, or a richer field.
```

**Seven seconds to a verdict**, against 75 seconds of blind sweeping followed by
a screen that read "measuring…" for 39 minutes. `still says measuring: False`
confirms the stale-UI half is dead too.

Note what the message got right: it correctly identified a sparse field and
named the levers. It was telling the truth about a symptom whose cause is §1.

## 3. A park that would have failed at dawn

Found by test-firing the dawn failsafe instead of trusting it.

**With tracking ON, the AM5 accepts `:hP#` and silently does nothing.** The mount
never moves, never reports parked, and the park times out at 60 s:

```
02:13:00 [goto] goto failed: ZWO AM5: park did not complete within 60s
```

Tracking off first, and the identical park lands in ~15 s. Reproduced both ways.

This is the one that mattered: a night always ends with the mount tracking, so
*park at dawn* — the single operation standing between the sun and the optics —
hit this every time. Every earlier successful park in testing happened to be
from an idle mount. Fixed in the driver (`eb50de5`) with the wire-command
**order** asserted in a regression test, and repeated independently inside the
failsafe script so it does not depend on the deployed server version.

## 4. What else was verified

| | result |
|---|---|
| Blackout filter slot | **works on the real wheel** — 8 slots, `Dark` at index 0, `opaque[0]=True`, `dark_slot=0` |
| Capture via the UI | frames `0007`, `0008` landed on disk from real button presses |
| Mount home / slew | home → pole (alt 37.3, az 0); goto Deneb → alt 71.5 |
| WebSocket / live UI | connects, renders live rig state |
| Dawn failsafe | **fires and parks a tracking mount in 47 s**, proven end to end |

## 5. Not completed, and why

- **A successful autofocus convergence.** Blocked by §1 — no setting reaches four
  measurable stars on this rig.
- **TPPA.** Needs plate solving, which needs stars; blocked by the same defect.
- **A mosaic.** Time. It needs a working focus and solve first to be meaningful.

Chasing these before fixing §1 would have been testing around the bug.

## 6. Three times tonight my own instrument was the bug

Recorded because it is a pattern, and the same pattern cost the user a night.

- The first UI run "tested Capture" **while displaying Equipment** — the app is
  not hash-routed, so navigating by URL silently stayed put. Then `exact=True`
  on the nav name made the match case-sensitive against an uppercase label.
- "websocket / live status: **PASS**" was a grep for the word *live* in the body,
  while **every** handshake was 403ing and the app had no data at all. Playwright's
  `extra_http_headers` do not apply to WebSocket upgrades.
- "capture: no new preview in 90 s" polled `preview_id` on `/api/status`, **a
  field that does not exist there** — while frames were demonstrably landing on
  disk.

Each one produced a confident, wrong result. The fix each time was to look at
what the system actually emits rather than what I assumed it would.

## 7. Also worth a look

- The focuser failed to reach **26000** and **33000** within 120 s, while
  reaching 29500 fine. Inconsistent, so probably not a travel limit — worth
  watching.
- Star counts at a fixed position varied **10 → 3** minutes apart. Consistent
  with §1: near the threshold, tiny changes flip stars in and out.
- `POST /api/mount/tracking` takes `on` as a **query parameter**, not a body.
  Easy to get wrong from a client; worth a body model.
