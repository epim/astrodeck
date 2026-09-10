// profileActive.ts - "did the activate actually land", owned by `next`
// (wave R7 section 2.1's finding, ruling 6).
//
// WHY THIS IS A COPY AND NOT A `export { waitForProfileActive } from
// "../../../../components/settings/ProfileList"` PASS-THROUGH.
//
// The helper is pure control flow over one GET, but it lives INSIDE a 929-line
// legacy presentation module. Re-exporting it from there keeps the promise's
// words and loses its point: every next-side importer would still pull
// `ProfileList.tsx` - and with it `Panel`, `HoldButton`, `LockedChip`, `Icon`
// and the whole Tailwind tree behind them - into the lazily split next bundle,
// which is the cost D-FU-2 spent a wave undoing. Section 2.1 is explicit: the
// legacy file KEEPS ITS OWN COPY for `#/classic`, and the rebuilding task owns
// the helper. Nothing under `ui/src/components/**` is edited.
//
// The two copies cannot drift silently. `rigProfilesDom.test.tsx`'s "no drift"
// section imports BOTH and asserts they behave identically against the same
// server (poll count, the rows handed to `onRows`, the resolved value, and the
// null a lapsed budget returns), and additionally compares the two function
// bodies with comments and whitespace normalised away - the same guard
// `create/quickPayload.ts` uses for the quick flow's helpers.
//
// STILL IMPORTING THE LEGACY ONE, AND NAMED AS THE FOLLOW-UP:
// `ui/src/next/hubs/rig/devices/rigConnect.ts:23`. That file belongs to no R7
// task, so this wave does not edit it; switching its import to this module is a
// one-line follow-up that removes the last `ProfileList` reference from
// `ui/src/next/**`.

import { listProfiles } from "../../../../api/backends";
import type { ProfileRow } from "../../../../types";

/** Wait until the server reports `id` as the ACTIVE profile - i.e. until the
 *  activate actually landed - re-listing as it goes. Resolves with the rows
 *  once the pointer moves, or null when the budget runs out.
 *
 *  WHY A POLL, in a codebase that has `useBusy` for exactly this: the activate
 *  route goes through `_spawn_connect`, which deliberately keeps its task OUT
 *  of `hub._busy` - a profile connect tears the current rig down first, and
 *  `disconnect_all()` cancels everything in `_busy`, which is how the connect
 *  used to cancel its own driver mid-teardown. `busy_lanes` is built from
 *  `_busy`, so this is the one long operation the rig publishes no lane for,
 *  and `useBusy("profile")` would be false the entire time.
 *
 *  What the rig DOES publish is the result. `connect_profile_id` sets the
 *  active pointer inside `connect_rigspec`, only on success - so the profile
 *  list is the answer to "did it land", and the only thing wrong with the old
 *  code was asking ~40ms after the POST, when the answer is still the previous
 *  rig's. That is why activating B left A wearing the accent ring, the ACTIVE
 *  chip and "auto-connects on boot": the re-list was real, it was just early.
 *
 *  A failed connect never moves the pointer, so it reads as the timeout - which
 *  is honest (we cannot tell "still connecting" from "failed" without a lane)
 *  as long as the caller says so rather than claiming success. */
export async function waitForProfileActive(
  id: string,
  onRows: (rows: ProfileRow[]) => void,
  budgetMs = 45000,
  everyMs = 1500,
): Promise<ProfileRow[] | null> {
  const until = Date.now() + budgetMs;
  while (Date.now() < until) {
    await new Promise((r) => setTimeout(r, everyMs));
    let rows: ProfileRow[];
    try {
      rows = await listProfiles();
    } catch {
      // Mid-teardown the controller can drop a request. A failed poll is not
      // an answer - keep asking until the budget says otherwise.
      continue;
    }
    onRows(rows);
    if (rows.some((r) => r.id === id && r.active)) return rows;
  }
  return null;
}
