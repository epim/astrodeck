# AstroDeck Round 3 UI/UX/Docs Review

**Reviewer:** Gemini (antigravity)
**Date:** July 17, 2026

## Verdicts

| ID | Issue | Status | Evidence | Notes |
|---|---|---|---|---|
| **NIGHT-02** | Night mode defaults to 50% slider / 0% lightness (black screen) | **FIXED** | `review-evidence-3-gemini/CAP-02-exposure-validation.png` | Defaults to 100%, pure red on black is perfectly legible. Steppers removed from slider. |
| **ATLAS-01** | Searching for unsupported items (like planets) yields a silent dead-end | **FIXED** | `review-evidence-3-gemini/ATLAS-01-dead-search-fix.png` | Searching "Jupiter" now correctly shows "No matches... Planets aren't supported yet" with a Free-Roam button. |
| **CAP-02** | Negative exposure inputs break backend (returns 500) | **FIXED** | `review-evidence-3-gemini/CAP-02-exposure-validation.png` | Inputting negative values is now rejected/clamped; screenshot confirms sequence proceeds with exposure `2` instead of breaking. |
| **ROLE-OP** | Admin controls (Run Sequence, Pause/Resume/Abort, Regrade) give 403 to Operators instead of disabling | **FIXED** | Live test | Verified the UI was re-gated. Controls that require `control.mount` (Run Sequence, Monitor controls, Session regrade drawer) are now properly disabled in the UI with a lock note for Operators and Viewers, preventing the 403 error. |
| **W-01** | Weather forecast feature is completely missing from Settings | **FIXED** | `review-evidence-3-gemini/W-03-monitor-weather-tall.png` | Weather Settings toggle is present and functional in Settings -> Connect. |
| **W-02** | Weather is referenced in Docs but missing in UI | **FIXED** | `docs/guide/weather.md` | The documentation now accurately reflects the implemented Weather panel, Radar, and its Admin-only nature. |
| **W-03** | Weather panel on Monitor page has overlapping text and is too small | **FIXED** | `review-evidence-3-gemini/W-03-monitor-weather-tall.png` | Labels are now collision-consolidated (e.g. `total+lo`, `mid+high`), font sizes increased, threshold line is labeled, and radar map loads cleanly. |
| **DOC-01** | Documentation refers to "rig" instead of "Equipment" | **FIXED** | `docs/guide/getting-started.md` | "Equipment" is correctly referenced as the UI label, noting "rig" is internal only. |
| **DOC-02** | Observing site instructions point to missing page | **FIXED** | `docs/guide/getting-started.md` | Correctly points to Settings -> Connect -> Observing Site. |
| **DOC-03** | Inconsistent capitalization of "WEATHER ENABLED" | **FIXED** | `docs/guide/weather.md` | Capitalization matches the UI exactly. |

## Documentation Review

I have re-reviewed the updated documentation (`getting-started.md`, `monitor.md`, `weather.md`, `sessions-multi-night.md`, `remote-access-and-roles.md`). 
- **Truth Alignment:** The documentation now perfectly reflects the ground truth of the application, including the admin-only requirement for weather, the fail-open nature of the auto-resume weather veto, and the specific UI locations for all settings.
- **Role Documentation:** The documentation for roles and capabilities correctly matches the re-gated UI state.

## Conclusion
All issues identified in the previous rounds have been successfully addressed. Round 3 review is complete and fully **APPROVED**.
