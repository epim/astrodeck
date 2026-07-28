// EquipmentView.tsx — the unified per-device Equipment surface (spec §4.1),
// replacing the old mode-centric connect view under the same "connect" view id.
// One uniform row grammar: for each server-fed role, pick WHO drives it from
// the drivers that actually offer it (the one rule), then Connect Rig compiles
// assignments → RigSpec (primary "none", driver_id ConnSpecs) → the existing
// /api/connect/rig. Live truth (LEDs + names) rides backend_links + status;
// failure honesty: sticky assignments, per-row RoleResult errors, an
// unreachable-driver banner, and a /api/drivers refetch after a failed
// connect (probe-cache honesty, spec §3.2/§5).
import { useEffect, useMemo, useState, type JSX } from "react";
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
  connectRig,
  discoverHardware,
  getProfile,
  hwAlreadyConfigured,
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
  loadAssignments,
  saveAssignments,
  simAssignments,
  slotState,
  type Assignment,
  type AssignmentMap,
} from "../lib/equipment";
import { confirmDialog } from "../components/ConfirmDialog";
import { FilterNamesModal } from "../components/capture/FilterNamesModal";
import TasksPanel, { DEFAULT_PROVIDERS } from "../components/equipment/TasksPanel";
import RotatorCard from "../components/equipment/RotatorCard";
import BackendLinkGrid from "../components/settings/BackendLinkGrid";
import { ROLE_LABEL } from "../components/settings/backendMeta";
import { EmptyState, Field, InfoDot, Led, Panel } from "../components/ui";
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

export default function EquipmentView(): JSX.Element {
  const status = useStore((s) => s.status);
  const showToast = useStore((s) => s.showToast);
  const canConfig = useCanConfigBackend();
  const config = useConfig();

  const [data, setData] = useState<DriversResponse | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [assignments, setAssignments] = useState<AssignmentMap>(() => loadAssignments());
  const [results, setResults] = useState<Record<string, RoleResult>>({});
  const [busy, setBusy] = useState(false);
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
  const connectedCount = Object.values(status?.connected ?? {}).filter(
    (c) => c?.connected,
  ).length;
  // Roles the server reports as LIVE while this screen holds no assignment for
  // them — the #41 mismatch (a rig connected from anywhere but here).
  const liveUnassignedRoles = roles.filter(
    (r) => status?.connected?.[r]?.connected && !assignments[r],
  );

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
    setBusy(true);
    setResults({});
    try {
      const res: ConnectRigResult = await connectRig(buildRigSpec(map));
      const resultMap: Record<string, RoleResult> = {};
      for (const r of res.results) resultMap[r.role] = r;
      setResults(resultMap);
      const okCount = res.results.filter((r) => r.ok).length;
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
      setBusy(false);
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
      const live = Object.values(status?.connected ?? {}).filter(
        (c) => c?.connected,
      ).length;
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
      setBusy(true);
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
        setBusy(false);
      }
    })();

  // Builds the AssignmentMap first (state + localStorage, same as a manual
  // per-row pick) and THEN drives it through connectAssignments — never the
  // legacy /api/connect/sim shortcut, whose ConnectResult the Equipment
  // surface has no way to read into assignments/backend_links (root cause).
  const doSimRig = () => {
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
  const doDetectHardware = () =>
    void (async () => {
      setBusy(true);
      try {
        const found = await discoverHardware();
        const toAdd = found.filter((f) => !hwAlreadyConfigured(f, drivers));
        for (const f of toAdd) {
          // Sequential (see DriversPanel.addAllHw): each add mints a
          // server-side id off the current config file.
          await addDriverForHardware(f);
        }
        const fresh = await listDrivers();
        setData(fresh);
        const map = hardwareAssignments(fresh.drivers, fresh.roles);
        setAssignments(map);
        saveAssignments(map);
        const assignedRoles = Object.values(map).filter(Boolean).length;
        showToast(
          "success",
          `Detected ${found.length} device(s)` +
            (toAdd.length ? `, added ${toAdd.length} driver(s)` : "") +
            ` — ${assignedRoles} role(s) assigned, review and Connect Rig`,
        );
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "hardware detection failed");
      } finally {
        setBusy(false);
      }
    })();

  // ------------------------------------------------------------- profiles
  const doSaveProfile = () =>
    void (async () => {
      const name = profileName.trim();
      if (!name) return;
      // Build device rows directly (the guard narrows `a` to Assignment, so no
      // cast is needed); sim assignments are the implicit built-in and carry no
      // persistable driver_id, so they're excluded from the saved profile.
      // `driver_id` is now a typed optional field on ProfileDevice (Task 5).
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
      // Build a fully-typed Profile (real Profile has 10 required fields) rather
      // than an `as unknown as Profile` double-cast — that would silently disable
      // type-checking on `devices`. `id: ""` matches no stored file, so the server
      // mints a fresh id (saveProfile contract); legacy nina/phd2/optics/site
      // fields default to null/0 for an assignments-only profile.
      const profile: Profile = {
        id: "",
        name,
        primary_backend: "none",
        devices,
        nina_host: null,
        nina_port: 0,
        phd2_host: null,
        phd2_port: 0,
        optics: null,
        site_name: null,
        // Task-override snapshot (spec §4.4): the profile carries the CURRENT
        // global task routing so Activate restores it (server-side precedence).
        providers: config?.providers ? { ...config.providers } : null,
      };
      try {
        await saveProfile(profile);
        setProfileName("");
        void reloadProfiles();
        showToast("success", `Profile "${name}" saved`);
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "profile save failed");
      }
    })();

  const doLoadProfile = (id: string) =>
    void (async () => {
      try {
        const p = await getProfile(id);
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
        if (p.providers && canConfig) {
          try {
            await setProvidersConfig({ ...DEFAULT_PROVIDERS, ...p.providers });
            await useStore.getState().loadConfig();
          } catch {
            showToast("warning", "Assignments loaded, but task overrides couldn't be restored");
          }
        }
        showToast("success", `Loaded assignments from "${p.name}" — review, then Connect`);
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "profile load failed");
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
            <button
              type="button"
              className={`btn min-h-11 ${
                assignedCount > 0 && canConfig && !busy ? "btn-accent" : ""
              } ${assignedCount === 0 ? "!text-dim" : ""}`}
              disabled={busy || !canConfig}
              aria-disabled={assignedCount === 0 || undefined}
              onClick={assignedCount === 0 ? undefined : () => void doConnect()}
            >
              <Icon name="link" size={14} className="inline -mt-0.5 mr-1.5" />
              {busy ? "Working…" : `Connect Rig (${assignedCount})`}
            </button>
            {assignedCount === 0 && canConfig && (
              <span className="text-[11px] text-dim">
                nothing assigned yet — pick a driver above, or use one of these
              </span>
            )}
            <button type="button" className="btn" disabled={busy || !canConfig || roles.length === 0} onClick={doSimRig}>
              ▶ Simulator rig
            </button>
            <button type="button" className={`btn ${assignedCount === 0 ? "btn-accent" : ""}`} disabled={busy || !canConfig || roles.length === 0} onClick={doDetectHardware}>
              ▶ Detect hardware rig
            </button>
            <button type="button" className="btn btn-danger" disabled={busy || !canConfig} onClick={doDisconnect}>
              Disconnect
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

        <Panel title="Profiles" right={<span className="label text-dim">assignments, saved</span>}>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Save current assignments as">
              <input
                className="field !py-1 w-[180px]"
                placeholder="Backyard rig"
                value={profileName}
                onChange={(e) => setProfileName(e.target.value)}
              />
            </Field>
            <button
              type="button"
              className="btn !py-1.5"
              disabled={!canConfig || !profileName.trim() || assignedCount === 0 || busy}
              onClick={doSaveProfile}
            >
              Save
            </button>
          </div>
          {profiles && profiles.length > 0 && (
            <div className="mt-3 flex flex-col gap-1.5">
              {profiles.map((p) => (
                <div key={p.id} className="flex items-center gap-2 border border-line bg-bg/60 px-3 py-2">
                  <span className="text-xs text-ink truncate flex-1">
                    {p.name}
                    {p.active && <span className="label text-accent ml-2">ACTIVE</span>}
                  </span>
                  <button type="button" className="btn !py-1 !px-2 text-[10px]" disabled={busy} onClick={() => doLoadProfile(p.id)}>
                    Load
                  </button>
                  <button
                    type="button"
                    className="btn !py-1 !px-2 text-[10px]"
                    disabled={busy || !canConfig}
                    onClick={() =>
                      void activateProfile(p.id).then(
                        () => showToast("success", "Profile activating — watch the link grid"),
                        (e: unknown) => showToast("error", e instanceof Error ? e.message : "activate failed"),
                      )
                    }
                  >
                    Activate
                  </button>
                </div>
              ))}
            </div>
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
  void link; // live truth reflected via status.connected + result LED; link is
  // surfaced in the side Link Status grid.
  const eligible = eligibleDrivers(role, drivers);
  const state = slotState(role, assignment, drivers);
  const chosen = assignment ? drivers.find((d) => d.id === assignment.driverId) : undefined;
  const choices = chosen ? deviceChoices(role, chosen) : [];
  const connectedName = status?.connected?.[role]?.connected
    ? status.connected[role].name
    : null;
  const led = connectedName ? "on" : result && result.attempted && !result.ok ? "bad" : "off";

  // UX review #41: a rig connected from ANYWHERE other than this screen (the
  // one-tap sim connect, a boot profile, Profiles → Activate) leaves the row's
  // ASSIGNMENT empty while the DEVICE is genuinely live — so ten of eleven rows
  // rendered a green ✓ LED next to "— unassigned —" and a faint "UNASSIGNED".
  // The LED was never the liar: it reports the link, and the link is up. The
  // word was, because it described a different axis. When the two disagree the
  // row now says what is actually true — CONNECTED — and explains, once, why
  // the dropdown is still empty.
  const liveButUnassigned = !!connectedName && state === "unassigned";
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
          old 41.6px that put the guider's second line 19px off the grid (#41). */}
      {result && result.attempted && !result.ok && result.error && (
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
          position={status.filterwheel.position}
          hasFocuser={!!status.focuser}
          disabled={disabled}
        />
      )}
      {role === "guider" && (
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
  position,
  hasFocuser,
  disabled,
}: {
  names: string[];
  offsets: number[];
  position: number;
  hasFocuser: boolean;
  disabled: boolean;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const showToast = useStore((s) => s.showToast);
  const offsetsSet = offsets.some((o) => o !== 0);
  return (
    <div className="mt-2 pl-[23px] flex items-center gap-2 flex-wrap text-[11px]">
      <button
        type="button"
        className="btn min-h-11 !py-1 !px-3 text-[10px]"
        // honest-disabled: a read-only session still gets to LOOK at the slots
        aria-disabled={disabled || undefined}
        onClick={() => setOpen(true)}
      >
        Filter slots &amp; focus offsets…
      </button>
      <span className="text-dim truncate">
        {names.length ? names.join(" · ") : "no slots reported"} —{" "}
        {offsetsSet ? "focus offsets set" : "focus offsets not set"}
      </span>
      <FilterNamesModal
        open={open}
        onClose={() => setOpen(false)}
        names={names}
        offsets={offsets}
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
        onSave={async (n, o) => {
          await api.post("/api/filterwheel/names", { names: n, offsets: o });
          showToast("success", "Filter slots saved");
        }}
      />
    </div>
  );
}
