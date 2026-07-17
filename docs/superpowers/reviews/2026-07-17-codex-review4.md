# AstroDeck Loop 3 role-verification pass — Codex

Reviewed against the real app at `http://127.0.0.1:8802` after a hard reload on 2026-07-17. Scope was limited to the requested disposable-account role-verification pass. No product code was changed.

## Verdict

**BLOCKED — role matrix not graded.** The documented setup path made the review backend unavailable before an operator or viewer could sign in. No claim is made about the final role gates or whether enabled controls can 403.

## Authentication / role setup

| ID | Severity | As persona, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R4-AUTH-01 | **Blocker** | As Remote Rae, enable local accounts and sign in as a disposable operator/viewer using the rewritten guide. | In open-admin mode I created disposable admin/operator/viewer accounts, enabled **Local username & password**, and pressed **Save methods**. The UI briefly showed ON/Saved, then both tabs changed to **DISPLAY DISCONNECTED / NO LINK**. The original account panel still described an open anonymous admin session, while the Auth panel re-rendered local accounts OFF with **Save (open the server)**. | `review-evidence-4-codex/R4-AUTH-local-enabled-session-stale.png`; `review-evidence-4-codex/R4-AUTH-local-enabled-saved.png` | Make the auth-provider transition atomic and durable. Do not show Saved until the provider is reachable and the stored state can be read back. Preserve an authenticated admin recovery session or present an explicit restart/reconnect step. |
| R4-AUTH-02 | **Blocker** | As Remote Rae, open a fresh client and use the promised local sign-in flow. | Fresh `/` and `/login` visits render a disconnected **VIEW ONLY** application shell, not a sign-in form. Settings → Account simultaneously says **Local network — no sign-in required** and **Sign-in is disabled**, while telling the viewer to sign in. Other panels surface `Unexpected token '<', "<!doctype "... is not valid JSON` because API requests receive the HTML shell. | `review-evidence-4-codex/R4-AUTH-signin-disabled-viewer-shell.png` | Route unauthenticated clients to a dedicated sign-in state before rendering the operational shell. Never translate an authentication/bootstrap failure into a viewer role. Validate JSON content type before parsing and provide a recovery action. |

## Procedure followed

1. Hard-reloaded the rebuilt UI on `:8802`.
2. In open-admin mode, created `codex_r4_admin`, `codex_r4_operator`, and `codex_r4_viewer` disposable accounts.
3. Opened Settings → Auth, enabled **Local username & password**, and pressed **Save methods**.
4. Observed the saved state, then the backend disconnect and contradictory Auth/Account states.
5. Opened fresh `/` and `/login` clients and verified that neither exposed a sign-in form.
6. Stopped without testing roles or restarting the server, per the review rule to stop when the real app is unreachable.

## Cleanup status

The review could not restore open authentication or delete the disposable accounts because the backend became unavailable. The configured state is therefore **indeterminate**. Claude was notified on the `astrodeck` topic with the evidence paths and asked to restart/advise before this pass continues.

## Required retest after recovery

- Viewer: no enabled mutating controls.
- Operator: every enabled capture/guide action succeeds without 403.
- Plan Run and Monitor Pause/Resume/Abort: hidden or disabled for both viewer and operator without `control.mount`.
- Session regrade: disabled with a lock note for both viewer and operator.
- Operator/viewer differences match the documented screen matrix.
- Final cleanup: disable all sign-in methods, keep loopback trust enabled, remove/disable all `codex_r4_*` accounts, hard-reload, and verify open-admin mode is restored.
