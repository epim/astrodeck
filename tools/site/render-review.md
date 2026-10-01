# Independent site render review

Local static server only. No astronomy server, live rig or real configuration was accessed.
Automated matrix: all seven pages at 320, 390 and 1440 CSS pixels in both light and dark themes.
Screenshots are under .probe/site-review/. Text-resize checks set the root font size to 200 percent.

## Layout matrix

| Page / width / theme | Result | Page overflow px | Minimum gutter px | Images | Browser errors |
|---|---|---:|---:|---:|---|
| index-320-light | PASS | 0 | 16 | 3 | None |
| features-320-light | PASS | 0 | 16 | 3 | None |
| flows-320-light | PASS | 0 | 16 | 1 | None |
| hardware-320-light | PASS | 0 | 16 | 0 | None |
| weather-320-light | PASS | 0 | 16 | 0 | None |
| getting-started-320-light | PASS | 0 | 16 | 0 | None |
| releases-320-light | PASS | 0 | 16 | 0 | None |
| index-320-dark | PASS | 0 | 16 | 3 | None |
| features-320-dark | PASS | 0 | 16 | 3 | None |
| flows-320-dark | PASS | 0 | 16 | 1 | None |
| hardware-320-dark | PASS | 0 | 16 | 0 | None |
| weather-320-dark | PASS | 0 | 16 | 0 | None |
| getting-started-320-dark | PASS | 0 | 16 | 0 | None |
| releases-320-dark | PASS | 0 | 16 | 0 | None |
| index-390-light | PASS | 0 | 16 | 3 | None |
| features-390-light | PASS | 0 | 16 | 3 | None |
| flows-390-light | PASS | 0 | 16 | 1 | None |
| hardware-390-light | PASS | 0 | 16 | 0 | None |
| weather-390-light | PASS | 0 | 16 | 0 | None |
| getting-started-390-light | PASS | 0 | 16 | 0 | None |
| releases-390-light | PASS | 0 | 16 | 0 | None |
| index-390-dark | PASS | 0 | 16 | 3 | None |
| features-390-dark | PASS | 0 | 16 | 3 | None |
| flows-390-dark | PASS | 0 | 16 | 1 | None |
| hardware-390-dark | PASS | 0 | 16 | 0 | None |
| weather-390-dark | PASS | 0 | 16 | 0 | None |
| getting-started-390-dark | PASS | 0 | 16 | 0 | None |
| releases-390-dark | PASS | 0 | 16 | 0 | None |
| index-1440-light | PASS | 0 | 88 | 3 | None |
| features-1440-light | PASS | 0 | 88 | 3 | None |
| flows-1440-light | PASS | 0 | 88 | 1 | None |
| hardware-1440-light | PASS | 0 | 88 | 0 | None |
| weather-1440-light | PASS | 0 | 88 | 0 | None |
| getting-started-1440-light | PASS | 0 | 88 | 0 | None |
| releases-1440-light | PASS | 0 | 88 | 0 | None |
| index-1440-dark | PASS | 0 | 88 | 3 | None |
| features-1440-dark | PASS | 0 | 88 | 3 | None |
| flows-1440-dark | PASS | 0 | 88 | 1 | None |
| hardware-1440-dark | PASS | 0 | 88 | 0 | None |
| weather-1440-dark | PASS | 0 | 88 | 0 | None |
| getting-started-1440-dark | PASS | 0 | 88 | 0 | None |
| releases-1440-dark | PASS | 0 | 88 | 0 | None |

## Interaction and fallback checks

| Check | Result | Detail |
|---|---|---|
| First keyboard focus is skip link | PASS |  |
| Keyboard focus has visible outline | PASS |  |
| Skip link moves focus to main | PASS |  |
| Mobile menu opens with keyboard | PASS |  |
| Menu tab enters navigation | PASS |  |
| Escape closes menu and restores focus | PASS |  |
| Initial theme uses system | PASS |  |
| Theme cycles to light | PASS |  |
| Theme cycles to dark | PASS |  |
| Theme persists after reload | PASS |  |
| Theme returns to system | PASS |  |
| System theme follows OS preference | PASS | rgb(16, 29, 39) / rgb(250, 249, 245) |
| Theme works with denied storage | PASS | [] |
| index navigation without JavaScript | PASS |  |
| index no-JS page reflows | PASS | overflow=0 |
| features navigation without JavaScript | PASS |  |
| features no-JS page reflows | PASS | overflow=0 |
| flows navigation without JavaScript | PASS |  |
| flows no-JS page reflows | PASS | overflow=0 |
| hardware navigation without JavaScript | PASS |  |
| hardware no-JS page reflows | PASS | overflow=0 |
| weather navigation without JavaScript | PASS |  |
| weather no-JS page reflows | PASS | overflow=0 |
| getting-started navigation without JavaScript | PASS |  |
| getting-started no-JS page reflows | PASS | overflow=0 |
| releases navigation without JavaScript | PASS |  |
| releases no-JS page reflows | PASS | overflow=0 |
| index 200-percent-text at 320px | PASS | overflow=0 |
| features 200-percent-text at 320px | PASS | overflow=0 |
| flows 200-percent-text at 320px | PASS | overflow=0 |
| hardware 200-percent-text at 320px | PASS | overflow=0 |
| weather 200-percent-text at 320px | PASS | overflow=0 |
| getting-started 200-percent-text at 320px | PASS | overflow=0 |
| releases 200-percent-text at 320px | PASS | overflow=0 |
| index 200-percent-text at 1440px | PASS | overflow=0 |
| features 200-percent-text at 1440px | PASS | overflow=0 |
| flows 200-percent-text at 1440px | PASS | overflow=0 |
| hardware 200-percent-text at 1440px | PASS | overflow=0 |
| weather 200-percent-text at 1440px | PASS | overflow=0 |
| getting-started 200-percent-text at 1440px | PASS | overflow=0 |
| releases 200-percent-text at 1440px | PASS | overflow=0 |

## Findings

No automated failures.

## Visual inspection

Visual inspection completed with the image viewer, not inferred from CSS or measurement results. This review covered a design authored by another worker; the reviewer authored the claim ledger and proposed copy separately.

### Evidence inspected

- All seven pages at 390 and 1440 pixels, each in light and dark themes: `PAGE-WIDTH-THEME-top.png` under `.probe/site-review/` (28 viewport images). Homepage, features and Flows captures were refreshed after the final simulator assets were regenerated at 17:34:41 UTC.
- Homepage: `index-390-light-progress.png`, `index-1440-dark-start.png`, `index-390-dark-flows.png`, and the refreshed four homepage top views. The simulator labels and captions match the visible states. The schematic sequence reads in order on a phone.
- Features: `features-1440-light-monitoring.png`, `features-1440-dark-monitoring.png`, `features-390-light-monitoring.png`, and `features-390-dark-monitoring.png`. The day and red night phone images remain separate, labeled and within the page width.
- Flows: `flows-390-light-canvas.png`, `flows-1440-dark-canvas.png`, and `flows-390-dark-wires.png`. The refreshed graph fits its capture. The image, caption, explanatory sections and following cards align without clipping.
- Hardware: `hardware-1440-light-native.png` and `hardware-390-dark-native.png`. Desktop status labels stay on one line; phone rows retain their connection, status and evidence labels.
- Weather: `weather-1440-light-safety.png` and `weather-390-dark-safety.png`. Safety and dusk guidance remain visually separate; links and the footer retain readable spacing.
- Setup: `getting-started-390-light-source.png` and `getting-started-1440-dark-lan.png`. Commands wrap within the code panel on phones. Notes and links remain distinct from commands.
- Releases: `releases-1440-dark-milestones.png` and `releases-390-light-milestones.png`. Four dated version records have clear hierarchy and no crowded columns.
- Repaired enlarged text: `index-320-text200-top.png`, `hardware-320-text200-top.png`, `getting-started-320-text200-top.png`, `releases-320-text200-top.png`, and `index-320-text200-start.png`. Large headings now break within the phone width. No content is clipped at the right edge.
- `no-javascript-320.png` was inspected to confirm navigation remains available with scripting disabled.

### Findings resolved during review

1. The original 200 percent text check overflowed at 320 pixels on the homepage (57 pixels), hardware (84), setup (173), and releases (23). Min-content widths in grid children and unbroken text caused most failures; the homepage phone caption had an additional nowrap label. The parent added scoped overflow wrapping, `min-width: 0` on relevant grid children, and wrapping for the phone figure label. The complete matrix rerun reports zero overflow in all 14 enlarged-text cases.
2. The desktop native-device table split Supported across lines. A desktop-only nowrap rule on the Status column fixed it without changing phone rows.
3. The releases on-page navigation still said Tagged milestones after the copy changed to repository release records. It now says Release records.

No unresolved visual or interaction finding remains in the reviewed scope. The full rerun passed 42 layout cases and 41 interaction, fallback and enlarged-text checks. Every normal phone case retained 16-pixel gutters. Images loaded and there were no browser page errors.

`browser_check.py` now exits with status 1 when any recorded check fails, so it can be used as an automation gate. That isolated exit-status change was syntax checked without repeating the browser matrix.

This is a Chromium review at the stated CSS viewports. Enlarged text was simulated by setting the root font size to 200 percent; no screen-reader or additional browser-engine review is claimed. Static-site inspection accessed only the local preview, not a live rig.
