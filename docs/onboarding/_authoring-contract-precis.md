# Authoring Contract — AstroDeck Precis (Reference-Card Edition)

This is the rulebook for writing topics in the **precis**: the compact, factual
reference version of the astrophotography primer. Not the scroll-essay. This page is
a stack of tight topic cards a reader can scan. One topic = a one-line definition, a
few labeled facts, one "what breaks if wrong" line, and the AstroDeck UI hook.

A build step takes the shell, `_shell_precis.html`, and injects topic files into it
at three marked spots. You write topics. The shell owns fonts, the design system, the
scroll-progress bar, the topic nav, the reduced-motion guard, and the **replayable
animation framework**.

If you've read the essay-edition contract (`_authoring-contract.md`), this is the same
machine with two differences: the layout is a compact card, not full-bleed sections,
and **animations replay every time a topic re-enters the viewport** instead of firing
once. Everything below is what's specific to the precis.

---

## 1. The mental model

The shell is the card stock. Your topic is one printed card on it. You do not invent
global color, type, or spacing — those live as CSS custom properties on `:root` in the
shell. You style by **using those tokens** (`var(--name)`), never by sneaking in a hex
value at 2am. If a value you need genuinely isn't in the token set, that's a
conversation with the creative director, not a hardcoded `#3a4f7c`.

The aesthetic is deliberately dense: maximum information, minimum words, generous but
efficient whitespace. Favor compact labeled facts over paragraphs. One dry aside per
topic is fine. Never pad for a joke.

---

## 2. The topic file format (three blocks, exact markers)

Each topic lives in its own file as **three fenced blocks**, each delimited by EXACT
HTML comments. The build step reads these byte for byte — do not paraphrase,
pluralize, add inner spaces, or change capitalization.

```
<!--STYLE-->
  /* your topic's CSS — no <style> tag, just rules */
<!--/STYLE-->

<!--BODY-->
  <!-- your topic's HTML -->
<!--/BODY-->

<!--SCRIPT-->
  // your topic's JS — no <script> tag, just code (often empty)
<!--/SCRIPT-->
```

Rules:

- **All three blocks present, in this order**, even when empty. An empty `SCRIPT`
  block is normal. Don't delete markers — the build looks for all six.
- **No wrapping tags inside blocks.** `STYLE` is poured into the shell's `<style>`
  (at `<!--INJECT:STYLES-->`), `BODY` into `<main id="precis">` (at
  `<!--INJECT:SECTIONS-->`), and `SCRIPT` into a `<script>` near `</body>` (at
  `<!--INJECT:SCRIPTS-->`). Adding your own `<style>`/`<script>` nests them and breaks
  the page.
- **Nothing outside the blocks.** No stray prose, no leading H1. The reader's text
  lives inside `BODY`.

The injection markers exist on their own lines in the shell, exactly:
`<!--INJECT:STYLES-->`, `<!--INJECT:SECTIONS-->`, `<!--INJECT:SCRIPTS-->`.

---

## 3. The slug-prefix rule (the one that keeps the peace)

Every topic gets a **slug** — short, lowercase, hyphenated. Polar alignment is `pa`,
light pollution is `lp`, plate solving is `solve`. You'll type it a lot, so keep it
short.

**Every class, id, and `@keyframes` name you introduce MUST be prefixed with your
slug.** No exceptions, not even for "this one's obviously unique."

- `.pa-diagram`, `.pa-dot`, `#pa-canvas`, `@keyframes pa-spin` — yes.
- `.diagram`, `.dot`, `.active`, `@keyframes spin` — no. All CSS lands in one shared
  stylesheet and all HTML in one shared DOM. There is no scoping, no shadow root. The
  slug prefix *is* your namespace.

The shared, **non-slugged** classes you may use (they're shell furniture, defined in
§5) are the only exception. All JS goes in one IIFE; declare nothing on `window`;
query only your slug-prefixed selectors.

### The topic wrapper

Your `BODY` block's outermost element is a `<section>` with the shared
`.precis-topic` class, your slug `id`, and a `data-nav` label. That wires you into the
layout column, the topic nav, and the **animation observer** (the observer keys off
`.precis-topic`). Put the visible content inside a `.precis-card`:

```html
<section class="precis-topic" id="pa" data-nav="Polar Alignment">
  <article class="precis-card">
    <p class="kicker"><span class="num">03</span> Pointing the Mount</p>
    <h2 class="reveal">Polar alignment</h2>
    <p class="def reveal">Aligning the mount's spin axis with the sky's, so one motor
      tracks a star all night.</p>

    <dl class="facts">
      <dt>Target</dt><dd>The celestial pole (near Polaris, north).</dd>
      <dt>Tolerance</dt><dd><span class="stat">&lt; 5 arcmin</span> error for long subs.</dd>
      <dt>Tool</dt><dd>Polar scope, or a software routine that reads two exposures.</dd>
    </dl>

    <p class="stakes">Off by a degree and stars smear into short streaks; every
      sub-exposure is <b>field-rotated</b> and the stack is soft.</p>
    <p class="hook">AstroDeck shows a live <b>drift target</b> with an amber
      bullseye; tightening the error rings the bell.</p>
    <p class="aside">It is aiming at a hinge you cannot see. Welcome.</p>
  </article>
</section>
```

- The `id` is your slug. The topic nav and the skip link depend on it.
- `data-nav` is the human label in the nav rail (one or two words). Omit it and the
  nav falls back to the raw id, which looks like a database leaked.
- Heading order is real: the page has one `<h1>`; your topic leads with `<h2>`,
  subheads go `<h3>` then `<h4>`. Don't skip levels for a font size.

---

## 4. The design tokens (use these, invent nothing)

Defined on `:root` in the shell. Reference with `var(--name)`. If it's not here, it
doesn't exist for you. **Same brand family as the essay edition** — Fraunces /
Newsreader / IBM Plex Mono, the same warm near-black night and amber lamp — with the
type and spacing scales trimmed for a denser card.

### Color
| Token | Use it for |
|---|---|
| `--c-bg` | Page background (the night). You rarely set it. |
| `--c-bg-raised` | Cards, panels — the `.precis-card` surface. |
| `--c-bg-sunk` | Wells, code, fact insets. |
| `--c-ink` | Primary text. |
| `--c-ink-soft` | Secondary text, supporting copy. |
| `--c-ink-faint` | Hairline decoration, the quiet non-text stuff. |
| `--c-caption` | Faint TEXT meant to be read (captions, asides) — AA on bg. |
| `--c-line` | Hairlines, borders, dividers. |
| `--c-accent` | THE amber. Highlights, active nav, one moment per card. |
| `--c-accent-soft` | Lighter amber for hovers / glints / stats. |
| `--c-accent-deep` | Pressed states, underlines. |
| `--c-cool` | Cool blue counter-accent (sky, links). Sparingly. |
| `--c-good` | Reassuring green. You'll almost never need it. |
| `--c-warn` | Warm rust, used by the shell on the "what breaks" line. |

Amber is a spotlight, not wallpaper. Aim for roughly one amber moment per card.

### Type
| Token | Use it for |
|---|---|
| `--font-display` | Headings. Fraunces. Not body text. |
| `--font-body` | Running prose. Newsreader. The default; you rarely set it. |
| `--font-mono` | Kickers, labels, data, code — anything technical. IBM Plex Mono. |
| `--t-xs` … `--t-4xl` | Size scale: `--t-xs`, `--t-sm`, `--t-base`, `--t-lg`, `--t-xl`, `--t-2xl`, `--t-3xl`, `--t-4xl`. |
| `--leading-tight` / `--leading-snug` / `--leading-body` | Line heights for headings / leads / prose. |

Use the scale steps. A heading is `--t-2xl`, not `2.3rem` because it "felt right."

### Space
| Token | Roughly |
|---|---|
| `--s-1` … `--s-8` | The spacing rhythm, small to large (`0.5rem` up through `9rem`). |
| `--gutter` | Responsive page-edge padding. |
| `--measure` | Reading-column width inside a card (~40rem). |
| `--measure-wide` | Wide width for the grid / figures (~64rem). |

### Edges, depth & motion
`--radius`, `--radius-sm`, `--shadow`, `--ring` (a 1px line via box-shadow);
`--ease` (house easing), `--ease-soft` (small UI transitions), `--dur` (~0.6s),
`--dur-fast` (~0.3s).

---

## 5. Shared classes you may (and should) use

These live in the shell — the only non-slugged classes you're allowed to apply,
because they're shared furniture, not your private styling.

- **`.precis-topic`** — the outer `<section>` wrapper. Always present. The animation
  observer watches these.
- **`.precis-card`** — the lifted card your content sits in. One per topic.
- **`.kicker`** — small mono label above the title. Optional `<span class="num">`
  inside for a running topic number.
- **`.def`** — the crisp one-line definition under the title (the precis equivalent of
  a lede). Keep it to one sentence.
- **`.facts`** — a `<dl>` rendered as a tidy two-column `label : value` grid. This is
  the workhorse. Use `<dt>` for the mono label, `<dd>` for the value.
- **`.stat`** — wrap a single key number to make it pop in amber-mono (`<span
  class="stat">f/5</span>`).
- **`.stakes`** — the one-line "what breaks if wrong." The shell prefixes it with a
  rust `BREAKS` tag automatically; just write the consequence.
- **`.hook`** — the one-line AstroDeck UI hook. The shell prefixes it with an amber
  `UI` tag automatically; just write what the screen does.
- **`.aside`** — a single dry italic aside, set apart, never load-bearing.
- **`.caption`** — small faint text for figure captions.
- **`.reveal`** — the scroll-in animation (see §6).
- **`.bleed-wide`** — on a direct child of `.precis-card`, widens a figure to the card
  edges.

Everything else is yours, and everything else is slugged.

---

## 6. Animation: how to make it REPLAY (the whole point of this edition)

The earlier docs fired each animation once, the instant a section's top edge touched
the viewport, and never replayed. The precis shell fixes both faults:

- A topic counts as **"in" only when it is properly on screen** — the observer uses
  `threshold 0.5` plus a `rootMargin: -15% 0px -15% 0px`, so the trigger band sits at
  the viewport center, not the top edge.
- On exit the shell **re-arms** the topic: it strips the active state so the animation
  resets, and replays it the next time the topic scrolls back into the center band.
  **Every animation replays on every re-entry.**

You wire your motion through one of two author hooks. Both are driven by the same
observer adding/removing the `.in` class on your `.precis-topic`.

### 6a. The `.reveal` shortcut (no code)

Add `class="reveal"` to anything that should fade + rise in. The shell reveals it when
the topic is `.in` and hides it again when the topic leaves — so it replays for free.
Stagger a group with an inline delay:

```html
<h2 class="reveal">Title</h2>
<p class="def reveal" style="--reveal-delay: .08s">One-line definition.</p>
<dl class="facts reveal" style="--reveal-delay: .16s"> … </dl>
```

Keep total stagger under ~0.4s. Don't carpet-bomb `.reveal` onto everything — a card
where every element floats at once feels like a screensaver. Don't redefine `.reveal`.

### 6b. The CSS path — `.in`-gated animations (replay built in)

Declare your slugged animation under the topic's `.in` class. When the observer adds
`.in`, it runs; on exit the observer removes `.in` **and forces a reflow**, so the
next entry restarts the animation rather than skipping it as a no-op. You write zero
JS — the replay is automatic.

```css
/* in your STYLE block */
@keyframes pa-sweep {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}
.pa-needle { transform: rotate(0deg); }          /* resting / final-safe state */
.precis-topic.in .pa-needle {
  animation: pa-sweep var(--dur) var(--ease) both;
}
```

```html
<!-- in your BODY block -->
<div class="pa-dial bleed-wide" aria-hidden="true"><div class="pa-needle"></div></div>
```

That `.pa-needle` rotates in on entry, and again on every re-entry. Reduced motion is
handled globally by the shell's CSS guard (animations are zeroed), so paint a sensible
resting state on the base selector as shown.

### 6c. The JS path — `AstroAnim.register(el, { play, reset })`

For canvas, `requestAnimationFrame` loops, or anything CSS can't express, register the
element with the global framework. The observer calls **`play()` when the topic enters
the center band** and **`reset()` when it leaves**. Register inside your IIFE; the
framework is already defined when your script runs.

```js
// in your SCRIPT block
(function () {
  var el = document.querySelector("#pa-canvas");
  if (!el || !window.AstroAnim) return;

  var raf = 0;
  function draw(t) { /* … paint a frame … */ raf = requestAnimationFrame(draw); }

  window.AstroAnim.register(el, {
    play:  function () { cancelAnimationFrame(raf); raf = requestAnimationFrame(draw); },
    reset: function () { cancelAnimationFrame(raf); /* clear to frame 0 */ }
  });
})();
```

Contract for your callbacks:

- `play()` is called once per entry (the framework guards against double-firing) and
  again on each re-entry. Make it idempotent — cancel any prior loop before starting.
- `reset()` is called on exit. Stop loops and restore frame zero, so the next `play()`
  starts clean.
- **Reduced motion is automatic.** When `prefers-reduced-motion: reduce` is set, the
  framework calls your `play()` exactly **once at registration** to paint the final
  frame, and never calls `reset()` and never replays. So make `play()` able to render a
  sensible final/static state, not just kick off motion. You do not need your own
  media-query check for the register path — but if your `draw` loop must not run under
  reduced motion, branch inside `play()` on `window.AstroAnim.reduceMotion`.

Both paths share the same trigger: the topic's `.in` state. You can use `.reveal`, the
CSS path, and the JS path together in one topic.

---

## 7. Accessibility (non-negotiable, not hard)

- One `<h1>` on the page (the shell title); your topic starts at `<h2>` and descends.
- Color is never the only signal. Amber/rust tags (`UI`, `BREAKS`) carry a word, not
  just a hue. Keep it that way.
- Images need `alt`. Decorative diagrams get `alt=""` + `aria-hidden="true"`; a diagram
  that carries meaning needs a real description or a `.caption` that says the same
  thing in words.
- Interactive things are real `<button>`/`<a>`/`<input>`, reachable by Tab, with the
  shell's `:focus-visible` ring left intact.
- Use `--c-ink` / `--c-ink-soft` for anything meant to be read. `--c-ink-faint` is
  decoration only; `--c-caption` is the faint-but-readable token.

---

## 8. Voice & altitude

The reader is brilliant at software and design and knows nothing about
astrophotography — that's the point. Build enough intuition that they can look at an
AstroDeck screen and have an opinion about it, then stop. You don't owe them the
physics.

This edition is a **reference card, not an essay.** Per topic: crisp one-line
definition, the few facts/numbers that matter, one "what breaks," one UI hook, at most
one dry aside. Compact labeled facts beat paragraphs. Maximum information, minimum
words.

**Banned (the tells of writing nobody chose to do):** staccato fragment triads
("X. Y. Z."); the "not just X, but Y" construction; empty superlatives (seamless,
revolutionary, powerful, effortless); "imagine a world where"; opening on a rhetorical
question; sentences that sound profound and mean nothing; emoji as punctuation.

---

## 9. Before you hand it in — the checklist

- [ ] File has all six markers, exact, in order: `STYLE`, `BODY`, `SCRIPT` (open +
      close each).
- [ ] No `<style>` / `<script>` tags inside the blocks.
- [ ] Outer element is `<section class="precis-topic" id="SLUG" data-nav="…">`, with a
      `.precis-card` inside.
- [ ] **Every** class, id, and `@keyframes` you added is slug-prefixed.
- [ ] You used `var(--token)` for color/type/space/motion — no stray hex, px, or
      hand-rolled easing.
- [ ] `.reveal` used with intention and staggered where it groups; you didn't redefine
      it.
- [ ] Any CSS animation is gated on `.precis-topic.in …` so it replays; any JS motion
      goes through `AstroAnim.register` with idempotent `play()` and a clean `reset()`.
- [ ] `play()` can paint a sensible final/static frame (reduced motion calls it once).
- [ ] All JS is inside one IIFE; nothing leaks to `window`.
- [ ] Heading levels descend; images have `alt`; controls are real, focusable
      elements.
- [ ] One definition, a few facts, one "breaks," one hook, at most one aside. Read it
      out loud; if a sentence made you wince, it'll make the worm wince too.
