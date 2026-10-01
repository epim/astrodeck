# Monitor screenshot follow-up

The final Monitor figure uses a fresh synthetic public Hanle scenario from site checkout `94bdd461bfb6a83f9d20301ef9a6bbd9217ce54d`. Only `monitor-desktop-light.png` changed. Its SHA-256 is `63fe4c1f024178caa42db9b89a8dfbb2ffc0de5f37e1452fe74f03e682519c19`; dimensions remain 1440 x 1000. The other three images are byte-identical to HEAD and retain their original `bf3eaddd` source, timestamp, hashes and default-site provenance.

The capture helper accepts `--monitor-only --synthetic-site hanle` (the earlier Siding Spring scenario remains available). Monitor-only mode requires a complete prior ledger and cannot replace an equipment or Flows record. The schema-2 ledger stores provenance per image. Existing fresh-directory, process identity, listener ancestry, loopback-only and final cleanup checks remain intact.

The scenario saves the fixed approximate public Hanle Indian Astronomical Observatory location in a never-used isolated simulator config. It verifies the saved site, commands a plain simulated slew one hour east of the meridian at declination +20 with `center=false` and `force=false`, and waits until the mount is settled, unparked, near the target and above 30 degrees. It takes one unsaved 1-second simulator exposure. The capture checks visible text for the unwanted warning and coordinate strings before writing the PNG. No application source, DOM content or screenshot pixels were edited to manufacture the state.

Successful final run: `.probe/site-monitor-followup/sim-hanle`, loopback port 8878, captured 2026-10-01 18:31:37 UTC. The owned server stopped in the finalizer; the listener count is zero and `server.pid` was removed. Logs and generated config/captures remain in ignored private scratch. The shared Python environment stayed read-only. Playwright/test dependencies and Chromium live only under `.probe/site-monitor-followup`; the UI was built from this site checkout using existing npm dependencies through a junction and Vite's runner config loader.

Original-resolution pixel review found no site label/coordinates, numerical mount coordinates or Atlas/survey imagery. The below-horizon warning and `no dark tonight` message are absent. To Dawn now reads `4h 49m`, with `dark until 16:21` in browser time. The page honestly remains idle, weather monitoring off, with a single stale simulator frame. The parent independently inspected the final pixels and confirmed the sensible countdown, absence of site/mount coordinates or warnings, and authentic idle state.

The earlier Siding Spring attempt was superseded at Claude's request because it showed the next-window presentation defect tracked as #640. Its PNG is not shipped. Earlier refused attempts and the Siding Spring run each used separate new directories and stopped their owned server. Simulator identity checks allow the legacy empty backend description only with exact simulator device names and no remote host/port; process ownership plus explicit simulator startup remain the authority. The readiness threshold accommodates the simulator's documented small unsynced pointing error while refusing slewing, parked, distant or below-horizon states.

Validation: 45 focused tests pass. Seventeen named mutants are killed and each edited source restored exactly, including Hanle's hemisphere-specific target declination, monitor-only selection, provenance retention and above-horizon readiness. Assertions are recorded in `mutation-evidence.md`. All four ledger hashes match the image bytes; the other three PNGs match HEAD exactly. The HTML/link/style/privacy gate passes all seven pages, changed tooling/provenance passes the private-value scan, and `git diff --check` is clean. No commit or publication was performed.

Reproduction from a fresh directory:

```text
python tools/site/capture_sim.py --config-dir .probe/<new-directory> --port 8878 --ui-dir ui/dist --venv-python <existing-server-python> --monitor-only --synthetic-site hanle
```

Set `PYTHONPATH` to the private test/Playwright dependencies and `PLAYWRIGHT_BROWSERS_PATH` to the private browser cache. Never reuse a completed probe directory. Use this scenario while Hanle is actually dark; inspect the resulting dawn value and drop the figure if the app presents an unsuitable window. The helper does not alter the app clock.
