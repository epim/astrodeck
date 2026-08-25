// SettingsView.tsx — the Settings surface (mounts at VIEWS.settings, replacing the
// PlaceholderView). A tabbed shell over the three W1.C/W1.6/W2.5 surfaces:
//   Connect  → DriversPanel (rig connection now lives on the Equipment tab)
//   Profiles → ProfileList (list / activate / rename / delete / save current rig)
//   Account  → AccountPanel (principal, View-only badge, Google sign-in)
// Plus the tri-state per-role link status (BackendLinkGrid) always visible at the
// top of the Connect + Profiles tabs so the boot-LED readout is one glance away.
//
// config.backend gating (W2.5): a viewer has no config.backend cap, so the picker
// and profile mutations are read-only for them — we surface a passive read-only
// banner and route them to the status grid instead of hiding Settings entirely
// (they can still SEE the rig + sign in).

import { useEffect, useLayoutEffect, useRef, useState, type JSX } from "react";
import { Segmented } from "../Segmented";
import { Panel } from "../ui";
import { Icon } from "../icons";
import { useBackendLinks, useBootConnectFailed, useConfig, useUpdate } from "../../store";
import {
  accessPhrase,
  useCan,
  useCanConfigBackend,
  useCanAdminUsers,
  useCanSystemUpdate,
  useCanViewSitePrecise,
  useIsViewer,
} from "../../lib/caps";
import BackendLinkGrid from "./BackendLinkGrid";
import DriversPanel from "./DriversPanel";
import SitePanel from "./SitePanel";
import OpticsPanel from "./OpticsPanel";
import SkyAtlasPanel from "./SkyAtlasPanel";
import NamingPanel from "./NamingPanel";
import WcsStampPanel from "./WcsStampPanel";
import SyncPanel from "./SyncPanel";
import WeatherPanel from "./WeatherPanel";
import CloudmapPanel from "./CloudmapPanel";
import ProfileList from "./ProfileList";
import CalibrationLibraryPanel from "./CalibrationLibraryPanel";
import CalibrationTolerancesPanel from "./CalibrationTolerancesPanel";
import AccountPanel from "./AccountPanel";
import UsersPanel from "./UsersPanel";
import AuthMethodPanel from "./AuthMethodPanel";
import SafetyPanel from "./SafetyPanel";
import SafetyLimitsPanel from "./SafetyLimitsPanel";
import StandardsPanel from "./StandardsPanel";
import EscalationPanel from "./EscalationPanel";
import AlertsPanel from "./AlertsPanel";
import UpdatePanel from "./UpdatePanel";
import FactoryResetPanel from "./FactoryResetPanel";
import CreditsPanel from "./CreditsPanel";
import RestrictedAssetsPanel from "./RestrictedAssetsPanel";

type Tab =
  | "connect"
  | "profiles"
  | "calibration"
  | "safety"
  | "alerts"
  | "updates"
  | "account"
  | "users"
  | "auth"
  // APPENDED: third-party licences. Deliberately NOT a nav-rail destination —
  // App.tsx records that a 15th rail entry already forced a padding change to
  // stay above the fold at 1440x900, and this tab strip scrolls with an edge
  // fade where the rail does not.
  | "credits";

/** The Settings tab strip. UX review #42: at tablet width the strip is 712px of
 *  content squeezed into a 698px flex slot by `Segmented`'s own
 *  `overflow-hidden`, so AUTH is amputated at the right edge with no fade, no
 *  chevron and no scrollbar — nothing on screen says there is more. Fix: give
 *  the strip its own full-width row, let it scroll horizontally (the inner
 *  `w-max` box keeps `Segmented` at its intrinsic width instead of being
 *  shrunk-and-clipped), and paint an edge fade on whichever side still has
 *  hidden tabs. */
function TabStrip({ children }: { children: JSX.Element }): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ left: false, right: false });
  const measure = () => {
    const el = ref.current;
    if (!el) return;
    const max = el.scrollWidth - el.clientWidth;
    setEdges({ left: el.scrollLeft > 2, right: max - el.scrollLeft > 2 });
  };
  useLayoutEffect(measure, []);
  useEffect(() => {
    // the strip's own width changes with the viewport AND with the tab set
    // (Alerts/Updates/Users/Auth are capability-gated), so watch both.
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    for (const c of Array.from(el.children)) ro.observe(c);
    return () => ro.disconnect();
  }, []);
  return (
    <div className="relative min-w-0 max-w-full">
      <div
        ref={ref}
        onScroll={measure}
        className="overflow-x-auto overscroll-x-contain"
      >
        <div className="w-max">{children}</div>
      </div>
      {edges.left && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-0 left-0 w-8 bg-gradient-to-r from-bg to-transparent"
        />
      )}
      {edges.right && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-bg to-transparent flex items-center justify-end"
        >
          <Icon name="arrow-right" size={14} className="text-dim mr-0.5" />
        </div>
      )}
    </div>
  );
}

export default function SettingsView(): JSX.Element {
  const [tab, setTab] = useState<Tab>("connect");
  const links = useBackendLinks();
  const bootFailed = useBootConnectFailed();
  const canConfig = useCanConfigBackend();
  const canAdminUsers = useCanAdminUsers();
  const canSystemUpdate = useCanSystemUpdate();
  const canAlerts = useCan("config.alerts");
  const update = useUpdate();
  const isViewer = useIsViewer();
  const canSeePrecise = useCanViewSitePrecise();
  const config = useConfig();

  // UX review #31: an unattended rig with no outbound channel is the default,
  // and nothing ever asks. Mark the tab the way an available update is marked,
  // so the gap is visible from anywhere in Settings rather than only to
  // somebody who already went looking for Alerts.
  const noAlertChannel =
    !!config &&
    (config.alerts ?? []).filter((s) => s.enabled).length === 0 &&
    !config.deadman_configured;

  // Admin-only tabs (W2.6) appear ONLY for the `admin.users` capability. Under the
  // open `none`/no-method default every caller is admin, so an offline LAN admin
  // still sees them; a viewer/operator never does.
  const TABS: { value: Tab; label: string }[] = [
    { value: "connect", label: "Connect" },
    { value: "profiles", label: "Profiles" },
    { value: "calibration", label: "Calibration" },
    { value: "safety", label: "Safety" },
    ...(canAlerts
      ? ([{ value: "alerts", label: noAlertChannel ? "Alerts •" : "Alerts" }] as {
          value: Tab;
          label: string;
        }[])
      : []),
    ...(canSystemUpdate
      ? ([
          {
            value: "updates",
            label: update?.update_available ? "Updates •" : "Updates",
          },
        ] as { value: Tab; label: string }[])
      : []),
    { value: "account", label: "Account" },
    // Ungated on purpose: a licence notice that only an admin can read is not
    // published. Every role sees this, including a viewer.
    { value: "credits", label: "Credits" },
    ...(canAdminUsers
      ? ([
          { value: "users", label: "Users" },
          { value: "auth", label: "Auth" },
        ] as { value: Tab; label: string }[])
      : []),
  ];

  // If the cap is lost while sitting on an admin tab (e.g. signed out), fall back.
  const activeTab: Tab =
    ((tab === "users" || tab === "auth") && !canAdminUsers) ||
    (tab === "updates" && !canSystemUpdate) ||
    (tab === "alerts" && !canAlerts)
      ? "connect"
      : tab;

  return (
    <div className="flex flex-col gap-4 w-full">
      {/* ----------------------------------------------------------- header
          #42: the strip gets its OWN row rather than sharing one with the title
          — on the 820px tablet that alone recovers the ~90px the heading was
          taking, and TabStrip makes whatever still doesn't fit reachable. */}
      <div className="flex flex-col gap-3">
        <h1 className="font-display text-lg tracking-[0.2em] text-ink uppercase">
          Settings
        </h1>
        <TabStrip>
          <Segmented
            options={TABS}
            value={activeTab}
            onChange={(t) => setTab(t)}
            ariaLabel="Settings section"
          />
        </TabStrip>
      </div>

      {/* boot-connect-failed banner — the active profile tried to auto-connect on
          boot and didn't fully come up; the link grid below shows which roles. */}
      {bootFailed && (
        <div className="flex items-center gap-3 border border-warn/50 bg-warn/10 px-3 py-2 text-xs">
          <Icon name="alert" size={14} className="text-warn shrink-0" />
          <span className="text-ink">
            The boot profile didn't fully connect.{" "}
            <span className="text-dim">Check the per-role status below and reconnect.</span>
          </span>
        </div>
      )}

      {/* read-only banner for viewers (W2.5: passive copy, not 403-on-tap). Hidden
          on the admin-only Users/Auth tabs (those are admin-gated already) and on
          Safety/Alerts (they carry their own config.solar_override/config.alerts
          read-only notes). */}
      {!canConfig &&
        activeTab !== "users" &&
        activeTab !== "auth" &&
        activeTab !== "safety" &&
        activeTab !== "alerts" && (
        <div className="flex items-center gap-3 border border-line2 bg-raise/40 px-3 py-2 text-xs">
          <Icon name="lock" size={14} className="text-dim shrink-0" />
          <span className="text-dim">
            {isViewer
              ? `Read-only — you can view rig status and sign in, but connecting rigs and editing profiles needs ${accessPhrase("config.backend")}.`
              : "Your role can't change backends or profiles. Connection status is shown for reference."}
          </span>
        </div>
      )}

      {/* ------------------------------------------------------------- SAFETY
          Sun avoidance first (it is armed by default and protects gear from a
          daytime slew), then the limits that end a night, then the non-weather
          failure policy. All three used to be one panel's worth of solar
          controls plus a config file. */}
      {activeTab === "safety" && (
        <div className="flex flex-col gap-4">
          <SafetyPanel />
          <SafetyLimitsPanel />
          {/* #239 stage A. Under Safety rather than a tab of its own: these are
              the thresholds that decide whether a frame is kept and when a
              night gives up, and they are gated on the same config.safety
              capability as the panels above. */}
          <StandardsPanel />
          <EscalationPanel />
        </div>
      )}

      {/* ------------------------------------------------------------- ALERTS */}
      {activeTab === "alerts" && canAlerts && <AlertsPanel />}

      {/* ------------------------------------------------------------ UPDATES */}
      {activeTab === "updates" && canSystemUpdate && <UpdatePanel />}

      {/* ------------------------------------------------------------ ACCOUNT */}
      {activeTab === "account" && <AccountPanel />}

      {/* ------------------------------------------------------------ CREDITS */}
      {/* The restricted assets sit ABOVE the credits list, not inside it. The
          credits page answers "what are we built on"; this answers "what are we
          not entitled to hand you, and what is AstroDeck doing about it" —
          which is the only part of the page that carries an action. */}
      {activeTab === "credits" && <RestrictedAssetsPanel />}
      {activeTab === "credits" && <CreditsPanel />}

      {/* -------------------------------------------------------------- USERS */}
      {activeTab === "users" && canAdminUsers && <UsersPanel />}

      {/* --------------------------------------------------------------- AUTH */}
      {activeTab === "auth" && canAdminUsers && <AuthMethodPanel />}

      {/* ------------------------------------------------------------ CONNECT */}
      {activeTab === "connect" && (
        <>
        <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
          {/* #12 cont.: on a narrow viewport the settings column used to be
              `order-2`, so Connection Status — a ~400px empty state before a
              rig exists — was pushed above everything and Observing Site
              started 426px down even as the first panel. The link readout is
              still on this tab (below), on Equipment's own Link Status panel,
              and in the header LED; the site is not reachable anywhere else.
              Profiles keeps the original order. */}
          <div className="order-1 min-w-0 flex flex-col gap-4">
            {/* #12: OBSERVING SITE used to sit at scroll offset 1485 of a
                3168px page, BELOW the driver plumbing — so the one setting
                that poisons twilight times, meridian timing, horizon gating
                and every delivered FITS header was the last thing a first-run
                user would ever find. It is now the first panel on the tab. */}
            <SitePanel />
            <OpticsPanel />
            <DriversPanel />
            {canSeePrecise && <WeatherPanel />}
            {/* Directly under Weather because it answers the neighbouring
                question: Weather says whether tonight is worth opening for,
                this says WHERE in the sky the cloud is. Same site dependency,
                same capability. */}
            {canSeePrecise && <CloudmapPanel />}
            <SkyAtlasPanel />
            <NamingPanel />
            <WcsStampPanel />
            <SyncPanel />
          </div>
          <div className="order-2 flex flex-col gap-4">
            <Panel title="Connection Status">
              <BackendLinkGrid links={links} />
            </Panel>
          </div>
        </div>

        {/* ------------------------------------------------------ DANGER ZONE
            Last thing on the tab, full width, behind a rule and a heading — so
            it is findable when it is wanted (Settings opens on Connect; you
            reach this by scrolling to the end) and never brushed against (it
            sits below six panels and a whole phone screen of content). This is
            the ONLY place it appears. Rendered for every role: a non-admin sees
            it dimmed with the reason stated, rather than a feature that
            silently does not exist. */}
        <div className="mt-6 pt-5 border-t border-line2 flex flex-col gap-3">
          <h2 className="font-display text-xs tracking-[0.2em] text-bad uppercase flex items-center gap-2">
            <Icon name="alert" size={13} aria-hidden />
            Danger zone
          </h2>
          <FactoryResetPanel />
        </div>
        </>
      )}

      {/* -------------------------------------------------------- CALIBRATION
          The library (what masters exist) above the tolerances (when they get
          reused), because the list is what you came to look at and the
          tolerances are what you change once and forget. */}
      {activeTab === "calibration" && (
        <div className="flex flex-col gap-4">
          <CalibrationLibraryPanel />
          <CalibrationTolerancesPanel />
        </div>
      )}

      {/* ----------------------------------------------------------- PROFILES */}
      {activeTab === "profiles" && (
        <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
          <div className="order-2 lg:order-1 min-w-0">
            {canConfig ? (
              <ProfileList />
            ) : (
              <Panel title="Profiles">
                <p className="text-xs text-dim">
                  Managing profiles needs {accessPhrase("config.backend")}.
                </p>
              </Panel>
            )}
          </div>
          <div className="order-1 lg:order-2">
            <Panel title="Connection Status">
              <BackendLinkGrid links={links} />
            </Panel>
          </div>
        </div>
      )}
    </div>
  );
}
