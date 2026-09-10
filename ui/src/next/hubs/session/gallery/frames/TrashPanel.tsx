// TrashPanel.tsx - the bin: what was deleted, when it goes, whether it can come
// back, and the two ways to end it early (wave R7, area H:
// `components/gallery/TrashPanel.tsx`).
//
// RESTORE IS THE POINT. A bin without restore is a delayed delete, and "trash"
// is a word that promises undo - so restore is the PRIMARY action here and
// purge is the one behind friction, not the other way round.
//
// PURGE IS THE ONLY IRREVERSIBLE ACTION IN THIS FEATURE, and it goes through the
// house two-step: a modal stating the count and the total size, and inside it a
// hold-to-confirm (`confirmDialog` mode "hold" - the same shape the solar
// override uses, not a new pattern invented here). One deliberate read, one
// deliberate hold. Nothing in this file erases a file on one click.
//
// THE LEDGER NOTE IS NOT DECORATION. Session reports reference frames by path;
// purging leaves those rows pointing at nothing and NOTHING in the codebase
// repairs it. The design's instruction was explicit: do not silently repair it,
// say it where the user can see it - so it rides in the confirmation, where the
// decision is being made, and again here, where the evidence is still
// recoverable.

import { useCallback, useEffect, useState, type JSX } from "react";

import { ApiError } from "../../../../../api";
import {
  listTrash, purgeAllTrashed, purgeTrashed, restoreTrashed,
} from "../../../../../api/gallery";
import { confirmDialog } from "../../../../../components/ConfirmDialog";
import {
  LEDGER_ORPHAN_NOTE, deletedAgo, partialFailureNote, purgeConfirmCopy,
  purgesIn, togglePath,
} from "../../../../../lib/gallery";
import { useStore } from "../../../../../store";
import type { GalleryTrashListing, TrashItem } from "../../../../../types";
import { NxIcon } from "../../../../icons";
import { explainLock } from "../../../../shell/explain";
import {
  ActionButton, Card, EmptyCard, Label, ListRow, Mono, Pill,
} from "../../../../ui";
import {
  bytesLabel, costLabel, countLabel, hy, lineOrNull, NOT_RESTORABLE, trashIntro,
  trashRowSub,
} from "./frameCopy";

/** One bin row. The whole row is the tick target (56 px), and the ticked state
 *  is carried by a WORD as well as the accent, because `:root.night` collapses
 *  the palette onto one hue and a border swap alone would say nothing at 2am. */
function TrashRow({ item, now, selected, onToggle }: {
  item: TrashItem; now: number; selected: boolean; onToggle: () => void;
}): JSX.Element {
  return (
    <ListRow
      data-testid="gallery-trash-row"
      icon={<NxIcon name={selected ? "check" : "clock"} size={16} />}
      tone={selected ? "accent" : undefined}
      title={item.name}
      sub={trashRowSub(item.original, deletedAgo(item.deleted_at, now), purgesIn(item.expires_at, now), item.bytes)}
      right={
        <>
          {!item.restorable && <Pill tone="warn" glyph={<NxIcon name="info" size={11} />}>{NOT_RESTORABLE}</Pill>}
          <span>{selected ? "TICKED" : ""}</span>
        </>
      }
      onPress={onToggle}
    />
  );
}

export function TrashPanel({ onChanged }: {
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
  // in ..." on screen is measured from the same instant.
  const now = Date.now() / 1000;

  const reload = useCallback(async () => {
    try {
      const d = await listTrash();
      setData(d);
      setErr(null);
      // Drop ticks for anything no longer in the bin - a stale path would
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
        title: `Restored ${countLabel(n)} ${n === 1 ? "frame" : "frames"}`,
        detail: lineOrNull(partialFailureNote(n, r.failed)) ?? undefined,
      });
      await reload();
      onChanged();
    } catch (e) {
      enqueueToast({
        level: "error", title: "Restore failed",
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
    // Step 1: a modal stating exactly what disappears. Step 2: the hold inside
    // it. `confirmDialog` resolves false on Escape, Cancel and dismiss.
    const go = await confirmDialog({
      title: hy(copy.title), body: hy(copy.body), tone: "danger",
      mode: "hold", confirmLabel: "Delete permanently",
    });
    if (!go) return;
    setBusy(true);
    try {
      const r = paths.length ? await purgeTrashed(paths) : await purgeAllTrashed();
      enqueueToast({
        level: r.failed.length ? "warning" : "success",
        title: `Permanently deleted ${countLabel(r.purged)} ${r.purged === 1 ? "file" : "files"}, ${bytesLabel(r.bytes)}`,
        detail: lineOrNull(partialFailureNote(r.purged, r.failed)) ?? undefined,
      });
      await reload();
      onChanged();
    } catch (e) {
      enqueueToast({
        level: "error", title: "Purge failed",
        detail: e instanceof ApiError ? e.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card data-testid="gallery-trash">
      <div className="nx-frames">
        <div className="nx-frames-actions">
          <Label>TRASH</Label>
          <span className="nx-frames-spacer" />
          <Mono size={10.5} tone="dim">
            {data ? costLabel(data.count, data.bytes) : "reading the bin"}
          </Mono>
        </div>

        {err && <Mono size={10} tone="bad" data-testid="gallery-trash-error">{hy(err)}</Mono>}

        <p className="nx-frames-note">{`${trashIntro(ttl)} ${LEDGER_ORPHAN_NOTE}`}</p>

        {items.length === 0 && !err ? (
          <EmptyCard
            title="THE TRASH IS EMPTY"
            hint={`Frames you delete from the library land here and stay recoverable for ${ttl} days.`}
          />
        ) : (
          <>
            <div className="nx-frames-actions">
              <ActionButton
                kind="ghost"
                data-testid="gallery-trash-all"
                onPress={() => setPicked(new Set(items.map((i) => i.path)))}
              >
                {`SELECT ALL ${countLabel(items.length)}`}
              </ActionButton>
              <ActionButton
                kind="ghost"
                lockedReason={picked.size === 0 ? "Nothing is ticked." : null}
                onExplain={explainLock}
                onPress={() => setPicked(new Set())}
              >
                CLEAR
              </ActionButton>
              <span className="nx-frames-spacer" />
              <ActionButton
                kind="primary"
                data-testid="gallery-trash-restore"
                busy={busy}
                lockedReason={blockedReason(picked.size)}
                onExplain={explainLock}
                onPress={() => void onRestore()}
              >
                {picked.size > 0 ? `RESTORE ${countLabel(picked.size)}` : "RESTORE"}
              </ActionButton>
              <ActionButton
                kind="danger"
                data-testid="gallery-trash-purge"
                lockedReason={blockedReason(picked.size)}
                onExplain={explainLock}
                onPress={() => void onPurge(pickedItems.map((i) => i.path), pickedItems.length, pickedBytes)}
              >
                DELETE PERMANENTLY
              </ActionButton>
              <ActionButton
                kind="danger"
                data-testid="gallery-trash-empty"
                lockedReason={busy ? "Another trash action is still running." : null}
                onExplain={explainLock}
                onPress={() => void onPurge([], data?.count ?? 0, data?.bytes ?? 0)}
              >
                EMPTY TRASH
              </ActionButton>
            </div>

            <div className="nx-frames-trashlist">
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
      </div>
    </Card>
  );
}
