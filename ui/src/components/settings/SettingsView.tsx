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

import { useState, type JSX } from "react";
import { Segmented } from "../Segmented";
import { Panel } from "../ui";
import { Icon } from "../icons";
import { useBackendLinks, useBootConnectFailed, useUpdate } from "../../store";
import {
  useCanConfigBackend,
  useCanAdminUsers,
  useCanSystemUpdate,
  useCanViewSitePrecise,
  useIsViewer,
} from "../../lib/caps";
import BackendLinkGrid from "./BackendLinkGrid";
import DriversPanel from "./DriversPanel";
import SitePanel from "./SitePanel";
import SkyAtlasPanel from "./SkyAtlasPanel";
import WeatherPanel from "./WeatherPanel";
import ProfileList from "./ProfileList";
import AccountPanel from "./AccountPanel";
import UsersPanel from "./UsersPanel";
import AuthMethodPanel from "./AuthMethodPanel";
import SafetyPanel from "./SafetyPanel";
import UpdatePanel from "./UpdatePanel";

type Tab =
  | "connect"
  | "profiles"
  | "safety"
  | "updates"
  | "account"
  | "users"
  | "auth";

export default function SettingsView(): JSX.Element {
  const [tab, setTab] = useState<Tab>("connect");
  const links = useBackendLinks();
  const bootFailed = useBootConnectFailed();
  const canConfig = useCanConfigBackend();
  const canAdminUsers = useCanAdminUsers();
  const canSystemUpdate = useCanSystemUpdate();
  const update = useUpdate();
  const isViewer = useIsViewer();
  const canSeePrecise = useCanViewSitePrecise();

  // Admin-only tabs (W2.6) appear ONLY for the `admin.users` capability. Under the
  // open `none`/no-method default every caller is admin, so an offline LAN admin
  // still sees them; a viewer/operator never does.
  const TABS: { value: Tab; label: string }[] = [
    { value: "connect", label: "Connect" },
    { value: "profiles", label: "Profiles" },
    { value: "safety", label: "Safety" },
    ...(canSystemUpdate
      ? ([
          {
            value: "updates",
            label: update?.update_available ? "Updates •" : "Updates",
          },
        ] as { value: Tab; label: string }[])
      : []),
    { value: "account", label: "Account" },
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
    (tab === "updates" && !canSystemUpdate)
      ? "connect"
      : tab;

  return (
    <div className="flex flex-col gap-4 w-full">
      {/* ----------------------------------------------------------- header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h1 className="font-display text-lg tracking-[0.2em] text-ink uppercase">
          Settings
        </h1>
        <Segmented
          options={TABS}
          value={activeTab}
          onChange={(t) => setTab(t)}
          ariaLabel="Settings section"
        />
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
          Safety (it carries its own config.solar_override read-only note). */}
      {!canConfig &&
        activeTab !== "users" &&
        activeTab !== "auth" &&
        activeTab !== "safety" && (
        <div className="flex items-center gap-3 border border-line2 bg-raise/40 px-3 py-2 text-xs">
          <Icon name="lock" size={14} className="text-dim shrink-0" />
          <span className="text-dim">
            {isViewer
              ? "Read-only — you can view rig status and sign in, but connecting rigs and editing profiles needs operator or admin access."
              : "Your role can't change backends or profiles. Connection status is shown for reference."}
          </span>
        </div>
      )}

      {/* ------------------------------------------------------------- SAFETY */}
      {activeTab === "safety" && <SafetyPanel />}

      {/* ------------------------------------------------------------ UPDATES */}
      {activeTab === "updates" && canSystemUpdate && <UpdatePanel />}

      {/* ------------------------------------------------------------ ACCOUNT */}
      {activeTab === "account" && <AccountPanel />}

      {/* -------------------------------------------------------------- USERS */}
      {activeTab === "users" && canAdminUsers && <UsersPanel />}

      {/* --------------------------------------------------------------- AUTH */}
      {activeTab === "auth" && canAdminUsers && <AuthMethodPanel />}

      {/* ------------------------------------------------------------ CONNECT */}
      {activeTab === "connect" && (
        <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
          <div className="order-2 lg:order-1 min-w-0 flex flex-col gap-4">
            <DriversPanel />
            <SitePanel />
            {canSeePrecise && <WeatherPanel />}
            <SkyAtlasPanel />
          </div>
          <div className="order-1 lg:order-2 flex flex-col gap-4">
            <Panel title="Connection Status">
              <BackendLinkGrid links={links} />
            </Panel>
          </div>
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
                  Managing profiles needs operator or admin access.
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
