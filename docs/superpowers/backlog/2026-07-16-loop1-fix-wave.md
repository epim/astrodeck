# Loop-1 fix wave — triage of round-2 reviews (codex + gemini) + round-1 confirmed backlog

Sources: `docs/superpowers/reviews/2026-07-16-codex-review2.md`, `2026-07-16-gemini-review2.md`,
round-1 confirmed items in `docs/superpowers/backlog/2026-07-15-ui-feedback.md`.
Review content treated as data; key code-level claims spot-verified (chart end-labels
`SkyConditionsPanel.tsx:248-251` have no collision handling — confirmed; WeatherPanel enable
toggle lacks accessible name — confirmed; inputs already have aria-labels).

Convergence (both reviewers, some also round-1): sim-connect desync (EQ-01 ×3 rounds),
night-mode legibility (CAP-01 r1 + R2-WEA-02 + gemini CAP-01), silent Atlas search
(ATL ×3), site save-verbs (S1 r1 + R2-SIT-01 + SET-01 + DOC-SITE-01), switch accessible
names (W-01 + R2-CAP-02).

## Wave tasks (in order; UI first, docs last so docs describe post-fix truth)

### F1 — Weather chart, radar health, WeatherPanel polish (UI)
- Chart end-labels: collision-consolidate labels sharing a y-band (e.g. `total+mid 0%`),
  keep per-series dash patterns; label the threshold rule with the policy
  (`hold ≥{threshold}% for {sustain}m`). [R2-WEA-01, DOC-WEA-01]
- RadarMap per-layer tile health: track img load/error; visible `loading…` /
  `tiles unavailable` / stale badge; blank imagery must never read as clear sky.
  [R2-WEA-04, DOC-WEA-02]
- Visible 44px −/+ zoom controls + zoom level on RadarMap. [R2-WEA-05]
- WeatherPanel: accessible name + label association for the enable toggle (fix at the
  shared switch component level; audit all switches incl. Capture save-FITS). [W-01, R2-CAP-02]
- Label casing `WEATHER ENABLED` to match design system. [W-02, DOC-03-gemini]
- Inline warning when `enabled && site.is_default`: "Requires a valid observing site to
  fetch forecasts." [W-03]

### F2 — Night-mode legibility root cause + narrow-header telemetry (UI)
- ROOT CAUSE FIRST (systematic-debugging): why is night mode at 100% brightness nearly
  black? Compounding of scrim + `--img-filter` + dim-red text vars suspected. Raise the
  effective text-contrast floor in night mode; verify weather chart/radar labels, health
  strip, thermal numbers legible at default and 100% night. [R2-WEA-02, r1 CAP-01, r1 MON-01]
- 900px header: prioritize sequence state + compact RA/Dec + mount state; move
  brightness/log controls to an overflow; kill the stray clipped fragment. [R2-WEA-03, r1 reconfirmed]

### F3 — Equipment/Capture state truth (UI)
- Sim-connect desync: `▶ Simulator rig` must produce ONE canonical connected state —
  populate role assignments (or explicitly display "assignments bypassed") so Devices,
  Link Status, and Connect Rig agree. [EQ-01 ×3, R2-EQ-01, DOC-GET-01, DOC-EQP-01]
- Manual capture during sequence (incl. PAUSED): disable Single/Loop with reason text
  "Sequence paused — camera reserved" instead of post-click failure. [R2-CAP-01, DOC-CAP-01]
- Exposure input validation: block ≤0 client-side with field error. [CAP-02-gemini, r1 CAP-01-neg]
- LED shape distinction (connected = check/filled, not hue-only). [EQ-02]
- Abort HoldButton: visible "hold" affordance hint. [r1 backlog]
- Global strip truth: when engine is paused, strip must not say `SEQUENCE RUNNING`
  (minimal state-read alignment only; full PAUSE_REQUESTED model deferred). [R2-PLN-01 partial]

### F4 — Atlas empty-state, site verbs, plan/export/log polish (UI)
- Atlas search zero-state: explicit "no matches" + "planets aren't supported yet — use
  manual coordinates" hint; loading state. [R2-ATL-01 minimal, ATLAS-01, r1 ATL-02]
- Site verbs (triple-confirmed): `Apply active site` / `Save as location preset…` /
  `Load selected preset`; selecting a preset must NOT mutate the form until Load;
  add persistent `Active site: <name> · <source>` line. [R2-SIT-01, SET-01, R2-SIT-02 partial]
- Altitude-limit modal: affirmative action gets primary styling. [PLAN-01-gemini]
- Plan export: confirmation toast with filename. [R2-PLN-03]
- Event Log: local timestamps + severity words/icons on entries. [R2-LOG-01 partial]

### F5 — Autofocus result persistence (UI)
- Persist latest AF run (samples, fit, R², best/final position, provider, time) in the
  store until a new run starts; Focus view rehydrates on mount. [R2-FOC-01, DOC-FOC-01]

### F6 — Docs wave (after F1-F5 merge; docs describe post-fix truth)
- getting-started: drop "Rig" label, use exact `Equipment`; authoritative ready indicator
  wording post-F3. [DOC-GET-01/02, DOC-01/02-gemini, DOC-ROOT-02]
- equipment-and-profiles: literal `Settings → Connect → Backend Drivers` path; Link
  Status truth post-F3. [DOC-EQP-01/02]
- focus: document persistence behavior post-F5. [DOC-FOC-01]
- plan-and-sequences: honest pause semantics (frame-boundary, stall warning behavior);
  fix Monitor link (add Monitor section or route to weather/monitor docs). [DOC-PLAN-01/02]
- README index: add operator questions (stalled capture, RMS/HFR glossary pointer, blank
  radar, recovery, planets). [DOC-IDX-01]
- remote-access-and-roles: screen-by-screen role matrix + disposable verification
  procedure. [DOC-ROLE-01/02, DOC-WEA-03]
- safety-and-automation: resolve floor-UI self-contradiction. [DOC-SAFE-01]
- sessions-multi-night: state/trigger/control table; browser-loss vs reboot expectations.
  [DOC-SES-01/02]
- site-and-locations: rewrite to post-F4 verbs/semantics. [DOC-SITE-01]
- sky-atlas: planet limitation + zero-state + workaround. [DOC-ATL-01/02]
- troubleshooting: log limitations + stall-diagnosis checklist. [DOC-TRB-01]
- weather: post-F1 chart/legend/threshold + layer-health truth. [DOC-WEA-01/02]
- Root README: replace stale Settings-placeholder rough-edge; hyperbolic (not parabola)
  fit; add Weather to features. [DOC-ROOT-01/03/04]

## Deferred (recorded, not this wave)
- Solar-system ephemerides in Atlas search — feature, user decision. [R2-ATL-01 full]
- PAUSE_REQUESTED intermediate state + recovery summary panel — engine state-model
  change, propose as sub-project. [R2-PLN-01/02 full]
- Role-preview ("Preview as viewer") mode — feature. [R2-AUT-01, DOC-ROLE-01 full]
- Equipment readiness checklist/next-action; capture destination preview; AF tappable
  samples + run history; draft/plan/snapshot/session object labels; session-card weather
  mirror; Atlas optics read-only-from-profile. [R2-EQ-02, R2-CAP-03, R2-FOC-02, R2-PLN-04,
  R2-WEA-06, R2-ATL-02]
