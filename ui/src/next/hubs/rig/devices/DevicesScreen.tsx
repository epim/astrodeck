// DevicesScreen.tsx - RIG · DEVICES, the hub root (plan hub-rig.md A).
//
// Top to bottom: the RIG title with the one-line rig summary, the FIRST NIGHT
// card while nothing is connected, the PROFILE row, the device list with ADD A
// DEVICE at the bottom of it, the two quick actions, and the footer note.
//
// WHAT THIS FILE DELIBERATELY DOES NOT DO.
//
//  * It does not decide what a row says. `roster.ts` does, and it is tested
//    without a DOM, because the rows make three claims from three different
//    sources and getting the join wrong is invisible in a screenshot.
//  * It does not mount `BackendLinkGrid`. That table is a Settings/Connection
//    surface in the new IA (plan E31); the per-role reason it exists to carry
//    is the device row's THIRD LINE here, so no link error lost a home.
//  * It does not blank on a refresh failure. The first load has nothing to fall
//    back to and gets an EmptyCard with RETRY; every later load is a background
//    refresh, and discarding a working tree because one poll failed is worse
//    than the stale row it replaces (EquipmentView:883-924).
//
// A TAP ON A NOT-CONNECTED ROW STILL OPENS ITS SHEET. The prototype toasted
// "Connect the rig first" and dead-ended. Every device sheet renders its own
// not-connected state with the controls dimmed and the reason attached, which
// is more useful than a refusal: you can read what a device would offer before
// you own one.

import { useCallback, useEffect, useState, type JSX } from "react";
import {
  BannerCard, Card, EmptyCard, ActionButton, ListRow, Mono,
} from "../../../ui";
import { nav } from "../../../router";
import { useLock } from "../../../lib/gateHook";
import { listDrivers, listProfiles } from "../../../../api/backends";
import { accessPhrase, useCanConfigBackend } from "../../../../lib/caps";
import { liveRoleCount, type AssignmentMap } from "../../../../lib/equipment";
import {
  useConfig, useEquipConnected, useSafety, useSequence, useStatus, useStore,
} from "../../../../store";
import type { DriversResponse, ProfileRow as ProfileRowData } from "../../../../types";
import { ConnectOnceCard } from "./ConnectOnceCard";
import { DeviceRow } from "./DeviceRow";
import { ProfileRow } from "./ProfileRow";
import { QuickActions } from "./QuickActions";
import { activateProfileRow, runSimulatorRig, type BusyWhat } from "./rigConnect";
import { deviceRows, rigSummary, type RosterInput } from "./roster";

const FOOTER_NOTE =
  "Tap a device for its controls - cooler and gain, tracking and jump steps, "
  + "autofocus rules, the filter ring, guiding, the rotator, safety limits, power "
  + "ports. The profile remembers all of it.";

/** Quoted from `views/EquipmentView.tsx:1107-1112`, em-dash and all. */
const READ_ONLY_NOTE = `Read-only - connecting equipment needs ${accessPhrase("config.backend")}.`;

const ADD_SUB = "scan this computer for USB, Alpaca and ASCOM drivers";

export function DevicesScreen(): JSX.Element {
  const status = useStatus();
  const config = useConfig();
  const equipConnected = useEquipConnected();
  const safety = useSafety();
  const seqState = useSequence()?.state;
  const canConfig = useCanConfigBackend();

  const [data, setData] = useState<DriversResponse | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ProfileRowData[] | null>(null);
  const [busy, setBusy] = useState<BusyWhat>(null);

  const reloadDrivers = useCallback(async () => {
    try {
      setData(await listDrivers());
      setLoadErr(null);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "couldn't load drivers");
    }
  }, []);
  const reloadProfiles = useCallback(() => {
    listProfiles().then(setProfiles).catch(() => setProfiles(null));
  }, []);

  useEffect(() => { void reloadDrivers(); reloadProfiles(); }, [reloadDrivers, reloadProfiles]);

  const toast = (level: string, message: string) =>
    useStore.getState().showToast(level, message);
  const explain = (reason: string) => toast("warning", reason);

  const liveDevices = liveRoleCount(status);
  const activeId = config?.active_profile_id ?? null;
  const activeProfile = activeId
    ? (profiles ?? []).find((r) => r.id === activeId) ?? null
    : null;

  const rosterInput: RosterInput = {
    connected: status?.connected,
    links: status?.backend_links ?? [],
    equipConnected,
    status: status ?? null,
    safety: safety ?? null,
    // The roof has its own 15 s poll and no row on this screen; passing null is
    // the honest value rather than a poll this screen does not make.
    dome: null,
    sequenceRunning: seqState === "running" || seqState === "paused",
  };
  const rows = deviceRows(rosterInput);
  const summary = rigSummary(rosterInput, activeProfile?.name ?? null);

  const addLock = useLock({ cap: "config.backend" });

  const onSimulator = () => void runSimulatorRig(
    data?.roles ?? [],
    data?.drivers ?? [],
    liveDevices,
    {
      setBusy,
      onAssign: (_m: AssignmentMap) => { /* stored by runSimulatorRig; the
        assignment TABLE lives on the ADD A DEVICE sheet and reads it back from
        localStorage when it opens */ },
      reloadDrivers,
    },
  );

  // First load failed with nothing cached: this is the one case that replaces
  // the tree, because there is no tree.
  if (loadErr && !data) {
    return (
      <div data-testid="rig-devices" style={{ padding: "0 2px" }}>
        <EmptyCard
          title="Couldn't load drivers"
          hint={loadErr}
          action={
            <ActionButton kind="secondary" onPress={() => void reloadDrivers()}>
              RETRY
            </ActionButton>
          }
          data-testid="devices-load-error"
        />
      </div>
    );
  }

  return (
    <div
      data-testid="rig-devices"
      style={{ display: "flex", flexDirection: "column", gap: 10, padding: "0 2px 24px" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10 }}>
        <div className="nx-display" style={{ fontWeight: 600, fontSize: 15, letterSpacing: ".1em" }}>
          RIG
        </div>
        <span data-testid="rig-summary"><Mono size={10} tone={summary.tone}>{summary.text}</Mono></span>
      </div>

      {loadErr && data && (
        <BannerCard
          tone="warn"
          text={`Couldn't refresh drivers: ${loadErr}`}
          cta={{ label: "RETRY", onPress: () => void reloadDrivers() }}
          data-testid="devices-refresh-error"
        />
      )}

      {!equipConnected && (
        <ConnectOnceCard
          profile={activeProfile}
          canConfig={canConfig}
          busy={busy != null}
          explain={explain}
          onConnectProfile={(row) => void activateProfileRow(row, liveDevices, {
            setBusy, onRows: setProfiles, reload: reloadProfiles,
          })}
          // DETECT MY HARDWARE opens the sheet that owns the scan and starts it
          // there, rather than scanning behind a screen that has nowhere to
          // show what answered.
          onDetect={() => nav.sheet("addDevice", { scan: "hardware" })}
          onSimulator={onSimulator}
        />
      )}

      <ProfileRow
        rows={profiles}
        activeId={activeId}
        liveDevices={liveDevices}
        canConfig={canConfig}
        busy={busy}
        setBusy={setBusy}
        onRows={setProfiles}
        reload={reloadProfiles}
      />

      <Card padding={0} data-testid="device-list">
        {rows.map((row) => (
          <DeviceRow key={row.role} row={row} onOpen={(sheet) => nav.sheet(sheet)} />
        ))}
        <ListRow
          data-testid="add-a-device"
          icon={
            <span
              aria-hidden="true"
              style={{
                width: 34, height: 34, borderRadius: 10, display: "flex",
                alignItems: "center", justifyContent: "center",
                border: "1px dashed rgba(0,210,255,.5)", color: "var(--accent)",
                fontFamily: "var(--font-mono, monospace)", fontSize: 16,
              }}
            >
              +
            </span>
          }
          title={<span style={{ color: "var(--accent)" }}>ADD A DEVICE</span>}
          sub={ADD_SUB}
          onPress={() => nav.sheet("addDevice")}
          lockedReason={addLock.lockedReason}
          onExplain={explain}
        />
      </Card>

      <QuickActions />

      {!canConfig && (
        <span data-testid="devices-readonly"><Mono size={11} tone="dim">{READ_ONLY_NOTE}</Mono></span>
      )}

      <div style={{ fontSize: 12, color: "var(--text-3, #7683a5)", lineHeight: 1.5, padding: "0 2px" }}>
        {FOOTER_NOTE}
      </div>
    </div>
  );
}
