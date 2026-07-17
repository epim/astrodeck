# Decisions wave — product-owner rulings 2026-07-17 (post-program)

Owner decisions from the close-out review (verbatim intent recorded):
1. OPERATOR RUN: "grant operators the ability to run, that's the intended use for that
   role. At some point this will be the interface to allow telescope rentals."
2. WEATHER: full weather for operators (radar map included — owner accepts that tile
   coordinates disclose site region to operators; consistent with the rental model.
   Viewer redaction unchanged — B's strip privacy stays for viewers).
3. SESSIONS: add an Abandon (soft-retire) action to dormant/complete cards.
4. AUTH: add a guided "Secure this server" setup card.
5. OIDC UI form: DEFERRED to backlog (config/API-only stands, docs cover it).

## Tasks

### I1 — Operator sequence-run grant (server + docs)
- Add `control.mount` to OPERATOR_CAPS in server/astrodeck/auth/capabilities.py (this is
  the capability gating Plan Run, Monitor Pause/Resume/Abort, session regrade, mount
  slewing). Update the TS mirror ROLE_CAPS in ui/src/lib/caps.ts IN THE SAME COMMIT
  (hand-maintained mirror — comment already points at source). Lock notes derive from
  the mirror (accessPhrase/rolesHolding) so copy self-updates — verify by test.
- Update RBAC tests expecting operator 403s on those routes (they now expect 200-family;
  find every test asserting operator-denied on control.mount routes).
- Docs: role matrix + per-guide role lines (remote-access-and-roles.md, monitor.md,
  plan-and-sequences.md, sessions-multi-night.md) — operator now runs/controls/regrades.
- LEDGER NOTE (not built): future rental tier may want control.sequence split from raw
  control.mount (slew-anywhere); revisit when rentals get designed.

### I2 — Weather visible to operators (server + UI + docs)
- Server: weather visibility moves from view.site_precise to a gate operators hold.
  Prefer a dedicated CAP_VIEW_WEATHER granted to operator+admin (NOT viewer), applied to:
  GET /api/weather, the WS "weather" event drop rule (both lanes — api/redact.py + the
  relay lane), and the IEM tile proxy routes. view.site_precise stays admin-only for
  site coordinates everywhere else — ONLY weather moves.
- UI: no gating changes should be needed for the panels (they render when data flows),
  but VERIFY: Sky Conditions + RadarMap on Monitor render for an operator; the
  Sessions-card ignore-tonight toggle now renders for operators (control.capture +
  weather alert present) — the previously-API-only override gets its UI path. Health
  strip weather tier too.
- Docs: weather.md (admin-only claims → operator+), remote-access-and-roles.md matrix,
  the known-quirk paragraph (now resolved — remove/replace).
- Tests: RBAC additions (operator 200 on /api/weather + tiles; viewer still denied;
  WS weather event reaches operator, dropped for viewer; downgrade re-tighten still
  works for viewer).

### I3 — Abandon action on session cards (UI + docs)
- SessionsPanel: an Abandon action on dormant/complete cards (issues the existing
  PATCH /api/sessions/{id} status="abandoned"; server route exists app.py:2165-2170,
  CAP_CONTROL_MOUNT — post-I1 operators can use it too). Confirm dialog (plain confirm,
  not hold — soft action, reversible via API), copy explaining soft-retire vs Delete.
- Docs: sessions-multi-night.md abandoned row (no longer UI-unreachable) + Delete
  cross-reference.

### I4 — Guided "Secure this server" card (UI)
- Settings→Auth: when auth is open (no methods) show a compact guided card: step 1
  create an admin account (inline or link to Users), step 2 enable Local sign-in,
  step 3 explains the sign-out/sign-in transition (composes with H2's toast + H1's
  Login routing). Correct order enforced (step 2 disabled until an enabled admin
  exists). Dismissible; reappears only while auth is open.
- Follow the AuthMethodPanel idioms; update remote-access-and-roles.md procedure to
  mention the card as the primary path (CLI/manual stays documented).

Order: I1 → I2 → I3 → I4 (I3 benefits from I1's grant; docs touch overlapping files
sequentially). Standard sdd: fresh implementer per task, two-verdict review, final CI.
