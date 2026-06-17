# Authoring Contract — AstroDeck Onboarding Primer

This is the rulebook for writing sections of the scroll-driven astrophotography
primer. The primer is one self-contained HTML page. A build step (or a human with
a clipboard) takes the global shell, `_shell.html`, and injects section files into
it at three marked spots. You write sections. The shell handles everything else:
fonts, the design system, scroll reveals, the progress bar, the dot navigation, and
the reduced-motion guard.

Your job is to write good sections that snap cleanly into that machine without
fighting it or leaking into anyone else's. If you follow this contract, your
section will look like it was always part of the whole. If you don't, it will look
like a ransom note. We can tell the difference.

---

## 1. The mental model

The shell is the stage. Your section is one act. You do not rebuild the stage, you
do not repaint the theater, and you absolutely do not name a variable `--text` and
hope for the best. Everything global already exists. Reach for it.

There is exactly one shared visual language, defined as CSS custom properties on
`:root` in the shell. You style your section by **using those tokens**, not by
inventing new colors and sizes. If a value you need genuinely doesn't exist in the
token set, that's a conversation with the creative director, not a hardcoded
`#3a4f7c` you sneak in at 2am.

---

## 2. The section file format

Each section lives in its own file. A section file is **three fenced blocks**,
each delimited by EXACT HTML comments. The build step reads these markers
literally — byte for byte — so do not paraphrase them, pluralize them, add spaces
inside them, or get creative with capitalization.

```
<!--STYLE-->
  /* your section's CSS goes here — no <style> tag, just rules */
<!--/STYLE-->

<!--BODY-->
  <!-- your section's HTML goes here -->
<!--/BODY-->

<!--SCRIPT-->
  // your section's JS goes here — no <script> tag, just code
<!--/SCRIPT-->
```

Rules of the format:

- **All three blocks must be present**, in this order, even if one is empty. An
  empty `SCRIPT` block is fine and common. Don't delete the markers — the build
  step looks for all six.
- **No wrapping tags inside the blocks.** The build injects your `STYLE` contents
  into the shell's existing `<style>` (at `<!--INJECT:STYLES-->`), your `BODY` into
  `<main>` (at `<!--INJECT:SECTIONS-->`), and your `SCRIPT` into a `<script>` near
  `</body>` (at `<!--INJECT:SCRIPTS-->`). Adding your own `<style>` or `<script>`
  tags will nest them and break the page.
- **Nothing outside the blocks** in the file. No stray prose, no leading H1. The
  text the reader sees lives inside `BODY`.

---

## 3. The slug rule (this is the one that keeps the peace)

Every section gets a **slug** — a short, lowercase, hyphenated id. Polar alignment
is `pa`. Light pollution is `lp`. Stacking is `stack`. Pick something short; you'll
type it a lot.

**Every class and id you introduce in your section MUST be prefixed with your
slug.** No exceptions, no "but this one is obviously unique."

- `.pa-diagram`, `.pa-dot`, `.pa-caption`, `#pa-canvas` — yes.
- `.diagram`, `.dot`, `.active`, `#canvas` — absolutely not. Someone else already
  used `.active`, and now your dot and their dot are fighting in production.

Why this matters: every section's CSS is poured into **one shared stylesheet**, and
every section's HTML lives in **one shared DOM**. There is no scoping, no shadow
root, no module boundary. The slug prefix *is* your namespace. It is the entire
reason two authors can work on two sections and not corrupt each other's work.

The same goes for JavaScript. If you need a global-ish variable or function, scope
it inside an IIFE (`(function(){ ... })();`) so it never touches `window`. Query
your own elements by your slug-prefixed selectors. Never assume you're the only
script on the page, because you are emphatically not.

### The section wrapper

Your `BODY` block's outermost element is a `<section>` with the shared
`.primer-section` class, a slug `id`, and a `data-nav` label. That's what wires you
into the layout column and the dot navigation:

```html
<section class="primer-section" id="pa" data-nav="Polar Alignment">
  <p class="kicker">Pointing the Mount</p>
  <h2 class="reveal">Polar alignment, or: aiming at a hinge</h2>
  <p class="lede reveal">One short paragraph that sets the scene.</p>
  <!-- ...the rest of your section, all .pa-* from here down... -->
</section>
```

- The `id` is your slug. The dot nav and the skip link depend on it.
- `data-nav` is the human label shown in the dot-nav tooltip. Keep it to one or two
  words. If you omit it, the nav falls back to the raw id, which looks like a
  database leaked.
- Use the shared classes `.kicker`, `.lede`, and `.caption` for the standard
  section furniture (see §5). Everything *else* is yours and gets the slug.

---

## 4. The design tokens (use these, invent nothing)

These are defined on `:root` in the shell. Reference them with `var(--name)`. This
table is the contract — if it's not here, it doesn't exist for you.

### Color
| Token | Use it for |
|---|---|
| `--c-bg` | The page background. You rarely set this; it's already the canvas. |
| `--c-bg-raised` | Panels, cards, anything lifted off the page. |
| `--c-bg-sunk` | Wells, code blocks, insets, anything pushed in. |
| `--c-ink` | Primary text. |
| `--c-ink-soft` | Secondary text, supporting copy. |
| `--c-ink-faint` | Captions, metadata, the quiet stuff. |
| `--c-line` | Hairlines, borders, dividers. |
| `--c-accent` | THE amber accent. Highlights, active states, one thing per view. |
| `--c-accent-soft` | Lighter amber for hovers and glints. |
| `--c-accent-deep` | Pressed states, underlines. |
| `--c-cool` | Cool blue counter-accent. Sky, links, "the other thing." Sparingly. |
| `--c-good` | A reassuring green. You will almost never need it. Resist. |

A note on the accent: amber is a spotlight, not wallpaper. If everything is
accented, nothing is. Aim for roughly one amber moment per screenful.

### Type
| Token | Use it for |
|---|---|
| `--font-display` | Headings. Fraunces. Don't set body text in it. |
| `--font-body` | Running prose. Newsreader. This is the default; you rarely set it. |
| `--font-mono` | Kickers, labels, data, code, anything that should feel technical. |
| `--t-xs` … `--t-4xl` | The size scale: `--t-xs`, `--t-sm`, `--t-base`, `--t-lg`, `--t-xl`, `--t-2xl`, `--t-3xl`, `--t-4xl`. |
| `--leading-tight` / `--leading-snug` / `--leading-body` | Line heights for headings / leads / prose. |

Use the scale steps. A heading is `--t-2xl`, not `2.6rem` because it "felt right."
Felt-right is how a design system dies.

### Space
| Token | Roughly |
|---|---|
| `--s-1` … `--s-8` | The spacing rhythm, small to enormous (`0.5rem` up through `11rem`). Use these for margins, padding, and gaps. |
| `--gutter` | The responsive page edge padding. Match it if you go full-bleed. |
| `--measure` | Ideal reading-column width (~38rem). |
| `--measure-wide` | Wide width for figures and big moments (~62rem). |

### Edges & depth
`--radius`, `--radius-sm`, `--shadow`, `--ring` (a 1px line border via box-shadow).

### Motion
| Token | Use it for |
|---|---|
| `--ease` | The house easing. Gentle, no overshoot. Use it for reveals and big moves. |
| `--ease-soft` | Linear-ish ease for small UI transitions (hovers, color). |
| `--dur` | Standard transition/animation duration (~0.7s). |
| `--dur-fast` | Quick interactions (~0.3s). |

---

## 5. Shared classes you may (and should) use

These live in the shell. They are the only non-slugged classes you're allowed to
apply, because they're shared furniture, not your private styling.

- **`.primer-section`** — the section wrapper. Always on your outer `<section>`.
- **`.reveal`** — the scroll-in animation. See §6.
- **`.kicker`** — the small mono label above a heading.
- **`.lede`** — the larger, lighter intro paragraph under a heading.
- **`.caption`** — small faint text for figure captions and asides.
- **`.bleed-wide`** / **`.bleed-full`** — on a *direct child* of `.primer-section`,
  these widen it to `--measure-wide` or full width, escaping the reading column.
  Use for diagrams and figures that need room to breathe.

Everything else is yours, and everything else is slugged.

---

## 6. Using `.reveal`

`.reveal` is how things fade and rise into view as the reader scrolls. The shell
runs one IntersectionObserver over every `.reveal` on the page and adds `.is-in`
when the element crosses into view. It reveals **once** and then stops watching, so
scrolling back up won't replay it. You don't write any JS for this — just add the
class.

- Put `.reveal` on the things that should animate in: a heading, a paragraph, a
  figure, a card. Don't carpet-bomb it onto every element; a section where
  literally everything floats up at once feels like a screensaver.
- **Stagger** with an inline delay using the `--reveal-delay` variable the shell
  reads:

  ```html
  <li class="pa-step reveal" style="--reveal-delay: .1s">…</li>
  <li class="pa-step reveal" style="--reveal-delay: .2s">…</li>
  <li class="pa-step reveal" style="--reveal-delay: .3s">…</li>
  ```

  Keep delays modest — somewhere under about half a second total across a group.
  The reader scrolled here to learn something, not to wait for your choreography.
- The reveal's *look* (fade + 22px rise) is fixed in the shell so the whole
  document moves with one personality. Don't redefine `.reveal`. If you need a
  different entrance for something special, build a slugged animation (§7) instead
  of overriding the shared one.

---

## 7. Section-local animation & JavaScript

If your section has its own motion (a rotating diagram, a draggable slider, a
canvas), keep all of it inside your section and inside your namespace.

- **`@keyframes` must be slug-prefixed**: `@keyframes pa-spin { … }`. Animation
  names are global too — an unprefixed `@keyframes spin` will collide just like a
  class.
- **All JS goes in one IIFE** in your `SCRIPT` block. Declare nothing on `window`.
  Select only your own slugged elements.
- **Honor reduced motion yourself** for anything beyond `.reveal`. The shell guards
  CSS transitions/animations globally and short-circuits `.reveal`, but a
  JS-driven `requestAnimationFrame` loop or a `setInterval` animation is invisible
  to that guard — you have to check it:

  ```js
  (function () {
    var reduce = window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      // render the final, static state and return — no loop, no autoplay
      return;
    }
    // ...your animation here...
  })();
  ```

  The rule of thumb: if a person who gets motion-sick opened this page, would your
  section make them regret it? If yes, gate it behind that check and ship a calm
  static version.

- **Don't autoplay anything loud or fast**, and never trap scroll. The reader owns
  the scrollbar. We are guests in their afternoon.

---

## 8. Accessibility (non-negotiable, and not hard)

- **Heading order is real.** The shell page has one `<h1>` (the title). Your section
  leads with an `<h2>`. Subheads inside go `<h3>`, then `<h4>`. Don't skip levels to
  get a font size — that's what the type tokens are for.
- **Color is never the only signal.** If amber means "this is the important one,"
  back it with a label, an icon, weight, or position. Some readers won't see the
  amber at all.
- **Images need `alt`.** Decorative-only? `alt=""` and `aria-hidden="true"`.
  Diagrams that carry meaning need a real description, or a `.caption` that conveys
  the same point in words.
- **Interactive things must work from the keyboard.** If you build a control, it's a
  real `<button>`/`<a>`/`<input>`, it's reachable by Tab, and it shows a focus ring
  (the shell provides a global `:focus-visible` outline — don't remove it).
- **Contrast.** The tokens are tuned to pass against the dark canvas. Use `--c-ink`
  and `--c-ink-soft` for anything you actually expect people to read. `--c-ink-faint`
  is for genuinely incidental text only.

---

## 9. Voice & altitude (since you'll be writing prose too)

The reader is brilliant about software and design and knows nothing about
astrophotography, and that's the whole point — you're building intuition, not
delivering a lecture. A few house rules:

- **Explain the idea well enough to have an opinion about the UI, then stop.** The
  goal is that, having read your section, the reader can look at the AstroDeck
  screen and think "that control is in the wrong place." You do not need to get them
  to the underlying physics. When you feel the pull to explain *why* the sky does
  the thing, that's usually the exit.
- **Calm, precise, confident, generous.** Restraint over hype. Real paragraphs with
  rhythm, not feature-bullets stacked like firewood.
- **Humor is welcome when it's dry, specific, and free.** A good line costs the
  reader nothing and earns a real exhale. A forced one costs clarity. If a joke and
  a clear sentence are fighting, the clear sentence wins.
- **Banned, because they're the tells of writing nobody chose to do:** staccato
  fragment triads ("X. Y. Z."); the "it's not just X, it's Y" construction; empty
  superlatives (revolutionary, seamless, powerful, effortless, game-changing);
  "imagine a world where"; opening on a rhetorical question; walls of punchy
  one-liners; sentences that sound profound and mean nothing; emoji as punctuation.

---

## 10. Before you hand it in — the checklist

- [ ] File has all six markers, exact, in order: `STYLE`, `BODY`, `SCRIPT` (open +
      close each).
- [ ] No `<style>` / `<script>` tags inside the blocks.
- [ ] Outer element is `<section class="primer-section" id="SLUG" data-nav="…">`.
- [ ] **Every** class, id, and `@keyframes` you added is slug-prefixed.
- [ ] You used `var(--token)` for color/type/space/motion — no stray hex, px sizes,
      or hand-rolled easing curves.
- [ ] `.reveal` is used with intention and staggered where it groups; you didn't
      redefine it.
- [ ] All JS is inside one IIFE; nothing leaks to `window`.
- [ ] Any JS-driven motion checks `prefers-reduced-motion` and has a static
      fallback.
- [ ] Heading levels descend properly; images have `alt`; controls are real,
      focusable elements.
- [ ] Read it out loud. If a sentence made you wince, it'll make the worm wince too.

Build it like the people downstream are smart, busy, and slightly tired. They are.
