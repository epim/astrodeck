# AstroDeck UI/UX Field Review

After completing a full viewport, text, verb, and truth sweep across the application from the perspectives of our core users, here is the final, interactively-verified field review report.

## 1. Equipment Screen

| ID | Severity | As `<persona>`, I tried to… | What happened | Evidence | Recommendation |
|---|---|---|---|---|---|
| **EQ-01** | **Blocker** | As **First-light Fran**, I tried to connect the simulator rig. | I clicked `▶ Simulator rig`. The backend connected (LEDs turned green), but the UI dropdowns remained `— unassigned —`. Because the dropdowns were empty, the `Connect Rig` button stayed disabled. I was stuck until I manually selected "Simulator" in every dropdown. | [EQ-01](file:///c:/Users/bear/astro/review-evidence-gemini/EQ-01-simulator-status-conflict.png) | Clicking the simulator rig button must automatically assign the simulator options in the UI dropdowns and enable the main Connect button. |
| **EQ-02** | Polish | As **2 a.m. Oliver**, I glanced to see if my camera was connected. | The connection status relies entirely on small circular LEDs (green vs grey). Under Night Mode, color alone cannot convey state reliably. | [EQ-1280](file:///c:/Users/bear/astro/review-evidence-gemini/EQ-1280-unassigned.png) | Add a shape distinction (e.g., a checkmark when connected, an X or empty circle when disconnected) alongside the color. |
| **EQ-03** | Opportunity | As **First-light Fran**, I looked for the Settings page to configure my setup. | The main navigation only has "Equipment", but no "Settings" tab. The nomenclature is slightly disjointed. | `page.html` | Consider renaming the tab to "Equipment / Settings" or adding a dedicated gear icon. |

## 2. Capture Screen

| ID | Severity | As `<persona>`, I tried to… | What happened | Evidence | Recommendation |
|---|---|---|---|---|---|
| **CAP-01** | **Major** | As **2 a.m. Oliver**, I turned on Night Mode to save my dark adaptation. | The red-shifted text (e.g., "Focus: FAIR...") became incredibly dark and muddy against the dark red background, rendering it completely illegible at a glance. | [CAP-02](file:///c:/Users/bear/astro/review-evidence-gemini/CAP-02-night-mode-first-image.png) | Increase the contrast ratio for text in Night Mode. The text needs to be a much brighter red/orange to stand out against the dark red background. |
| **CAP-02** | **Major** | As **First-light Fran**, I entered an exposure time and clicked SINGLE. | I accidentally entered `-5` for the exposure. *The UI silently accepted it, bypassing any validation.* It even updated the metadata to `-5s`, generated a blank frame, and threw a generic "Few stars — check focus" error, misleadingly treating an impossible exposure like a real frame. | [CAP-03](file:///c:/Users/bear/astro/review-evidence-gemini/CAP-03-invalid-exposure-no-feedback.png) | Add immediate field validation. Negative exposure values should turn the input border red and disable the SINGLE/LOOP buttons immediately, rather than failing silently. |

## 3. Plan Screen

| ID | Severity | As `<persona>`, I tried to… | What happened | Evidence | Recommendation |
|---|---|---|---|---|---|
| **PLAN-01** | **Major** | As **First-light Fran**, I tried to add a target that was below the altitude limit. | A warning modal popped up asking if I wanted to "Add anyway?". However, the `CANCEL` button was styled as the primary action (bright cyan), while `ADD ANYWAY` was styled as a muted secondary button. | [PLAN-01](file:///c:/Users/bear/astro/review-evidence-gemini/PLAN-01-target-add-failed.png) | Swap the button styling. The affirmative action to proceed should be the primary highlight, or at least visually distinct as the "continue" path. |
| **PLAN-02** | Minor | As **Remote Rae**, I viewed a plan with a very long name on my 900px tablet. | The plan name "UX Review M42 Multi-night 2026-..." overflowed its container, and the blue input border clipped right through the edge of the sidebar. | [PLAN-03](file:///c:/Users/bear/astro/review-evidence-gemini/PLAN-03-long-name-900.png) | Add `text-overflow: ellipsis` and ensure the input field has a `max-width: 100%` constraint. |

## 4. Atlas Screen

| ID | Severity | As `<persona>`, I tried to… | What happened | Evidence | Recommendation |
|---|---|---|---|---|---|
| **ATLAS-01** | Polish | As **First-light Fran**, I typed "Jupiter" into the target search box. | I hit Enter. *Nothing happened visually.* No spinner, no inline error, and no "no results" text. The field simply remained "Jupiter". In contrast, searching for "M31" eventually renders a result, making the silent failure of "Jupiter" very confusing. | [ATLAS-01](file:///c:/Users/bear/astro/review-evidence-gemini/ATLAS-01-jupiter-no-result.png) | Add a magnifying glass search button, a loading spinner during the query, and explicit empty-state text if no target is found. |

## 5. Settings / Location Screen

| ID | Severity | As `<persona>`, I tried to… | What happened | Evidence | Recommendation |
|---|---|---|---|---|---|
| **SET-01** | Minor | As **Returning Riley**, I tried to save my backyard location. | I was confronted with two distinct verbs right next to each other: `SAVE SITE` vs `SAVE CURRENT...`. Interactively testing them revealed that `SAVE SITE` persists the *active* session's site (showing a "Site saved" toast), while `SAVE CURRENT...` actually opens an inline secondary flow to save the values as a *reusable preset* in the library. | [SET-01](file:///c:/Users/bear/astro/review-evidence-gemini/SET-01-site-save-verbs.png) | Rename `SAVE SITE` to `APPLY TO SESSION` and `SAVE CURRENT...` to `SAVE TO LIBRARY` to clarify the data flow and avoid verb ambiguity. |

---

## Top 10 Ranked by User Pain

1. **Equipment Connect Desync (EQ-01)**: `▶ Simulator rig` doesn't assign devices in the UI, leaving the main `Connect Rig` button permanently disabled until the user manually guesses to change the dropdowns.
2. **Night Mode Illegibility (CAP-01)**: Night mode text contrast is so dark that 2 a.m. Oliver cannot read critical metrics like focus HFR.
3. **Missing Field Validation (CAP-02)**: Silent acceptance of negative exposure times (`-5s`) wastes time, generates impossible frames, and misleads users with generic "few stars" errors instead of catching the input immediately.
4. **Modal Button Reversal (PLAN-01)**: The "CANCEL" button is styled as the primary action, tricking users into aborting their intent.
5. **Dead Search Feedback (ATLAS-01)**: The Atlas search box provides zero feedback (no spinner, no button, no empty state) when querying non-existent targets like "Jupiter".
6. **Ambiguous Save Verbs (SET-01)**: Conflicting `SAVE SITE` vs `SAVE CURRENT...` buttons leave users unsure where their location data is actually going, requiring them to click to discover the secondary "preset" flow.
7. **Responsive Input Overflow (PLAN-02)**: Long plan names break container layouts and bleed off the screen on 900px widths.
8. **Color-Dependent Status (EQ-02)**: Equipment LEDs rely entirely on color (green vs grey), which fails accessibility and Night Mode checks.
9. **Missing Settings Nomenclature (EQ-03)**: Users looking for global "Settings" might miss that it's embedded within the "Equipment" flow.
10. **Silent Background Failures**: The overall trend of buttons (like Simulator Rig) executing background actions without updating the frontend state creates a massive trust deficit for users.
