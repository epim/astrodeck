// components/gallery/TrashPanel.tsx — the bin: what was deleted, when it goes,
// whether it can come back, and the two ways to end it early.
//
// RESTORE IS THE POINT. A bin without restore is a delayed delete, and "Trash"
// is a word that promises undo — so restore is the accent-coloured action here
// and purge is the one behind friction, not the other way round.
//
// PURGE IS THE ONLY IRREVERSIBLE ACTION IN THIS FEATURE, and it goes through the
// house two-step: a modal that states the count and the total size, and inside
// it a hold-to-confirm (confirmDialog mode "hold" — the same shape SafetyPanel
// uses for the solar override, not a new pattern invented here). One deliberate
// read, one deliberate hold. Nothing in this file erases a file on one click.
//
// Blocked buttons speak (house rule §11.8): they use HonestButton rather than
// the native `disabled` attribute, which removes the control from the
// accessibility tree and takes the reason with it, leaving a grey rectangle that
// cannot even be focused to ask why.

import { useCallback, useEffect, useState } from "react";
import type { JSX } from "react";
import { ApiError } from "../../api";
import {
  listTrash, purgeAllTrashed, purgeTrashed, restoreTrashed,
} from "../../api/gallery";
import { useStore } from "../../store";
import { confirmDialog } from "../ConfirmDialog";
import { Icon } from "../icons";
import { EmptyState, HonestButton, Panel } from "../ui";
import {
  LEDGER_ORPHAN_NOTE, deletedAgo, fmtBytes, fmtCount, fmtFrameCost,
  partialFailureNote, purgeConfirmCopy, purgesIn, togglePath,
} from "../../lib/gallery";
import type { GalleryTrashListing, TrashItem } from "../../types";

function TrashRow({ item, now, selected, onToggle }: {
  item: TrashItem; now: number; selected: boolean; onToggle: () => void;
}): JSX.Element {
  return (
    <div className="flex items-start gap-2 py-1.5 border-b border-line/40 last:border-0 min-w-0">
      <label className="min-h-[44px] min-w-[44px] flex items-center justify-center shrink-0 cursor-pointer">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          aria-label={`Select ${item.name}`}
          className="w-4 h-4 accent-[var(--accent)]"
        />
      </label>
      <div className="min-w-0 flex-1 py-1.5">
        <div className="mono text-xs text-ink truncate" title={item.path}>{item.name}</div>
        {/* The ORIGINAL path, not the trash path: "where does it go back to" is
            the question a restore raises, and the trash path is bookkeeping the
            user never chose (it can even carry a -1 collision suffix). */}
        <div className="text-[11px] text-dim truncate" title={item.original}>
          from {item.original}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-dim">
          <span>deleted {deletedAgo(item.deleted_at, now)}</span>
          <span className="inline-flex items-center gap-1">
            <Icon name="clock" size={11} aria-hidden />
            {purgesIn(item.expires_at, now)}
          </span>
          <span className="tabular-nums">{fmtBytes(item.bytes)}</span>
          {!item.restorable && (
            // Glyph + words, not a colour: this is the one row state that
            // changes what an action will do, and the night palette is no place
            // to encode that in hue alone.
            <span className="inline-flex items-center gap-1 text-warn">
              <Icon name="alert" size={11} aria-hidden />
              can&apos;t restore — a file already sits at that path
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export default function TrashPanel({ onChanged }: {
  /** Fired after anything left the bin, so the library grid can re-read: a
   *  restored frame reappears in the listing, and a purge changes what the disk
   *  actually holds. */
  onChanged: () => void;
}): JSX.Element {
  const enqueueToast = useStore((s) => s.enqueueToast);
  const [data, setData] = useState<GalleryTrashListing | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  // One clock read per render pass rather than one per row, so every "purges
  // in …" on screen is measured from the same instant.
  const now = Date.now() / 1000;

  const reload = useCallback(async () => {
    try {
      const d = await listTrash();
      setData(d);
      setErr(null);
      // Drop ticks for anything no longer in the bin — a stale path would
      // otherwise ride into the next purge request purely to be refused.
      setPicked((p) => new Set([...p].filter((x) => d.items.some((i) => i.path === x))));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "could not read the trash");
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  const items = data?.items ?? [];
  const pickedItems = items.filter((i) => picked.has(i.path));
  const pickedBytes = pickedItems.reduce((a, i) => a + (i.bytes || 0), 0);
  const ttl = data?.ttl_days ?? 30;

  const explain = (reason: string): void =>
    enqueueToast({ level: "info", title: "Not available", detail: reason });

  const blockedReason = (need: number): string | null => {
    if (busy) return "Another trash action is still running.";
    if (need === 0) return "Tick the rows you mean first.";
    return null;
  };

  async function onRestore(): Promise<void> {
    const paths = pickedItems.map((i) => i.path);
    if (!paths.length) return;
    setBusy(true);
    try {
      const r = await restoreTrashed(paths);
      const n = r.restored.length;
      enqueueToast({
        level: r.failed.length ? "warning" : "success",
        title: `Restored ${fmtCount(n)} ${n === 1 ? "frame" : "frames"}`,
        detail: partialFailureNote(n, r.failed) ?? undefined,
      });
      await reload();
      onChanged();
    } catch (e) {
      enqueueToast({
        level: "error",
        title: "Restore failed",
        detail: e instanceof ApiError ? e.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  /** `paths` empty means EMPTY THE TRASH (the server's `all: true`). */
  async function onPurge(paths: string[], count: number, bytes: number): Promise<void> {
    if (count <= 0) return;
    const copy = purgeConfirmCopy(count, bytes);
    // Step 1 — a modal stating exactly what disappears. Step 2 — the hold
    // inside it. `confirmDialog` resolves false on Escape/Cancel/dismiss.
    const go = await confirmDialog({
      title: copy.title,
      body: copy.body,
      tone: "danger",
      mode: "hold",
      confirmLabel: "Delete permanently",
    });
    if (!go) return;
    setBusy(true);
    try {
      const r = paths.length ? await purgeTrashed(paths) : await purgeAllTrashed();
      enqueueToast({
        level: r.failed.length ? "warning" : "success",
        title: `Permanently deleted ${fmtCount(r.purged)} ${r.purged === 1 ? "file" : "files"}, ${fmtBytes(r.bytes)}`,
        detail: partialFailureNote(r.purged, r.failed) ?? undefined,
      });
      await reload();
      onChanged();
    } catch (e) {
      enqueueToast({
        level: "error",
        title: "Purge failed",
        detail: e instanceof ApiError ? e.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  //: one shape for every action button in the bin; >=44px on a phone.
  const actionBtn = "!py-1.5 !px-3 text-[11px] min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5";

  return (
    <Panel
      title="Trash"
      right={
        <span className="text-xs text-dim">
          {data ? fmtFrameCost(data.count, data.bytes) : "…"}
        </span>
      }
    >
      {err && (
        <p className="flex items-center gap-1.5 text-xs text-bad mb-2">
          <Icon name="alert" size={12} aria-hidden /> {err}
        </p>
      )}

      <p className="text-[11px] text-dim mb-3 leading-relaxed">
        Deleted frames move here and purge automatically after {ttl} days.{" "}
        <strong className="text-ink">The disk space is not freed until they purge</strong> — so
        if you deleted them to make room tonight, empty the trash too. {LEDGER_ORPHAN_NOTE}
      </p>

      {items.length === 0 && !err ? (
        <EmptyState
          icon="trash"
          title="The trash is empty"
          hint={`Frames you delete from the gallery land here and stay recoverable for ${ttl} days.`}
        />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 mb-2">
            <button
              className={`btn ${actionBtn}`}
              onClick={() => setPicked(new Set(items.map((i) => i.path)))}
            >
              <Icon name="check" size={13} /> Select all {fmtCount(items.length)}
            </button>
            <HonestButton
              className={`btn ${actionBtn}`}
              reason={picked.size === 0 ? "Nothing is ticked." : null}
              onClick={() => setPicked(new Set())}
              onExplain={explain}
            >
              Clear
            </HonestButton>
            <div className="flex-1" />
            <HonestButton
              className={`btn btn-accent ${actionBtn}`}
              reason={blockedReason(picked.size)}
              onClick={() => void onRestore()}
              onExplain={explain}
            >
              <Icon name="upload" size={13} />
              Restore {picked.size > 0 ? fmtCount(picked.size) : ""}
            </HonestButton>
            <HonestButton
              className={`btn btn-danger ${actionBtn}`}
              reason={blockedReason(picked.size)}
              onClick={() => void onPurge(pickedItems.map((i) => i.path), pickedItems.length, pickedBytes)}
              onExplain={explain}
            >
              <Icon name="trash" size={13} /> Delete permanently
            </HonestButton>
            <HonestButton
              className={`btn btn-danger ${actionBtn}`}
              reason={busy ? "Another trash action is still running." : null}
              onClick={() => void onPurge([], data?.count ?? 0, data?.bytes ?? 0)}
              onExplain={explain}
            >
              Empty trash
            </HonestButton>
          </div>

          {/* Scrolls in its own box: a 500-item bin must not push the library
              grid off the bottom of the page. */}
          <div className="max-h-[52vh] overflow-y-auto">
            {items.map((i) => (
              <TrashRow
                key={i.path}
                item={i}
                now={now}
                selected={picked.has(i.path)}
                onToggle={() => setPicked((p) => togglePath(p, i.path))}
              />
            ))}
          </div>
        </>
      )}
    </Panel>
  );
}
