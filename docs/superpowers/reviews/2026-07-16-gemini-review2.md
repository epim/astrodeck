# AstroDeck UI/UX & Documentation Field Review (Round 2)

After completing a full viewport, text, verb, and truth sweep across the application, including the new Weather features, and performing a literal walkthrough of the documentation, here is the final field review report. 

*Note: All Round 1 findings remain open and are included here as requested, alongside new findings from the Weather addendum and Documentation review.*

---

## Part 1: UI/UX Field Review (Including Weather Addendum)

### 1. Settings & Weather Screen (NEW)

| ID | Severity | As `<persona>`, I tried to… | What happened | Evidence | Recommendation |
|---|---|---|---|---|---|
| **W-01** | **Major** | As **First-light Fran**, I tried to turn on the weather toggle using a screen reader / automation. | The `weather enabled` toggle lacks a standard `<label for="...">` mapping or `aria-labelledby` attribute. This made it extremely difficult to target programmatically, failing accessibility checks. | [W-03](file:///c:/Users/bear/astro/review-evidence-2-gemini-v2/W-03-settings-weather-panel.png) | Use a native `<input type="checkbox">` visually hidden with a properly linked `<label>` or add explicit ARIA tags to the custom switch component. |
| **W-02** | **Minor** | As **First-light Fran**, I looked at the weather settings panel. | The `weather enabled` text is entirely lowercase, which looks inconsistent next to fully capitalized labels like `CLOUD THRESHOLD (%)` and `ASTROSPHERIC API KEY`. | [W-03](file:///c:/Users/bear/astro/review-evidence-2-gemini-v2/W-03-settings-weather-panel.png) | Capitalize to `WEATHER ENABLED` to match the design system's uppercase label style. |
| **W-03** | **Opportunity** | As **2 a.m. Oliver**, I enabled weather but forgot to set my site. | The weather feature silently fails to fetch anything if the default (0, 0) site is used. While documented, the UI provides no warning. | [W-03](file:///c:/Users/bear/astro/review-evidence-2-gemini-v2/W-03-settings-weather-panel.png) | If weather is enabled but the site is (0, 0), display an inline warning: "Requires a valid observing site to fetch forecasts." |

### 2. Equipment Screen (Round 1)

| ID | Severity | As `<persona>`, I tried to… | What happened | Evidence | Recommendation |
|---|---|---|---|---|---|
| **EQ-01** | **Blocker** | As **First-light Fran**, I tried to connect the simulator rig. | I clicked `▶ Simulator rig`. The backend connected (LEDs turned green), but the UI dropdowns remained `— unassigned —`. The `Connect Rig` button stayed disabled until I manually changed the dropdowns. | `review-evidence-gemini/EQ-01-simulator-status-conflict.png` | Clicking the simulator rig button must automatically assign the simulator options in the UI dropdowns and enable the main Connect button. |
| **EQ-02** | Polish | As **2 a.m. Oliver**, I glanced to see if my camera was connected. | The connection status relies entirely on small circular LEDs (green vs grey). Under Night Mode, color alone cannot convey state reliably. | `review-evidence-gemini/EQ-1280-unassigned.png` | Add a shape distinction (e.g., a checkmark when connected) alongside the color. |

### 3. Capture Screen (Round 1)

| ID | Severity | As `<persona>`, I tried to… | What happened | Evidence | Recommendation |
|---|---|---|---|---|---|
| **CAP-01** | **Major** | As **2 a.m. Oliver**, I turned on Night Mode. | The red-shifted text became incredibly dark and muddy against the dark red background, rendering it completely illegible at a glance. | `review-evidence-gemini/CAP-02-night-mode-first-image.png` | Increase the contrast ratio for text in Night Mode. |
| **CAP-02** | **Major** | As **First-light Fran**, I entered an exposure time and clicked SINGLE. | I accidentally entered `-5`. The UI silently accepted it, generated a blank frame, and threw a generic "Few stars" error instead of catching the invalid input. | `review-evidence-gemini/CAP-03-invalid-exposure-no-feedback.png` | Add immediate field validation to block negative exposures. |

### 4. Plan & Atlas Screens (Round 1)

| ID | Severity | As `<persona>`, I tried to… | What happened | Evidence | Recommendation |
|---|---|---|---|---|---|
| **PLAN-01** | **Major** | As **First-light Fran**, I tried to add a target below the altitude limit. | A warning modal popped up, but the `CANCEL` button was styled as the primary action, while `ADD ANYWAY` was muted. | `review-evidence-gemini/PLAN-01-target-add-failed.png` | Swap the button styling. The affirmative action should be primary. |
| **ATLAS-01** | Polish | As **First-light Fran**, I typed "Jupiter" into the search box. | I hit Enter and nothing happened visually (no spinner, no error). The silent failure is confusing. | `review-evidence-gemini/ATLAS-01-jupiter-no-result.png` | Add a loading spinner and explicit "no results" state. |
| **SET-01** | Minor | As **Returning Riley**, I tried to save my backyard location. | I was confronted with two distinct verbs: `SAVE SITE` vs `SAVE CURRENT...`. The ambiguity left me unsure where my data was going. | `review-evidence-gemini/SET-01-site-save-verbs.png` | Rename `SAVE SITE` to `APPLY TO SESSION` and `SAVE CURRENT...` to `SAVE TO LIBRARY`. |

---

## Part 2: Documentation Field Review

| ID | Severity | Persona | Doc says (quoted line + file:line) | App shows / gap | Evidence | Recommendation |
|---|---|---|---|---|---|---|
| **DOC-01** | **Major** | **First-light Fran** | `Go to the Equipment view (the Equipment tab / left-rail "Rig").` (`getting-started.md`, Line 67) | The left-rail tab is explicitly named "Equipment", not "Rig". Telling a new user to look for a "Rig" tab causes immediate confusion. | [Settings UI](file:///c:/Users/bear/astro/review-evidence-2-gemini-v2/W-03-settings-weather-panel.png) | Change documentation to `(the Equipment tab on the left rail)`. |
| **DOC-02** | **Minor** | **First-light Fran** | `Go to Settings → Connect → Observing Site...` (`getting-started.md`, Line 80) | There is no distinct "Observing Site" sub-page; it is simply a section header on the "Connect" page. | [Connect UI](file:///c:/Users/bear/astro/review-evidence-2-gemini-v2/W-03-settings-weather-panel.png) | Update to `Go to Settings → Connect and look under the Observing Site section`. |
| **DOC-03** | **Polish** | **2 a.m. Oliver** | `weather enabled — the master toggle.` (`weather.md`, Line 24) | The UI uses the literal lowercase "weather enabled" label, violating the capitalization design patterns used for every other input field. | [Weather Panel](file:///c:/Users/bear/astro/review-evidence-2-gemini-v2/W-03-settings-weather-panel.png) | Update the UI label to `WEATHER ENABLED` and update the documentation to match. |

---

## Top 10 Ranked by User Pain

1. **Equipment Connect Desync (EQ-01)**: `▶ Simulator rig` doesn't assign devices in the UI, leaving the main `Connect Rig` button permanently disabled until the user manually guesses to change the dropdowns.
2. **Night Mode Illegibility (CAP-01)**: Night mode text contrast is so dark that 2 a.m. Oliver cannot read critical metrics.
3. **Missing Field Validation (CAP-02)**: Silent acceptance of negative exposure times (`-5s`) wastes time and generates impossible frames.
4. **Modal Button Reversal (PLAN-01)**: The "CANCEL" button is styled as the primary action, tricking users into aborting their intent.
5. **Weather Toggle Inaccessible (W-01)**: The `weather enabled` toggle lacks proper ARIA/label mapping, breaking automation and screen readers.
6. **Dead Search Feedback (ATLAS-01)**: The Atlas search box provides zero feedback when querying non-existent targets like "Jupiter".
7. **Ambiguous Save Verbs (SET-01)**: Conflicting `SAVE SITE` vs `SAVE CURRENT...` buttons leave users unsure where their location data is actually going.
8. **Documentation Navigation Mismatch (DOC-01)**: `getting-started.md` tells users to look for a non-existent "Rig" tab.
9. **Color-Dependent Status (EQ-02)**: Equipment LEDs rely entirely on color (green vs grey), failing accessibility checks.
10. **Silent Weather Failure (W-03)**: Enabling weather with a default (0, 0) site silently fails to fetch forecasts without warning the user in the UI.

### Top 3 Doc Changes to Help Personas:
For **First-light Fran**, fix the incorrect references to UI elements (like the non-existent "Rig" tab and "Observing Site" page) so she can actually find the controls she's instructed to use. For **2 a.m. Oliver**, ensure that the documentation explicitly highlights the interaction differences in Night Mode and where to find the overriding settings for weather and safety. For **Returning Riley**, clarify the difference between session-scoped saves and library-scoped saves across all documentation to prevent data loss confusion upon return.
