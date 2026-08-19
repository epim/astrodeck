// NamingPanel.tsx — Settings → capture file-naming template (PRO-11).
// Live client preview (advisory; server render is authoritative). config.site_optics.
import { useEffect, useState, type JSX } from "react";
import { setNamingConfig } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { useCan } from "../../lib/caps";
import { Panel, Field } from "../ui";
import { Icon } from "../icons";
import {
  DEFAULT_TEMPLATE, NAMING_TOKENS, renderTemplatePreview,
} from "../../lib/naming";

const SAMPLE = { TARGET: "M42", FRAMETYPE: "Light", FILTER: "Ha", DATE: "2026-07-23",
  TIME: "213045", DATETIME: "2026-07-23_213045", NIGHT: "2026-07-23", FRAMENR: "0001" };

export default function NamingPanel(): JSX.Element {
  const config = useConfig();
  const canEdit = useCan("config.site_optics");
  const stored = config?.naming?.template ?? DEFAULT_TEMPLATE;
  const [draft, setDraft] = useState(stored);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setDraft(stored); }, [stored]);

  const dirty = draft !== stored;
  const save = async () => {
    if (busy || !canEdit || !dirty) return;
    setBusy(true); setErr(null);
    try {
      await setNamingConfig({ template: draft });
      await useStore.getState().loadConfig();
      useStore.getState().showToast("success", "capture naming saved");
    } catch (e) {
      setErr(e instanceof ApiError
        ? (e.status === 403 ? "config.site_optics required" : e.message)
        : "Could not save.");
    } finally { setBusy(false); }
  };
  const reset = () => { if (canEdit) setDraft(DEFAULT_TEMPLATE); };

  const saveInert = busy || !canEdit || !dirty;
  const resetInert = busy || !canEdit || draft === DEFAULT_TEMPLATE;
  return (
    <Panel title="File Naming">
      <div className="flex flex-col gap-3">
        <Field label="Template">
          <input className="field mono" value={draft} readOnly={!canEdit}
                 onChange={(e) => setDraft(e.target.value)} aria-label="File-naming template"
                 spellCheck={false} />
        </Field>
        <div className="text-[12px] text-dim">Preview</div>
        <div className="mono text-[12px] text-ink break-all">
          captures/{renderTemplatePreview(draft || DEFAULT_TEMPLATE, SAMPLE)}
        </div>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Tokens">
          {NAMING_TOKENS.map((t) => (
            <button key={t} type="button"
              aria-disabled={!canEdit || undefined}
              title={!canEdit ? "config.site_optics required" : `insert $$${t}$$`}
              onClick={!canEdit ? undefined : () => setDraft((d) => `${d}$$${t}$$`)}
              className={`chip-btn text-[11px] px-2 py-1 border uppercase tracking-wide
                ${!canEdit ? "!text-dim cursor-not-allowed" : "bg-raise border-line2 text-dim"}`}>
              {`$$${t}$$`}
            </button>
          ))}
        </div>
        <div className="flex gap-2 flex-wrap">
          <button type="button" aria-disabled={saveInert || undefined}
            title={!canEdit ? "config.site_optics required" : undefined}
            onClick={saveInert ? undefined : () => void save()}
            className={`btn btn-touch inline-flex items-center gap-1
              ${saveInert ? "!text-dim cursor-not-allowed" : "btn-accent"}`}>
            {!canEdit && <Icon name="lock" size={11} />} Save
          </button>
          <button type="button" aria-disabled={resetInert || undefined}
            onClick={resetInert ? undefined : reset}
            className={`btn btn-touch ${resetInert ? "!text-dim cursor-not-allowed" : ""}`}>
            Reset to default
          </button>
        </div>
        {err && <p className="text-[12px] text-warn">{err}</p>}
        {!canEdit && (
          <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
            <Icon name="lock" size={11} /> Read-only — changing naming needs config.site_optics access.
          </p>
        )}
        <p className="text-[11px] text-dim">
          Folders via <span className="mono">/</span>; tokens like{" "}
          <span className="mono">$$TARGET$$</span>. The default reproduces the classic layout.
        </p>
      </div>
    </Panel>
  );
}
