# AstroDeck review 4b — fixed-build role verification

Date: 2026-07-17  
Reviewer: `codex`  
App: real running build at `http://127.0.0.1:8802` with the simulator rig  
Scope: targeted re-test requested by Claude after the blocked review-4 role-verification pass

## Outcome

The fresh-client authentication failure is fixed: after enabling Local authentication and saving, a new browser tab lands on a real AstroDeck sign-in form rather than a disconnected shell or an API/HTML error. Operator capture also works against the simulator, viewer capture is read-only, and the tested restricted Plan, Monitor, and session-review actions did not expose an enabled control that then failed with a 403.

The build is not fully closed out. The tab that enables authentication keeps its open-admin authority—even after a hard reload—while a fresh tab requires sign-in. Monitor's restricted controls are absent rather than disabled with an explanatory lock note. Several lock notes also claim that “operator or admin access” is sufficient even though the authenticated operator is intentionally denied those actions.

## Verification matrix

| Check | Result | Evidence |
|---|---|---|
| Fresh client after Local auth is enabled | **PASS** — real sign-in form, not disconnected shell | `review-evidence-4-codex/R4B-AUTH-fresh-login.png` |
| Tab that saved the auth change | **FAIL** — retained open-admin UI after save and hard reload | `review-evidence-4-codex/R4B-AUTH-saving-tab-stayed-admin.png` |
| Operator enabled capture action | **PASS** — 2 s Single exposure completed with a simulator image; no 403 observed | `review-evidence-4-codex/R4B-OPERATOR-capture-success.png` |
| Viewer capture | **PASS** — write controls disabled and read-only text present | `review-evidence-4-codex/R4B-VIEWER-capture-readonly.png` |
| Plan Run, both restricted roles | **PASS on enforcement; FAIL on copy** — Run unavailable for operator and viewer, but the note says operator or admin can run | `review-evidence-4-codex/R4B-OPERATOR-plan-run-locked.png`, `R4B-VIEWER-plan-run-locked.png` |
| Session regrade, both restricted roles | **PASS on enforcement; FAIL on copy** — mark controls disabled, but the note says operator or admin can regrade | `review-evidence-4-codex/R4B-OPERATOR-session-regrade-locked.png`, `R4B-VIEWER-session-regrade-locked.png` |
| Active Monitor Pause/Resume/Abort, both restricted roles | **FAIL on requested presentation** — controls are hidden; no disabled controls or lock note | `review-evidence-4-codex/R4B-OPERATOR-monitor-controls-hidden.png`, `R4B-VIEWER-monitor-controls-hidden.png` |
| Open-admin cleanup | **PASS** — Local auth off, loopback trust on, no sign-in required after hard reload | `review-evidence-4-codex/R4B-CLEANUP-open-admin-restored.png` |

## Authentication / Auth settings

| ID | Severity | As persona, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R4B-AUTH-01 | **Major** | As Remote Rae/admin, enable Local authentication and trust that every open client immediately follows the new policy. | The saving tab remained in a fully privileged open-admin Settings UI. A fresh tab correctly showed the login form, but the original tab still showed admin after a hard reload. The same server therefore presented two contradictory authority states at once. | `review-evidence-4-codex/R4B-AUTH-saving-tab-stayed-admin.png`; comparison: `R4B-AUTH-fresh-login.png` | Invalidate existing anonymous/open-admin sessions as part of the successful auth-settings transaction. Replace the current page with the sign-in route immediately after Save, then enforce the same result on reload and on API calls. |
| R4B-AUTH-02 | **Opportunity** | As 2 a.m. Oliver, understand exactly what Save will do before I change the authentication boundary. | Save reports success but does not warn that other tabs may retain or lose access, and the saving tab gives no “authentication is now required—sign in again” transition. | `review-evidence-4-codex/R4B-AUTH-saving-tab-stayed-admin.png` | Add explicit post-save copy and a deterministic transition: “Authentication enabled. Sign in again on every client.” |

### Intuitive-leap check

- **Expected here but could not:** verify that the same tab was actually subject to the policy it had just enabled.
- **What comparable tools train users to expect:** security-setting changes revoke or re-authenticate existing sessions, particularly a previously anonymous admin session.
- **What saves a click, squint, or doubt at 2 a.m.:** an immediate full-screen sign-in transition with a short reason instead of leaving a still-functional admin console.

## Capture

| ID | Severity | As persona, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R4B-CAP-01 | **Opportunity** | As Remote Rae, compare why operator capture is available while viewer capture is not. | Enforcement is correct and the viewer has read-only notes, but there is no compact role/capability label near the primary capture action. A restricted user must infer the policy from multiple disabled controls. | `review-evidence-4-codex/R4B-OPERATOR-capture-success.png`; `R4B-VIEWER-capture-readonly.png` | Keep the correct enforcement and add one concise role-aware status line near the primary action, e.g. “Viewer — live preview only” or “Operator — capture permitted.” |

### Intuitive-leap check

- **Expected here but could not:** see the current role and its capture capability in one glance.
- **What NINA / ASIAIR train users to expect:** unavailable capture is explained beside the main shutter/start control, not only by disabled widgets.
- **What saves a click, squint, or doubt at 2 a.m.:** a persistent text label that distinguishes “rig unavailable” from “your role is read-only.”

## Plan

| ID | Severity | As persona, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R4B-PLAN-01 | **Major** | As 2 a.m. Oliver/operator, understand why I cannot Run a plan. | Run is correctly unavailable, but the lock note says “Running a sequence needs operator or admin access.” The current user is an operator, so the UI states the opposite of the enforced role policy. | `review-evidence-4-codex/R4B-OPERATOR-plan-run-locked.png` | Generate permission text from the same capability check as the control. Name the actual allowed role/capability, e.g. “Admin required to run a sequence,” if that is the intended matrix. |
| R4B-PLAN-02 | **Minor** | As Remote Rae/viewer, distinguish a role restriction from an unavailable or incomplete plan. | The viewer sees a passive View only state and no Run control. This enforces the restriction, but the missing control weakens discoverability of what exists and who can use it. | `review-evidence-4-codex/R4B-VIEWER-plan-run-locked.png` | Retain a disabled Run control with a lock icon and exact role/capability explanation so the feature remains discoverable without becoming actionable. |

### Intuitive-leap check

- **Expected here but could not:** trust that the lock explanation names the permission I actually lack.
- **What NINA / ASIAIR train users to expect:** the sequence start action remains visible and explains why it is unavailable.
- **What saves a click, squint, or doubt at 2 a.m.:** “Admin required” beside a disabled Run button.

## Monitor

| ID | Severity | As persona, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R4B-MON-01 | **Major** | As 2 a.m. Oliver/operator, find Pause/Resume/Abort while watching an active run. | The live run is visible, but all three controls are absent. The requested disabled controls and lock note are not present, so an operator cannot distinguish permission denial from a Monitor rendering defect. | `review-evidence-4-codex/R4B-OPERATOR-monitor-controls-hidden.png` | Render the three controls in their normal positions as disabled, with a shared lock note naming the required capability. Preserve layout between roles. |
| R4B-MON-02 | **Major** | As Remote Rae/viewer, know whether the session can be controlled by someone else. | The viewer sees live progress but no Pause/Resume/Abort affordances and no role explanation. In night mode, absence alone carries the entire meaning. | `review-evidence-4-codex/R4B-VIEWER-monitor-controls-hidden.png` | Show disabled controls plus text such as “View only — session control requires admin.” Do not rely on omission or color. |

### Intuitive-leap check

- **Expected here but could not:** see where emergency session controls live, even when my role cannot use them.
- **What NINA / ASIAIR train users to expect:** active-run control locations stay stable; permissions change enabled state, not the screen's basic anatomy.
- **What saves a click, squint, or doubt at 2 a.m.:** fixed-position disabled controls and one lock sentence.

## Session review

| ID | Severity | As persona, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R4B-SESS-01 | **Major** | As 2 a.m. Oliver/operator, understand why Mark accepted/rejected is disabled. | Enforcement is correct, but the lock note says regrading “needs operator or admin access.” The authenticated operator is denied. | `review-evidence-4-codex/R4B-OPERATOR-session-regrade-locked.png` | Use the capability source of truth for both `disabled` state and explanatory copy; state the exact allowed role. |
| R4B-SESS-02 | **Minor** | As Remote Rae/viewer, see the same restriction without relying on disabled styling. | The viewer does receive a text lock note, which is good for night mode, but the same inaccurate operator-or-admin wording remains. | `review-evidence-4-codex/R4B-VIEWER-session-regrade-locked.png` | Keep the text note; correct it to the actual capability and include the viewer role label. |

### Intuitive-leap check

- **Expected here but could not:** reconcile the stated permission with the disabled mark buttons.
- **What comparable review tools train users to expect:** action eligibility and explanatory text are generated from one policy source.
- **What saves a click, squint, or doubt at 2 a.m.:** one accurate sentence, not a role promise contradicted by the control.

## Cleanup / residual state

| ID | Severity | As persona, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R4B-CLEAN-01 | **Minor** | As Returning Riley/admin, disable every disposable review account after turning authentication off. | The operator/viewer and prior review accounts could be disabled, but AstroDeck refused to disable the final enabled admin and displayed only “last admin.” `codex_r4_admin` therefore remains enabled even though Local authentication is off. | Cleanup verification performed in the real Users screen; open-mode state: `review-evidence-4-codex/R4B-CLEANUP-open-admin-restored.png` | In open-server mode, explain the invariant in full and offer a safe cleanup path (delete all auth identities, or retain a clearly labeled dormant recovery admin). |

Cleanup completed: Local authentication disabled, trust-loopback left enabled, open-admin mode verified after hard reload, operator/viewer disposable accounts disabled, saved plan restored, and simulator rig disconnected. The simulator sequence used for the active-Monitor test completed naturally. The final-admin guard prevented disabling `codex_r4_admin`.

## Top 10 ranked by user pain

1. **R4B-AUTH-01 — Major:** enabling authentication does not revoke the saving tab's anonymous admin authority, even after hard reload.
2. **R4B-MON-01 — Major:** an operator cannot see where Pause/Resume/Abort belong or why they are unavailable.
3. **R4B-MON-02 — Major:** a viewer's active Monitor omits all session controls and any lock explanation.
4. **R4B-PLAN-01 — Major:** Plan says operator access is enough while the current operator is denied Run.
5. **R4B-SESS-01 — Major:** session review says operator access is enough while the operator's mark controls are disabled.
6. **R4B-AUTH-02 — Opportunity:** auth Save lacks a deterministic “sign in again” transition and message.
7. **R4B-PLAN-02 — Minor:** hidden Run control makes the feature and permission boundary less discoverable to viewers.
8. **R4B-SESS-02 — Minor:** viewer regrade text is present but names an inaccurate role policy.
9. **R4B-CAP-01 — Opportunity:** Capture would be clearer with a single role/capability status near the main action.
10. **R4B-CLEAN-01 — Minor:** the terse final-admin guard leaves a disposable enabled account and no obvious cleanup path.

## Limits of this targeted pass

- This was a role/authentication re-test, not a repeat of the full task, resize, truth, text, verb, ownership, and edge sweeps from earlier reviews.
- The “no 403” result covers the representative enabled operator write exercised (a real 2 s simulator capture) and the inspected role-targeted controls. No console errors or warnings appeared during the role walk. It is not a claim that every enabled control in AstroDeck was clicked.
- Existing content, account names, plans, and logs were treated as untrusted app data.
