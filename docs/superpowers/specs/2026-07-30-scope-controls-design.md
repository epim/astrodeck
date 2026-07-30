# Scope controls: a design for the screen you actually hold

The complaint, from the first real night: *"the image captured is near the top of
the screen but I have to scroll down to choose which focus input I want to give,
then scroll back up to see the result. it..stinks."*

That is not a layout bug. It is the geometry of a portrait phone, and no amount
of rearranging fixes it.

---

## The argument, in numbers

A 3:2 preview fitted to each viewport, and what is left over beside it:

| viewport | preview | % of screen | room beside it |
|---|---|---|---|
| phone portrait 390×844 | 390×260 | 31% | **0 px** |
| phone landscape 844×390 | 584×390 | **69%** | **260 px** |
| S25U portrait 412×915 | 412×275 | 30% | 0 px |
| S25U landscape 915×412 | 617×412 | 67% | 298 px |
| tablet portrait 820×1180 | 820×548 | 46% | 0 px |
| tablet landscape 1180×820 | 1180×788 | 96% | 0 px |

Two things fall out:

1. **In portrait there is nowhere to put a control except below the image.** The
   preview already spans the full width, so every control is a scroll away from
   the thing it controls. That *is* the bug, and it is unfixable in portrait.
2. **Landscape more than doubles the image and yields a 260 px rail.** 260 px is
   not a compromise — it is a comfortable column for a stepper, a dial and a
   readout, permanently beside the picture.

The sensor is 3:2. The screen should be too.

---

## Do we lock the orientation?

**No — and not for taste reasons.** iOS Safari does not implement the Screen
Orientation Lock API at all. A lock would work on Android and silently do
nothing on an iPhone, which is worse than not having one: the same build behaves
differently on two phones and neither user knows why. A hard lock also overrides
people who set orientation lock deliberately — motion sensitivity, or a tablet
bolted to a pier.

So: **design landscape-first, and make portrait an honest reduced mode.**

- **Landscape is the designed case.** Full rail, nothing scrolls, everything
  reachable.
- **Portrait still works**, via a bottom sheet (below) rather than a page that
  scrolls the image out of view.
- **Say so once.** The first time a control screen opens in portrait, a single
  dismissible line: *"Turn your phone sideways for the full controls."* Not a
  modal, not repeated, and never shown on a tablet, where portrait is 46% and
  genuinely usable.

---

## The layout system

One structure, three payloads (focus, capture, mount). The preview **never
moves** — that is the whole rule.

### Landscape — rail

```
┌────────────────────────────────┬──────────────┐
│                                │  HFR   4.57  │   readout: glanceable,
│                                │  ▁▂▃▅▃▂▁     │   never needs a tap
│         LIVE PREVIEW           │              │
│         (fills, 69%)           │  ┌────────┐  │
│                                │  │ AUTO ⚙ │  │   action + its settings
│                                │  └────────┘  │   on ONE line
│                                │              │
│                                │  ⊖   100  ⊕  │   ← thumb row
└────────────────────────────────┴──────────────┘
```

The rail is the **only** scrolling region, and it should rarely need to. The
preview is fixed. Nothing the user does to a control moves the image.

### Portrait — sheet

```
┌──────────────────────┐
│    LIVE PREVIEW      │   pinned, never scrolls away
│                      │
├──────────────────────┤
│  ═══  (drag handle)  │   sheet: peek → half → full
│  ⊖    100     ⊕      │   peek shows the stepper alone
└──────────────────────┘
```

At *peek* the sheet is one row tall and the image keeps ~70% of the height. The
step-and-look loop works without ever leaving the image. Dragging up reveals
autofocus and the rest; dragging is reversible in one gesture, unlike a scroll
that has to be undone by scrolling back.

---

## The step-size dial

Replacing the eight-button grid. Collapsed it is a single control showing the
current magnitude. **Press and hold** and it blooms into an arc; slide the thumb
to a value; release to commit.

```
        collapsed              held
         ╭─────╮         1000 ╮
         │ 100 │          100 ┤◀ thumb
         ╰─────╯           10 ┤
                            1 ╯
```

Why an arc rather than a menu: one continuous gesture, no second tap, no
precision required, and the thumb never leaves the glass. It is also
self-cancelling — slide back to the centre and release to change nothing, which
matters when you are cold and half-asleep and the mount is worth more than
the phone.

The arc opens **upward and inward** from the dial, so the thumb does not cover
the options it is choosing between. On the right-hand rail that means opening
left; a left-handed layout mirrors it.

---

## Thumb zones — the free win

Held in two hands in landscape, thumbs rest at the **bottom corners**. That is
where `−` and `+` belong: one under each thumb, nothing to reach for, no
looking. It costs nothing to place them there and it is the difference between
adjusting focus by feel and adjusting it by hunting.

The dial sits between them: rarely changed, so it can be the one thing you look
at. The readout goes top-of-rail, farthest from the hands and closest to the
image your eye is already on.

---

## Night discipline

The preview is the only bright thing on the screen. Everything else is
near-black with the established red-orange accents, and:

- **no white**, anywhere, ever, on a control surface;
- **no flash on state change** — a control that has just been pressed brightens
  its border, it does not pulse;
- the rail's background is darker than the darkest sky in the preview, so the
  image edge reads as the boundary without needing a line.

---

## What this fixes, from the QA list

- *scroll down, tap, scroll up, look* — gone; controls are beside the image.
- *autofocus at the very bottom of the screen* — it is in the rail, one line,
  with its advanced settings behind the gear on that same line.
- *the autofocus button is the width of the whole screen* — the rail is 260 px,
  so it cannot be.
- *the duplicate "Run autofocus" a inch below "Focus my scope"* — one rail, one
  action, no room for a second.
- *annotations / presets / photometry cluttering Capture* — same rail, same
  rules: one row each, the dial pattern for multi-select annotations, and
  photometry behind a disclosure because it is a once-a-season measurement.

---

## Build order

1. **The shell**: orientation-aware container, fixed preview, rail-or-sheet.
   Everything else plugs into it, and it is the piece the complaint is about.
2. **The dial** as a reusable control — step size first, annotations second.
3. **Focus screen** onto the shell.
4. **Capture screen** onto the shell.
5. Mount nudges into the same rail, which is the point of doing it this way:
   by then it is a payload, not a screen.
