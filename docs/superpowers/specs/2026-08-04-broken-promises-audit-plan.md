# Finding every place AstroDeck claims something it does not do

A plan, not a wish. The bug class below has produced the majority of the real
defects found in this project over the last week, including the two most
expensive: a polar aligner that ran in simulation for twelve days, and a "Warm"
button that cuts a TEC dead while the settings screen promises "a safe ramp".

## The class, named precisely

**A claim made in one place that the code responsible for delivering it does not
keep.** The claim can live in UI copy, a docstring, a config label, a comment, a
test name, or in the mere existence of an offered option. What makes it this
class rather than an ordinary bug is that *something in the product asserts the
behaviour*, so nobody goes looking — the assertion is taken as evidence.

These are not found by asking "does this work?". They are found by asking
**"what does this promise, and who keeps it?"**

## The taxonomy, derived from real instances

Every row is something actually found, not a hypothetical.

### A. Display reads a different source than execution
The console shows a value that is not the one the rig acts on.
- `/api/config` reported the global providers block while the **active profile's**
  block won. Polar ran the simulator for 12 days showing "AstroDeck native".
- The same defect sat unfound in **five optics keys** — Settings, the Atlas FOV
  rectangle and the mosaic altitudes read global; the plate solver, the FITS
  `TELESCOP` card and native TPPA read the profile.
- Polar refusals never reach the screen after a reload: `/api/polar/state` says
  `error` with a full explanation, the UI says "Not started" (#133, still open).

### B. An offered option the executor will not honour
- The guide dropdown offered "AstroDeck native" whenever an imaging camera was
  connected; `_resolve_guide` required a **guide camera**, so the user picked
  native and the badge said PHD2.
- Mirror image, same day: the offer *refused* the PHD2 bridge unless one was
  already connected, while the resolver honours `backend` unconditionally — so
  the screen printed "no bridge is connected" directly above "using the PHD2
  bridge".

### C. An invariant asserted in prose and enforced by nothing
- `key_index_id`'s docstring said "filesystem-safe id". It embedded the raw FITS
  `FILTER` card, so a filter named `../../../../pwned` escaped four levels and
  became an arbitrary `.fits` write.
- `_unlink_saved`'s docstring stated the NINA rule ("saved_paths live on the
  imaging host and are not local") and enforced nothing, so a network peer could
  name any file the service account could delete.
- `tiles.py` justified its open auth posture as "matches the cutout route" — and
  the cutout route was itself un-gated by oversight. A comment can inherit a
  mistake by citation.

### D. Copy promising behaviour that does not exist
- `SafetyLimitsPanel.tsx:62`: "park, then warm the camera **at a safe ramp**".
  Three call sites turn the cooler off and none of them ramps.

### E. Siblings that drifted apart under one stated contract
- `safe_id_path` and `safe_subpath` claim the same containment contract;
  the older one accepted `CON`, `COM3`, `NUL`, `x ` and `x.`.
- `_can_solve` got a deliberate "not configured → warn and proceed" path;
  autofocus never did, so a rig with no autofocus provider could **never**
  auto-resume after a restart and refused every ten minutes forever.

### F. Verification that verifies nothing
- An assertion scanning `group.slice(0, chip.length + 800)` where the index was
  `-1`, so it covered one and a half of three chips and passed by arithmetic.
- Tests appended **below** a file's summary block: they ran, their failures were
  discarded, and the count never moved. I did this myself yesterday.
- Anonymous-access tests written against a client with no auth configured, where
  every route returns 200 and the assertion passes for the wrong reason. Also
  mine.

### G. The source says one thing, the runtime does another
- `text-[11px]` on any `.btn` is **dead** — `.btn`'s unlayered `font-size:12px`
  beats Tailwind's layered utility. ~59 lines across 20+ files render 12px while
  reading 11px.
- `fmtBytes` divides by 1024 and labels the result `GB`.

## The detectors — one per class, because no single technique finds them all

This is the part that makes it a plan rather than a hope. Each class has a
different cheapest detector, and they differ by orders of magnitude in cost.

| class | detector | cost | permanent? |
|---|---|---|---|
| **B** offer vs resolver | **property test**: for every capability, `offered ⊆ what the resolver would honour`, swept over a rig × override matrix | cheap | yes — CI forever |
| **A** display vs execution | **provenance**: every displayed value carries which layer produced it; assert the UI binds the winning layer | medium | yes |
| **E** sibling drift | **shared-corpus test**: one attack/input table bound to *both* implementations | cheap | yes |
| **F** vacuous tests | **mutation**: break the behaviour, assert the test goes red | medium | yes, sampled |
| **C** unenforced invariants | **LLM sweep** of docstrings/comments for assertions, each checked against enforcing code | expensive | no — convert findings to tests |
| **D** false copy | **LLM sweep** of user-facing strings that promise behaviour, traced to the code that would deliver | expensive | no — same |
| **G** source vs runtime | **measure the artefact**: rendered page, actual bytes, real serial reply | medium | partly |

Two rules that fall out of this table and matter more than the table:

**The deliverable is tests, not a report.** A report is read once and then rots.
Every one of the ~14 instances above is now pinned by a test that fails if it
comes back. An audit that ends in a document has bought nothing.

**Rank by what believing the claim costs you.** A wrong docstring is a papercut.
The polar layering cost twelve nights. The warm ramp costs sensor life, and it
does so on the unattended path. Severity here is not "how wrong" but "what does
a user lose by trusting it".

## Execution

**Phase 1 — the mechanical detectors (cheap, permanent, do first).**
Build B, E and the provenance half of A as standing tests. These three already
have working prototypes in the tree from this week
(`test_the_offer_never_promises_what_the_resolver_would_discard`, the shared
`TRAVERSAL` corpus, the `effective` block) — generalise them rather than invent.
Every capability, every guard pair, every displayed config value.

**Phase 2 — mutation sampling for class F.**
Take the assertions that guard the highest-cost behaviours (safety gates, path
containment, the provider resolvers, the cooling and parking paths), break each
deliberately, and confirm the suite goes red. Anything that stays green is not a
test. Sample rather than exhaust — full mutation testing on 3125 tests is not
worth the wall clock.

**Phase 3 — the LLM sweeps for C and D.**
Two passes, run wide and in parallel, each producing candidate claims with the
code that ought to enforce them:
- every docstring and comment containing an assertion of the form
  *always / never / must / guaranteed / cannot / is safe / matches*;
- every user-facing string in `ui/src` that describes what the system will do,
  as opposed to labelling what is on screen.
Then a verification pass per candidate that is adversarial by default: assume the
claim IS kept, and try to prove it is not. Findings become Phase 1-style tests.

**Phase 4 — runtime measurement for G.**
The rendered page against the compiled CSS at real viewports; encoded bytes
against their labels; device replies against their drivers' assumptions. This
one cannot be done from source and has caught three defects this week that no
amount of reading would have.

## What this plan will NOT catch, stated so nobody relies on it

- A claim nobody wrote down. If the product silently does the wrong thing and no
  comment, label or option ever asserted otherwise, none of these detectors fire.
  That is ordinary bug-hunting and needs the usual tools.
- A claim that is true today and false after the next change, unless Phase 1
  turned it into a standing test. This is the whole argument for preferring
  cheap permanent detectors over expensive one-shot sweeps.
- My own claims. Three times this week I reported something confidently and was
  wrong — a "clipped" button that was wrapping, a race that was a refusal, a
  compressible test pattern I called a picture. The audit needs to treat
  *findings* as claims too, and the sabotage check is what does that.

## First move

Phase 1's class-B property test, generalised across all four provider
capabilities. It is the cheapest, it is permanent, and it is the exact shape of
the defect that put a simulated polar aligner in front of a real telescope for
twelve days.
