// ProfileList.tsx — profile management (W1.6). Lists saved profiles, shows which
// is ACTIVE (and that the active one auto-connects on boot), and offers:
//   Activate  → POST /api/profiles/{id}/activate (sets active AND connects; the
//               per-role result streams back over WS into backend_links). Danger
//               hold-confirm when the profile resolves a real mount/focuser.
//   Rename    → PATCH /api/profiles/{id}
//   Delete    → DELETE /api/profiles/{id} (hold-confirm)
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
} from "../../api/backends";
import { ApiError } from "../../api";
import { useStore } from "../../store";
import { confirmDialog } from "../ConfirmDialog";
import { Panel, Led, HoldButton, EmptyState, Field } from "../ui";
import { Icon } from "../icons";

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
  const [rows, setRows] = useState<ProfileRow[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [captureName, setCaptureName] = useState("");
  const [capturing, setCapturing] = useState(false);

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

  const onDelete = async (row: ProfileRow) => {
    setBusyId(row.id);
    try {
      await deleteProfile(row.id);
      showToast("success", `Deleted "${row.name}"`);
      await refresh();
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "delete failed");
    } finally {
      setBusyId(null);
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
          <button
            type="button"
            className="btn !py-1 !px-2 text-[10px]"
            onClick={refresh}
            title="Refresh profile list"
          >
            <Icon name="refresh" size={12} className="inline -mt-0.5 mr-1" />
            Refresh
          </button>
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
                onDelete={() => onDelete(row)}
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
  onDelete,
}: {
  row: ProfileRow;
  busy: boolean;
  renaming: boolean;
  onActivate: () => void;
  onStartRename: () => void;
  onCancelRename: () => void;
  onCommitRename: (next: string) => void;
  onDelete: () => void;
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

      <div className="flex items-center gap-1.5">
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
          className="btn btn-touch !py-1 !px-2 text-[11px]"
          disabled={busy || renaming}
          onClick={onStartRename}
          aria-label={`Rename ${row.name}`}
          title="Rename"
        >
          Rename
        </button>
        {/* Delete is irreversible → hold-to-confirm (the danger primitive). */}
        <HoldButton label={`Delete ${row.name}`} onConfirm={onDelete}>
          {(bind) => (
            <button
              type="button"
              className="btn btn-danger btn-touch !py-1 !px-2 text-[11px] relative overflow-hidden select-none"
              style={{ touchAction: "none" }}
              disabled={busy}
              aria-label={bind["aria-label"]}
              title="Hold to delete"
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
              <span className="relative inline-flex items-center">
                <Icon name="x" size={12} />
              </span>
            </button>
          )}
        </HoldButton>
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
