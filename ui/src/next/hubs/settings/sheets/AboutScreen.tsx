// AboutScreen.tsx - Settings > ABOUT (`#/settings/about`, plan section C.8).
//
// Two info rows (ENGINE, THIS APP), four chevron rows into their sheets
// (UPDATE, CREDITS, HELP, LOG EXPORT - the last already landed by T-SET-4's
// `LogExportSheet.tsx` under the `logExport` name), and a plain link to the
// classic UI.
//
// HONEST-ABSENT (plan D.2): the engine version is never shown as a
// placeholder while `/healthz` is in flight or after it fails - the version
// clause is left off entirely (host alone still renders), the same
// resolution `ConnectionSheet.tsx` (T-SET-2) already uses for the same
// fetch. UPDATE is a row visible to every role, honest-disabled (dimmed,
// `aria-disabled`, still focusable, explains on press) rather than hidden
// for anyone lacking `system.update` (plan D.1) - the sheet itself is
// reachable by direct deep-link regardless and mounts the panel unconditionally
// (`UpdateSheet.tsx`), which self-gates its own controls.
import { useEffect, useState, type JSX } from "react";
import { Card, ListRow } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { explainLock } from "../../../shell/explain";
import { appVersion } from "../../../lib/versions";
import { useUpdate } from "../../../../store";
import { accessPhrase, useCanSystemUpdate } from "../../../../lib/caps";
import { getHealth } from "../../../../api/backends";
import { deriveBase } from "../../../../lib/base";

const NOTE_STYLE = {
  margin: "6px 0 0",
  fontSize: 11.5,
  lineHeight: 1.45,
  color: "var(--text-faint)",
} as const;

export function AboutScreen(): JSX.Element {
  const [engineVersion, setEngineVersion] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const h = await getHealth();
        if (live) setEngineVersion(h.version || null);
      } catch {
        /* leave the engine clause off rather than printing a placeholder */
      }
    })();
    return () => { live = false; };
  }, []);

  const update = useUpdate();
  const canUpdate = useCanSystemUpdate();

  const host = typeof window === "undefined" ? "" : window.location.host;
  const pathname = typeof window === "undefined" ? "/" : window.location.pathname;
  const isRelay = deriveBase(pathname) !== "";
  const secure = typeof window === "undefined" ? true : window.isSecureContext;

  const engineSub = engineVersion ? `${engineVersion} · ${host}` : host;
  const appSub =
    `${appVersion()} · ${isRelay ? "relay" : "direct"} · ` +
    `${secure ? "secure connection" : "not a secure connection"}`;
  const updateSub =
    update?.update_available && update.latest
      ? `v${update.current} -> v${update.latest} available`
      : update?.current
        ? `v${update.current} - up to date`
        : undefined;

  return (
    <div data-testid="screen-about">
      <Card>
        <ListRow title="ENGINE ON THE RIG" sub={engineSub} data-testid="row-engine" />
        <ListRow title="THIS APP" sub={appSub} data-testid="row-app" />
      </Card>

      {isRelay && (
        <p style={NOTE_STYLE} data-testid="about-relay-note">
          You are signed in over the relay. This session belongs to this
          browser; signing out here does not sign out the screen on the rig.
        </p>
      )}

      <Card>
        <ListRow
          icon={<NxIcon name="refresh" />}
          title="UPDATE"
          sub={updateSub}
          chevron
          lockedReason={canUpdate ? null : `needs ${accessPhrase("system.update")}`}
          onExplain={explainLock}
          onPress={() => nav.sheet("update")}
          data-testid="row-update"
        />
        <ListRow
          icon={<NxIcon name="layers" />}
          title="CREDITS AND LICENCES"
          chevron
          onPress={() => nav.sheet("credits")}
          data-testid="row-credits"
        />
        <ListRow
          icon={<NxIcon name="info" />}
          title="HELP AND TROUBLESHOOTING"
          chevron
          onPress={() => nav.sheet("help")}
          data-testid="row-help"
        />
        <ListRow
          icon={<NxIcon name="download" />}
          title="LOG EXPORT"
          sub="frames and diagnostics for support"
          chevron
          onPress={() => nav.sheet("logExport")}
          data-testid="row-log-export"
        />
      </Card>

      <ListRow
        title="OPEN THE CLASSIC UI"
        sub="the previous interface, unchanged. Everything here works there too."
        onPress={() => { window.location.hash = "#/classic"; }}
        data-testid="row-classic"
      />
    </div>
  );
}
