// ProfilesEditor.tsx - the PROFILES library, rebuilt in the design's own
// vocabulary (wave R7, T-R7-17; plan section 3.F20). Replaces
// `components/settings/ProfileList.tsx` at its one mount inside the new UI,
// `hubs/rig/sheets/profiles.tsx`. The legacy file is untouched, not deleted and
// no longer imported from anywhere under `ui/src/next/**`; it still serves
// `#/classic`.
//
// This is the area ROOT: the one file that imports `profiles.css`, per the wave
// rule that `next.css` belongs to a single task and every other area carries
// its own stylesheet.
//
// WHAT CHANGED, AND WHY IT IS NOT A RE-SKIN.
//
//   FIVE NATIVE `disabled` ATTRIBUTES ARE GONE (`ProfileList.tsx` :566 :678
//   :695 :716 :765). A `disabled` element is removed from the accessibility
//   tree, which takes the REASON with it - and every one of these was a
//   TRANSIENT state, not a permission: "another profile is connecting" and "an
//   action on this row is running" are exactly the two things a user needs told
//   when a tap does nothing. They are `lockedReason` + `onExplain` now.
//
//   THE READ-ONLY SENTENCE IS SAID ONCE. The sheet used to print its own
//   `Read-only - changing profiles needs admin access.` line UNDER a panel that
//   showed a differently worded `LockedChip` on Delete and nothing at all on
//   the other four controls, so a viewer read two sentences and got no answer
//   about the other four. One `LockNote`, one reason, and the same reason on
//   every control.
//
//   A FAILED LOAD IS NOT AN EMPTY LIBRARY. `Couldn't load profiles` now carries
//   RETRY, and the empty state is rendered only when the list actually came
//   back empty - the two used to be a hint apart.
//
//   THE SHEET'S LIVE LINE IS FED FROM HERE. It used to run its own
//   `listProfiles()` once on mount and never again, so after an activate or a
//   delete the header still named the old active profile. `onRows` hands the
//   sheet the same rows this editor is rendering.
//
// SHARED, NEVER RE-DERIVED: `profileActivateConfirm` / `profileConnectsNothing`
// / `profileResolvesRealMotion` / `liveRoleCount` (`lib/equipment.ts`),
// `profileDeleteConfirm` / `profileDeleteLock` (`lib/profileDelete.ts`),
// `profileOverrideSummary` (`lib/effective.ts`), `parseProfileFile` /
// `profileExportFilename` (`lib/profileFile.ts`) and `waitForProfileActive`
// (`./profileActive`, this area's own copy - see that file's header).

import { useCallback, useEffect, useRef, useState, type JSX } from "react";
import { ApiError } from "../../../../api";
import {
  activateProfile, captureProfile, deleteProfile, getProfile, importProfile,
  listProfiles, renameProfile, saveProfile,
} from "../../../../api/backends";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import { useCanConfigBackend } from "../../../../lib/caps";
import {
  liveRoleCount, profileActivateConfirm, profileConnectsNothing,
  profileResolvesRealMotion,
} from "../../../../lib/equipment";
import { profileDeleteConfirm, profileDeleteLock } from "../../../../lib/profileDelete";
import { parseProfileFile, profileExportFilename } from "../../../../lib/profileFile";
import { useStore } from "../../../../store";
import type { Profile, ProfileRow } from "../../../../types";
import { NxIcon } from "../../../icons";
import { useLock } from "../../../lib/gateHook";
import {
  ActionButton, Card, EmptyCard, Field, Label, LockNote, Mono, TextInput,
} from "../../../ui";
import { ProfileCard } from "./ProfileCard";
import { UploadGlyph } from "./glyphs";
import { waitForProfileActive } from "./profileActive";
import {
  ACTIVATE_FAILED, CAPTURE_FAILED, CAPTURE_NEEDS_RIG, DEFAULT_CAPTURE_NAME,
  DELETE_FAILED, EMPTY_HINT, EMPTY_TITLE, EXPORT_FAILED, FORCE_CONFIRM,
  IMPORTED, IMPORT_LABEL, LANE_BUSY, LOADING, LOAD_FAILED, NAME_LABEL,
  NAME_PLACEHOLDER, PROFILES_CAP, PROFILES_EYEBROW, REFRESH_LABEL,
  RENAME_FAILED, RETRY_LABEL, SAVE_BLURB, SAVE_BUSY, SAVE_BUTTON, SAVE_EYEBROW,
  UPDATE_FAILED, UPDATE_NEEDS_RIG, activated, activating, alreadyGone,
  deleted, errText, exported, importFailed, isGone, isLaneConflict,
  isRunningConflict, notActiveYet, rowBusyToast, updateConfirm, updated,
} from "./profilesModel";
import "./profiles.css";

export function ProfilesEditor({ onRows }: {
  /** The sheet's live line reads the SAME rows this editor renders, so it can
   *  never name a profile the list has already replaced. */
  onRows?: (rows: ProfileRow[] | null) => void;
} = {}): JSX.Element {
  const enqueueToast = useStore((s) => s.enqueueToast);
  // Every write here is `config.backend` server-side. The gate is read once and
  // handed to every control, so the sheet says one thing.
  const { lockedReason, onExplain } = useLock({ cap: PROFILES_CAP });
  // The capability alone, without the link state - the last-line guard before
  // the one irreversible call, for a token downgraded while a dialog was open.
  const canConfig = useCanConfigBackend();

  const [rows, setRows] = useState<ProfileRow[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [connectingId, setConnectingId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [captureName, setCaptureName] = useState("");
  const [capturing, setCapturing] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const toast = (level: "info" | "success" | "warning" | "error", title: string) => {
    enqueueToast({ level, title });
  };

  // `publish` is the ONE place rows reach both this component and the sheet, so
  // the header and the list cannot disagree about which profile is active.
  const publish = useCallback((next: ProfileRow[] | null) => {
    setRows(next);
    onRows?.(next);
  }, [onRows]);

  const refresh = useCallback(async () => {
    try {
      publish(await listProfiles());
      setLoadErr(null);
    } catch (e) {
      // The rows are NOT cleared: a failed refresh after a successful action
      // must not blank a library that is still there. The error rides above the
      // list instead, and RETRY asks again.
      setLoadErr(errText(e, LOAD_FAILED));
    }
  }, [publish]);

  useEffect(() => { void refresh(); }, [refresh]);

  // ------------------------------------------------------------- activate
  //
  // The dialog is gated on what the activate DROPS, not on what the profile
  // attaches: every activate calls `hub._teardown()` first
  // (`hub.py::_connect_rigspec_unlocked`), so a simulator or assignments-only
  // profile used to silently tear down a live rig with no dialog at all. The
  // real-motion check survives as one of three escalations inside
  // `profileActivateConfirm`.
  const onActivate = async (row: ProfileRow) => {
    // Busy FIRST, then the round trip. `getProfile` is a network hop and the
    // dialog is a frame after it, so between the tap and any visible change the
    // card was fully live - on touch that gap is one double-tap wide, and the
    // second tap's reward was the server's raw "'profile' is already running".
    if (busyId === row.id) return;
    setBusyId(row.id);
    try {
      let full: Profile | null = null;
      try {
        // `getProfile` is typed `RedactedProfile` because a viewer or operator
        // receives a partial record. Every action here is `config.backend`-
        // gated, so a caller who reaches this line holds it and the server
        // sends the whole record. The cast asserts the capability the type
        // cannot see; it is not a shortcut.
        full = await getProfile(row.id) as Profile;
      } catch {
        /* fall back to the row's mode heuristic below */
      }
      const spec = profileActivateConfirm({
        name: row.name,
        connectsNothing: full ? profileConnectsNothing(full) : false,
        realMotion: full
          ? profileResolvesRealMotion(full)
          : row.mode !== "empty" && row.mode !== "alpaca",
        liveDevices: liveRoleCount(useStore.getState().status),
        sequenceRunning: (() => {
          const state = useStore.getState().sequence?.state;
          return state === "running" || state === "paused";
        })(),
      });
      if (spec && !(await confirmDialog(spec))) return;
      await activateAndWait(row, false);
    } finally {
      setBusyId(null);
      setConnectingId(null);
    }
  };

  /** POST the activate, then hold the row until the RIG answers.
   *
   *  The route `_spawn_connect`s: it returns `{"started": "profile"}` the
   *  instant the task exists, ~40 ms, with the teardown not yet finished. A
   *  re-list at that point runs against the previous rig's active pointer, so
   *  the panel went on naming the OLD profile as the one that boots next time
   *  while re-arming ACTIVATE on a rig that was mid-swap. */
  const activateAndWait = async (row: ProfileRow, force: boolean) => {
    try {
      await activateProfile(row.id, force);
    } catch (e) {
      await onActivateFailed(e, row, force);
      return;
    }
    setConnectingId(row.id);
    toast("info", activating(row.name));
    const landed = await waitForProfileActive(row.id, publish);
    if (landed) {
      toast("success", activated(row.name));
    } else {
      await refresh();
      toast("warning", notActiveYet(row.name));
    }
  };

  const onActivateFailed = async (e: unknown, row: ProfileRow, wasForced: boolean) => {
    if (isRunningConflict(e) && !wasForced) {
      if (await confirmDialog(FORCE_CONFIRM)) await activateAndWait(row, true);
      return;
    }
    // The UNCODED 409 is `_spawn_connect`'s own lane guard, raised before
    // `force` is even looked at - a force retry there would 409 again, and the
    // raw quoted lane name is not a sentence.
    if (isLaneConflict(e)) { toast("warning", LANE_BUSY); return; }
    toast("error", errText(e, ACTIVATE_FAILED));
  };

  // --------------------------------------------------------------- rename
  const onRename = async (row: ProfileRow, next: string) => {
    const name = next.trim();
    setRenamingId(null);
    if (name === "" || name === row.name) return;
    setBusyId(row.id);
    try {
      await renameProfile(row.id, name);
      await refresh();
    } catch (e) {
      toast("error", errText(e, RENAME_FAILED));
    } finally {
      setBusyId(null);
    }
  };

  // --------------------------------------------------------------- delete
  //
  // The one irreversible action on a row. The copy and the friction level come
  // from `lib/profileDelete.ts` so no future edit can quietly drop the "what is
  // NOT lost" sentence, and so deleting the ACTIVE profile keeps escalating to
  // a hold-confirm that says the boot consequence out loud.
  const onDelete = async (row: ProfileRow) => {
    if (busyId === row.id) { toast("warning", rowBusyToast(row.name)); return; }
    setBusyId(row.id);
    try {
      // Re-list BEFORE deciding how scary the dialog is. `row.active` is the
      // flag from the last render and can be a whole activation stale: the
      // pointer moves inside `connect_profile_id` only after the connect
      // succeeds, so a refresh routinely lands before it. Measured: activate B,
      // and the card still showed A as ACTIVE while `GET /api/profiles` already
      // reported B - so deleting B took the plain tap-confirm and said nothing
      // about boot.
      let fresh = row;
      try {
        const live = await listProfiles();
        publish(live);
        const mine = live.find((r) => r.id === row.id);
        if (!mine) { toast("warning", alreadyGone(row.name)); return; }
        fresh = mine;
      } catch {
        /* offline or a server hiccup: fall through on the last-known row. The
           dialog still names the profile and still states what is lost; only
           the active-profile escalation may be missed, and the server is the
           one that enforces anything real. */
      }
      if (!(await confirmDialog(profileDeleteConfirm(fresh)))) return;
      // Last line, and genuinely reachable: the dialog above is an await, and a
      // session can be downgraded while it is open. `profileDeleteLock` returns
      // the SENTENCE, so a refusal here can always say why.
      const late = profileDeleteLock(canConfig);
      if (late) { toast("error", late); return; }
      await deleteProfile(row.id);
      toast("success", deleted(row.name));
      await refresh();
    } catch (e) {
      // 404 means something already removed it: the action the user wanted has
      // happened, so report it truthfully rather than showing a scary failure
      // beside a row that is about to disappear.
      if (isGone(e)) toast("warning", alreadyGone(row.name));
      else toast("error", errText(e, DELETE_FAILED));
      // Re-list either way, so what is on screen matches the server.
      await refresh();
    } finally {
      setBusyId(null);
    }
  };

  // ------------------------------------------------------ update from rig
  //
  // Overwrite this profile's devices/backend with whatever is connected right
  // now, keeping its id and name so it stays the SAME row - no duplicate, no
  // orphaned active-profile pointer. No server route does this in one call, so
  // it is composed from three that do: capture the live rig under a scratch
  // name, copy its device data onto THIS profile, upsert (POST /api/profiles
  // honours an existing id as an in-place overwrite), then delete the scratch.
  //
  // The merge starts from the TARGET and pulls ONLY the four fields
  // `hub.capture_profile` actually populates, enumerated explicitly and never
  // spread: the capture leaves optics, providers, phd2_host, phd2_port and
  // nina_port unset, and a spread would silently null all five.
  const onUpdateFromRig = async (row: ProfileRow) => {
    if (busyId === row.id) { toast("warning", rowBusyToast(row.name)); return; }
    if (!(await confirmDialog(updateConfirm(row)))) return;
    setBusyId(row.id);
    let scratchId: string | null = null;
    try {
      const target = await getProfile(row.id) as Profile;
      const captured = await captureProfile(`__update_scratch__${row.id}`);
      scratchId = captured.id;
      const live = await getProfile(captured.id) as Profile;
      await saveProfile({
        ...target,
        devices: live.devices,
        primary_backend: live.primary_backend,
        nina_host: live.nina_host,
        site_name: live.site_name,
      });
      await deleteProfile(captured.id);
      scratchId = null;
      toast("success", updated(row.name));
      await refresh();
    } catch (e) {
      toast("error", e instanceof ApiError && e.status === 409
        ? UPDATE_NEEDS_RIG
        : errText(e, UPDATE_FAILED));
      if (scratchId) {
        try { await deleteProfile(scratchId); } catch {
          /* best-effort only - a leftover scratch row is visible in the list
             and deletable like any other */
        }
      }
    } finally {
      setBusyId(null);
    }
  };

  // ------------------------------------------------------- export / import
  //
  // Profiles have no server export route (unlike `GET /api/plans/{id}/export`),
  // so export is a client-side Blob download of the full record; import posts
  // straight to `POST /api/profiles`, which already refuses to trust a client
  // id for a NEW record (`app.py::save_profile`).
  const onExport = async (row: ProfileRow) => {
    try {
      const full = await getProfile(row.id);
      const filename = profileExportFilename(full.name);
      const blob = new Blob([JSON.stringify(full, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast("success", exported(filename));
    } catch (e) {
      toast("error", errText(e, EXPORT_FAILED));
    }
  };

  const onImportFile = async (file: File) => {
    try {
      await importProfile(parseProfileFile(await file.text()));
      toast("success", IMPORTED);
      await refresh();
    } catch (e) {
      toast("error", importFailed(e instanceof Error ? e.message : String(e)));
    }
  };

  // ------------------------------------------------------------- capture
  const onCapture = async () => {
    const name = captureName.trim() || DEFAULT_CAPTURE_NAME;
    setCapturing(true);
    try {
      await captureProfile(name);
      setCaptureName("");
      toast("success", `Saved the current rig as "${name}"`);
      await refresh();
    } catch (e) {
      // 409 when nothing is connected - the one failure with a next step.
      toast("error", e instanceof ApiError && e.status === 409
        ? CAPTURE_NEEDS_RIG
        : errText(e, CAPTURE_FAILED));
    } finally {
      setCapturing(false);
    }
  };

  return (
    <section className="nx-prof" data-testid="rig-profiles">
      <div className="nx-prof-head">
        <Label size={11}>{PROFILES_EYEBROW}</Label>
        <span className="nx-prof-headact">
          <ActionButton
            kind="ghost"
            glyph={<UploadGlyph size={14} />}
            lockedReason={lockedReason}
            onExplain={onExplain}
            onPress={() => fileRef.current?.click()}
            data-testid="profiles-import"
          >
            {IMPORT_LABEL}
          </ActionButton>
          <ActionButton
            kind="ghost"
            glyph={<NxIcon name="refresh" size={14} />}
            onPress={() => { void refresh(); }}
            data-testid="profiles-refresh"
          >
            {REFRESH_LABEL}
          </ActionButton>
        </span>
      </div>

      {/* The picker itself. `hidden` rather than a class, and never `disabled`:
          it is not a control anyone presses - IMPORT above is, and it carries
          the lock. */}
      <input
        ref={fileRef}
        type="file"
        accept=".json,application/json"
        hidden
        aria-label="Import a profile file"
        data-testid="profiles-file"
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) void onImportFile(f);
        }}
      />

      <LockNote reason={lockedReason} data-testid="profiles-locknote" />

      {loadErr ? (
        <EmptyCard
          title={LOAD_FAILED}
          hint={loadErr}
          action={
            <ActionButton kind="secondary" onPress={() => { void refresh(); }}
              data-testid="profiles-retry">
              {RETRY_LABEL}
            </ActionButton>
          }
          data-testid="profiles-error"
        />
      ) : rows == null ? (
        <Card data-testid="profiles-loading"><Mono>{LOADING}</Mono></Card>
      ) : rows.length === 0 ? (
        <EmptyCard title={EMPTY_TITLE} hint={EMPTY_HINT} data-testid="profiles-empty" />
      ) : (
        <div className="nx-prof-list" data-testid="profiles-list">
          {rows.map((row) => (
            <ProfileCard
              key={row.id}
              row={row}
              busy={busyId === row.id}
              connecting={connectingId === row.id}
              otherConnecting={connectingId !== null && connectingId !== row.id}
              renaming={renamingId === row.id}
              onActivate={() => { void onActivate(row); }}
              onStartRename={() => setRenamingId(row.id)}
              onCancelRename={() => setRenamingId(null)}
              onCommitRename={(next) => { void onRename(row, next); }}
              onUpdateFromRig={() => { void onUpdateFromRig(row); }}
              onExport={() => { void onExport(row); }}
              onDelete={() => { void onDelete(row); }}
              lockedReason={lockedReason}
              onExplain={onExplain}
            />
          ))}
        </div>
      )}

      <Card className="nx-prof-save" data-testid="profiles-save-card">
        <Label size={11}>{SAVE_EYEBROW}</Label>
        <p className="nx-prof-note">{SAVE_BLURB}</p>
        <div className="nx-prof-saverow">
          <Field label={NAME_LABEL} className="nx-prof-savefield">
            <TextInput
              value={captureName}
              onChange={setCaptureName}
              onEnter={() => { if (!lockedReason) void onCapture(); }}
              placeholder={NAME_PLACEHOLDER}
              ariaLabel={NAME_LABEL}
              lockedReason={lockedReason}
              data-testid="profile-save-input"
            />
          </Field>
          <ActionButton
            kind="primary"
            busy={capturing}
            lockedReason={lockedReason}
            onExplain={onExplain}
            onPress={() => { void onCapture(); }}
            data-testid="profile-save-current"
          >
            {capturing ? SAVE_BUSY : SAVE_BUTTON}
          </ActionButton>
        </div>
      </Card>
    </section>
  );
}
