// NotifyRow.tsx - THIS PHONE. The browser notification permission, which
// nothing in the shipped UI exposes today except a store flag nobody can reach.
//
// THREE STATES, AND ONLY ONE OF THEM IS A SWITCH:
//   no Notification API at all  -> the row is ABSENT. A dead control is worse
//                                  than an absence (the NavMoreSheet haptics
//                                  precedent: "hidden entirely when unsupported
//                                  - no dead control").
//   permission "denied"         -> honest-disabled, and the reason names the
//                                  browser, because nothing in this app can
//                                  undo it.
//   otherwise                   -> the switch. Turning it on also asks the OS
//                                  (`store.setNotifyEnabled` -> `lib/notify.ts`
//                                  `requestNotifyPermission()`).

import { useState, type JSX } from "react";
import { useStore, useNotifyEnabled } from "../../../../store";
import { requestNotifyPermission } from "../../../../lib/notify";
import { Switch } from "../../../ui";

export const NOTIFY_NOTE =
  "Web notifications and a beep when the link drops or a run ends. This device only - "
  + "it does not follow you to another browser.";

export const NOTIFY_DENIED =
  "notifications are blocked for this site in your browser settings";

/** Push while the app is CLOSED does not come from here, and saying so is the
 *  difference between a switch that under-delivers and one that is understood.
 *  (README "Platform": Web Push on Android, iOS only when installed, with ntfy
 *  as the reliable path.) */
export const NOTIFY_REACH =
  "Push to your phone when the app is closed goes through an alert channel above "
  + "(ntfy is the reliable path). This switch only covers the browser while it is open.";

function permission(): NotificationPermission | null {
  if (typeof Notification === "undefined") return null;
  try { return Notification.permission; } catch { return null; }
}

export function NotifyRow(): JSX.Element | null {
  const enabled = useNotifyEnabled();
  const [perm, setPerm] = useState<NotificationPermission | null>(() => permission());

  if (perm == null) return null;

  return (
    <div data-testid="notify-row" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <Switch
        checked={enabled && perm === "granted"}
        label="NOTIFY ON THIS PHONE"
        note={NOTIFY_NOTE}
        lockedReason={perm === "denied" ? NOTIFY_DENIED : null}
        onExplain={(r) => useStore.getState().enqueueToast({ level: "warning", title: r })}
        onChange={(next) => {
          useStore.getState().setNotifyEnabled(next);
          if (!next) { setPerm(permission()); return; }
          // RE-READ WHEN THE PROMPT IS ANSWERED, not one turn later. The store's
          // `setNotifyEnabled` fires `requestNotifyPermission()` and drops the
          // promise, and a `setTimeout(..., 0)` lands while the browser dialog
          // is still open: `Notification.permission` is still "default", the
          // knob stays OFF, and a user who has just pressed ALLOW is looking at
          // a switch that says their notifications are off. Awaiting the same
          // helper reads the answer at the moment it exists. It short-circuits
          // on an already-granted or already-denied permission, and a browser
          // shows ONE dialog for concurrent requests, so this adds no second
          // prompt.
          void requestNotifyPermission().then(() => setPerm(permission()));
        }}
        data-testid="notify-switch"
      />
      <div className="nx-empty-hint">{NOTIFY_REACH}</div>
    </div>
  );
}
