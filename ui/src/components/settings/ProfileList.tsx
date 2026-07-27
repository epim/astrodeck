// ProfileList.tsx — profile management (W1.6). Lists saved profiles, shows which
// is ACTIVE (and that the active one auto-connects on boot), and offers:
//   Activate  → POST /api/profiles/{id}/activate (sets active AND connects; the
//               per-role result streams back over WS into backend_links). Danger
//               hold-confirm when the profile resolves a real mount/focuser.
//   Rename    → PATCH /api/profiles/{id}
//   Update    → "Update from current rig" (F7 #5a) — overwrites the profile's
//               devices/backend with whatever's currently connected. No server
//               endpoint does this directly, so it's composed from three
//               existing calls (capture the live rig under a scratch name, copy
//               its device data onto THIS profile's id/name, upsert, then
//               delete the scratch record) — see onUpdateFromRig below.
//   Delete    → DELETE /api/profiles/{id}. Irreversible, so it goes through
//               the app's confirmDialog (danger tone, Cancel focused, the
//               affirmative never the default) with copy that states what is
//               and is NOT lost — see lib/profileDelete.ts, where that
//               judgement lives and is tested. Deleting the ACTIVE profile is
//               allowed but escalates to a HOLD-confirm and says the boot
//               consequence out loud.
//   Export/Import (F7 #5b) → client-side JSON download / file-picker upload,
//               PlanLibraryPanel precedent (commit de2839a). Profiles have no
//               server export route, so export is a Blob download of the full
//               Profile; import posts straight to POST /api/profiles, which
//               already refuses to trust a client id for a NEW record
//               (app.py::save_profile).
//   Save current rig as profile → POST /api/profiles/capture (409 when no rig).
//
// Activation is async on the server ({started} immediately); we re-list to flip the
// active flag and rely on backend_links (the tri-state grid) for the live outcome.

import { useEffect, useRef, useState, type JSX } from "react";
import type { Profile, ProfileRow } from "../../types";
import {
  listProfiles,
  getProfile,
  captureProfile,
  renameProfile,
  deleteProfile,
  activateProfile,
  saveProfile,
  importProfile,
} from "../../api/backends";
import { ApiError } from "../../api";
import { useStore } from "../../store";
import { confirmDialog } from "../ConfirmDialog";
import { Panel, Led, HoldButton, EmptyState, Field, LockedChip } from "../ui";
import { Icon } from "../icons";
import { parseProfileFile, profileExportFilename } from "../../lib/profileFile";
import { profileDeleteConfirm, profileDeleteLock } from "../../lib/profileDelete";
import { useCanConfigBackend } from "../../lib/caps";

const MODE_LABEL: Record<ProfileRow["mode"], string> = {
  alpaca: "Native / Alpaca",
  nina: "NINA bridge",
  mixed: "Mixed backends",
  empty: "Empty",
};

// Does a full profile resolve a real (non-sim) mount or focuser? Used to gate the
// activation hold-confirm — activating could attach + command real hardware.
// Intent (explicit, no dead clauses): prompt when EITHER an explicit telescope/
// focuser device row points at a non-sim backend, OR a NINA-host-only legacy
// profile (real rig), OR a non-sim primary with no explicit motion rows — because
// the server may resolve a real mount/focuser from that primary. The last case is
// a deliberate over-prompt (we can't know what the primary resolves without
// connecting); we fail SAFE toward asking.
function resolvesRealMotion(p: Profile): boolean {
  const MOTION = ["telescope", "focuser"];
  // An explicit, non-sim mount/focuser device row.
  const deviceMotion = p.devices.some(
    (d) => MOTION.includes(d.role) && d.backend !== "sim",
  );
  // A nina_host-only legacy profile is a real rig too.
  const ninaRig = !!p.nina_host && p.devices.length === 0;
  // A non-sim primary with NO explicit motion rows could still resolve a real
  // mount/focuser server-side, so we prompt to be safe.
  const primaryMayResolveMotion =
    p.primary_backend !== "sim" &&
    !p.devices.some((d) => MOTION.includes(d.role));
  return deviceMotion || ninaRig || primaryMayResolveMotion;
}

export default function ProfileList(): JSX.Element {
  const showToast = useStore((s) => s.showToast);
  // Every write on this panel is CAP_CONFIG_BACKEND server-side. SettingsView
  // already hides the whole panel from a principal without it, so this is
  // defence in depth — but it is also what keeps Delete honest if the panel is
  // ever mounted elsewhere, or a session is downgraded while it is open.
  const canConfig = useCanConfigBackend();
  const [rows, setRows] = useState<ProfileRow[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [captureName, setCaptureName] = useState("");
  const [capturing, setCapturing] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = async () => {
    try {
      setRows(await listProfiles());
      setLoadErr(null);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const onActivate = async (row: ProfileRow) => {
    // Pull the full profile to decide whether the connect is destructive.
    let real = row.mode !== "empty" && row.mode !== "alpaca" ? true : false;
    try {
      const full = await getProfile(row.id);
      real = resolvesRealMotion(full);
    } catch {
      /* fall back to the mode heuristic above */
    }
    if (real) {
      const ok = await confirmDialog({
        title: `Activate "${row.name}"?`,
        body: "This profile drives a real mount or focuser. Activating sets it as the boot rig and connects now — the hardware will attach and may move. Hold to confirm.",
        mode: "hold",
        tone: "danger",
        confirmLabel: "Activate & connect",
      });
      if (!ok) return;
    }
    setBusyId(row.id);
    try {
      await activateProfile(row.id);
      showToast("success", `Activating "${row.name}" — connecting…`);
      await refresh();
    } catch (e) {
      if (e instanceof ApiError && e.code === "running") {
        // A run/connect is in flight; offer a forced activate.
        const ok = await confirmDialog({
          title: "Rig is busy",
          body: "A connect or sequence is already running. Force-activate this profile anyway?",
          mode: "confirm",
          tone: "danger",
          confirmLabel: "Force activate",
        });
        if (ok) {
          try {
            await activateProfile(row.id, true);
            showToast("success", `Force-activating "${row.name}"…`);
            await refresh();
          } catch (e2) {
            showToast("error", e2 instanceof Error ? e2.message : "activate failed");
          }
        }
      } else {
        showToast("error", e instanceof Error ? e.message : "activate failed");
      }
    } finally {
      setBusyId(null);
    }
  };

  // Rename uses an in-card themed input (no OS window.prompt, which breaks
  // night-mode + the dimmer). onRename commits the new name; the card owns the
  // input state and calls this on Enter/blur.
  const onRename = async (row: ProfileRow, next: string) => {
    const name = next.trim();
    setRenamingId(null);
    if (name === "" || name === row.name) return;
    setBusyId(row.id);
    try {
      await renameProfile(row.id, name);
      await refresh();
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "rename failed");
    } finally {
      setBusyId(null);
    }
  };

  // Delete — the only irreversible action on the row. Same shape as onActivate
  // (await confirmDialog, bail on anything but an explicit yes), but the copy
  // and the friction level come from lib/profileDelete so they can be tested
  // and so no future edit can quietly drop the "what is NOT lost" sentence.
  //
  // Dismissal can never delete: ConfirmHost focuses Cancel, and Escape /
  // backdrop / Cancel all resolve false — we only proceed on `ok === true`.
  const onDelete = async (row: ProfileRow) => {
    if (busyId === row.id) {
      // aria-disabled, not `disabled` — so the tap still lands and still gets
      // an answer instead of silently doing nothing.
      showToast("warning", `"${row.name}" is busy — wait for the current action to finish`);
      return;
    }
    const locked = profileDeleteLock(canConfig);
    if (locked) {
      // Unreachable while SettingsView gates this whole panel on config.backend,
      // but a token can be downgraded mid-session, and this component must not
      // depend on a caller to stay honest. Say the reason; never fire the call.
      showToast("error", locked);
      return;
    }
    // Re-list BEFORE deciding how scary this dialog is. `row.active` is the
    // flag from the last render, and it can be a whole activation stale:
    // activation is async server-side ({started} returns immediately, the
    // active pointer is set inside connect_profile_id only after the connect
    // succeeds), so onActivate's follow-up refresh routinely re-lists before
    // the pointer has moved. MEASURED: activate profile B, and the card still
    // shows profile A as ACTIVE while GET /api/profiles already reports B —
    // so deleting B took the plain tap-confirm and said nothing about boot.
    // Same instinct as onActivate pulling the full profile before it decides:
    // on the one irreversible action, ask the server, don't trust the render.
    let fresh = row;
    try {
      const live = await listProfiles();
      setRows(live);
      const mine = live.find((r) => r.id === row.id);
      if (!mine) {
        showToast("warning", `"${row.name}" is already gone — list refreshed`);
        return;
      }
      fresh = mine;
    } catch {
      // Offline / server hiccup: fall through on the last-known row rather
      // than blocking the delete. The dialog still names the profile and
      // still states what is lost; only the active-profile escalation may be
      // missed, and the server is the one that enforces anything real.
    }
    const ok = await confirmDialog(profileDeleteConfirm(fresh));
    if (!ok) return;
    setBusyId(row.id);
    try {
      await deleteProfile(row.id);
      showToast("success", `Deleted "${row.name}" — the profile only; the rig is untouched`);
      await refresh();
    } catch (e) {
      // 404 = someone/something already removed it. The action the user wanted
      // has happened, so report it truthfully and still re-list, rather than
      // showing a scary failure beside a row that is about to disappear.
      if (e instanceof ApiError && e.status === 404) {
        showToast("warning", `"${row.name}" was already gone — list refreshed`);
        await refresh();
      } else {
        showToast("error", e instanceof Error ? e.message : "delete failed");
        // The list may or may not still contain the row; re-list so what is on
        // screen matches the server either way (F7 #5: never leave a stale row).
        await refresh();
      }
    } finally {
      setBusyId(null);
    }
  };

  // "Update from current rig" (F7 #5a) — the minimum-viable EDIT affordance:
  // overwrite this profile's devices/backend with whatever's connected right
  // now, keeping its id/name (so it stays the SAME row — no duplicate, no
  // orphaned active-profile pointer). No server route does this in one call,
  // so it's composed from three that do: capture the live rig under a scratch
  // name (server mints a throwaway id), copy its device data onto THIS
  // profile, upsert (POST /api/profiles honors the existing id as an in-place
  // overwrite — app.py::save_profile), then delete the scratch record. On any
  // failure after the capture, best-effort clean up the scratch row rather
  // than leaving it behind.
  //
  // The merge starts from the TARGET profile and pulls ONLY the fields
  // hub.capture_profile actually populates (hub.py:1180-1207: devices,
  // primary_backend, nina_host, site_name) — enumerated explicitly, never a
  // spread from the capture, so the target's optics / providers / phd2_host /
  // phd2_port / nina_port survive (the capture leaves all five unset, and a
  // spread would silently null them; nina_port in particular is only ever the
  // model default 1888 on a capture, never the live rig's real port). A
  // future capture_profile change can't widen this overwrite without an
  // explicit edit here.
  const onUpdateFromRig = async (row: ProfileRow) => {
    setBusyId(row.id);
    let scratchId: string | null = null;
    try {
      const target = await getProfile(row.id);
      const captured = await captureProfile(`__update_scratch__${row.id}`);
      scratchId = captured.id;
      const fresh = await getProfile(captured.id);
      const merged: Profile = {
        ...target,
        devices: fresh.devices,
        primary_backend: fresh.primary_backend,
        nina_host: fresh.nina_host,
        site_name: fresh.site_name,
      };
      await saveProfile(merged);
      await deleteProfile(captured.id);
      scratchId = null;
      showToast("success", `Updated "${row.name}" from the current rig`);
      await refresh();
    } catch (e) {
      const msg =
        e instanceof ApiError && e.status === 409
          ? "Connect a rig first, then update the profile from it."
          : e instanceof Error
            ? e.message
            : "update failed";
      showToast("error", msg);
      if (scratchId) {
        try {
          await deleteProfile(scratchId);
        } catch {
          /* best-effort cleanup only — a leftover scratch row is harmless
             (visible in the list, deletable like any other) */
        }
      }
    } finally {
      setBusyId(null);
    }
  };

  // Export (F7 #5b): no server route does this for profiles (unlike
  // /api/plans/{id}/export), so fetch the full record and download it as a
  // Blob — PlanLibraryPanel's confirmation-toast idiom (R2-PLN-03) carries over.
  const exportRow = async (row: ProfileRow) => {
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
      showToast("success", `Exported ${filename}`);
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "export failed");
    }
  };

  const importFile = async (file: File) => {
    try {
      const raw = parseProfileFile(await file.text());
      await importProfile(raw);
      showToast("success", "Profile imported");
      await refresh();
    } catch (e) {
      showToast("error", `Import failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const onCapture = async () => {
    const name = captureName.trim() || "Captured rig";
    setCapturing(true);
    try {
      await captureProfile(name);
      setCaptureName("");
      showToast("success", `Saved current rig as "${name}"`);
      await refresh();
    } catch (e) {
      // 409 / "connect a rig first" when nothing is connected.
      const msg =
        e instanceof ApiError && e.status === 409
          ? "Connect a rig first, then save it as a profile."
          : e instanceof Error
            ? e.message
            : "capture failed";
      showToast("error", msg);
    } finally {
      setCapturing(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <Panel
        title="Profiles"
        right={
          <div className="inline-flex items-center gap-1.5">
            <button
              type="button"
              className="btn !py-1 !px-2 text-[10px]"
              onClick={() => fileRef.current?.click()}
              title="Import a profile from file"
            >
              <Icon name="upload" size={12} className="inline -mt-0.5 mr-1" />
              Import
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              aria-label="Import a profile file"
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f) void importFile(f);
              }}
            />
            <button
              type="button"
              className="btn !py-1 !px-2 text-[10px]"
              onClick={refresh}
              title="Refresh profile list"
            >
              <Icon name="refresh" size={12} className="inline -mt-0.5 mr-1" />
              Refresh
            </button>
          </div>
        }
      >
        {loadErr && (
          <EmptyState icon="alert" title="Couldn't load profiles" hint={loadErr} />
        )}
        {!loadErr && rows == null && <p className="text-dim text-xs">Loading profiles…</p>}
        {!loadErr && rows != null && rows.length === 0 && (
          <EmptyState
            icon="rig"
            title="No profiles yet"
            hint="Connect a rig from the picker, then save it below — or activate one here to auto-connect it on boot."
          />
        )}
        {!loadErr && rows != null && rows.length > 0 && (
          <div className="flex flex-col gap-2">
            {rows.map((row) => (
              <ProfileCard
                key={row.id}
                row={row}
                busy={busyId === row.id}
                renaming={renamingId === row.id}
                onActivate={() => onActivate(row)}
                onStartRename={() => setRenamingId(row.id)}
                onCancelRename={() => setRenamingId(null)}
                onCommitRename={(next) => onRename(row, next)}
                onUpdateFromRig={() => onUpdateFromRig(row)}
                onExport={() => exportRow(row)}
                onDelete={() => onDelete(row)}
                deleteLock={profileDeleteLock(canConfig)}
              />
            ))}
          </div>
        )}
      </Panel>

      {/* Save current rig as a profile (capture). */}
      <Panel title="Save Current Rig">
        <p className="text-[11px] text-dim mb-3 leading-relaxed max-w-xl">
          Snapshot the rig you're connected to right now into a reusable profile.
          Activate it later to connect the same set of devices — and have it
          auto-connect on boot.
        </p>
        <div className="grid grid-cols-[1fr_auto] gap-2 items-end max-w-md">
          <Field label="Profile name">
            <input
              className="field"
              placeholder="Backyard SCT"
              value={captureName}
              onChange={(e) => setCaptureName(e.target.value)}
            />
          </Field>
          <button
            type="button"
            className="btn btn-accent min-h-11"
            disabled={capturing}
            onClick={onCapture}
          >
            {capturing ? "Saving…" : "Save Rig"}
          </button>
        </div>
      </Panel>
    </div>
  );
}

function ProfileCard({
  row,
  busy,
  renaming,
  onActivate,
  onStartRename,
  onCancelRename,
  onCommitRename,
  onUpdateFromRig,
  onExport,
  onDelete,
  deleteLock,
}: {
  row: ProfileRow;
  busy: boolean;
  renaming: boolean;
  onActivate: () => void;
  onStartRename: () => void;
  onCancelRename: () => void;
  onCommitRename: (next: string) => void;
  onUpdateFromRig: () => void;
  onExport: () => void;
  onDelete: () => void;
  /** Why this principal cannot delete, or null when they can (lib/profileDelete). */
  deleteLock: string | null;
}): JSX.Element {
  return (
    <div
      className={`border bg-bg/60 px-3 py-2.5 flex items-center gap-3 flex-wrap
        ${row.active ? "border-accent" : "border-line"}`}
    >
      <Led state={row.active ? "on" : "off"} label={row.active ? "Active profile" : undefined} />
      <div className="min-w-0 flex-1">
        {renaming ? (
          <RenameField
            initial={row.name}
            onCommit={onCommitRename}
            onCancel={onCancelRename}
          />
        ) : (
          <>
            <div className="flex items-center gap-2">
              <span className="font-sans text-sm text-ink truncate">{row.name}</span>
              {row.active && (
                <span className="mono text-[9px] tracking-[0.18em] uppercase text-accent border border-accent/50 px-1.5 py-0.5">
                  Active
                </span>
              )}
            </div>
            <div className="text-[10px] text-dim truncate">
              {MODE_LABEL[row.mode]} · {row.devices_count} device{row.devices_count === 1 ? "" : "s"}
              {row.site_name ? ` · ${row.site_name}` : ""}
              {row.active && <span className="text-accent"> · auto-connects on boot</span>}
            </div>
          </>
        )}
      </div>

      {/* Normalized action row (F7 #5c): every button shares the same height
          (btn + !py-1) and horizontal padding (!px-3) so Activate/Rename/
          Update/Delete read as one consistent set instead of varying weights
          — Delete in particular now carries an icon + label like its
          siblings, not a bare icon-only X. */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <button
          type="button"
          className={`btn !py-1 !px-3 text-[11px] ${row.active ? "" : "btn-accent"}`}
          disabled={busy}
          onClick={onActivate}
          title={row.active ? "Reconnect this profile" : "Set active and connect"}
        >
          <Icon name="play" size={12} className="inline -mt-0.5 mr-1" />
          {row.active ? "Reconnect" : "Activate"}
        </button>
        <button
          type="button"
          className="btn btn-touch !py-1 !px-3 text-[11px]"
          disabled={busy || renaming}
          onClick={onStartRename}
          aria-label={`Rename ${row.name}`}
          title="Rename"
        >
          Rename
        </button>
        {/* "Update from current rig" (F7 #5a) — the edit affordance: overwrite
            this profile's stored devices with whatever's connected now. It
            destroys stored state (the profile's device intent), so it gets the
            SAME hold-to-confirm friction as Delete below — not a lighter
            single-click confirm. */}
        <HoldButton
          label={`Update ${row.name} from the current rig`}
          onConfirm={onUpdateFromRig}
        >
          {(bind) => (
            <button
              type="button"
              className="btn btn-touch !py-1 !px-3 text-[11px] relative overflow-hidden select-none"
              style={{ touchAction: "none" }}
              disabled={busy}
              aria-label={bind["aria-label"]}
              title="Hold to overwrite this profile's devices with the currently connected rig"
              onPointerDown={bind.onPointerDown}
              onPointerUp={bind.onPointerUp}
              onPointerCancel={bind.onPointerUp}
              onKeyDown={bind.onKeyDown}
              onKeyUp={bind.onKeyUp}
            >
              <span
                aria-hidden
                className="absolute inset-y-0 left-0 pointer-events-none"
                style={{
                  width: `${Math.round(bind.progress * 100)}%`,
                  background: "color-mix(in srgb, var(--text) 60%, transparent)",
                  transition: "width 80ms linear",
                }}
              />
              <span className="relative inline-flex items-center gap-1">
                <Icon name="refresh" size={12} />
                Update
              </span>
            </button>
          )}
        </HoldButton>
        {/* Export (F7 #5b) — client-side JSON download, icon-only (same height
            as its siblings via btn + !py-1; standard download iconography). */}
        <button
          type="button"
          className="btn btn-touch !py-1 !px-2 text-[11px]"
          disabled={busy}
          onClick={onExport}
          aria-label={`Export ${row.name}`}
          title="Export profile to file"
        >
          <Icon name="download" size={12} />
        </button>
        {/* ------------------------------------------------------------ DELETE
            The one irreversible action on a row of otherwise-safe ones, on a
            phone, in the dark, possibly with gloves. Three deliberate choices:

            1. PLACEMENT. `ml-auto` pushes it to the far right of whichever
               line it wraps onto, and a divider separates it from the safe
               set. Before this it sat second-from-left on the wrapped second
               line — measured at x=96 on a 390px phone, i.e. directly under a
               right thumb, 6px from Export. Now it is the furthest control
               from the safe group instead of embedded in it.

            2. WEIGHT. It is no longer `btn-danger`. A red-outlined, red-glowing
               control was the loudest thing in the card and pulled both eye and
               thumb toward the one action you cannot undo — and in night mode,
               where the palette collapses toward red, that outline stops
               distinguishing anything at all. Quiet chrome at rest; the danger
               tone appears in the confirm dialog, where it means something.
               Meaning is never carried by colour here: trash glyph + the word
               DELETE (house rule: glyph + word).

            3. FRICTION. A single tap opens the confirm — it does NOT delete.
               This replaces a hold-to-confirm that, when tapped, did nothing
               whatsoever and explained nothing (its only cue was `title="Hold
               to delete"`, which never fires on touch — measured: tap, no
               dialog, no toast, list unchanged). Silent inertia is worse than
               a dialog: the user cannot tell a blocked action from a broken
               one. The friction now lives where it can also explain itself.

            The 44px floor is kept by `btn-touch` (+ the coarse-pointer .btn
            rule); nothing here shrinks the target. */}
        <div className="ml-auto flex items-center pl-2 border-l border-line/60">
          {deleteLock ? (
            // No config.backend: dim + aria-disabled + the stated reason, via
            // the house primitive. NEVER the native `disabled` attribute — that
            // strips the element from the a11y tree along with the reason
            // (house rule §11.8), and a bare grey box on touch is exactly the
            // dead end this is meant to prevent. LockedChip's reason is
            // reachable by tap, by keyboard and by screen reader.
            <LockedChip reason={deleteLock} className="text-[11px]">
              <span className="inline-flex items-center gap-1">
                <Icon name="trash" size={12} />
                Delete
              </span>
            </LockedChip>
          ) : (
            <button
              type="button"
              className={`btn btn-touch !py-1 !px-3 text-[11px] ${busy ? "opacity-50" : ""}`}
              // INLINE, not a `text-dim` utility: `.btn { color: var(--text) }`
              // in index.css is UNLAYERED, so it beats any Tailwind colour
              // utility regardless of class order (the tooltip-portal war
              // story) — a `text-dim` class here would silently do nothing.
              // The recession is deliberate: this is the one control on the
              // row that must not attract a thumb. Measured on the card
              // background: 7.53:1 day / 6.59:1 night, against 15.56:1 for its
              // siblings — visibly quieter, still far past AA, so "recessive"
              // never becomes "hard to read at 2am".
              style={{ color: "var(--text-dim)" }}
              // Busy is transient (an op is in flight on THIS row) and is not a
              // permission — so it gets aria-disabled, not `disabled`, and the
              // handler states the reason if a finger lands on it anyway.
              aria-disabled={busy || undefined}
              onClick={onDelete}
              aria-label={`Delete ${row.name}`}
              title="Delete this profile"
            >
              <span className="inline-flex items-center gap-1">
                <Icon name="trash" size={12} />
                Delete
              </span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// Inline, fully-themed rename affordance (replaces the OS window.prompt that broke
// night-mode + the dimmer). Commits on Enter or the check button; cancels on Escape
// or the x button. Blur commits too, so tapping away on touch saves rather than
// silently dropping the edit.
function RenameField({
  initial,
  onCommit,
  onCancel,
}: {
  initial: string;
  onCommit: (next: string) => void;
  onCancel: () => void;
}): JSX.Element {
  const [value, setValue] = useState(initial);
  const inputRef = useRef<HTMLInputElement>(null);
  const committed = useRef(false);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const commit = () => {
    if (committed.current) return;
    committed.current = true;
    onCommit(value);
  };
  const cancel = () => {
    if (committed.current) return;
    committed.current = true;
    onCancel();
  };

  return (
    <div className="flex items-center gap-1.5">
      <input
        ref={inputRef}
        className="field !py-1 text-sm flex-1 min-w-0"
        value={value}
        aria-label="Profile name"
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          } else if (e.key === "Escape") {
            e.preventDefault();
            cancel();
          }
        }}
      />
      <button
        type="button"
        className="btn btn-accent btn-touch !py-1 !px-2 text-[11px]"
        // onMouseDown so the click lands before the input's onBlur fires.
        onMouseDown={(e) => {
          e.preventDefault();
          commit();
        }}
        aria-label="Save name"
        title="Save"
      >
        <Icon name="check" size={12} />
      </button>
      <button
        type="button"
        className="btn btn-touch !py-1 !px-2 text-[11px]"
        onMouseDown={(e) => {
          e.preventDefault();
          cancel();
        }}
        aria-label="Cancel rename"
        title="Cancel"
      >
        <Icon name="x" size={12} />
      </button>
    </div>
  );
}
