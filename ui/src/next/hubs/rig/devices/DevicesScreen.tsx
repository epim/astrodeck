// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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

import { useCallback, useEffect, useRef, useState, type JSX } from "react";
import {
  BannerCard, Card, EmptyCard, ActionButton, ListRow, Mono,
} from "../../../ui";
import { nav } from "../../../router";
import { useLock, useOnRelay } from "../../../lib/gateHook";
import { LOCAL_ONLY_REASON } from "../../../lib/gate";
import { listDrivers, listProfiles } from "../../../../api/backends";
import { accessPhrase, useCan, useCanConfigBackend } from "../../../../lib/caps";
import { liveRoleCount, type AssignmentMap } from "../../../../lib/equipment";
import { runIsLive } from "../../../../lib/lastSessionFrame";
import {
  retryTransient, retryingLine, useRetryOnReturn, type LoadRetry,
} from "../../../../lib/retryLoad";
import {
  useConfig, useEquipConnected, useSafety, useSequence, useStatus, useStore,
  useWsConnected,
} from "../../../../store";
import type { DriversResponse, ProfileRow as ProfileRowData } from "../../../../types";
import { RECONNECT_CAP } from "../profiles/profilesModel";
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
/** An operator can reconnect a SAVED profile (#759) and nothing else on this
 *  screen that writes, so the note says both instead of calling the whole screen
 *  read-only while CONNECT is armed. */
const RECONNECT_ONLY_NOTE =
  `Adding and changing equipment needs ${accessPhrase("config.backend")}. `
  + "You can reconnect a saved profile.";

const ADD_SUB = "scan this computer for USB, Alpaca and ASCOM drivers";

export function DevicesScreen(): JSX.Element {
  const status = useStatus();
  const config = useConfig();
  const equipConnected = useEquipConnected();
  const safety = useSafety();
  const sequence = useSequence();
  const canConfig = useCanConfigBackend();
  const canReconnect = useCan(RECONNECT_CAP);

  const [data, setData] = useState<DriversResponse | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ProfileRowData[] | null>(null);
  const [busy, setBusy] = useState<BusyWhat>(null);

  // A TRANSIENT FAILURE IS ASKED AGAIN BY ITSELF (#859): a timeout, a network
  // error or a proxy's 502/503/504 waits 2 s, 5 s, 15 s and asks again, with
  // the retrying banner meanwhile; the newest load wins and an unmounted one
  // writes nothing. A failure left after that is re-asked once on
  // websocket-up or tab-visible.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);
  const driversGen = useRef(0);
  const driversPhase = useRef<"loading" | "failed" | "ok">("loading");
  const [driversRetry, setDriversRetry] = useState<LoadRetry | null>(null);
  const wsConnected = useWsConnected();

  const reloadDrivers = useCallback(async () => {
    driversPhase.current = "loading";
    const gen = ++driversGen.current;
    const mine = () => alive.current && gen === driversGen.current;
    try {
      const answer = await retryTransient(listDrivers, {
        stop: () => !mine(),
        onRetry: (r) => { if (mine()) setDriversRetry(r); },
      });
      if (!mine()) return;
      setData(answer);
      setLoadErr(null);
      setDriversRetry(null);
      driversPhase.current = "ok";
    } catch (e) {
      if (!mine()) return;
      setLoadErr(e instanceof Error ? e.message : "couldn't load drivers");
      setDriversRetry(null);
      driversPhase.current = "failed";
    }
  }, []);
  // The newest profiles read wins too: a mount read still retrying must not
  // overwrite (or null) the rows a later reload, after an activate or a save,
  // has already landed.
  const profilesGen = useRef(0);
  const reloadProfiles = useCallback(() => {
    const gen = ++profilesGen.current;
    const mine = () => alive.current && gen === profilesGen.current;
    retryTransient(listProfiles, { stop: () => !mine() })
      .then((rows) => { if (mine()) setProfiles(rows); })
      .catch(() => { if (mine()) setProfiles(null); });
  }, []);
  /** Rows handed in by an activate or a delete: newer than any read still out. */
  const takeProfiles = useCallback((rows: ProfileRowData[]) => {
    ++profilesGen.current;
    setProfiles(rows);
  }, []);

  useEffect(() => { void reloadDrivers(); reloadProfiles(); }, [reloadDrivers, reloadProfiles]);
  useRetryOnReturn(() => driversPhase.current === "failed", () => { void reloadDrivers(); }, wsConnected);

  const toast = (level: string, message: string, opts?: { verbatim?: boolean }) =>
    useStore.getState().showToast(level, message, opts);
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
    sequenceRunning: runIsLive(sequence),
  };
  const rows = deviceRows(rosterInput);
  const summary = rigSummary(rosterInput, activeProfile?.name ?? null);

  const addLock = useLock({ cap: "config.backend" });

  // The LAN sentence for the verbs on this screen that reach a fenced route:
  // the popover's save/delete go to `/api/profiles`, RUN THE SIMULATOR goes to
  // `/api/connect` and `/api/drivers`, and DETECT MY HARDWARE opens the sheet
  // that starts a `/api/discover` scan on arrival. Those prefixes are on
  // `app.py`'s relay fence, so the rig answers 403 `local_only` for every role.
  //
  // ACTIVATING A SAVED PROFILE IS NOT ONE OF THEM (#685): the rig allow-lists
  // `POST /api/profiles/<id>/activate` (without `force`) through the fence, so
  // the popover's activate ignores this reason, and so does the FIRST NIGHT
  // card's CONNECT <profile>, which is the same call: `ConnectOnceCard` puts
  // this reason on DETECT MY HARDWARE and RUN THE SIMULATOR only, and CONNECT
  // answers to the capability and the busy flag.
  //
  // Passed down rather than taken with `useLock` inside each card: those two
  // components are props-only and take `canConfig` as a boolean, and the ONE
  // thing that must not happen is a relay tab reading "needs admin access"
  // while holding admin. The cards put this reason FIRST, which is the order
  // `gate.ts` uses for the same pair.
  //
  // ADD A DEVICE keeps `addLock` alone: it only navigates, and the sheet it
  // opens renders the driver list, the roles table and the assignment rows
  // read-only over the relay (ARCHITECTURE section 8 - nothing is hidden, the
  // writes carry the reason).
  const lanReason = useOnRelay() ? LOCAL_ONLY_REASON : null;

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
  // the tree, because there is no tree. Reached only once the retries are
  // spent (#859); while one is pending the tree shows the retrying banner.
  if (loadErr && !data && !driversRetry) {
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

      {driversRetry && (
        <BannerCard tone="warn" text={retryingLine(driversRetry)} data-testid="devices-retrying" />
      )}

      {loadErr && data && !driversRetry && (
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
          canReconnect={canReconnect}
          lanReason={lanReason}
          busy={busy != null}
          explain={explain}
          onConnectProfile={(row) => void activateProfileRow(row, liveDevices, {
            setBusy, onRows: takeProfiles, reload: reloadProfiles,
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
        lanReason={lanReason}
        busy={busy}
        setBusy={setBusy}
        onRows={takeProfiles}
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
        <span data-testid="devices-readonly">
          <Mono size={11} tone="dim">{canReconnect ? RECONNECT_ONLY_NOTE : READ_ONLY_NOTE}</Mono>
        </span>
      )}

      <div style={{ fontSize: 12, color: "var(--text-3, #7683a5)", lineHeight: 1.5, padding: "0 2px" }}>
        {FOOTER_NOTE}
      </div>
    </div>
  );
}
