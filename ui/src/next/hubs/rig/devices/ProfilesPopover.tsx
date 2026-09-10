// ProfilesPopover.tsx - the quick switch behind SWITCH on the PROFILE row
// (plan hub-rig.md A.3).
//
// WHY BOTH A POPOVER AND A SHEET (deviation E29). The design says popover. A
// popover cannot hold rename, update-from-rig, import and export without
// becoming a screen with a shadow, so it holds the four things a switch needs -
// list, activate, save the running rig, delete - and its last row opens the
// `profiles` sheet, which mounts the shipped `ProfileList` for the rest. They
// are not two implementations of one thing: they are one surface split at the
// point where "switch rigs" stops and "curate the library" starts.
//
// ACTIVATE IS DESTRUCTIVE AND DOES NOT LOOK IT. Every activate runs the hub's
// teardown FIRST (`hub.py::_connect_rigspec_unlocked`), so the cost is the rig
// you are DROPPING, not the one the profile stores. `profileActivateConfirm`
// owns that judgement and `waitForProfileActive` owns "did it actually land" -
// both imported, because the version of this that guessed either one is what
// turned one tap into 10 devices connected, 0 devices after, no dialog.

import { useState, type JSX } from "react";
import { ActionButton, Divider, Label, Mono, TextInput } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { ApiError } from "../../../../api";
import { captureProfile, deleteProfile, listProfiles } from "../../../../api/backends";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import { accessPhrase } from "../../../../lib/caps";
import { profileOverrideSummary } from "../../../../lib/effective";
import { profileSaveLock } from "../../../../lib/equipment";
import { profileDeleteConfirm, profileDeleteLock } from "../../../../lib/profileDelete";
import { useStore } from "../../../../store";
import type { ProfileRow } from "../../../../types";
import { activateProfileRow, type BusyWhat } from "./rigConnect";

/** The library's own "nothing to save" sentence names the Equipment screen's
 *  assignment rows, which are one sheet away in this IA. The DECISION stays in
 *  `profileSaveLock` - only the destination is repointed, exactly as the plan
 *  repoints the InfoDot and the empty-scan note. A library edit makes this a
 *  no-op and the shipped sentence survives, which is the safe direction. */
function repointSaveLock(reason: string | null): string | null {
  if (!reason) return null;
  return reason.replace(
    "pick drivers on the rows above",
    "pick drivers on ADD A DEVICE",
  );
}

const ACTIVATE_BUSY = "A rig action is already running - wait for it to finish.";

export interface ProfilesPopoverProps {
  rows: ProfileRow[] | null;
  liveDevices: number;
  canConfig: boolean;
  busy: BusyWhat;
  setBusy: (w: BusyWhat) => void;
  /** Fresh rows from any path that changes them (activate polls, delete, save). */
  onRows: (rows: ProfileRow[]) => void;
  reload: () => void;
  onClose: () => void;
}

export function ProfilesPopover(p: ProfilesPopoverProps): JSX.Element {
  const [name, setName] = useState("");
  const [pending, setPending] = useState<string | null>(null);

  const toast = (level: string, message: string, opts?: { verbatim?: boolean }) =>
    useStore.getState().showToast(level, message, opts);

  const activateLock = !p.canConfig
    ? `Activating a profile needs ${accessPhrase("config.backend")}.`
    : p.busy != null ? ACTIVATE_BUSY : null;
  const deleteLock = profileDeleteLock(p.canConfig) ?? (p.busy != null ? ACTIVATE_BUSY : null);
  const saveLock = repointSaveLock(profileSaveLock({
    permission: p.canConfig ? null : `Saving a profile needs ${accessPhrase("config.backend")}.`,
    name,
    live: p.liveDevices,
    // The popover saves what is RUNNING. Browser-local picks are saved from the
    // ADD A DEVICE sheet, where the rows they refer to actually are.
    assigned: 0,
    busy: p.busy != null,
  }));

  const explain = (reason: string) => toast("warning", reason);

  const doActivate = (row: ProfileRow) => void (async () => {
    setPending(row.id);
    try {
      // The SAME call the FIRST NIGHT card makes. Two implementations of
      // "activate a profile" would be two answers to "did it land".
      await activateProfileRow(row, p.liveDevices, {
        setBusy: p.setBusy, onRows: p.onRows, reload: p.reload,
      });
    } finally {
      setPending(null);
    }
  })();

  const doDelete = (row: ProfileRow) => void (async () => {
    p.setBusy("save");
    try {
      // Re-list first: the friction level depends on whether this is still the
      // ACTIVE profile, and this popover may have been open a while.
      let fresh = row;
      try {
        const rows = await listProfiles();
        p.onRows(rows);
        fresh = rows.find((r) => r.id === row.id) ?? row;
      } catch { /* decide on what we have rather than refusing to decide */ }
      if (!(await confirmDialog(profileDeleteConfirm(fresh)))) return;
      await deleteProfile(row.id);
      toast("success", `Deleted "${fresh.name}"`);
      p.reload();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "profile delete failed", { verbatim: true });
    } finally {
      p.setBusy(null);
    }
  })();

  const doSave = () => void (async () => {
    p.setBusy("save");
    try {
      const row = await captureProfile(name.trim());
      setName("");
      toast("success",
        `Profile "${row.name}" saved - the ${p.liveDevices} device`
        + `${p.liveDevices === 1 ? "" : "s"} connected now. Activate it to bring this rig back.`);
      p.reload();
    } catch (e) {
      const msg = e instanceof ApiError && e.status === 409
        ? "The rig disconnected before the snapshot - reconnect, then save."
        : e instanceof Error ? e.message : "profile save failed";
      toast("error", msg, { verbatim: true });
    } finally {
      p.setBusy(null);
    }
  })();

  const rows = p.rows;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 260, maxWidth: 320 }}>
      <Label>PROFILES</Label>

      {rows == null && <Mono size={10.5} tone="dim">reading the profile library...</Mono>}
      {rows != null && rows.length === 0 && (
        <Mono size={10.5} tone="dim">
          No profiles yet. Save the rig you are running and next time is one tap.
        </Mono>
      )}

      {(rows ?? []).map((row) => {
        const summary = profileOverrideSummary(row);
        return (
          <div key={row.id} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <button
              type="button"
              className={activateLock ? "nx-row nx-locked" : "nx-row"}
              data-testid={`profile-pick-${row.id}`}
              aria-disabled={activateLock ? true : undefined}
              title={activateLock ?? undefined}
              style={{ flex: 1, minWidth: 0 }}
              onClick={() => (activateLock ? explain(activateLock) : doActivate(row))}
            >
              <span
                aria-hidden="true"
                style={{
                  width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
                  background: row.active ? "var(--good)" : "rgba(120,140,200,.35)",
                }}
              />
              <span className="nx-row-text">
                <span className="nx-row-title">{row.name}</span>
                <span className="nx-row-sub">
                  {pending === row.id
                    ? "connecting..."
                    : [`${row.devices_count} device${row.devices_count === 1 ? "" : "s"}`, summary]
                      .filter(Boolean).join(" · ")}
                </span>
              </span>
            </button>
            <button
              type="button"
              className={deleteLock ? "nx-pill nx-locked" : "nx-pill"}
              data-testid={`profile-delete-${row.id}`}
              aria-label={`Delete ${row.name}`}
              aria-disabled={deleteLock ? true : undefined}
              title={deleteLock ?? undefined}
              onClick={() => (deleteLock ? explain(deleteLock) : doDelete(row))}
            >
              <NxIcon name="x" size={12} />
            </button>
          </div>
        );
      })}

      {p.liveDevices > 0 && (
        <Mono size={10} tone="warn">
          {`Activating drops the ${p.liveDevices} device${p.liveDevices === 1 ? "" : "s"} running `}
          now before it connects anything - it is a swap, not an addition.
        </Mono>
      )}

      <Divider />

      <Label>SAVE CURRENT AS</Label>
      <div style={{ display: "flex", gap: 6 }}>
        <TextInput
          value={name}
          onChange={setName}
          placeholder="Backyard"
          ariaLabel="New profile name"
          data-testid="profile-save-name"
        />
        <ActionButton
          kind="secondary"
          onPress={doSave}
          lockedReason={saveLock}
          onExplain={explain}
          data-testid="profile-save"
        >
          SAVE
        </ActionButton>
      </div>

      <ActionButton
        kind="ghost"
        full
        onPress={() => { p.onClose(); nav.sheet("profiles"); }}
        data-testid="profile-all-options"
      >
        ALL PROFILE OPTIONS &rsaquo;
      </ActionButton>
    </div>
  );
}
