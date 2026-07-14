// SkyAtlasPanel.tsx — Settings → "Sky Atlas" (offline-pack spec §6).
// Online-fetch toggle (POST /api/config/survey) + offline pack
// status/download/delete (GET/POST/DELETE /api/survey/pack*). Progress polls
// every 2s only while the card is mounted AND a fetch is running.
import { useEffect, useState, type JSX } from "react";
import type { PackStatus } from "../../types";
import { deletePack, getPackStatus, setSurveyConfig, startPackFetch } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { useCan } from "../../lib/caps";
import { confirmDialog } from "../ConfirmDialog";
import { Panel, Toggle } from "../ui";
import { Icon } from "../icons";
import { packProgressPct, packStatusLabel } from "./skyAtlasMeta";

const POLL_MS = 2000;

export default function SkyAtlasPanel(): JSX.Element {
  const config = useConfig();
  const showToast = useStore((s) => s.showToast);
  // config.site_optics — same cap the survey/pack routes require server-side.
  // Without it the card renders read-only (DriversPanel/SafetyPanel idiom):
  // controls disabled, no 403-on-tap.
  const canEdit = useCan("config.site_optics");
  const onlineFetch = config?.survey?.online_fetch ?? false;
  const [status, setStatus] = useState<PackStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [lastFailed, setLastFailed] = useState(0);

  const reload = async () => {
    try {
      const s = await getPackStatus();
      setStatus(s);
      if (s.fetching) setLastFailed(s.fetching.failed);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "couldn't load pack status");
    }
  };
  useEffect(() => { void reload(); }, []);

  // 2s progress poll, only while a fetch runs (GuideFramePreview idiom).
  const fetching = !!status?.fetching;
  useEffect(() => {
    if (!fetching) return;
    const id = window.setInterval(() => { void reload(); }, POLL_MS);
    return () => window.clearInterval(id);
  }, [fetching]);

  const toggleOnline = async (v: boolean) => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await setSurveyConfig({ online_fetch: v });
      await useStore.getState().loadConfig();
    } catch (e) {
      setErr(e instanceof ApiError
        ? (e.status === 403 ? "config.site_optics required to change survey settings" : e.message)
        : "Could not save.");
    } finally { setBusy(false); }
  };

  const download = async () => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await startPackFetch();
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError
        ? (e.status === 403 ? "config.site_optics required to download the offline sky pack"
          : e.status === 507 ? "Not enough space on the capture volume for the pack."
          : e.message)
        : e instanceof Error ? e.message : "download failed");
    } finally { setBusy(false); }
  };

  const remove = () => void (async () => {
    const ok = await confirmDialog({
      title: "Delete offline sky pack?",
      body: "The Atlas will have no survey imagery until it's downloaded again (or online fetch is enabled).",
      tone: "danger",
      confirmLabel: "Delete pack",
    });
    if (!ok) return;
    setBusy(true);
    setErr(null);
    try {
      await deletePack();
      await reload();
      showToast("success", "offline sky pack deleted");
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 403
        ? "config.site_optics required to delete the offline sky pack"
        : e instanceof Error ? e.message : "delete failed");
    } finally { setBusy(false); }
  })();

  const f = status?.fetching ?? null;
  const showRetryHint = (f && f.failed > 0) || (!f && !status?.present && lastFailed > 0);
  return (
    <Panel title="Sky Atlas">
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <span className="label">Online survey fetch (CDS)</span>
          <Toggle checked={onlineFetch} onChange={(v) => void toggleOnline(v)}
                  disabled={busy || !canEdit} label="Online survey fetch" showState />
        </div>
        <p className="text-[12px] text-dim">
          When on, small fields load full-resolution imagery from CDS; the offline pack remains the fallback.
        </p>
        <div className="text-[12px] mono text-ink">{packStatusLabel(status)}</div>
        {f && (
          <div className="h-2 bg-black/30 border border-line2" role="progressbar"
               aria-valuenow={packProgressPct(f)} aria-valuemin={0} aria-valuemax={100}
               aria-label="Pack download progress">
            <div className="h-full bg-[var(--accent)]" style={{ width: `${packProgressPct(f)}%` }} />
          </div>
        )}
        <div className="flex gap-2 flex-wrap">
          <button type="button" className="btn btn-touch" onClick={() => void download()}
                  disabled={busy || !!f || !canEdit}>
            {status?.present ? "Update offline sky pack" : "Download offline sky pack (~250 MB)"}
          </button>
          {status?.present && (
            <button type="button" className="btn btn-touch text-warn" onClick={remove}
                    disabled={busy || !!f || !canEdit}>
              Delete pack
            </button>
          )}
        </div>
        {showRetryHint && (
          <p className="text-[12px] text-warn">
            Some tiles failed — running the download again resumes and retries them.
          </p>
        )}
        {err && <p className="text-[12px] text-warn">{err}</p>}
        {!canEdit && (
          <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
            <Icon name="lock" size={11} />
            Read-only — changing survey settings needs config.site_optics access.
          </p>
        )}
        <p className="text-[11px] text-dim">DSS2 imagery © AAO/STScI, served from CDS/ESA HiPS mirrors.</p>
      </div>
    </Panel>
  );
}
