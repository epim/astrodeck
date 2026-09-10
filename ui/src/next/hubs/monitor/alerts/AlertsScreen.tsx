// AlertsScreen.tsx - MONITOR - ALERTS. Three stacked sections, because the
// design's screen and the gap analysis are asking for different halves of the
// same question.
//
//   RECENT      the design's list: what has needed attention tonight.
//   SINKS       GAP-ANALYSIS §9's "dead-man's-switch and alert sinks (test
//               round-trip)" - the reused `AlertsPanel`, whole, including the
//               empty-sinks warning that states the CONSEQUENCE ("Nothing is
//               watching this rig..."), per-sink health, TEST as a real
//               round-trip, min level, which events notify, heartbeat, and the
//               dead-man's-switch card.
//   THIS PHONE  the browser's own notification permission.
//
// THE DELIVERY TOAST LIVES HERE AND NOWHERE ELSE. `store.alert` carries a
// MONOTONIC `key` "so repeat identical {sink,ok} still re-fires a UI effect";
// one owner means a hub switch cannot double-fire it.

import { useEffect, useMemo, useRef, useState, type JSX } from "react";
import { getAlertHealth } from "../../../../api/alerts";
import { useStore, useAlert, useConfig, useLogs, useSeq } from "../../../../store";
import { humanizeLog } from "../../../../lib/humanize";
import { fmtLogTime } from "../../../../lib/logFormat";
import AlertsPanel from "../../../../components/settings/AlertsPanel";
import type { AlertHealth, LogLine } from "../../../../types";
import { Card, Label, Mono } from "../../../ui";
import { monState } from "../monState";
import { NotifyRow } from "./NotifyRow";

/** What counts as "needed your attention": every warning and error, plus the
 *  sequence's own state-change lines (run start / run end / hold), which are
 *  the events the sinks themselves send at `info` level. Anything else is the
 *  running commentary the LOG tab is for. */
export function alertWorthy(rows: readonly LogLine[]): LogLine[] {
  return rows.filter((r) => {
    const level = r.data?.level;
    if (level === "warning" || level === "error") return true;
    return r.data?.source === "sequence";
  });
}

/** `GET /api/alerts/health` - a pure read (it never does I/O server-side), so
 *  it re-polls on the config broadcast rather than on an interval of its own.
 *  Same rule `AlertsPanel` follows; no second subscription. */
function useAlertHealth(): AlertHealth | null {
  const config = useConfig();
  const [health, setHealth] = useState<AlertHealth | null>(null);
  const version = config?.version;
  useEffect(() => {
    let live = true;
    void getAlertHealth()
      .then((h) => { if (live) setHealth(h); })
      .catch(() => { /* best-effort - the last snapshot stays on screen */ });
    return () => { live = false; };
  }, [version]);
  return health;
}

export function AlertsScreen(): JSX.Element {
  const seq = useSeq();
  const logs = useLogs();
  const alert = useAlert();
  const config = useConfig();
  const health = useAlertHealth();

  // One toast per delivery result, keyed on the store's monotonic `key` so two
  // identical failures in a row are two toasts, not one.
  const lastKey = useRef<number | null>(null);
  useEffect(() => {
    if (!alert) return;
    if (lastKey.current === alert.key) return;
    lastKey.current = alert.key;
    useStore.getState().enqueueToast(
      alert.ok
        ? { level: "success", title: `Alert delivered to ${alert.sink}` }
        : { level: "error", title: `Alert to ${alert.sink} failed: ${alert.error ?? "no reason given"}` },
    );
  }, [alert]);

  const recent = useMemo(() => alertWorthy(logs).reverse().slice(0, 20), [logs]);
  const sinkCount = config?.alerts?.length ?? 0;
  const undelivered = health?.undelivered ?? 0;

  return (
    <div data-testid="monitor-alerts" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10 }}>
        <Label size={11}>ALERTS</Label>
        <Mono size={10} tone="dim">
          {sinkCount === 0
            ? "no channel yet · " + monState({ state: seq.state, target: seq.target })
            : `${sinkCount} channel${sinkCount === 1 ? "" : "s"}`
              + (undelivered > 0 ? ` · ${undelivered} undelivered` : " · all delivered")}
        </Mono>
      </div>

      {/* ---------------------------------------------------------- RECENT */}
      <Card padding={12} data-testid="alerts-recent">
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <Label>RECENT</Label>
          {/* The last DELIVERY result, pinned. Its toast is 2.8 s long and the
              question it answers ("did the 01:15 cloud hold actually reach my
              phone?") is asked in the morning. */}
          {alert && (
            <div data-testid="alerts-delivery" style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
              <span
                aria-hidden="true"
                style={{
                  width: 8, height: 8, borderRadius: "50%", marginTop: 5, flexShrink: 0,
                  background: alert.ok ? "var(--good)" : "var(--bad)",
                }}
              />
              <span style={{ fontSize: 12, lineHeight: 1.45, color: "var(--text)", overflowWrap: "anywhere" }}>
                {alert.ok
                  ? `Last delivery: reached ${alert.sink}.`
                  : `Last delivery: ${alert.sink} refused it - ${alert.error ?? "no reason given"}.`}
              </span>
            </div>
          )}
          {recent.length === 0 && !alert && (
            <Mono size={10.5} tone="dim">Nothing has needed your attention tonight.</Mono>
          )}
          {recent.map((l, i) => (
            <div key={`${l.ts}-${i}`} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
              <span
                aria-hidden="true"
                style={{
                  width: 8, height: 8, borderRadius: "50%", marginTop: 5, flexShrink: 0,
                  background: l.data.level === "error" ? "var(--bad)"
                    : l.data.level === "warning" ? "var(--warn)" : "var(--good)",
                }}
              />
              <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                <span style={{ fontSize: 12, lineHeight: 1.45, color: "var(--text)", overflowWrap: "anywhere" }}>
                  {humanizeLog(l.data)}
                </span>
                <Mono size={10} tone="dim">{fmtLogTime(l.ts)}</Mono>
              </div>
            </div>
          ))}
        </div>
      </Card>

      {/* ----------------------------------------------------------- SINKS */}
      <div data-testid="alerts-sinks">
        <AlertsPanel />
      </div>

      {/* ------------------------------------------------------ THIS PHONE */}
      <Card padding={12} data-testid="alerts-phone">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Label>THIS PHONE</Label>
          <NotifyRow />
        </div>
      </Card>
    </div>
  );
}
