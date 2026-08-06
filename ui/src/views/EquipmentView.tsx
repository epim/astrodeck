// EquipmentView.tsx — the unified per-device Equipment surface (spec §4.1),
// replacing the old mode-centric connect view under the same "connect" view id.
// One uniform row grammar: for each server-fed role, pick WHO drives it from
// the drivers that actually offer it (the one rule), then Connect Rig compiles
// assignments → RigSpec (primary "none", driver_id ConnSpecs) → the existing
// /api/connect/rig. Live truth (LEDs + names) rides backend_links + status;
// failure honesty: sticky assignments, per-row RoleResult errors, an
// unreachable-driver banner, and a /api/drivers refetch after a failed
// connect (probe-cache honesty, spec §3.2/§5).
import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";
import type {
  ConnectRigResult,
  DriverInfo,
  DriversResponse,
  Profile,
  ProfileDevice,
  ProfileRow,
  RoleResult,
} from "../types";
import { api, ApiError } from "../api";
import {
  activateProfile,
  addDriverForHardware,
  captureProfile,
  connectRig,
  discoverHardware,
  getProfile,
  hwAlreadyConfigured,
  listBackends,
  listDrivers,
  listProfiles,
  saveProfile,
  setProvidersConfig,
} from "../api/backends";
import { useConfig, useStore } from "../store";
import { accessPhrase, useCanConfigBackend } from "../lib/caps";
import {
  buildRigSpec,
  deviceChoices,
  eligibleDrivers,
  hardwareAssignments,
  hasRealMotion,
  liveRoleCount,
  guiderSlotNote,
  guiderSlotState,
  loadAssignments,
  profileActivateConfirm,
  profileConnectsNothing,
  profileResolvesRealMotion,
  profileSaveLock,
  profileSaveSource,
  persistableAssignedCount,
  saveAssignments,
  simAssignments,
  slotState,
  type Assignment,
  type AssignmentMap,
} from "../lib/equipment";
import { confirmDialog } from "../components/ConfirmDialog";
import { FilterNamesModal } from "../components/capture/FilterNamesModal";
import TasksPanel from "../components/equipment/TasksPanel";
import { DEFAULT_PROVIDERS } from "../lib/providerWrite";
import RotatorCard from "../components/equipment/RotatorCard";
import BackendLinkGrid from "../components/settings/BackendLinkGrid";
// One implementation of "wait for a profile connect to actually land" — see
// ProfileList, which owns profile semantics and explains why this is a poll and
// not `useBusy` (the activate lane is the one long op the rig publishes no lane
// for).
import { waitForProfileActive } from "../components/settings/ProfileList";
import { ROLE_LABEL } from "../components/settings/backendMeta";
import { EmptyState, Field, HonestButton, InfoDot, Led, Panel } from "../components/ui";
import { Icon } from "../components/icons";

const SLOT_WORD: Record<string, { word: string; tone: string }> = {
  unassigned: { word: "UNASSIGNED", tone: "text-faint" },
  ok: { word: "ASSIGNED", tone: "text-accent" },
  "driver-removed": { word: "DRIVER REMOVED", tone: "text-bad" },
  "driver-unreachable": { word: "DRIVER UNREACHABLE", tone: "text-warn" },
  "driver-disabled": { word: "DRIVER DISABLED", tone: "text-warn" },
  // amended post-review: the driver is up, but its LIVE probe no longer
  // offers the assigned device (e.g. a camera unplugged mid-session).
  "device-missing": { word: "DEVICE MISSING", tone: "text-warn" },
};

// ------------------------------------------------- is THIS pick what is running?
//
// `status.connected` is `{role: Device.describe()}` verbatim — hub.summary()
// ["devices"] straight into poll_status — so it carries more than `DeviceInfo`
// declares: the Alpaca addressing a ConnSpec was built from (`dev_type` /
// `dev_num`), plus host/port/role/backend. DeviceInfo names only the three
// fields every other surface reads, so the extra keys are narrowed HERE, once,
// with their provenance written down beside them.
//
// Every field is optional on purpose. A server too old to publish a key, or a
// backend that simply has no addressing, must read as "I don't know" — never as
// "a different device".
type LiveDevice = {
  name?: string;
  connected?: boolean;
  dev_type?: string;
  dev_num?: number;
};

/** Driver types whose `ConnSpec.extra['name']` provably reaches the device the
 *  server then describes back to us — i.e. whose `get_device` reads it. Each
 *  one was read, not assumed: `NativeSession.get_device` (`name = (conn.extra
 *  or {}).get("name") …`, which is also what ascom-local delegates to),
 *  zwo_am5, zwo_usb, zwo_asi, player_one, wanderer_snowflake, asiair_backend.
 *
 *  Deliberately NOT `sim`: `SimSession.get_device` documents `conn` as
 *  "accepted for interface parity but unused", so the offer this page stores
 *  ("Simulated camera") is described back as "Sim Camera 533MM" — comparing
 *  those two strings is what reported an eleven-role simulator rig as eleven
 *  pending edits. NOT `nina` either: its session is bound at `open()` and the
 *  names come from NINA's own equipment info, not from us. NOT `phd2` (no
 *  device at all).
 *
 *  Unlisted means NOT TRUSTED. A backend earns a place here by round-tripping
 *  the name, never by being new — the failure this list exists to prevent is a
 *  fabricated "(N changed)", and that is what an unvetted default would bring
 *  back. */
const NAME_ROUND_TRIPS = new Set([
  "alpaca", "ascom-local", "zwo-am5", "zwo-usb", "zwo-asi", "player-one",
  "wanderer-snowflake", "asiair",
]);

/** "same" / "different" / "unknown" — three answers, because two would force a
 *  guess. Only "different" un-adopts a role, so an identity we cannot establish
 *  never manufactures a disagreement. */
export type IdentityVerdict = "same" | "different" | "unknown";

/** Does the device the server reports for a role match the pick on this screen?
 *
 *  Compared on the identity the server actually ROUND-TRIPS. The Alpaca lane
 *  (native / ascom-local) echoes `dev_type` + `dev_num` back through
 *  `describe()` exactly as the ConnSpec sent them, so that is authoritative
 *  when either side carries it — including when only ONE side does, which is
 *  itself evidence: an Alpaca-addressed pick cannot be the device filling this
 *  role if that device reports no Alpaca addressing (and vice versa).
 *
 *  The display name is corroboration, not identity: it decides only when
 *  neither side is Alpaca-addressed AND the pick's driver is one that provably
 *  round-trips it (`NAME_ROUND_TRIPS`) — which is how a "ZWO AM5N" pick is
 *  still told apart from a live "Sim Mount EQ6-R" on the USB/serial on-ramp,
 *  where offers carry no addressing at all.
 *
 *  `live == null` or a `dev_type` key the server never sent leaves us with
 *  nothing to compare, and the answer is "unknown". Liveness is the CALLER's
 *  question (the guider is an engine with no `connected` entry at all). */
export function compareRoleIdentity(
  a: Assignment,
  live: LiveDevice | null | undefined,
  driverType?: string,
): IdentityVerdict {
  const pinned = a.devType != null || a.devNum != null;
  // `"dev_type" in live` distinguishes "the server says this device has no
  // Alpaca addressing" (real information: sim, nina, serial hardware) from "the
  // server never told us" (an older build) — the second must not read as a
  // contradiction, or every role on it would report a pending edit forever.
  const addrKnown = !!live && "dev_type" in live;
  const liveType = (live?.dev_type ?? "").trim();
  if (addrKnown && (pinned || liveType)) {
    if (!pinned || !liveType) return "different";
    return liveType === (a.devType ?? "").trim() && (live?.dev_num ?? 0) === (a.devNum ?? 0)
      ? "same"
      : "different";
  }
  const want = (a.name ?? "").trim();
  const have = (live?.name ?? "").trim();
  if (want && have && driverType && NAME_ROUND_TRIPS.has(driverType))
    return want === have ? "same" : "different";
  return "unknown";
}

export default function EquipmentView(): JSX.Element {
  const status = useStore((s) => s.status);
  const showToast = useStore((s) => s.showToast);
  const canConfig = useCanConfigBackend();
  const config = useConfig();

  const [data, setData] = useState<DriversResponse | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [assignments, setAssignments] = useState<AssignmentMap>(() => loadAssignments());
  const [results, setResults] = useState<Record<string, RoleResult>>({});
  // UX review #18: the busy flag used to be a bare boolean, so the ONLY
  // in-progress label in Rig Actions — "Working…" — always rendered on the
  // Connect Rig button, whichever button you had actually pressed. MEASURED:
  // tap "▶ Detect hardware rig" and the progress appears three controls away
  // while the button you touched looks untouched. Naming the action fixes the
  // label without changing what gets disabled (`busy` is still every one of
  // them; two rig actions must never overlap).
  const [busyWhat, setBusyWhat] = useState<
    null | "connect" | "scan" | "disconnect" | "save" | "load" | "activate"
  >(null);
  const busy = busyWhat !== null;
  const [profiles, setProfiles] = useState<ProfileRow[] | null>(null);
  const [profileName, setProfileName] = useState("");

  const reloadDrivers = async () => {
    try {
      setData(await listDrivers());
      setLoadErr(null);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "couldn't load drivers");
    }
  };
  const reloadProfiles = () =>
    listProfiles().then(setProfiles).catch(() => setProfiles(null));
  useEffect(() => {
    void reloadDrivers();
    void reloadProfiles();
  }, []);

  const drivers = useMemo(() => data?.drivers ?? [], [data]);
  const roles = data?.roles ?? [];
  const links = status?.backend_links ?? [];
  const linkByRole = useMemo(
    () => Object.fromEntries(links.map((l) => [l.role, l])),
    [links],
  );

  const setAssignment = (role: string, a: Assignment | null) => {
    setAssignments((m) => {
      const next = { ...m, [role]: a };
      saveAssignments(next);
      return next;
    });
  };

  // Any configured driver that is enabled but unreachable → one visible
  // banner (spec §5) instead of dead dropdown entries.
  const downDrivers = drivers.filter(
    (d) => !d.implicit && d.enabled && !d.status.reachable,
  );

  const assignedCount = roles.filter((r) => assignments[r]).length;

  // The assignment map as last CONNECTED, so an edit is distinguishable from a
  // re-press of the same thing. Null until this browser has connected or has
  // adopted a rig that was already up (below).
  const [connectedMap, setConnectedMap] = useState<AssignmentMap | null>(null);
  // What a SAVE would actually store: the save loop skips simulator rows (they
  // carry no persistable driver_id), so gating on the raw pick count let eleven
  // sim picks open Save and write a profile with zero devices.
  const savableAssigned = persistableAssignedCount(assignments);
  const simOnlyPicks = assignedCount > 0 && savableAssigned === 0;
  // ONE definition of "this role is live" for the whole page — the rows, the
  // counts in the copy, and what Save decides to capture. It joins the two
  // things the server publishes, because neither alone is the whole truth:
  // `status.connected` is the DEVICE map (no entry for the guider, which is an
  // engine), and `backend_links[].connected` is the per-role live state the
  // Link Status grid renders (hub._role_live_connected — device OR engine).
  // Reading only the first is what put "GUIDING · UNASSIGNED" three inches from
  // "GUIDING · CONNECTED ✓".
  const isRoleLive = (r: string) =>
    !!status?.connected?.[r]?.connected || !!linkByRole[r]?.connected;
  const connectedCount = liveRoleCount(status);

  // WOULD PRESSING CONNECT CHANGE ANYTHING? Until now the button looked
  // identical whether the rig was already up with these exact picks or had
  // three edits waiting, so the only way to find out was to press it and watch
  // the whole rig cycle. Roles whose pick differs from what is actually
  // running:
  const rigUp = connectedCount > 0;
  // Compared BY VALUE. An Assignment is an object, and re-picking the same
  // driver from the dropdown builds a fresh one — reference equality would call
  // that an edit and light the button up over a change that is not one.
  const asgKey = (a: Assignment | null | undefined) =>
    a ? JSON.stringify(a) : "";
  const dirtyRoles = connectedMap
    ? roles.filter((r) => asgKey(assignments[r]) !== asgKey(connectedMap[r]))
    : [];
  // What the SERVER reports about the device filling a role — the whole
  // `describe()` block, not just its display name (see LiveDevice above). The
  // guider is the exception the whole page keeps running into: it is an engine,
  // not a device, so it has no `connected` entry at all and its name rides
  // `status.guider` (see RoleSlot) — with no addressing to compare, which is
  // exactly the "unknown" compareRoleIdentity is built to return.
  const liveDeviceOf = (r: string): LiveDevice | null => {
    const dev = status?.connected?.[r] as LiveDevice | undefined;
    if (dev?.connected) return dev;
    if (r === "guider" && linkByRole[r]?.connected)
      return status?.guider ? { name: status.guider.name, connected: true } : null;
    return null;
  };
  // Adopt a rig that was already up when this browser arrived (another tab, a
  // tablet, a resumed session). Without this the button would sit enabled and
  // unexplained after every reload, which is the state it is meant to remove.
  //
  // But adoption used to copy the local picks WHOLESALE, and this browser's
  // localStorage knows nothing about a rig it did not start. A rig brought up
  // by a boot profile — or the one-tap simulator, or the field tablet — was
  // therefore reported as "the rig is connected with these exact picks" purely
  // because the picks existed: the button went dim, its title claimed there was
  // nothing to apply, and the only way to make it pressable was to spoil a row
  // and put it back. So a role is adopted as already-running only when the
  // server CORROBORATES it: same role live, same device identity.
  //
  // IDENTITY, NOT DISPLAY NAME (review round 2). Matching on the name alone was
  // wrong in the direction that costs the most: a simulator rig connected from
  // this very screen reported ELEVEN pending edits on every reload, because
  // `SimSession.get_device` ignores `ConnSpec.extra['name']` — the pick we
  // stored says "Simulated camera" and the device describes itself as "Sim
  // Camera 533MM". "(N changed)" over a rig that IS those picks is the exact
  // fabricated number this button family exists to make trustworthy, and the
  // reconnect it invites tears the whole rig down. compareRoleIdentity compares
  // what the server round-trips instead, and answers "unknown" rather than
  // guessing; only a positive "different" un-adopts a role.
  const adoptRunningMap = (): AssignmentMap => {
    const out: AssignmentMap = {};
    for (const r of roles) {
      const a = assignments[r];
      if (!a) { out[r] = null; continue; }
      if (!isRoleLive(r)) { out[r] = null; continue; }
      const driverType = drivers.find((d) => d.id === a.driverId)?.type;
      const verdict = compareRoleIdentity(a, liveDeviceOf(r), driverType);
      out[r] = verdict === "different" ? null : a;
    }
    return out;
  };
  useEffect(() => {
    // roles.length: /api/drivers and the first status frame race, and adopting
    // against an empty role list would claim nothing matched. It is also what
    // keeps `drivers` (read for the driver TYPE, above) fresh without a dep of
    // its own — both come off the same `data` state, so a non-empty role list
    // means the driver rows are loaded too.
    if (rigUp && connectedMap === null && roles.length > 0)
      setConnectedMap(adoptRunningMap());
    if (!rigUp && connectedMap !== null) setConnectedMap(null);
    // assignments intentionally NOT a dependency: adopting on every keystroke
    // would make an edit look like it was already connected.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rigUp, connectedMap, roles.length]);
  // Roles the server reports as LIVE while this screen holds no assignment for
  // them — the #41 mismatch (a rig connected from anywhere but here).
  const liveUnassignedRoles = roles.filter((r) => isRoleLive(r) && !assignments[r]);

  // The one connect-rig implementation, parameterized on the AssignmentMap to
  // drive (root-cause fix, sim-connect desync EQ-01 ×3 rounds): both the
  // Connect Rig button (the user's own dropdown picks) and ▶ Simulator rig
  // (an all-sim map it builds itself, see doSimRig) funnel through here so
  // Devices/Connect Rig/Link Status always agree on ONE connected rig —
  // Link Status in particular only populates from the RigSpec connect path
  // (backend_links stays [] for the legacy /api/connect/sim shortcut this
  // replaces), so reusing this path is what makes that surface honest too.
  const connectAssignments = async (map: AssignmentMap) => {
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
    setBusyWhat("connect");
    setResults({});
    try {
      const res: ConnectRigResult = await connectRig(buildRigSpec(map));
      const resultMap: Record<string, RoleResult> = {};
      for (const r of res.results) resultMap[r.role] = r;
      setResults(resultMap);
      const okCount = res.results.filter((r) => r.ok).length;
      // What is actually RUNNING on the rig now. Everything below compares the
      // live picks against this, so the button can say whether pressing it
      // would change anything.
      //
      // Built from the RESULTS, not from what we asked for (review round 2).
      // Recording the whole map as connected made a partial connect — focuser
      // refused, camera and mount up — read "Rig connected", dim, titled "Every
      // pick above is already what the rig is running", which is #15's literal
      // complaint: the only way to retry the one role that failed was to spoil
      // a row and put it back. A role the server did not bring up is not
      // running, so it stays unclaimed and shows up as the pending edit it is.
      // (`res.results` covers every role in `map`: buildRigSpec puts each
      // assigned role in spec.roles, and the orchestrator emits exactly one
      // RoleResult per requested role.)
      const landed: AssignmentMap = {};
      for (const [role, a] of Object.entries(map))
        landed[role] = resultMap[role]?.ok ? a : null;
      setConnectedMap(landed);
      const attempted = res.results.filter((r) => r.attempted).length;
      if (okCount === 0 && attempted > 0) {
        showToast("error", "Rig connect attempted but no roles came up — see per-row errors");
        void reloadDrivers(); // cache honesty: reflect reality immediately
      } else {
        showToast("success", `Rig connected — ${okCount}/${attempted} roles up`);
        if (okCount < attempted) void reloadDrivers();
      }
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.status === 422
            ? `Invalid rig: ${e.message}`
            : e.message
          : e instanceof Error
            ? e.message
            : "connect failed";
      showToast("error", msg);
      void reloadDrivers();
    } finally {
      setBusyWhat(null);
    }
  };

  const doConnect = () => connectAssignments(assignments);

  // UX review #17, both halves.
  //
  // (a) DISCONNECT was a one-tap `btn-danger` with no dialog — on a touch
  //     device, next to Connect Rig, mid-run. It now confirms, and names what
  //     it will actually do (aborting a running sequence is not obvious from
  //     the word "disconnect").
  //
  // (b) After the POST the UI kept reporting every device CONNECTED. Root
  //     cause: `status` only ever arrives on the WS "status" frame, and
  //     `hub.disconnect_all()` cancels the status-poll task as its first step —
  //     so the last frame the client ever sees is the one from BEFORE the
  //     teardown, and it sticks until a page reload. RIG ACTIONS looked right
  //     only because it reads local state we cleared ourselves. The client
  //     therefore re-reads /api/status for the action it initiated and feeds it
  //     through the SAME store reducer a WS frame would take, so nothing is
  //     re-derived here.
  const doDisconnect = () =>
    void (async () => {
      // Same `isRoleLive` authority the rows and the Profiles copy use, so the
      // page never quotes two different sizes for one rig (it read "drops 10
      // connected devices" three inches under "save the 11 connected devices").
      const live = connectedCount;
      const seqState = useStore.getState().sequence?.state;
      const running = seqState === "running" || seqState === "paused";
      const ok = await confirmDialog({
        title: "Disconnect the whole rig?",
        body: running
          ? "A sequence is running. Disconnecting aborts it and drops every device, including the mount and the guider."
          : `This drops ${live || "every"} connected device${live === 1 ? "" : "s"} — camera, mount, guider and the rest. You can reconnect from this screen.`,
        tone: "danger",
        confirmLabel: "Disconnect",
        cancelLabel: "Stay connected",
      });
      if (!ok) return;
      setBusyWhat("disconnect");
      try {
        await api.post("/api/disconnect");
        setResults({});
        // (b): authoritative re-read, dispatched exactly as a WS status frame.
        try {
          const fresh = await api.get<Record<string, unknown>>("/api/status");
          useStore.getState().handleEvent({
            type: "status",
            data: fresh,
            ts: Date.now() / 1000,
          });
        } catch {
          /* the POST already succeeded; the next WS frame will correct us */
        }
        showToast("success", "Disconnected");
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "disconnect failed");
      } finally {
        setBusyWhat(null);
      }
    })();

  // Builds the AssignmentMap first (state + localStorage, same as a manual
  // per-row pick) and THEN drives it through connectAssignments — never the
  // legacy /api/connect/sim shortcut, whose ConnectResult the Equipment
  // surface has no way to read into assignments/backend_links (root cause).
  const doSimRig = async () => {
    // CONFIRM WHEN A REAL RIG IS UP. connectAssignments only holds for real
    // MOTION, and a simulator map contains none — so this one button could
    // disconnect live hardware and replace it with fakes on a single tap, with
    // no dialog, while the scope was attached and tracking. Being quietly
    // simulated is a genuinely expensive state to be in: a whole polar
    // alignment session was spent on 2026-08-02 against a simulator nobody had
    // noticed was selected.
    if (connectedCount > 0) {
      const ok = await confirmDialog({
        title: "Replace the connected rig with simulators?",
        body: `${connectedCount} role${connectedCount === 1 ? " is" : "s are"} `
          + "connected to real hardware. This disconnects them and connects "
          + "simulated devices instead — nothing you do afterwards reaches the sky.",
        tone: "danger",
        confirmLabel: "Use simulators",
      });
      if (!ok) return;
    }
    const map = simAssignments(roles, drivers);
    setAssignments(map);
    saveAssignments(map);
    void connectAssignments(map);
  };

  // "▶ Detect hardware rig" (native-hardware follow-up 2026-07-21): scan every
  // hardware backend (the SAME discoverHardware() helper DriversPanel's "Scan
  // for USB/serial hardware" uses), add whatever isn't already configured,
  // then auto-fill the AssignmentMap with the hardwareAssignments() heuristic
  // — mirrors doSimRig in every way except it does NOT connect; the user
  // reviews the picks and presses Connect Rig themselves.
  //
  // UX review #18, the other half: an empty scan reported SUCCESS. A beginner
  // with nothing plugged in tapped this, seven real probes went out and all
  // returned 200 with no devices, and the app said "Detected 0 device(s) — 0
  // role(s) assigned, review and Connect Rig" in the success tone — which reads
  // as "it worked" and names an action that cannot work. An empty scan is now
  // reported as what it is, with the one number that is genuinely invisible
  // (how many hardware backends were actually asked) and the two things a user
  // can do about it.
  const doDetectHardware = () =>
    void (async () => {
      setBusyWhat("scan");
      try {
        // Same predicate discoverHardware() scans on, so the count reported is
        // the number of probes that really went out — never a guess.
        const scanned = (await listBackends()).filter(
          (b) => b.hardware && b.discoverable && b.driver_type,
        ).length;
        const found = await discoverHardware();
        if (found.length === 0) {
          showToast(
            "warning",
            `No hardware answered — ${scanned} USB/serial backend${scanned === 1 ? "" : "s"} ` +
              `scanned, none reported a device. Check power and cables, or add a driver by ` +
              `hand under Settings → Backend Drivers.`,
          );
          return;
        }
        const toAdd = found.filter((f) => !hwAlreadyConfigured(f, drivers));
        let added = 0;
        try {
          for (const f of toAdd) {
            // Sequential (see DriversPanel.addAllHw): each add mints a
            // server-side id off the current config file.
            await addDriverForHardware(f);
            added++;
          }
        } catch (e) {
          // A batch write that fails PART WAY has still created drivers, and
          // they must appear: bailing to the outer catch left the list showing
          // none of them, so the next tap of this button — or of Settings →
          // Backend Drivers' "Add all" — created a second row for the same
          // physical device. Reload, then say how far it got.
          await reloadDrivers();
          showToast(
            "error",
            `Added ${added} of ${toAdd.length} driver${toAdd.length === 1 ? "" : "s"}, then ` +
              `${e instanceof Error ? e.message : "the next one failed"} — the ones that ` +
              `landed are listed above; re-run the scan to add the rest.`,
          );
          return;
        }
        const fresh = await listDrivers();
        setData(fresh);
        const map = hardwareAssignments(fresh.drivers, fresh.roles);
        setAssignments(map);
        saveAssignments(map);
        const assignedRoles = Object.values(map).filter(Boolean).length;
        if (assignedRoles === 0) {
          // Devices exist but nothing matched a role: still not a success.
          showToast(
            "warning",
            `Found ${found.length} device(s)` +
              (toAdd.length ? `, added ${toAdd.length} driver(s)` : "") +
              ` — but none offered a device slot, so no row was filled in. Pick a driver` +
              ` on the rows above.`,
          );
          return;
        }
        showToast(
          "success",
          `Detected ${found.length} device(s)` +
            (toAdd.length ? `, added ${toAdd.length} driver(s)` : "") +
            ` — ${assignedRoles} role(s) assigned, review and Connect Rig`,
        );
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "hardware detection failed");
      } finally {
        setBusyWhat(null);
      }
    })();

  // ------------------------------------------------------------- profiles
  //
  // UX review S1, the structural finding — the one authority moves.
  //
  // A profile now captures the CONNECTED rig: POST /api/profiles/capture, the
  // same server-side snapshot Settings → Save Current Rig takes, so there is
  // ONE implementation of "what a profile is made of". This browser's
  // AssignmentMap is the FALLBACK, and only for a rig that has been picked from
  // the dropdowns but not yet connected.
  //
  // Both halves of the old behaviour were measured on a live instance first:
  //
  //   * THE DEAD END. Connect the way the setup guide tells you to and this
  //     browser's map is empty, so Save sat at `disabled=true`, opacity 0.35,
  //     no title, no aria-label — beside eleven live devices. Typing a name,
  //     pressing Enter and tapping it produced nothing at all.
  //   * THE RIG-KILLER. The old path dropped every sim row and wrote
  //     `primary_backend: "none"`, so a saved simulator rig persisted as
  //     `devices: [] · mode: "empty"` — the one profile shape that resolves to
  //     `RigSpec(primary="none", roles={})` and connects NOTHING. Capturing the
  //     same rig writes `primary_backend: "sim"`: disconnect → activate → 10
  //     devices back, verified against /api/status.
  const saveSource = profileSaveSource(connectedCount, savableAssigned);
  const saveLock = profileSaveLock({
    permission: canConfig
      ? null
      : `Saving a profile needs ${accessPhrase("config.backend")}.`,
    name: profileName,
    live: connectedCount,
    assigned: savableAssigned,
    simOnly: simOnlyPicks,
    busy,
  });

  const doSaveProfile = () =>
    void (async () => {
      if (saveLock) {
        showToast("warning", saveLock);
        return;
      }
      const name = profileName.trim();
      setBusyWhat("save");
      try {
        if (saveSource === "connected-rig") {
          const row = await captureProfile(name);
          // The capture can only describe devices by ADDRESS (host/port/
          // dev_type/dev_num) — that is all the server can see. Where THIS
          // browser also knows which configured driver fills a role, stamp the
          // driver_id on as well: the registry then re-resolves the address at
          // connect time (a USB serial path moves between sessions) and Load
          // can restore the dropdowns. Strictly additive and best-effort — the
          // captured profile is already saved and already activates without it.
          //
          // Re-read the full record first: `captureProfile` is typed
          // `Promise<ProfileRow>` (the list shape, no device rows), same as
          // ProfileList's update-from-rig path does before merging.
          let partial: string | null = null;
          try {
            const captured = await getProfile(row.id);
            let enriched = false;
            const devices = captured.devices.map((d) => {
              const a = assignments[d.role];
              if (!a || a.driverId === "sim" || d.driver_id) return d;
              enriched = true;
              return { ...d, driver_id: a.driverId };
            });
            if (enriched) await saveProfile({ ...captured, devices });
          } catch {
            partial =
              "but which driver fills each role wasn't recorded, so Load won't " +
              "restore the dropdowns";
          }
          const n = connectedCount;
          showToast(
            partial ? "warning" : "success",
            `Profile "${name}" saved — the ${n} device${n === 1 ? "" : "s"} connected now` +
              (partial ? `, ${partial}.` : ". Activate it to bring this rig back."),
          );
        } else {
          // Nothing is connected: store the picks, and SAY that is what this is.
          // Build device rows directly (the guard narrows `a` to Assignment, so
          // no cast is needed); sim assignments are the implicit built-in and
          // carry no persistable driver_id. `driver_id` is a typed optional
          // field on ProfileDevice (Task 5).
          const devices: ProfileDevice[] = [];
          for (const [role, a] of Object.entries(assignments)) {
            if (!a || a.driverId === "sim") continue;
            devices.push({
              role,
              backend: "native", // placeholder; the server resolves via driver_id
              driver_id: a.driverId,
              dev_type: a.devType ?? "",
              dev_num: a.devNum ?? 0,
              name: a.name ?? "",
              host: "",
              port: 0,
              extra: {},
            });
          }
          // Build a fully-typed Profile (real Profile has 10 required fields)
          // rather than an `as unknown as Profile` double-cast — that would
          // silently disable type-checking on `devices`. `id: ""` matches no
          // stored file, so the server mints a fresh id (saveProfile contract).
          const profile: Profile = {
            id: "",
            name,
            // "none" = explicit-only. Honest here (these ARE the only roles the
            // profile knows) and no longer a trap, because Activate now states
            // out loud when a profile puts nothing back.
            primary_backend: "none",
            devices,
            nina_host: null,
            nina_port: 0,
            phd2_host: null,
            phd2_port: 0,
            optics: null,
            site_name: null,
            // Task-override snapshot (spec §4.4): the profile carries the
            // CURRENT global task routing so Activate restores it.
            providers: config?.providers ? { ...config.providers } : null,
          };
          await saveProfile(profile);
          showToast(
            devices.length > 0 ? "success" : "warning",
            devices.length > 0
              ? `Profile "${name}" saved — ${devices.length} picked slot` +
                  `${devices.length === 1 ? "" : "s"}, not yet proven. Connect the rig, ` +
                  `then save again to store what actually came up.`
              : `Profile "${name}" saved, but every pick is the built-in simulator, ` +
                  `which a profile can't address — it will connect nothing. Connect the ` +
                  `rig first, then save.`,
          );
        }
        setProfileName("");
        void reloadProfiles();
      } catch (e) {
        const msg =
          e instanceof ApiError && e.status === 409
            ? "The rig disconnected before the snapshot — reconnect, then save."
            : e instanceof Error
              ? e.message
              : "profile save failed";
        showToast("error", msg);
      } finally {
        setBusyWhat(null);
      }
    })();

  // Which row's Load is in flight (for its own label), and which load is the
  // CURRENT one. Load is three round trips — read the profile, write the task
  // providers, re-read config — and had no in-flight state at all, so Load A /
  // Load B was two interleaved sequences racing: B's assignments could land
  // first and A's provider write last, leaving B's dropdowns above A's task
  // routing on the server. The generation token is checked before every write
  // and after every read, so a superseded load stops instead of finishing on
  // top of the newer one.
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const loadGen = useRef(0);

  const doLoadProfile = (id: string) =>
    void (async () => {
      const gen = ++loadGen.current;
      setBusyWhat("load");
      setLoadingId(id);
      try {
        const p = await getProfile(id);
        if (gen !== loadGen.current) return;
        const next: AssignmentMap = {};
        for (const d of p.devices ?? []) {
          // driver_id is a typed optional field on ProfileDevice (Task 5);
          // server-persisted and round-tripped to restore the assignment.
          const driverId = d.driver_id;
          if (!driverId) continue; // raw-addressing rows: connect via Activate
          next[d.role] = {
            driverId,
            devType: d.dev_type || undefined,
            devNum: d.dev_num,
            name: d.name || undefined,
          };
        }
        setAssignments(next);
        saveAssignments(next);
        // Restore the profile's task overrides into global config so the Tasks
        // rows and the server's resolution match the loaded snapshot. Viewers
        // (no config.backend) skip this — assignments alone are still useful.
        if (p.providers && canConfig && gen === loadGen.current) {
          try {
            await setProvidersConfig({ ...DEFAULT_PROVIDERS, ...p.providers });
            if (gen !== loadGen.current) return;
            await useStore.getState().loadConfig();
          } catch {
            showToast("warning", "Assignments loaded, but task overrides couldn't be restored");
          }
        }
        if (gen !== loadGen.current) return;
        showToast("success", `Loaded assignments from "${p.name}" — review, then Connect`);
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "profile load failed");
      } finally {
        // Only the CURRENT load owns the busy flag: a superseded one must not
        // clear it out from under the load that replaced it.
        if (gen === loadGen.current) {
          setBusyWhat(null);
          setLoadingId(null);
        }
      }
    })();

  // ACTIVATE, gated on what it DROPS (UX review S1, the novice's catastrophe).
  //
  // This button had NO confirmation at all — not even the `resolvesRealMotion`
  // one Settings → Profiles carries. Every activate runs `hub._teardown()`
  // first (`hub.py::_connect_rigspec_unlocked`), so on a live rig it is a
  // destructive action wearing a play glyph. MEASURED: 10 devices connected,
  // one tap, no dialog, 0 devices — and the profile it activated stored
  // nothing, so there was nothing to come back. On the step the setup guide had
  // just described as how you get your rig back.
  //
  // The predicate is now the live rig, not the profile's hardware. The full
  // profile is fetched first so the dialog can say whether anything reconnects
  // (`profileConnectsNothing`) — the same "ask the server, don't trust the
  // render" instinct ProfileList's delete path already uses.
  const doActivateProfile = (row: ProfileRow) =>
    void (async () => {
      // In-flight state, and it lasts as long as the RIG does. Activate was the
      // one rig action on this screen with no busy state at all: the pre-flight
      // `getProfile` is a whole round trip before any dialog appears, and the
      // POST after it `_spawn_connect`s and resolves in ~40ms while the
      // teardown has not even finished — so the button re-armed instantly
      // beside a rig that was half-down. Every other control here is already
      // gated on `busy`, which also makes the row's own "a rig action is
      // already running" reason (below) reachable for the first time.
      setBusyWhat("activate");
      try {
        let full: Profile | null = null;
        try {
          full = await getProfile(row.id);
        } catch {
          /* fall through: still confirm on the teardown, just without the
             "puts nothing back" escalation we could not verify */
        }
        const seqState = useStore.getState().sequence?.state;
        const spec = profileActivateConfirm({
          name: row.name,
          connectsNothing: full ? profileConnectsNothing(full) : false,
          realMotion: full
            ? profileResolvesRealMotion(full)
            : row.mode !== "empty" && row.mode !== "alpaca",
          liveDevices: connectedCount,
          sequenceRunning: seqState === "running" || seqState === "paused",
        });
        if (spec && !(await confirmDialog(spec))) return;
        await activateProfile(row.id);
        showToast("info", `Activating "${row.name}" — connecting the rig…`);
        const rows = await waitForProfileActive(row.id, setProfiles);
        if (rows) {
          showToast("success", `"${row.name}" is active — the link grid has the per-role result`);
        } else {
          void reloadProfiles();
          showToast(
            "warning",
            `"${row.name}" is not active yet — the connect is still running, or it ` +
              `failed. The Link Status grid shows how far it got.`,
          );
        }
      } catch (e) {
        // Two different 409s reach here. The coded one is the sequence/loop/
        // polar guard, which `force` bypasses — but forcing aborts a running
        // sequence, so it is Settings → Profiles (which asks first) that offers
        // it, not this button. The uncoded one is _spawn_connect's own lane
        // guard, which force does NOT bypass; saying "already running" raw was
        // the whole of what the user got.
        const msg =
          e instanceof ApiError && e.code === "running"
            ? "A sequence, capture loop or polar alignment is running — stop it first, " +
              "or force-activate from Settings → Profiles."
            : e instanceof ApiError && e.status === 409
              ? "Another profile is still connecting — wait for it to finish before switching again."
              : e instanceof Error
                ? e.message
                : "activate failed";
        showToast("error", msg);
      } finally {
        setBusyWhat(null);
      }
    })();

  // ---------------------------------------------------------------- render
  // A first-load failure (no data yet) has nothing to fall back to, so it's
  // the one case that blanks the whole view (with a retry). Every later
  // reload is a BACKGROUND refresh (re-run after every connect/probe/toggle
  // for cache honesty) — a transient failure there must not discard the
  // already-loaded Devices/Tasks/Rotator/Profiles tree; it surfaces as a
  // dismissable inline banner instead (mirrors ProfileList's in-place error).
  if (loadErr && !data) {
    return (
      <Panel title="Equipment">
        <EmptyState
          icon="alert"
          title="Couldn't load drivers"
          hint={loadErr}
          action={
            <button type="button" className="btn btn-accent !py-1.5" onClick={() => void reloadDrivers()}>
              Retry
            </button>
          }
        />
      </Panel>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_minmax(260px,340px)]">
      {/* UX review #51: the nav calls this EQUIPMENT and every button on it
          calls the same thing a "rig", with nothing on screen saying they are
          the same word. One heading settles it. */}
      <div className="lg:col-span-2 flex items-baseline gap-3 flex-wrap">
        <h1 className="font-display text-lg tracking-[0.2em] text-ink uppercase">
          Equipment
        </h1>
        <span className="text-[11px] text-dim">
          your rig
        </span>
      </div>
      {/* Background-refresh failure (data already loaded): a non-destructive
          inline banner + retry, spanning both columns — never the full-panel
          blank the first-load EmptyState above uses. */}
      {loadErr && data && (
        <div className="lg:col-span-2 flex items-center gap-2 border border-bad/50 bg-bad/5 px-3 py-2">
          <Icon name="alert" size={14} className="text-bad shrink-0" />
          <span className="text-sm text-ink flex-1">Couldn't refresh drivers: {loadErr}</span>
          <button type="button" className="btn !py-1 !px-2 text-[11px]" onClick={() => void reloadDrivers()}>
            Retry
          </button>
        </div>
      )}
      <div className="flex flex-col gap-4 min-w-0">
        <Panel
          title="Devices"
          right={
            <InfoDot
              label="About device assignment"
              content="For each device slot, pick which configured driver runs it — only drivers that actually offer that device are listed. Manage drivers under Settings → Backend Drivers."
            />
          }
        >
          {/* #41 cont.: the explanation belongs ONCE at panel level — ten
              identical sub-lines under ten rows is noise, not an explanation. */}
          {liveUnassignedRoles.length > 0 && (
            <p className="text-[11px] text-dim mb-3 inline-flex items-start gap-1.5 leading-snug">
              <Icon name="info" size={12} className="shrink-0 mt-0.5" />
              <span>
                {liveUnassignedRoles.length} device
                {liveUnassignedRoles.length === 1 ? " is" : "s are"} connected
                but not assigned here — this rig was started somewhere else (the
                one-tap simulator, a boot profile, or Profiles → Activate). Pick
                a driver on those rows to save them into a profile.
              </span>
            </p>
          )}
          {downDrivers.length > 0 && (
            <p className="text-[11px] text-warn mb-3 inline-flex items-center gap-1.5">
              <Icon name="alert" size={12} />
              {downDrivers.map((d) => d.label).join(", ")}{" "}
              {downDrivers.length === 1 ? "is" : "are"} configured but unreachable —
              assignments stay put and re-light when it returns.
            </p>
          )}
          <div className="flex flex-col gap-2.5">
            {roles.map((role) => (
              <RoleSlot
                key={role}
                role={role}
                drivers={drivers}
                assignment={assignments[role] ?? null}
                link={linkByRole[role]}
                result={results[role]}
                status={status}
                disabled={!canConfig || busy}
                onAssign={(a) => setAssignment(role, a)}
              />
            ))}
            {roles.length === 0 && (
              <p className="text-dim text-xs">Loading device slots…</p>
            )}
          </div>
        </Panel>

        <TasksPanel drivers={drivers} busy={busy} />
        <RotatorCard />

        <Panel title="Rig Actions">
          {/* UX-31: naive first-run — promote the real bootstraps instead of a
              greyed-out primary "Connect Rig (0)". */}
          {assignedCount === 0 && (
            <p className="text-xs text-dim mb-3 leading-relaxed">
              No equipment yet — <span className="text-ink">Detect hardware rig</span> to auto-assign your
              connected gear, or start the <span className="text-ink">Simulator rig</span> to explore.
            </p>
          )}
          <div className="flex flex-wrap items-center gap-3">
            {/* House rule: never the native `disabled` attribute on a control
                a user could want to press. With nothing assigned this used to
                render at opacity 0.35 with no reachable reason (#16's
                screenshot evidence). It is now dim + aria-disabled, with the
                reason stated in the paragraph above and again beside it. */}
            {/* Three states, because there are three situations and they used
                to look the same:
                  nothing assigned  — dim, nothing to connect
                  connected, clean  — dim, pressing would re-cycle a working rig
                                      for no gain (and drop the mount mid-run)
                  edits pending     — accented, and SAYS HOW MANY, so you can
                                      tell a real change from a stray tap
                Still aria-disabled rather than the native attribute: a dim
                control must still be focusable enough to explain itself. */}
            {(() => {
              const nothingToDo = assignedCount === 0
                || (rigUp && dirtyRoles.length === 0);
              const label = busyWhat === "connect" ? "Connecting…"
                : rigUp && dirtyRoles.length > 0
                  ? `Reconnect Rig (${dirtyRoles.length} changed)`
                  : rigUp ? "Rig connected"
                    : `Connect Rig (${assignedCount})`;
              return (
                <button
                  type="button"
                  className={`btn min-h-11 ${
                    !nothingToDo && canConfig && !busy ? "btn-accent" : ""
                  } ${nothingToDo ? "!text-dim" : ""}`}
                  disabled={busy || !canConfig}
                  aria-disabled={nothingToDo || undefined}
                  title={rigUp && dirtyRoles.length > 0
                    ? `Will reconnect: ${dirtyRoles.join(", ")}`
                    : rigUp && assignedCount > 0
                      ? "Every pick above is already what the rig is running — change one to re-enable"
                      : rigUp
                        ? "A rig is connected; nothing is picked here to apply to it"
                        : undefined}
                  onClick={nothingToDo ? undefined : () => void doConnect()}
                >
                  <Icon name="link" size={14} className="inline -mt-0.5 mr-1.5" />
                  {label}
                </button>
              );
            })()}
            {assignedCount === 0 && canConfig && (
              <span className="text-[11px] text-dim">
                nothing assigned yet — pick a driver above, or use one of these
              </span>
            )}
            {/* Detect first, simulator second and quieter (QA: "the equipment
                page shouldn't list Simulator rig as first class"). On a real
                observatory the simulator is a demo and a test fixture, not one
                of two equal ways to start a night — offering it with the same
                weight as the hardware invites connecting to nothing and
                wondering why the mount will not move. It stays one tap away
                because it IS how you explore the app with the scope in the
                garage. */}
            <button type="button" className={`btn ${assignedCount === 0 ? "btn-accent" : ""}`} disabled={busy || !canConfig || roles.length === 0} onClick={doDetectHardware}>
              {busyWhat === "scan" ? "Scanning USB & serial…" : "▶ Detect hardware rig"}
            </button>
            <button
              type="button"
              className="btn !text-dim !border-line"
              title="Connect a simulated rig — for exploring the app with no hardware attached"
              disabled={busy || !canConfig || roles.length === 0}
              onClick={() => void doSimRig()}
            >
              Simulator
            </button>
            <button type="button" className="btn btn-danger" disabled={busy || !canConfig} onClick={doDisconnect}>
              {busyWhat === "disconnect" ? "Disconnecting…" : "Disconnect"}
            </button>
            {hasRealMotion(assignments, drivers) && (
              <span className="text-[11px] text-warn inline-flex items-center gap-1">
                <Icon name="alert" size={12} /> Drives a real mount/focuser — hold to confirm.
              </span>
            )}
          </div>
          {/* UX review #16. The reviewer's measurement — "11 rows ASSIGNED,
              page reload, all back to unassigned" — did NOT reproduce here:
              lib/equipment persists the map to localStorage on every pick and
              it survives a reload (verified, 3 rows in and out). What IS true
              is the half of the finding nobody can see: these picks live in
              ONE browser's localStorage. Open the rig from the desk laptop
              instead of the field tablet, or clear site data, and they are
              gone — with nothing on screen ever having said so. A profile is
              the durable store; say that while the picks are still unsaved. */}
          {assignedCount > 0 && connectedCount === 0 && canConfig && (
            <p className="text-[11px] text-dim mt-3 inline-flex items-start gap-1.5 leading-snug">
              <Icon name="info" size={11} className="shrink-0 mt-0.5" />
              <span>
                {assignedCount} slot{assignedCount === 1 ? "" : "s"} picked but
                not connected. Picks are remembered in{" "}
                <span className="text-ink">this browser only</span> — save them
                as a profile below to keep them on every device.
              </span>
            </p>
          )}
          {!canConfig && (
            <p className="text-[11px] text-dim mt-2 inline-flex items-center gap-1.5">
              <Icon name="lock" size={11} />
              Read-only — connecting equipment needs {accessPhrase("config.backend")}.
            </p>
          )}
        </Panel>

        {/* The panel chip states the ONE thing the list cannot show and the
            whole of S1 turns on: a profile lives on the controller, the
            dropdown picks live in this browser. */}
        <Panel title="Profiles" right={<span className="label text-dim">kept on the controller</span>}>
          <div className="flex flex-wrap items-end gap-2">
            {/* The field label names what will actually be stored, because the
                two sources describe different rigs and the old label named the
                wrong one whenever a rig was connected from anywhere else. */}
            <Field
              label={
                saveSource === "connected-rig"
                  ? `Save the ${connectedCount} connected device${connectedCount === 1 ? "" : "s"} as`
                  : saveSource === "assignments"
                    ? "Save the picks above as"
                    : "Save this rig as"
              }
            >
              <input
                className="field !py-1 w-[180px]"
                placeholder="Backyard rig"
                value={profileName}
                onChange={(e) => setProfileName(e.target.value)}
              />
            </Field>
            {/* House rule §11.8: never the native `disabled` attribute here.
                This was the review's clearest dead end — `disabled=true`,
                opacity 0.35, no title, no aria-label, no reason anywhere on
                screen, next to a live eleven-device rig. HonestButton stays
                pressable and ANSWERS. */}
            <HonestButton
              className="btn !py-1.5 min-h-11"
              reason={saveLock}
              onClick={doSaveProfile}
              onExplain={(r) => showToast("warning", r)}
            >
              {busyWhat === "save" ? "Saving…" : "Save"}
            </HonestButton>
          </div>
          {saveLock && (
            <p className="text-[11px] text-dim mt-2 inline-flex items-start gap-1.5 leading-snug">
              <Icon name="lock" size={11} className="shrink-0 mt-0.5" />
              <span>{saveLock}</span>
            </p>
          )}
          {profiles && profiles.length > 0 && (
            <div className="mt-3 flex flex-col gap-1.5">
              {profiles.map((p) => (
                <div key={p.id} className="flex items-center gap-2 border border-line bg-bg/60 px-3 py-2">
                  <span className="text-xs text-ink truncate flex-1">
                    {p.name}
                    {p.active && <span className="label text-accent ml-2">ACTIVE</span>}
                  </span>
                  <button type="button" className="btn min-h-11 !py-1 !px-2 text-[10px]" disabled={busy} onClick={() => doLoadProfile(p.id)}>
                    {loadingId === p.id ? "Loading…" : "Load"}
                  </button>
                  {/* House rule §11.8 again: this one carried a PERMISSION in
                      a native `disabled`, so a viewer got a grey rectangle
                      that did nothing and could not be focused to ask why. */}
                  <HonestButton
                    className="btn min-h-11 !py-1 !px-2 text-[10px]"
                    reason={
                      !canConfig
                        ? `Activating a profile needs ${accessPhrase("config.backend")}.`
                        : busy
                          ? "A rig action is already running — wait for it to finish."
                          : null
                    }
                    onClick={() => doActivateProfile(p)}
                    onExplain={(r) => showToast("warning", r)}
                  >
                    Activate
                  </HonestButton>
                </div>
              ))}
            </div>
          )}
          {connectedCount > 0 && (
            <p className="text-[11px] text-dim mt-3 inline-flex items-start gap-1.5 leading-snug">
              <Icon name="alert" size={11} className="shrink-0 mt-0.5" />
              <span>
                Activating drops the {connectedCount} device
                {connectedCount === 1 ? "" : "s"} running now before it connects
                anything — it is a swap, not an addition.
              </span>
            </p>
          )}
        </Panel>
      </div>

      {/* live per-role truth: the boot-LED tri-state grid (kept per spec §4.3) */}
      <Panel title="Link Status">
        <BackendLinkGrid links={links} dense />
      </Panel>
    </div>
  );
}

// One device slot row: assignment select → (optional) device select → slot
// state word → live LED + connected name → inline RoleResult error. The
// The guider slot is the one row whose dropdown is NOT where you choose your
// guider, and saying nothing about that made it read as a dead end: on a rig
// with a real guide camera the list showed "Simulator" and nothing else, so the
// honest conclusion was that AstroDeck cannot guide (user, 2026-07-30 — "how am
// I supposed to select a guider? the only option is simulator").
//
// It is not a dead end, it is a different shape. AstroDeck's native guider is
// not a DEVICE you assign; it is the Rust engine running on your guide CAMERA,
// picked on the Guide screen's provider row (providers.guide, resolved by
// providers.py::_resolve_guide). Only bridge guiders — PHD2, NINA — are devices
// this dropdown can fill, which is why an unconfigured rig sees only the sim.
//
// So this line says which of those three situations the rig is actually in, and
// names the screen that holds the control. It reports state the row cannot
// otherwise show; it never restates the dropdown next to it.
function GuiderSlotNote({
  drivers,
  status,
}: {
  drivers: DriverInfo[];
  status: ReturnType<typeof useStore.getState>["status"];
}): JSX.Element | null {
  const native = drivers.find((d) => d.id === "astrodeck");
  const gc = status?.guide_camera;
  const text = guiderSlotNote(
    guiderSlotState(!!native?.status.reachable, !!gc?.connected),
    { guideCamName: gc?.name, nativeError: native?.status.error },
  );
  return (
    <p className="mt-1.5 pl-[23px] text-[11px] text-dim inline-flex items-start gap-1.5 leading-snug">
      <Icon name="info" size={11} className="shrink-0 mt-0.5" />
      <span>{text}</span>
    </p>
  );
}

// guider row nests the read-only guide-camera line (spec review finding 3).
function RoleSlot({
  role,
  drivers,
  assignment,
  link,
  result,
  status,
  disabled,
  onAssign,
}: {
  role: string;
  drivers: DriverInfo[];
  assignment: Assignment | null;
  link?: { connected?: boolean; error?: string | null };
  result?: RoleResult;
  status: ReturnType<typeof useStore.getState>["status"];
  disabled: boolean;
  onAssign: (a: Assignment | null) => void;
}): JSX.Element {
  const eligible = eligibleDrivers(role, drivers);
  const state = slotState(role, assignment, drivers);
  const chosen = assignment ? drivers.find((d) => d.id === assignment.driverId) : undefined;
  const choices = chosen ? deviceChoices(role, chosen) : [];
  // UX review S1, the two-panels-disagree half. This row used to `void link`
  // and read liveness ONLY from `status.connected`, while the Link Status grid
  // three inches away read `backend_links`. For every device those agree — but
  // the GUIDER has no `connected` entry at all (the guiding engine is not a
  // device in that map), so the row printed "GUIDING · UNASSIGNED" beside a
  // green ✓ "GUIDING · CONNECTED" in the grid, on the night's load-bearing
  // question. MEASURED on /api/status: `connected` has no `guider` key while
  // `backend_links` carries `{role:"guider", ok:true, connected:true}`.
  //
  // `backend_links[].connected` is NOT a stale echo of the last connect — the
  // server recomputes it per status frame from `_role_live_connected` (device
  // OR engine, hub.py::backend_links), which is exactly the authority the grid
  // trusts. Both surfaces now read it, so they cannot disagree.
  const deviceName = status?.connected?.[role]?.connected
    ? status.connected[role].name
    : null;
  const linkUp = !!link?.connected;
  const live = !!deviceName || linkUp;
  const connectedName =
    deviceName ?? (linkUp && role === "guider" ? status?.guider?.name ?? null : null);
  const led = live ? "on" : result && result.attempted && !result.ok ? "bad" : "off";

  // UX review #41: a rig connected from ANYWHERE other than this screen (the
  // one-tap sim connect, a boot profile, Profiles → Activate) leaves the row's
  // ASSIGNMENT empty while the DEVICE is genuinely live — so ten of eleven rows
  // rendered a green ✓ LED next to "— unassigned —" and a faint "UNASSIGNED".
  // The LED was never the liar: it reports the link, and the link is up. The
  // word was, because it described a different axis. When the two disagree the
  // row now says what is actually true — CONNECTED — and explains, once, why
  // the dropdown is still empty.
  const liveButUnassigned = live && state === "unassigned";
  const meta = liveButUnassigned
    ? { word: "CONNECTED", tone: "text-good" }
    : SLOT_WORD[state];

  const pickDriver = (driverId: string) => {
    if (!driverId) return onAssign(null);
    const d = drivers.find((x) => x.id === driverId);
    const offers = d ? deviceChoices(role, d) : [];
    const first = offers[0];
    onAssign({
      driverId,
      devType: first?.dev_type,
      devNum: first?.dev_num,
      name: first?.name,
    });
  };

  return (
    <div className="border border-line bg-bg/60 px-3 py-2.5">
      <div className="flex items-center gap-3 flex-wrap">
        <Led state={led} label={`${ROLE_LABEL[role] ?? role} link`} />
        <span className="label w-28 shrink-0">{ROLE_LABEL[role] ?? role}</span>
        <select
          className="field !py-1 max-w-[200px]"
          value={assignment?.driverId ?? ""}
          disabled={disabled}
          onChange={(e) => pickDriver(e.target.value)}
          aria-label={`${ROLE_LABEL[role] ?? role} driver`}
        >
          <option value="">— unassigned —</option>
          {eligible.map((d) => (
            <option key={d.id} value={d.id}>
              {d.label}
            </option>
          ))}
          {/* sticky: a chosen driver that is no longer eligible stays listed
              (greyed by the state word) instead of silently vanishing */}
          {assignment && !eligible.some((d) => d.id === assignment.driverId) && (
            <option value={assignment.driverId}>
              {chosen?.label ?? assignment.driverId}
            </option>
          )}
        </select>
        {choices.length > 1 && (
          <select
            className="field !py-1 max-w-[180px]"
            value={`${assignment?.devType ?? ""}#${assignment?.devNum ?? ""}`}
            disabled={disabled}
            onChange={(e) => {
              const [dt, dn] = e.target.value.split("#");
              const offer = choices.find(
                (o) => o.dev_type === dt && String(o.dev_num) === dn,
              );
              if (offer && assignment)
                onAssign({
                  ...assignment,
                  devType: offer.dev_type,
                  devNum: offer.dev_num,
                  name: offer.name,
                });
            }}
            aria-label={`${ROLE_LABEL[role] ?? role} device`}
          >
            {choices.map((o) => (
              <option key={`${o.dev_type}#${o.dev_num}`} value={`${o.dev_type}#${o.dev_num}`}>
                {o.name} #{o.dev_num}
              </option>
            ))}
          </select>
        )}
        <span className={`mono text-[10px] tracking-wider ${meta.tone}`}>{meta.word}</span>
        <div className="flex-1" />
        <span className="mono text-xs truncate max-w-[220px] text-ink/90">
          {connectedName ?? <span className="text-dim">—</span>}
        </span>
      </div>
      {/* honest per-row failure: connect result error, else live link error.
          Sub-lines are indented to 23px — LED (11px) + gap-3 (12px) — so they
          start on the same column as the role label above them, instead of the
          old 41.6px that put the guider's second line 19px off the grid (#41).

          Gated on `live`, exactly as the LED above already is. `result` is the
          receipt from the last connect THIS browser ran, and nothing clears it:
          when the device came up afterwards — a retry from the tablet, a
          profile activate, the driver's own reconnect — the row switched its
          LED, its state word and its device name to the live truth and kept
          printing "camera: connection refused" underneath all three. */}
      {!live && result && result.attempted && !result.ok && result.error && (
        <p className="mt-1.5 pl-[23px] text-[10px] text-bad">{result.error}</p>
      )}
      {/* UX review #36: per-filter focus offsets — the single most important
          setting on a mono rig, with a working learn-offsets routine behind it
          — were reachable ONLY from a 24px unlabelled slider glyph on Capture
          whose only description ("Edit filter slot names") never mentions
          focus at all. A mono convert spent an evening believing the feature
          didn't exist. Same modal, second entrance, on the screen where you
          configure the wheel, named for what it does. */}
      {role === "filterwheel" && status?.filterwheel && (
        <FilterSlotsEditor
          names={status.filterwheel.names}
          offsets={status.filterwheel.offsets ?? []}
          opaque={status.filterwheel.opaque ?? []}
          position={status.filterwheel.position}
          hasFocuser={!!status.focuser}
          disabled={disabled}
        />
      )}
      {role === "guider" && (
        <>
          <div className="mt-2 pl-[23px] flex items-center gap-2 text-[11px]">
            <span className="label">guide cam</span>
            <span className="mono text-dim truncate">
              {(() => {
                const gc = status?.guide_camera;
                const guider = status?.guider;
                const on = !!gc?.connected || !!guider;
                return on ? (gc?.name ?? guider?.name ?? "guide camera") : "— not connected —";
              })()}
            </span>
          </div>
          <GuiderSlotNote drivers={drivers} status={status} />
        </>
      )}
    </div>
  );
}

// The filter-wheel row's slot-names + per-filter-focus-offsets entrance (#36).
// Wiring is deliberately identical to CaptureView's — same modal, same two
// endpoints — so there is exactly one implementation of the behaviour and two
// places to reach it.
function FilterSlotsEditor({
  names,
  offsets,
  opaque = [],
  position,
  hasFocuser,
  disabled,
}: {
  names: string[];
  offsets: number[];
  opaque?: boolean[];
  position: number;
  hasFocuser: boolean;
  disabled: boolean;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const showToast = useStore((s) => s.showToast);
  // A blackout slot's offset is a placeholder zero, so it must not count as
  // evidence that offsets were measured — nor as evidence they were not.
  const offsetsSet = offsets.some((o, i) => o !== 0 && !opaque[i]);
  const darkSlot = opaque.findIndex(Boolean);
  // UX review #4, the parent half. An inline `onClose={() => setOpen(false)}`
  // is a new function identity on every render of this component — and this
  // component re-renders on every device-status frame, several times a second.
  // FilterNamesModal is now immune to that on its own terms, but a modal's
  // close handler is genuinely a stable thing and passing a fresh one down
  // every frame is what armed the trap in the first place.
  const close = useCallback(() => setOpen(false), []);
  return (
    <div className="mt-2 pl-[23px] flex items-center gap-2 flex-wrap text-[11px]">
      <button
        type="button"
        className="btn min-h-11 !py-1 !px-3 text-[10px]"
        // NOT aria-disabled. It carried one — announcing "dimmed"/unavailable to
        // every screen reader — while opening a modal that is fully editable
        // whatever this flag says, and with no dimming at all, so nothing in a
        // screenshot review could contradict it. The one control the session
        // really can't drive is Learn offsets, and the modal already states that
        // reason on the button itself (`learnDisabledReason` below).
        onClick={() => setOpen(true)}
      >
        Filter slots &amp; focus offsets…
      </button>
      <span className="text-dim truncate">
        {names.length ? names.join(" · ") : "no slots reported"} —{" "}
        {offsetsSet ? "focus offsets set" : "focus offsets not set"}
        {darkSlot >= 0 &&
          ` — darks via ${names[darkSlot] || `slot ${darkSlot + 1}`}`}
      </span>
      <FilterNamesModal
        open={open}
        onClose={close}
        names={names}
        offsets={offsets}
        opaque={opaque}
        position={position}
        canLearn={hasFocuser}
        learnDisabledReason={
          disabled
            ? "this session can't change equipment"
            : !hasFocuser
              ? "no focuser is connected"
              : null
        }
        onLearn={async (refSlot) => {
          await api.post("/api/filterwheel/learn-offsets", { ref_slot: refSlot });
          showToast("info", "Learning filter offsets…");
        }}
        onSave={async (n, o, op) => {
          await api.post("/api/filterwheel/names",
                         { names: n, offsets: o, opaque: op });
          showToast("success", "Filter slots saved");
        }}
      />
    </div>
  );
}
