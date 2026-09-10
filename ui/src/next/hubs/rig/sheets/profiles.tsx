// profiles.tsx - the PROFILES sheet (plan A.3 / deviation E29).
//
// The popover on the devices screen is the quick switch. This is the library:
// rename, update-from-rig, import, export, delete, and save-current - all of it
// already implemented, correctly, in `components/settings/ProfileList`, which
// takes no props and reads the store itself. So this file is chrome around it.
//
// Re-skinning ProfileList into the new language is a NAMED follow-up, not this
// wave: its four irreversible actions (delete, update-from-rig, import, and the
// activate that tears the running rig down first) carry friction and copy that
// took several rounds to get right, and re-drawing them is a change worth its
// own review rather than a side effect of moving the screen.

import { useEffect, useState, type JSX } from "react";
import { Mono, Sheet } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { listProfiles } from "../../../../api/backends";
import ProfileList from "../../../../components/settings/ProfileList";
import { accessPhrase, useCanConfigBackend } from "../../../../lib/caps";
import type { ProfileRow } from "../../../../types";

export function ProfilesSheet(): JSX.Element {
  const [rows, setRows] = useState<ProfileRow[] | null>(null);
  const canConfig = useCanConfigBackend();

  useEffect(() => {
    listProfiles().then(setRows).catch(() => setRows(null));
  }, []);

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
      <ProfileList />
      {!canConfig && (
        <Mono size={11} tone="dim">
          {`Read-only — changing profiles needs ${accessPhrase("config.backend")}.`}
        </Mono>
      )}
      <div style={{ height: 8 }} />
    </Sheet>
  );
}
