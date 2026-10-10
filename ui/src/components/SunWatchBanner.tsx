// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
import { useStore } from "../store";
import { sunWatchNotice } from "../lib/sunWatch";
import { Icon } from "./icons";

/**
 * Full-width strip under the connection banner when the sun watch cannot
 * protect the tube (#894): it is BLIND (cannot read the mount) or STANDING DOWN
 * (the rig's position is unknown, so it will not park). Returns null otherwise.
 *
 * The server knew both and published them on `/api/safety/state`, which no
 * screen read, so a rig with no alert sink showed a quiet UI while nothing
 * watched the tube. The words come from `lib/sunWatch.ts` (the since-time, what
 * the net cannot do, and the step), one copy shared with the #/next root.
 *
 * NOT SHOWN on the Monitor view, where the health strip says the same thing in
 * its own place (the run banner makes the same call). "Not running" is a
 * health-strip notice only: a net someone switched off is a standing choice,
 * and a strip on every screen for it would teach people to ignore this one.
 */
export default function SunWatchBanner() {
  const sunWatch = useStore((s) => s.status?.sun_watch);
  const mountConnected = useStore((s) => !!s.status?.connected?.telescope?.connected);
  const view = useStore((s) => s.view);

  if (view === "monitor") return null;
  const notice = sunWatchNotice(sunWatch, mountConnected, Date.now() / 1000);
  if (!notice || notice.tier !== 2) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="sun-watch-banner"
      data-kind={notice.kind}
      className="flex items-start gap-2 px-4 py-2 border-b border-warn/50 bg-warn/10 shrink-0 text-xs"
    >
      <Icon name="alert" size={14} className="text-warn shrink-0 mt-0.5" />
      <div className="min-w-0">
        <span className="text-ink">{notice.text}</span>
        {notice.action && <span className="block text-dim">{notice.action}</span>}
      </div>
    </div>
  );
}
