// profiles.tsx - the PROFILES sheet (plan hub-rig.md A.3 / deviation E29).
//
// The popover on the devices screen is the quick switch. This is the library:
// activate, rename, update-from-rig, import, export, delete, and save-current.
//
// STAGE 2, CLOSING THE STAGE-1 DECISION THIS FILE USED TO DOCUMENT. Wave 1
// mounted `components/settings/ProfileList` whole - two `Panel`s, five native
// `disabled` attributes, a `LockedChip` on Delete and no stated reason at all
// on the other four controls - and named the re-skin as a follow-up, on the
// grounds that its irreversible actions carry friction and copy that took
// several rounds to get right. Wave R7's T-R7-17 is that follow-up, and it
// keeps every one of those judgements by SHARING them rather than re-typing
// them: `profileActivateConfirm` (what an activate DROPS, not what it
// attaches), `profileDeleteConfirm` (what is and is not lost, and the
// hold-confirm on the active profile), `profileOverrideSummary` (#129) and
// `waitForProfileActive` all still decide. Only the presentation is new. The
// legacy panel is not edited and still serves `#/classic`.
//
// THE LIVE LINE IS FED BY THE EDITOR. It used to run its own `listProfiles()`
// once on mount and never again, so after an activate or a delete the header
// went on naming the profile that had just been replaced. The editor hands up
// the same rows it is rendering, so the two cannot disagree.
//
// THE READ-ONLY SENTENCE IS NOT REPEATED HERE. The editor renders one
// `LockNote` from the same gate every control reads; a second sentence under
// the list was the shape ARCHITECTURE.md section 8 asks the `LockNote`
// primitive to remove.

import { useState, type JSX } from "react";
import { Mono, Sheet } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { ProfilesEditor } from "../profiles";
import type { ProfileRow } from "../../../../types";

export function ProfilesSheet(): JSX.Element {
  const [rows, setRows] = useState<ProfileRow[] | null>(null);

  const active = (rows ?? []).find((r) => r.active) ?? null;
  const live = rows == null
    ? "reading the profile library..."
    : `${rows.length} saved · ${active ? `${active.name} active` : "none active"}`;

  return (
    <Sheet
      title="PROFILES"
      backLabel="RIG"
      icon={<NxIcon name="layers" size={18} />}
      live={live}
      onBack={() => nav.back()}
      data-testid="sheet-profiles"
    >
      <Mono size={11} tone="dim">
        A profile is a saved set of devices and where to find them. Activating one
        drops the rig running now before it connects anything - it is a swap, not
        an addition.
      </Mono>
      <ProfilesEditor onRows={setRows} />
      <div style={{ height: 8 }} />
    </Sheet>
  );
}
