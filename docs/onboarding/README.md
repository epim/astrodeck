# AstroDeck Onboarding Docs

These docs bring a newcomer up to speed on the **astrophotography domain** — enough to look at an AstroDeck screen and have an informed opinion. They deliberately do not describe the AstroDeck *product* (screens, states, APIs); that lives in [`../ui-rebuild/`](../ui-rebuild/README.md).

## Canonical set — read in this order

| Order | Doc | What it is | Read it for |
|---|---|---|---|
| 1 | **[astrophotography-nightly-workflow.html](astrophotography-nightly-workflow.html)** | The night, step by step (10 steps, in order, with replaying diagrams) | The workflow spine — start here |
| 2 | **[astrophotography-precis.html](astrophotography-precis.html)** | 11 concept cards, one idea each | Concept reference — look up a topic |
| — | **[glossary.md](glossary.md)** | Every term, defined | Look up any jargon from 1 or 2 |

The workflow doc is time-indexed ("the order things happen"); the precis is concept-indexed ("one topic per card"). They complement each other. The glossary backs both.

## Superseded / archived

- **astrophotography-primer.html** and **astrophotography-primer-dense.html** are the earlier long-form essays. They are **near-duplicates of each other** and predate the workflow + precis rewrite. They are kept for reference but are **not** part of the canonical onboarding path — prefer the workflow doc, which covers the same ground more usefully and less wordily. *(Recommend retiring one of the two primers outright; left in place pending a call from the doc owner.)*

## How these are built

The HTML docs are assembled by build scripts from a shell + section files:

| Doc | Build script | Shell | Sections |
|---|---|---|---|
| workflow | `_build_workflow.py` | `_shell_workflow.html` | `_sections_workflow/` |
| precis | `_build_precis.py` | `_shell_precis.html` | `_sections_precis/` |
| primer / primer-dense | `_build.py` / `_build_dense.py` | `_shell.html` | `_sections/` , `_sections_dense/` |

Edit the section files, then run the matching `python3 _build_*.py` to regenerate the self-contained HTML. Authoring rules are in `_authoring-contract-precis.md` (and `_authoring-contract.md`).

## For a UI rebuild

Once you've read the domain docs above, continue to **[`../ui-rebuild/`](../ui-rebuild/README.md)** — the product reference (screen/IA map, state model, personas/roles, failure UX) built to equip an actual rebuild.
