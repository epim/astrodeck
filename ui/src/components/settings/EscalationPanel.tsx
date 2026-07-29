// EscalationPanel.tsx — what the sequencer does when something goes wrong that
// is not a weather trip: autofocus failed, guiding dropped, the camera never
// reached its setpoint, a frame came back bloated, nothing progressed at all.
//
// The entire EscalationConfig block was config-file-and-API-only. The defaults
// are deliberately gentle (everything warns, nothing aborts), which is right for
// a first night and wrong for an unattended remote rig — and there was no way to
// change that from the product.
//
// RBAC: gated on config.alerts server-side (_require_config_field_caps maps the
// escalation block to CAP_CONFIG_ALERTS — recovery and notification policy are
// filed together). Non-holders see the values read-only with the reason named.
//
// SAVE contract: set_escalation replaces the block wholesale, so we echo the
// full draft — same as SafetyPanel.

import { useEffect, useState, type JSX } from "react";
import type { EscalationConfig } from "../../types";
import { setEscalationConfig } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { accessPhrase, useCan } from "../../lib/caps";
import { Panel, Field, Toggle } from "../ui";
import { Icon } from "../icons";

type Action = "warn" | "abort" | "skip";

const ACTION_LABEL: Record<Action, string> = {
  warn: "Log it and carry on",
  skip: "Skip this target",
  abort: "End the run",
};

/** One "when X fails, do Y" row. Rendered as a labelled select rather than a
 *  free-text field so the vocabulary matches the server's exactly. */
function ActionRow({
  title,
  blurb,
  value,
  options,
  disabled,
  onChange,
}: {
  title: string;
  blurb: string;
  value: string;
  options: { value: string; label: string }[];
  disabled: boolean;
  onChange: (v: string) => void;
}): JSX.Element {
  return (
    <div className="grid gap-2 sm:grid-cols-[1fr_13rem] sm:items-center py-3 border-t border-line">
      <div className="min-w-0">
        <div className="text-sm text-ink">{title}</div>
        <p className="text-[11px] text-dim max-w-md">{blurb}</p>
      </div>
      <select
        className="field"
        value={value}
        aria-label={title}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

const THREE: { value: string; label: string }[] = [
  { value: "warn", label: ACTION_LABEL.warn },
  { value: "skip", label: ACTION_LABEL.skip },
  { value: "abort", label: ACTION_LABEL.abort },
];

export default function EscalationPanel(): JSX.Element {
  const config = useConfig();
  const esc = config?.escalation;
  const canEdit = useCan("config.alerts");

  const [draft, setDraft] = useState<EscalationConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    if (esc) setDraft({ ...esc });
    setErr(null);
    setSavedAt(null);
  }, [esc]);

  if (!esc || !draft) {
    return (
      <Panel title="When something fails">
        <p className="text-xs text-dim">Loading…</p>
      </Panel>
    );
  }

  const patch = (p: Partial<EscalationConfig>) =>
    setDraft((d) => (d ? { ...d, ...p } : d));
  const dirty = JSON.stringify(draft) !== JSON.stringify(esc);
  const ro = !canEdit;

  const save = async () => {
    if (busy || !dirty) return;
    setErr(null);
    setBusy(true);
    try {
      await setEscalationConfig(draft);
      await useStore.getState().loadConfig();
      setSavedAt(Date.now());
    } catch (e) {
      setErr(
        e instanceof ApiError
          ? e.status === 403
            ? `Changing these needs ${accessPhrase("config.alerts")} (config.alerts).`
            : e.message || "Could not save."
          : "Could not save.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title="When something fails"
      right={
        <span className="inline-flex items-center gap-1.5 text-[11px] text-dim">
          <Icon name="alert" size={14} />
          Run recovery
        </span>
      }
    >
      <p className="text-[11px] text-dim leading-relaxed max-w-xl mb-1">
        Failures that are not weather. Everything defaults to logging a warning
        and carrying on, which is the right answer while you are sitting next to
        the rig and the wrong one for an unattended night.
      </p>

      {/* ------------------------------------------------------- preflight */}
      <div className="pt-3 mt-2">
        <h3 className="label">before the run starts</h3>
      </div>

      <div className="grid gap-2 sm:grid-cols-[1fr_13rem] sm:items-center py-3 border-t border-line">
        <div className="min-w-0">
          <div className="text-sm text-ink">Require the camera to be at temperature</div>
          <p className="text-[11px] text-dim max-w-md">
            Darks only match lights taken at the same sensor temperature. If the
            cooler has not settled, the calibration library will not match.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Toggle
            checked={draft.require_cooling}
            onChange={(v) => patch({ require_cooling: v })}
            disabled={busy || ro}
            label="Require the camera to be at temperature"
          />
          <select
            className="field grow"
            value={draft.cooling_action}
            aria-label="If the camera is not at temperature"
            disabled={busy || ro || !draft.require_cooling}
            onChange={(e) => patch({ cooling_action: e.target.value as Action })}
          >
            {THREE.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="grid gap-2 sm:grid-cols-[1fr_13rem] sm:items-center py-3 border-t border-line">
        <div className="min-w-0">
          <div className="text-sm text-ink">Require guiding</div>
          <p className="text-[11px] text-dim max-w-md">
            Refuse to start long exposures unguided. Leave off for short subs on
            a well-aligned mount, or for a rig with no guide camera.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Toggle
            checked={draft.require_guiding}
            onChange={(v) => patch({ require_guiding: v })}
            disabled={busy || ro}
            label="Require guiding"
          />
          <select
            className="field grow"
            value={draft.guiding_action}
            aria-label="If guiding is unavailable"
            disabled={busy || ro || !draft.require_guiding}
            onChange={(e) => patch({ guiding_action: e.target.value as Action })}
          >
            {THREE.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* --------------------------------------------------- during the run */}
      <div className="pt-4 mt-2">
        <h3 className="label">during the run</h3>
      </div>

      <ActionRow
        title="Autofocus failed"
        blurb="A sweep found no minimum — usually thin cloud or a starless narrowband field. Carrying on keeps the old focus position."
        value={draft.af_failure_action}
        options={THREE}
        disabled={busy || ro}
        onChange={(v) => patch({ af_failure_action: v as Action })}
      />

      <ActionRow
        title="A frame failed the quality gate"
        blurb="Bloated stars from wind, a satellite, or a guiding excursion. Discard drops the sub; retake shoots a replacement."
        value={draft.hfr_reject_action}
        options={[
          { value: "warn", label: "Keep it, log a warning" },
          { value: "discard", label: "Discard the frame" },
          { value: "retake", label: "Shoot a replacement" },
        ]}
        disabled={busy || ro}
        onChange={(v) => patch({ hfr_reject_action: v as EscalationConfig["hfr_reject_action"] })}
      />

      {draft.hfr_reject_action === "retake" && (
        <div className="grid gap-3 sm:grid-cols-2 pl-0 sm:pl-4 pb-3">
          <Field
            label="Retake limit per target"
            hint="Stops a windy night burning the whole session on replacements that also fail."
          >
            <input
              className="field"
              type="number"
              min={1}
              max={100}
              step={1}
              value={draft.hfr_retake_limit_per_target}
              disabled={busy || ro}
              onChange={(e) =>
                patch({
                  hfr_retake_limit_per_target: Math.min(100, Math.max(1, Number(e.target.value) || 1)),
                })
              }
            />
          </Field>
        </div>
      )}

      {/* ------------------------------------------------------- watchdog */}
      <div className="py-3 border-t border-line">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            label="No-progress watchdog (minutes)"
            hint="End the run if no frame has been saved for this long. Catches the silent hangs a per-failure rule cannot see — a wedged filter wheel, a mount that never finishes slewing. 0 turns it off."
          >
            <input
              className="field"
              type="number"
              min={0}
              max={600}
              step={5}
              value={Math.round((draft.no_progress_watchdog_s ?? 0) / 60)}
              disabled={busy || ro}
              onChange={(e) =>
                patch({
                  no_progress_watchdog_s:
                    Math.min(600, Math.max(0, Number(e.target.value) || 0)) * 60,
                })
              }
            />
          </Field>
          <div className="flex items-end pb-1">
            <p className="text-[11px] text-dim">
              {draft.no_progress_watchdog_s > 0
                ? `Ends the run after ${Math.round(draft.no_progress_watchdog_s / 60)} min with nothing saved.`
                : "Off — a wedged run waits until you notice."}
            </p>
          </div>
        </div>
      </div>

      {/* ------------------------------------------------------ reconnect */}
      <div className="flex items-start justify-between gap-3 py-3 border-t border-line">
        <div className="min-w-0">
          <div className="text-sm text-ink">Reconnect and resume after a dropout</div>
          <p className="text-[11px] text-dim max-w-md">
            Network-attached (Alpaca) devices only. A USB device that vanishes is
            not recoverable this way.
          </p>
        </div>
        <Toggle
          checked={draft.reconnect_resume}
          onChange={(v) => patch({ reconnect_resume: v })}
          disabled={busy || ro}
          label="Reconnect and resume after a dropout"
          showState
        />
      </div>

      {draft.reconnect_resume && (
        <div className="grid gap-3 sm:grid-cols-2 pb-3">
          <Field
            label="Reconnect attempts"
            hint="How many times to retry before giving up and following the failure action above."
          >
            <input
              className="field"
              type="number"
              min={1}
              max={20}
              step={1}
              value={draft.reconnect_retries}
              disabled={busy || ro}
              onChange={(e) =>
                patch({
                  reconnect_retries: Math.min(20, Math.max(1, Number(e.target.value) || 1)),
                })
              }
            />
          </Field>
        </div>
      )}

      {/* ---------------------------------------------------------- save */}
      {canEdit && (
        <div className="flex items-center gap-3 pt-4 border-t border-line mt-2">
          <button
            type="button"
            className="btn btn-accent min-h-[44px] sm:min-h-0 inline-flex items-center gap-2"
            onClick={save}
            disabled={busy || !dirty}
          >
            <Icon name="check" size={15} />
            {busy ? "Saving…" : "Save"}
          </button>
          {dirty && !busy && (
            <span className="text-[11px] text-warn">Unsaved changes</span>
          )}
          {savedAt && !dirty && !busy && !err && (
            <span className="text-[11px] text-good inline-flex items-center gap-1.5">
              <Icon name="check" size={13} /> Saved
            </span>
          )}
        </div>
      )}

      {!canEdit && (
        <div className="flex items-center gap-3 border border-line2 bg-raise/40 px-3 py-2 text-xs mt-4">
          <Icon name="lock" size={14} className="text-dim shrink-0" />
          <span className="text-dim">
            Changing these needs {accessPhrase("config.alerts")}{" "}
            (config.alerts). The current settings are shown for reference.
          </span>
        </div>
      )}

      {err && (
        <p className="text-xs text-bad inline-flex items-center gap-1.5 mt-3">
          <Icon name="alert" size={13} className="shrink-0" />
          {err}
        </p>
      )}
    </Panel>
  );
}
