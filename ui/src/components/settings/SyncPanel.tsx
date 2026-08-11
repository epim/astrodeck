// SyncPanel.tsx — Settings → "File sync" (file-sync Phase 2).
//
// Destination picker (POST /api/config/sync) + what the push runner has actually
// been doing (GET /api/sync/push) + a "Push now" button (POST /api/sync/push/now).
//
// The button is not a convenience. Everything else on this card is a promise
// about 02:00 — that frames will leave the rig while the night is still going —
// and a promise nobody can test until 02:00 is how this feature would quietly
// not work. Push now turns it into something an operator can check while they
// are still standing at the keyboard.
import { useEffect, useState, type JSX } from "react";
import type { SyncPushStatus } from "../../types";
import { getSyncPushStatus, pushSyncNow, setSyncPushConfig } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { useCan } from "../../lib/caps";
import { Panel, Toggle } from "../ui";
import { Icon } from "../icons";
import { formatAge } from "../../lib/telemetry";

// Only while a pass is in flight — a pass takes tens of seconds on a full night
// and the card is otherwise static. Same idiom as SkyAtlasPanel's fetch poll.
const POLL_MS = 3000;

/** "2.31 GB" / "412 MB" — bytes at the scale a night is actually measured in. */
export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  if (n >= 1e3) return `${Math.round(n / 1e3)} kB`;
  return `${Math.round(n)} B`;
}

/**
 * The one line an operator reads. It has to distinguish four states that all
 * look like "nothing is happening" from the outside: off, on-but-never-run,
 * working, and broken.
 */
export function syncSummary(s: SyncPushStatus | null): { text: string; tone: "ok" | "warn" | "dim" } {
  if (!s || !s.enabled) return { text: "Off — frames stay on the rig.", tone: "dim" };
  if (!s.configured) return { text: "On, but no destination is set.", tone: "warn" };
  if (s.alarm) {
    return {
      text: `FAILING — ${s.consecutive_failures} passes in a row. Frames are NOT leaving the rig.`,
      tone: "warn",
    };
  }
  if (!s.passes) return { text: "On. No pass has run yet.", tone: "dim" };
  const when = s.last_ok_at ? `${formatAge(Date.now() - s.last_ok_at * 1000)} ago` : "never";
  return {
    text: `${s.total_sent} frame${s.total_sent === 1 ? "" : "s"} sent `
      + `(${formatBytes(s.total_bytes)}). Last success ${when}.`,
    tone: s.last_ok_at ? "ok" : "warn",
  };
}

export default function SyncPanel(): JSX.Element {
  const config = useConfig();
  const showToast = useStore((s) => s.showToast);
  // config.site_optics writes the block; view.media runs a pass and reads the
  // status (it moves science frames). Both are admin-only in the shipped role
  // map, but they are asked for separately so the card degrades honestly if
  // that ever changes.
  const canEdit = useCan("config.site_optics");
  const canPush = useCan("view.media");
  const saved = config?.sync_push;

  const [status, setStatus] = useState<SyncPushStatus | null>(null);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Seed the field from config once it arrives, and re-seed when someone else
  // changes it (the config broadcast lands in the store).
  useEffect(() => { setPath(saved?.path ?? ""); }, [saved?.path]);

  const reload = async () => {
    if (!canPush) return;
    try {
      setStatus(await getSyncPushStatus());
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 403)) {
        setErr(e instanceof Error ? e.message : "couldn't load sync status");
      }
    }
  };
  // Re-read the status whenever the CONFIG moves, not only on mount. The config
  // can change without this panel doing the changing — another browser tab, or
  // the WS `config` broadcast after any save — and a status frozen at mount
  // would leave "Push now" disabled against a destination that is now live.
  useEffect(() => { void reload(); }, [canPush, saved?.enabled, saved?.path]);

  const running = !!status?.running;
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => { void reload(); }, POLL_MS);
    return () => window.clearInterval(id);
  }, [running]);

  const save = async (next: { enabled?: boolean; path?: string }) => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await setSyncPushConfig({
        enabled: next.enabled ?? saved?.enabled ?? false,
        kind: "local_dir",
        path: (next.path ?? path).trim(),
        label: saved?.label ?? "",
        limit_per_pass: saved?.limit_per_pass ?? 0,
      });
      await useStore.getState().loadConfig();
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError
        ? (e.status === 403 ? "config.site_optics required to change file sync"
          : e.status === 422 ? "Enter a destination folder before turning sync on."
          : e.message)
        : "Could not save.");
    } finally { setBusy(false); }
  };

  const pushNow = async () => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const s = await pushSyncNow();
      setStatus(s);
      // The RESULT, not "started" — this is the whole point of the button.
      if (s.last?.error) showToast("error", s.last.error);
      else if (s.last) showToast("success", s.last.summary);
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 403
        ? "view.media required to push frames"
        : e instanceof Error ? e.message : "push failed");
    } finally { setBusy(false); }
  };

  const enabled = saved?.enabled ?? false;
  const dirty = (saved?.path ?? "") !== path.trim();
  const sum = syncSummary(status);
  const last = status?.last ?? null;

  return (
    <Panel title="File sync">
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <span className="label">Push frames to a folder</span>
          <Toggle checked={enabled} onChange={(v) => void save({ enabled: v })}
                  disabled={busy || !canEdit || (!enabled && !path.trim())}
                  label="Push frames to a folder" showState />
        </div>
        <p className="text-[12px] text-dim">
          Copies each frame to another machine while the night is still running, so
          processing can start before the run ends. Frames are only ever added
          there — sync never deletes anything at the destination.
        </p>

        <label className="flex flex-col gap-1">
          <span className="label">Destination folder</span>
          <input type="text" className="input mono" value={path} spellCheck={false}
                 placeholder="\\nas\astro  or  D:\incoming"
                 aria-label="Destination folder"
                 disabled={busy || !canEdit}
                 onChange={(e) => setPath(e.target.value)} />
        </label>
        <p className="text-[11px] text-dim">
          A path this rig can write to: a mapped drive, or a UNC share exported by
          the machine you process on.
        </p>
        {dirty && canEdit && (
          <div>
            <button type="button" className="btn btn-touch" disabled={busy}
                    onClick={() => void save({ path })}>
              Save destination
            </button>
          </div>
        )}

        <div className={`text-[12px] ${sum.tone === "warn" ? "text-warn"
          : sum.tone === "ok" ? "text-ink" : "text-dim"}`}>
          {sum.text}
        </div>
        {last && (
          <div className="text-[11px] mono text-dim">
            last pass: {last.summary}
            {last.already_there > 0 && ` · ${last.already_there} already there`}
            {last.extra_at_destination > 0
              && ` · ${last.extra_at_destination} extra at destination (left alone)`}
          </div>
        )}

        <div className="flex gap-2 flex-wrap">
          <button type="button" className="btn btn-touch" onClick={() => void pushNow()}
                  disabled={busy || running || !canPush || !status?.configured}>
            {running ? "Pushing…" : "Push now"}
          </button>
        </div>
        {enabled && status?.configured && (
          <p className="text-[11px] text-dim">
            Otherwise a pass runs {Math.round((status.debounce_s ?? 20))}s after a
            frame lands, and a sweep runs every{" "}
            {Math.round((status.sweep_interval_s ?? 900) / 60)} min so a missed
            frame or an offline hour catches up on its own.
          </p>
        )}
        {err && <p className="text-[12px] text-warn">{err}</p>}
        {!canEdit && (
          <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
            <Icon name="lock" size={11} />
            Read-only — changing file sync needs config.site_optics access.
          </p>
        )}
      </div>
    </Panel>
  );
}
