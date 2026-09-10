// addDevice.tsx - the ADD A DEVICE sheet (plan hub-rig.md A.5 + A.7).
//
// The design's device rows assume assignment has already happened; GAP-2 says
// it has no screen. This is that screen, and it is three things stacked, in the
// order a first night actually goes:
//
//   1. FIND what is plugged in or on the network (scan), and declare by hand
//      what a scan cannot see (the driver sheet, one level deeper).
//   2. SAY WHICH DRIVER RUNS WHICH ROLE - the assignment table, whose one rule
//      is `eligibleDrivers`: a driver is offered on a row only when it is
//      enabled, reachable, and its live probe actually offers that role.
//   3. CONNECT, with the friction each outcome deserves.
//
// FIVE BEHAVIOURS LIFTED WHOLE FROM `views/EquipmentView.tsx`, each of which was
// a measured bug before it was a rule:
//
//   * SEQUENTIAL BATCH ADDS. `for`, never `Promise.all`: concurrent POSTs race
//     the server's id mint off one config file. And the list is re-read on
//     EVERY outcome including failure, because a batch that dies at item 4 has
//     still created three, and not showing them makes the next tap create
//     duplicates.
//   * ADOPT A RUNNING RIG, BY IDENTITY. A rig brought up elsewhere (a boot
//     profile, the tablet, the one-tap simulator) is adopted as "already
//     connected" only where the SERVER corroborates the pick - same role live,
//     same device identity (`compareRoleIdentity`, imported so there is one
//     answer). Matching on display name alone reported an eleven-role simulator
//     rig as eleven pending edits.
//   * DIRTY DIFF BY VALUE. An Assignment is an object; re-picking the identical
//     driver builds a fresh one, and reference equality would call that an edit
//     and light RECONNECT over a change that is not one.
//   * SUPERSEDED-LOAD GENERATION TOKEN. Two overlapping loads/scans must not
//     finish on top of each other; the later one wins and the earlier one stops
//     rather than clearing the busy flag out from under it.
//   * NAMED BUSY. `busyWhat`, not a boolean, so the in-progress label lands on
//     the button that was pressed instead of always on CONNECT.

import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";
import {
  ActionButton, BannerCard, Card, Divider, EmptyCard, Label, ListRow, Mono, Sheet,
} from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { useLock } from "../../../lib/gateHook";
import type { SheetProps } from "../../sheets";
import {
  addDriverForHardware, deleteDriver, discoverBackend, discoverHardware,
  hwAlreadyConfigured, listBackends, listDrivers, probeDriver, updateDriver,
  type HwFound,
} from "../../../../api/backends";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import { driverTypeChip, offersSummary } from "../../../../components/settings/driversMeta";
import { ROLE_LABEL } from "../../../../components/settings/backendMeta";
import type {
  DiscoveredAlpaca, DiscoveredNina,
} from "../../../../components/settings/backendMeta";
import { compareRoleIdentity } from "../../../../views/EquipmentView";
import { accessPhrase, useCanConfigBackend } from "../../../../lib/caps";
import {
  deviceChoices, eligibleDrivers, guiderSlotNote, guiderSlotState,
  liveRoleCount, loadAssignments, saveAssignments, slotState,
  type Assignment, type AssignmentMap,
} from "../../../../lib/equipment";
import { useConfig, useStatus, useStore } from "../../../../store";
import type {
  BackendInfo, DriverInfo, DriversResponse, RoleResult,
} from "../../../../types";
import {
  connectAssignments, disconnectRig, runSimulatorRig, type BusyWhat,
} from "../devices/rigConnect";

// ------------------------------------------------------------------- the copy

const SLOT_WORD: Record<string, string> = {
  unassigned: "UNASSIGNED",
  ok: "ASSIGNED",
  "driver-removed": "DRIVER REMOVED",
  "driver-unreachable": "DRIVER UNREACHABLE",
  "driver-disabled": "DRIVER DISABLED",
  "device-missing": "DEVICE MISSING",
};

/** The one-rule explainer, with its destination repointed at this sheet. */
const ONE_RULE_NOTE =
  "For each device slot, pick which configured driver runs it — only drivers "
  + "that actually offer that device are listed. Manage drivers above.";

const NO_DRIVERS_NOTE =
  "No drivers configured yet — add your NINA instance, Alpaca servers or PHD2 "
  + "below, or scan for USB/serial hardware (ZWO, Player One, Wanderer…) plugged "
  + "into this machine. The built-ins (Simulator, native engine, ASTAP) are "
  + "always available.";

const DELETE_BODY =
  "Profiles referencing this driver will show 'driver removed' until reassigned.";

const BUILT_INS_NOTE =
  "Simulator, the AstroDeck native engine and ASTAP are built in: they need no "
  + "declaration and cannot be deleted, only used.";

const READ_ONLY_NOTE = `Read-only — connecting equipment needs ${accessPhrase("config.backend")}.`;

// -------------------------------------------------------------- network scan

export interface NetFound {
  /** The driver TYPE to declare, not the backend registry name. */
  type: string;
  label: string;
  host: string;
  port: number;
}

/** Every network backend the server says is discoverable, asked in parallel and
 *  normalised to one row shape. Shared with the `driver` sheet, which fills its
 *  host/port fields from the same rows - two scanners would be two answers to
 *  "is my NINA visible from here". One backend failing must not blank the scan,
 *  so each call is caught on its own (same rule `discoverHardware` follows). */
export async function scanNetwork(): Promise<NetFound[]> {
  let backends: BackendInfo[] = [];
  try {
    backends = await listBackends();
  } catch {
    return [];
  }
  const net = backends.filter((b) => b.discoverable && !b.hardware);
  const out: NetFound[] = [];
  const results = await Promise.all(net.map(async (b) => {
    try { return { b, payload: await discoverBackend(b.name) }; }
    catch { return { b, payload: null }; }
  }));
  for (const { b, payload } of results) {
    if (!Array.isArray(payload)) continue;
    if (b.name === "nina") {
      for (const n of payload as DiscoveredNina[]) {
        out.push({ type: "nina", label: n.hostname || n.host, host: n.host, port: n.port });
      }
    } else {
      for (const a of payload as DiscoveredAlpaca[]) {
        out.push({
          type: "alpaca",
          label: `${b.label} · ${a.devices?.length ?? 0} devices`,
          host: a.address,
          port: a.port,
        });
      }
    }
  }
  return out;
}

// ------------------------------------------------------------------ the sheet

export function AddDeviceSheet({ params }: SheetProps): JSX.Element {
  const status = useStatus();
  const config = useConfig();
  const canConfig = useCanConfigBackend();

  const [data, setData] = useState<DriversResponse | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [assignments, setAssignments] = useState<AssignmentMap>(() => loadAssignments());
  const [connectedMap, setConnectedMap] = useState<AssignmentMap | null>(null);
  const [results, setResults] = useState<Record<string, RoleResult>>({});
  const [busyWhat, setBusyWhat] = useState<BusyWhat>(null);
  const [hw, setHw] = useState<{ found: HwFound[]; scanned: number } | null>(null);
  const [net, setNet] = useState<NetFound[] | null>(null);

  const busy = busyWhat !== null;
  const drivers = useMemo(() => data?.drivers ?? [], [data]);
  const roles = useMemo(() => data?.roles ?? [], [data]);
  const links = useMemo(() => status?.backend_links ?? [], [status]);

  // Generation token: two overlapping loads must not finish on top of each
  // other, and the loser must not clear the busy flag the winner owns.
  const gen = useRef(0);

  const toast = (level: string, message: string) =>
    useStore.getState().showToast(level, message);
  const explain = (reason: string) => toast("warning", reason);

  const reload = useCallback(async () => {
    const mine = ++gen.current;
    try {
      const fresh = await listDrivers();
      if (mine !== gen.current) return;
      setData(fresh);
      setLoadErr(null);
    } catch (e) {
      if (mine !== gen.current) return;
      setLoadErr(e instanceof Error ? e.message : "couldn't load drivers");
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  const setAssignment = (role: string, a: Assignment | null) => {
    setAssignments((m) => {
      const next = { ...m, [role]: a };
      saveAssignments(next);
      return next;
    });
  };

  const linkByRole = useMemo(
    () => Object.fromEntries(links.map((l) => [l.role, l])),
    [links],
  );
  const isRoleLive = (r: string) =>
    !!status?.connected?.[r]?.connected || !!linkByRole[r]?.connected;
  const liveDevices = liveRoleCount(status);
  const rigUp = liveDevices > 0;

  // What the SERVER reports about the device filling a role - the whole
  // describe() block, not just the display name. The guider is the exception:
  // an engine, with no `connected` entry at all.
  const liveDeviceOf = (r: string) => {
    const dev = status?.connected?.[r] as
      { name?: string; connected?: boolean; dev_type?: string; dev_num?: number } | undefined;
    if (dev?.connected) return dev;
    if (r === "guider" && linkByRole[r]?.connected) {
      return status?.guider ? { name: status.guider.name, connected: true } : null;
    }
    return null;
  };

  useEffect(() => {
    if (rigUp && connectedMap === null && roles.length > 0) {
      const out: AssignmentMap = {};
      for (const r of roles) {
        const a = assignments[r];
        if (!a || !isRoleLive(r)) { out[r] = null; continue; }
        const driverType = drivers.find((d) => d.id === a.driverId)?.type;
        out[r] = compareRoleIdentity(a, liveDeviceOf(r), driverType) === "different" ? null : a;
      }
      setConnectedMap(out);
    }
    if (!rigUp && connectedMap !== null) setConnectedMap(null);
    // `assignments` is deliberately NOT a dependency: adopting on every pick
    // would make an edit look like it was already connected.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rigUp, connectedMap, roles.length]);

  // BY VALUE, not by reference.
  const asgKey = (a: Assignment | null | undefined) => (a ? JSON.stringify(a) : "");
  const dirtyRoles = connectedMap
    ? roles.filter((r) => asgKey(assignments[r]) !== asgKey(connectedMap[r]))
    : [];
  const assignedCount = roles.filter((r) => assignments[r]).length;
  const liveUnassigned = roles.filter((r) => isRoleLive(r) && !assignments[r]);
  const downDrivers = drivers.filter((d) => !d.implicit && d.enabled && !d.status.reachable);

  // --------------------------------------------------------------- scanning

  const doScanHardware = useCallback(() => void (async () => {
    setBusyWhat("scan");
    try {
      const scanned = (await listBackends())
        .filter((b) => b.hardware && b.discoverable && b.driver_type).length;
      const found = await discoverHardware();
      setHw({ found, scanned });
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "hardware detection failed");
    } finally {
      setBusyWhat(null);
    }
    // toast is a stable store call; the deps that matter are none.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  })(), []);

  // DETECT MY HARDWARE on the FIRST NIGHT card opens this sheet with the scan
  // already asked for, so the button that promised a scan produces one.
  const autoScan = params.scan === "hardware";
  useEffect(() => { if (autoScan) doScanHardware(); }, [autoScan, doScanHardware]);

  const doScanNetwork = () => void (async () => {
    setBusyWhat("scan");
    try { setNet(await scanNetwork()); }
    finally { setBusyWhat(null); }
  })();

  const addOne = (f: HwFound) => void (async () => {
    setBusyWhat("scan");
    try {
      await addDriverForHardware(f);
      await reload();
      toast("success", `${f.name} added - assign it to a role below.`);
    } catch (e) {
      await reload();
      toast("error", e instanceof Error ? e.message : "could not add that driver");
    } finally {
      setBusyWhat(null);
    }
  })();

  const addAll = (list: HwFound[]) => void (async () => {
    setBusyWhat("scan");
    let added = 0;
    try {
      for (const f of list) {
        // Sequential on purpose: concurrent POSTs race the server's id mint.
        await addDriverForHardware(f);
        added++;
      }
      await reload();
      toast("success", `Added ${added} driver${added === 1 ? "" : "s"} - assign them below.`);
    } catch (e) {
      // A partial batch has still created drivers, and they must appear.
      await reload();
      toast("error",
        `Added ${added} of ${list.length} driver${list.length === 1 ? "" : "s"}, then `
        + `${e instanceof Error ? e.message : "the next one failed"} - the ones that landed `
        + "are listed below; re-run the scan to add the rest.");
    } finally {
      setBusyWhat(null);
    }
  })();

  // ----------------------------------------------------------- driver rows

  const toggleDriver = (d: DriverInfo) => void (async () => {
    setBusyWhat("save");
    try { await updateDriver(d.id, { enabled: !d.enabled }); await reload(); }
    catch (e) { toast("error", e instanceof Error ? e.message : "could not change that driver"); }
    finally { setBusyWhat(null); }
  })();

  const probeOne = (d: DriverInfo) => void (async () => {
    setBusyWhat("scan");
    try { setData(await probeDriver(d.id)); }
    catch (e) { toast("error", e instanceof Error ? e.message : "probe failed"); }
    finally { setBusyWhat(null); }
  })();

  const removeDriver = (d: DriverInfo) => void (async () => {
    const ok = await confirmDialog({
      title: `Delete "${d.label}"?`,
      body: DELETE_BODY,
      tone: "danger",
      confirmLabel: "Delete driver",
      cancelLabel: "Keep it",
    });
    if (!ok) return;
    setBusyWhat("save");
    try { await deleteDriver(d.id); await reload(); }
    catch (e) { toast("error", e instanceof Error ? e.message : "delete failed"); }
    finally { setBusyWhat(null); }
  })();

  // ------------------------------------------------------------- connecting

  const hooks = {
    setBusy: setBusyWhat,
    onResults: setResults,
    onLanded: (landed: AssignmentMap | null) => setConnectedMap(landed),
    reloadDrivers: reload,
  };

  const doConnect = () => void connectAssignments(assignments, drivers, hooks);
  const doSim = () => void runSimulatorRig(roles, drivers, liveDevices, {
    ...hooks,
    onAssign: (m: AssignmentMap) => setAssignments(m),
  });
  const doDisconnect = () => void disconnectRig(liveDevices, hooks);

  const configLock = useLock({ cap: "config.backend" });
  const connectLock = useLock({ cap: "config.backend", busyLane: "connect" });
  const lock = (base: string | null) => base ?? (busy ? "A rig action is already running." : null);

  const connectLabel = !connectedMap
    ? `CONNECT RIG (${assignedCount})`
    : dirtyRoles.length > 0
      ? `RECONNECT RIG (${dirtyRoles.length} CHANGED)`
      : "RIG CONNECTED";

  // ------------------------------------------------------------------ live

  const reachable = drivers.filter((d) => d.status.reachable).length;
  const live = `${drivers.length} driver${drivers.length === 1 ? "" : "s"} configured`
    + ` · ${reachable} reachable`;

  const unconfigured = (hw?.found ?? []).filter((f) => !hwAlreadyConfigured(f, drivers));

  const guiderNote = (() => {
    if (!roles.includes("guider")) return null;
    const nativeDriver = drivers.find((d) => d.id === "astrodeck");
    const state = guiderSlotState(
      !!nativeDriver?.status.reachable,
      !!status?.guide_camera?.connected,
    );
    return guiderSlotNote(state, {
      guideCamName: status?.guide_camera?.name ?? null,
      nativeError: nativeDriver?.status.error ?? null,
    }).replace("on the Guide screen", "on the Guider sheet");
  })();

  return (
    <Sheet
      title="ADD A DEVICE"
      backLabel="RIG"
      icon={<NxIcon name="plus" size={18} />}
      live={live}
      onBack={() => nav.back()}
      data-testid="sheet-add-device"
    >
      {loadErr && !data && (
        <EmptyCard
          title="Couldn't load drivers"
          hint={loadErr}
          action={<ActionButton kind="secondary" onPress={() => void reload()}>RETRY</ActionButton>}
        />
      )}
      {loadErr && data && (
        <BannerCard
          tone="warn"
          text={`Couldn't refresh drivers: ${loadErr}`}
          cta={{ label: "RETRY", onPress: () => void reload() }}
        />
      )}

      {/* ------------------------------------------------------- 1. find it */}
      <ActionButton
        kind="primary"
        size="lg"
        full
        data-testid="scan-hardware"
        busy={busyWhat === "scan"}
        lockedReason={lock(configLock.lockedReason)}
        onExplain={explain}
        onPress={doScanHardware}
      >
        SCAN THIS COMPUTER
      </ActionButton>
      <ActionButton
        kind="secondary"
        full
        data-testid="scan-network"
        lockedReason={lock(configLock.lockedReason)}
        onExplain={explain}
        onPress={doScanNetwork}
      >
        SCAN THE NETWORK
      </ActionButton>

      {hw != null && hw.found.length === 0 && (
        <Mono size={11} tone="warn">
          {`No hardware answered — ${hw.scanned} USB/serial backend${hw.scanned === 1 ? "" : "s"} `}
          scanned, none reported a device. Check power and cables, or add a driver by hand below.
        </Mono>
      )}

      {hw != null && hw.found.length > 0 && (
        <Card padding={0} data-testid="hw-found">
          {hw.found.map((f, i) => {
            const already = hwAlreadyConfigured(f, drivers);
            return (
              <ListRow
                key={`${f.driver_type}:${f.port_path ?? ""}:${f.index ?? i}`}
                data-testid={`hw-found-${i}`}
                title={f.name}
                sub={`${f.backend_label} · ${f.roles.join(", ")}`}
                right={already ? (
                  <Mono size={10} tone="dim">already configured</Mono>
                ) : (
                  <ActionButton
                    kind="secondary"
                    data-testid={`hw-add-${i}`}
                    lockedReason={lock(configLock.lockedReason)}
                    onExplain={explain}
                    onPress={() => addOne(f)}
                  >
                    ADD
                  </ActionButton>
                )}
              />
            );
          })}
          {unconfigured.length > 1 && (
            <ListRow
              title="ADD ALL"
              sub={`${unconfigured.length} devices this computer reported that are not configured yet`}
              right={
                <ActionButton
                  kind="secondary"
                  data-testid="hw-add-all"
                  lockedReason={lock(configLock.lockedReason)}
                  onExplain={explain}
                  onPress={() => addAll(unconfigured)}
                >
                  ADD ALL
                </ActionButton>
              }
            />
          )}
        </Card>
      )}

      {net != null && net.length === 0 && (
        <Mono size={11} tone="dim">nothing found on this network</Mono>
      )}
      {net != null && net.length > 0 && (
        <Card padding={0} data-testid="net-found">
          {net.map((n, i) => (
            <ListRow
              key={`${n.type}:${n.host}:${n.port}`}
              data-testid={`net-found-${i}`}
              title={n.label}
              sub={`${n.host}:${n.port}`}
              right={<Mono size={10} tone="accent">DECLARE ›</Mono>}
              onPress={() => nav.sheet("driver", {
                type: n.type, host: n.host, port: String(n.port),
              })}
              lockedReason={lock(configLock.lockedReason)}
              onExplain={explain}
            />
          ))}
        </Card>
      )}

      <ListRow
        data-testid="open-driver-sheet"
        title={<span style={{ color: "var(--accent)" }}>NOT FOUND? ADD A DRIVER</span>}
        sub="declare a NINA instance, an Alpaca server or PHD2 by host and port"
        chevron
        onPress={() => nav.sheet("driver")}
        lockedReason={lock(configLock.lockedReason)}
        onExplain={explain}
      />

      {/* ------------------------------------------- 2. the configured list */}
      <Divider />
      <Label>CONFIGURED DRIVERS</Label>
      {drivers.length === 0 && <Mono size={11} tone="dim">{NO_DRIVERS_NOTE}</Mono>}
      <Card padding={0} data-testid="driver-list">
        {drivers.map((d) => {
          const chip = driverTypeChip(d.label, d.type);
          const serialNative = d.host === "" && d.transport === "serial";
          return (
            <ListRow
              key={d.id}
              data-testid={`driver-${d.id}`}
              icon={
                <span
                  aria-hidden="true"
                  style={{
                    width: 8, height: 8, borderRadius: "50%",
                    background: !d.enabled
                      ? "var(--text-3, #7683a5)"
                      : d.status.reachable ? "var(--good)" : "var(--bad)",
                  }}
                />
              }
              title={chip ? `${d.label} · ${chip}` : d.label}
              sub={d.status.reachable ? offersSummary(d) : (d.status.error ?? "not reachable")}
              right={
                <span style={{ display: "flex", gap: 4, alignItems: "center" }}>
                  <ActionButton
                    kind="ghost"
                    data-testid={`driver-toggle-${d.id}`}
                    lockedReason={lock(configLock.lockedReason)}
                    onExplain={explain}
                    onPress={() => toggleDriver(d)}
                  >
                    {d.enabled ? "ON" : "OFF"}
                  </ActionButton>
                  <ActionButton
                    kind="ghost"
                    data-testid={`driver-probe-${d.id}`}
                    lockedReason={lock(configLock.lockedReason)}
                    onExplain={explain}
                    onPress={() => probeOne(d)}
                  >
                    PROBE
                  </ActionButton>
                  {!d.implicit && (
                    <ActionButton
                      kind="ghost"
                      data-testid={`driver-delete-${d.id}`}
                      lockedReason={lock(configLock.lockedReason)}
                      onExplain={explain}
                      onPress={() => removeDriver(d)}
                    >
                      DELETE
                    </ActionButton>
                  )}
                  {!d.implicit && serialNative && (
                    <ActionButton
                      kind="ghost"
                      data-testid={`driver-port-${d.id}`}
                      lockedReason={lock(configLock.lockedReason)}
                      onExplain={explain}
                      onPress={() => nav.sheet("driver", { edit: d.id })}
                    >
                      EDIT PORT
                    </ActionButton>
                  )}
                </span>
              }
            />
          );
        })}
      </Card>
      <Mono size={11} tone="dim">{BUILT_INS_NOTE}</Mono>

      {downDrivers.length > 0 && (
        <BannerCard
          tone="warn"
          text={`${downDrivers.map((d) => d.label).join(", ")} — configured but unreachable — assignments stay put and re-light when it returns.`}
        />
      )}

      {/* ------------------------------------------------ 3. role assignment */}
      <Divider />
      <Label>WHICH DRIVER RUNS WHICH DEVICE</Label>
      <Mono size={11} tone="dim">{ONE_RULE_NOTE}</Mono>

      {liveUnassigned.length > 0 && (
        <BannerCard
          tone="info"
          text={config?.active_profile_id
            ? `${liveUnassigned.length} role(s) are live but unassigned here — the active profile connected them. Pick a driver on those rows to keep them.`
            : `${liveUnassigned.length} role(s) are live but unassigned here — this rig was started somewhere else (the one-tap simulator, or Profiles → Activate). Pick a driver on those rows to save them into a profile.`}
        />
      )}

      <Card padding={0} data-testid="role-table">
        {roles.map((role) => {
          const a = assignments[role] ?? null;
          const options = eligibleDrivers(role, drivers);
          const picked = a ? drivers.find((d) => d.id === a.driverId) : undefined;
          const choices = picked ? deviceChoices(role, picked) : [];
          const word = SLOT_WORD[slotState(role, a, drivers)] ?? "UNASSIGNED";
          const rowLock = lock(configLock.lockedReason);
          const result = results[role];
          return (
            <ListRow
              key={role}
              data-testid={`role-${role}`}
              title={ROLE_LABEL[role] ?? role}
              sub={
                <>
                  <span style={{ display: "block" }}>{word}</span>
                  {result && !result.ok && result.error && (
                    <span style={{ display: "block", whiteSpace: "normal", color: "var(--bad)" }}>
                      {result.error}
                    </span>
                  )}
                </>
              }
              right={
                <span style={{ display: "flex", gap: 4, alignItems: "center" }}>
                  <select
                    className={rowLock ? "nx-input nx-locked" : "nx-input"}
                    aria-label={`Driver for ${ROLE_LABEL[role] ?? role}`}
                    aria-disabled={rowLock ? true : undefined}
                    data-locked={rowLock ? "true" : undefined}
                    title={rowLock ?? undefined}
                    data-testid={`role-driver-${role}`}
                    value={a?.driverId ?? ""}
                    onChange={(e) => {
                      if (rowLock) { explain(rowLock); return; }
                      const id = e.target.value;
                      if (!id) { setAssignment(role, null); return; }
                      const d = drivers.find((x) => x.id === id);
                      const first = d ? deviceChoices(role, d)[0] : undefined;
                      setAssignment(role, {
                        driverId: id,
                        devType: first?.dev_type,
                        devNum: first?.dev_num,
                        name: first?.name,
                      });
                    }}
                  >
                    <option value="">not assigned</option>
                    {options.map((d) => (
                      <option key={d.id} value={d.id}>{d.label}</option>
                    ))}
                  </select>
                  {choices.length > 1 && (
                    <select
                      className={rowLock ? "nx-input nx-locked" : "nx-input"}
                      aria-label={`Device for ${ROLE_LABEL[role] ?? role}`}
                      aria-disabled={rowLock ? true : undefined}
                      title={rowLock ?? undefined}
                      data-testid={`role-device-${role}`}
                      value={`${a?.devType ?? ""}:${a?.devNum ?? ""}`}
                      onChange={(e) => {
                        if (rowLock) { explain(rowLock); return; }
                        const [dt, dn] = e.target.value.split(":");
                        const offer = choices.find(
                          (o) => (o.dev_type ?? "") === dt && String(o.dev_num ?? "") === dn,
                        );
                        if (!a) return;
                        setAssignment(role, {
                          ...a, devType: offer?.dev_type, devNum: offer?.dev_num, name: offer?.name,
                        });
                      }}
                    >
                      {choices.map((o) => (
                        <option key={`${o.dev_type}:${o.dev_num}`} value={`${o.dev_type ?? ""}:${o.dev_num ?? ""}`}>
                          {o.name}
                        </option>
                      ))}
                    </select>
                  )}
                </span>
              }
            />
          );
        })}
      </Card>

      {guiderNote && <Mono size={11} tone="dim">{guiderNote}</Mono>}

      {assignedCount > 0 && liveDevices === 0 && canConfig && (
        <Mono size={11} tone="dim">
          {`${assignedCount} slot(s) picked but not connected. Picks are remembered in this `}
          browser only — save them as a profile to keep them on every device.
        </Mono>
      )}

      <ActionButton
        kind="primary"
        size="lg"
        full
        data-testid="connect-rig"
        busy={busyWhat === "connect"}
        lockedReason={lock(connectLock.lockedReason)}
        onExplain={explain}
        onPress={doConnect}
      >
        {connectLabel}
      </ActionButton>
      <ActionButton
        kind="secondary"
        full
        data-testid="run-simulator"
        lockedReason={lock(connectLock.lockedReason)}
        onExplain={explain}
        onPress={doSim}
      >
        RUN THE SIMULATOR
      </ActionButton>
      <ActionButton
        kind="danger"
        full
        data-testid="disconnect-rig"
        busy={busyWhat === "disconnect"}
        lockedReason={lock(connectLock.lockedReason)}
        onExplain={explain}
        onPress={doDisconnect}
      >
        DISCONNECT
      </ActionButton>

      {!canConfig && <Mono size={11} tone="dim">{READ_ONLY_NOTE}</Mono>}
      <div style={{ height: 8 }} />
    </Sheet>
  );
}
