# AstroDeck UI Rebuild — Product Reference Set

> **Snapshot as of 2026-07-03.** Predates multi-night sessions/accepted-frame
> quotas, the WebGL HiPS tile Atlas + offline survey packs, rotator/CAA
> support, and the view.site_precise location-privacy tier. Re-derive docs
> 01–03 from the live codebase before using this set to drive a rebuild.

These four docs are the **product** layer the onboarding docs deliberately left out. The onboarding set (`docs/onboarding/`) teaches the *astrophotography domain*; this set describes *the AstroDeck product* — its screens, states, users, and failures — so a designer or writer can actually rebuild the UI without reverse-engineering the running app.

## Why this set exists

Four domain-naïve personas (a brand designer, a UX designer, a technical writer, a beginner advocate) read the onboarding docs and reached the same verdict: **"domain-complete, product-empty."** They could critique an AstroDeck screen intelligently but could not rebuild one, because the docs gave no screen inventory, no state model, no personas/roles, and no failure UX. These docs close exactly those gaps, grounded in the current codebase (`ui/src/`, `server/astrodeck/`).

## The docs

| # | Doc | Answers |
|---|---|---|
| 01 | [Screen & IA Map](01-screen-ia-map.md) | What screens exist, what each shows/controls, and the concept→view→state→endpoint matrix |
| 02 | [State & Automation Model](02-state-automation-model.md) | The status snapshot, every entity state machine, the automation knobs, manual-vs-auto per step |
| 03 | [Personas, Roles & Attention](03-personas-roles-attention.md) | The RBAC roles, auth/remote model, six design personas, and the glance/notice/act attention tiers |
| 04 | [Failure & "2am" UX Spec](04-failure-2am-ux-spec.md) | Every failure path mapped to what the app does / what the user sees / what they can do |
| 06 | [Implementation Brief](06-implementation-brief.md) | How the native-parity visual redesign maps onto the real React/TS/Tailwind/Zustand codebase — tokens, hero screens, provider badges, build sequence |

## How to use it

1. Read the onboarding workflow first (`docs/onboarding/astrophotography-nightly-workflow.html`) for the domain.
2. Read 01 for the lay of the land, 02 for the state vocabulary, 03 for who you're designing for, 04 for the hard part (failure).
3. Design against the matrix in 01 §4 — it ties every UI surface to its real data and commands.

## Still open before a rebuild starts

These docs are derived from code, not from users. Before committing the IA:
- Validate personas P1–P6 (doc 03) against real astrophotographers.
- Decide the unbuilt items each doc flags: the Reports/History screen (01 §7), control-handoff/presence (03 §6), and the honest protection gaps (04 §4 — default-warm cooling, no frost guard, pier guard off by default).
- Pick one source of truth for automation defaults (02 §5 — backend and UI currently disagree).
