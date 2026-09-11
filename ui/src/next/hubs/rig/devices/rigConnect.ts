// rigConnect.ts - the ONE connect / simulator / disconnect implementation for
// this hub (plan hub-rig.md A.7, lifted from `views/EquipmentView.tsx:346-493`).
//
// It lives in its own module because TWO surfaces drive it: the FIRST NIGHT
// card's RUN THE SIMULATOR on the devices screen, and the footer of the ADD A
// DEVICE sheet. The shipped view learned the hard way what happens when the sim
// shortcut takes a different path from Connect Rig - the "sim-connect desync"
// fixed three times over (`lib/equipment.ts` simAssignments doc). The legacy
// `POST /api/connect/sim` is deliberately NOT used here: it returns a shape the
// assignment map cannot read, and it leaves `backend_links` empty, so the device
// rows and the per-role reasons on the screen you just pressed would have no
// data at all. Both buttons compile an `AssignmentMap` and post the SAME
// `/api/connect/rig` RigSpec.
//
// Everything that talks to the user is here too - the hold-confirm before a
// real mount attaches, the simulator swap confirm, and the two disconnect
// bodies - because those sentences are the difference between an informed tap
// and a 10-device rig disappearing (measured, review S1).

import { api, ApiError } from "../../../../api";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import { activateProfile, connectRig, getProfile } from "../../../../api/backends";
import { waitForProfileActive } from "../profiles/profileActive";
import {
  buildRigSpec, hasRealMotion, profileActivateConfirm, profileConnectsNothing,
  profileResolvesRealMotion, saveAssignments, simAssignments,
  type AssignmentMap,
} from "../../../../lib/equipment";
import { useStore } from "../../../../store";
import type {
  ConnectRigResult, DriverInfo, Profile, ProfileRow, RoleResult,
} from "../../../../types";

/** Busy is NAMED, not boolean (EquipmentView:192-195): the in-progress label has
 *  to land on the button that was actually pressed, and with a bare flag it
 *  always landed on CONNECT. */
export type BusyWhat =
  | null | "connect" | "scan" | "disconnect" | "save" | "load" | "activate";

export interface ConnectHooks {
  setBusy(what: BusyWhat): void;
  /** Per-role outcomes, for the inline error under each assignment row. */
  onResults?(results: Record<string, RoleResult>): void;
  /** What is RUNNING now - built from the results, never from what was asked
   *  for, so a partial connect leaves the refused role visibly pending. */
  onLanded?(landed: AssignmentMap | null): void;
  /** Re-read the driver list: a failed connect is also cache news. */
  reloadDrivers?(): void | Promise<void>;
}

const toast = (level: string, message: string, opts?: { verbatim?: boolean }) =>
  useStore.getState().showToast(level, message, opts);

/** Is a sequence holding the rig right now? Read at the moment of asking, not
 *  from a render-time prop, because the dialog it feeds is the last word before
 *  a teardown aborts that sequence. */
function sequenceRunning(): boolean {
  const s = useStore.getState().sequence?.state;
  return s === "running" || s === "paused";
}

/**
 * Compile `map` and connect it. The hold-confirm fires only when the map puts a
 * REAL (non-sim) driver on a motion role - connecting one commands the hardware
 * to attach, and it may move.
 */
export async function connectAssignments(
  map: AssignmentMap,
  drivers: DriverInfo[],
  hooks: ConnectHooks,
): Promise<void> {
  if (hasRealMotion(map, drivers)) {
    const ok = await confirmDialog({
      title: "Connect this rig?",
      body: "This rig includes a real mount or focuser. Connecting will command the hardware to attach and may move it. Hold to confirm.",
      mode: "hold",
      tone: "danger",
      confirmLabel: "Connect rig",
    });
    if (!ok) return;
  }
  hooks.setBusy("connect");
  hooks.onResults?.({});
  try {
    const res: ConnectRigResult = await connectRig(buildRigSpec(map));
    const resultMap: Record<string, RoleResult> = {};
    for (const r of res.results) resultMap[r.role] = r;
    hooks.onResults?.(resultMap);

    // Built from the RESULTS. Recording the whole map as connected made a
    // partial connect read "Rig connected", so the only way to retry the one
    // role that failed was to spoil a row and put it back.
    const landed: AssignmentMap = {};
    for (const [role, a] of Object.entries(map)) landed[role] = resultMap[role]?.ok ? a : null;
    hooks.onLanded?.(landed);

    const okCount = res.results.filter((r) => r.ok).length;
    const attempted = res.results.filter((r) => r.attempted).length;
    if (okCount === 0 && attempted > 0) {
      toast("error", "Rig connect attempted but no roles came up - see per-row errors");
      void hooks.reloadDrivers?.();
    } else {
      toast("success", `Rig connected - ${okCount}/${attempted} roles up`);
      if (okCount < attempted) void hooks.reloadDrivers?.();
    }
  } catch (e) {
    const msg = e instanceof ApiError
      ? (e.status === 422 ? `Invalid rig: ${e.message}` : e.message)
      : e instanceof Error ? e.message : "connect failed";
    toast("error", msg, { verbatim: true });
    void hooks.reloadDrivers?.();
  } finally {
    hooks.setBusy(null);
  }
}

/**
 * The one-tap simulator rig. It builds the same AssignmentMap a user would
 * assemble by hand and drives it through `connectAssignments`, so the device
 * rows, the assignment table and the per-role link states all read one rig.
 *
 * The confirm fires only when real devices are live: `connectAssignments`'
 * own hold is about MOTION, and a simulator map contains none - so without this
 * a single tap could replace a tracking mount with a fake and say nothing. A
 * whole polar-alignment session was spent against a simulator nobody had
 * noticed was selected (2026-08-02).
 */
export async function runSimulatorRig(
  roles: string[],
  drivers: DriverInfo[],
  liveDevices: number,
  hooks: ConnectHooks & { onAssign(map: AssignmentMap): void },
): Promise<void> {
  if (liveDevices > 0) {
    const ok = await confirmDialog({
      title: "Replace the connected rig with simulators?",
      body: `${liveDevices} role${liveDevices === 1 ? " is" : "s are"} connected to real `
        + "hardware. This disconnects them and connects simulated devices instead "
        + "- nothing you do afterwards reaches the sky.",
      tone: "danger",
      confirmLabel: "Use simulators",
    });
    if (!ok) return;
  }
  const map = simAssignments(roles, drivers);
  hooks.onAssign(map);
  saveAssignments(map);
  await connectAssignments(map, drivers, hooks);
}

/**
 * Activate a saved profile: set it active AND connect it.
 *
 * Two surfaces drive this - the FIRST NIGHT card's CONNECT <PROFILE> and the
 * profiles popover - so it lives here rather than in either of them. The
 * pre-flight `getProfile` is what lets the dialog say whether anything
 * reconnects; `waitForProfileActive` is what turns the route's instant
 * `{started}` into "it actually landed", and it is imported for the reason its
 * own comment gives: two copies of that poll would drift.
 */
export async function activateProfileRow(
  row: ProfileRow,
  liveDevices: number,
  hooks: { setBusy(w: BusyWhat): void; onRows(rows: ProfileRow[]): void; reload(): void },
): Promise<void> {
  hooks.setBusy("activate");
  try {
    let full: Profile | null = null;
    try {
      full = await getProfile(row.id) as Profile;
    } catch {
      /* still confirm on the teardown, just without the escalation we could
         not verify */
    }
    const spec = profileActivateConfirm({
      name: row.name,
      connectsNothing: full ? profileConnectsNothing(full) : false,
      realMotion: full
        ? profileResolvesRealMotion(full)
        : row.mode !== "empty" && row.mode !== "alpaca",
      liveDevices,
      sequenceRunning: sequenceRunning(),
    });
    if (spec && !(await confirmDialog(spec))) return;
    await activateProfile(row.id);
    toast("info", `Activating "${row.name}" - connecting the rig...`);
    const rows = await waitForProfileActive(row.id, hooks.onRows);
    if (rows) {
      toast("success", `"${row.name}" is active - every device row has its own result`);
    } else {
      hooks.reload();
      toast("warning",
        `"${row.name}" is not active yet - the connect is still running, or it failed. `
        + "The device rows show how far it got.");
    }
  } catch (e) {
    const msg = e instanceof ApiError && e.code === "running"
      ? "A sequence, capture loop or polar alignment is running - stop it first, "
        + "or force-activate from the profiles sheet."
      : e instanceof ApiError && e.status === 409
        ? "Another profile is still connecting - wait for it to finish before switching again."
        : e instanceof Error ? e.message : "activate failed";
    toast("error", msg, { verbatim: true });
  } finally {
    hooks.setBusy(null);
  }
}

/**
 * Tear the whole rig down. The re-read afterwards is not belt-and-braces: the
 * hub cancels its own status poll as the FIRST step of `disconnect_all`, so the
 * last frame this client ever sees is the one from BEFORE the teardown, and
 * every device goes on reading CONNECTED until a reload. The fresh `/api/status`
 * is dispatched through the same reducer a WS frame would take, so nothing is
 * re-derived here.
 */
export async function disconnectRig(
  liveDevices: number,
  hooks: ConnectHooks,
): Promise<void> {
  const ok = await confirmDialog({
    title: "Disconnect the whole rig?",
    body: sequenceRunning()
      ? "A sequence is running. Disconnecting aborts it and drops every device, including the mount and the guider."
      : `This drops ${liveDevices || "every"} connected device${liveDevices === 1 ? "" : "s"} - camera, mount, guider and the rest. You can reconnect from this screen.`,
    tone: "danger",
    confirmLabel: "Disconnect",
    cancelLabel: "Stay connected",
  });
  if (!ok) return;
  hooks.setBusy("disconnect");
  try {
    await api.post("/api/disconnect");
    hooks.onResults?.({});
    hooks.onLanded?.(null);
    try {
      const fresh = await api.get<Record<string, unknown>>("/api/status");
      useStore.getState().handleEvent({ type: "status", data: fresh, ts: Date.now() / 1000 });
    } catch {
      /* the POST already succeeded; the next WS frame will correct us */
    }
    toast("success", "Disconnected");
  } catch (e) {
    toast("error", e instanceof Error ? e.message : "disconnect failed", { verbatim: true });
  } finally {
    hooks.setBusy(null);
  }
}
