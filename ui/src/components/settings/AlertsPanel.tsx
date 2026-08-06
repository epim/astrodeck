// AlertsPanel.tsx — Settings → Alerts: add/edit/test/delete outbound alert
// sinks (ntfy / webhook / Telegram / Discord / Slack / email) + per-sink
// health + the dead-man's-switch card (PRO-9 spec §3 task U3).
//
// Idioms copied verbatim from cited precedent:
//  - draft/dirty-ish + write-only secret input placeholder "(unchanged)",
//    never seeded from config: WeatherPanel.tsx (Astrospheric key field).
//  - confirm-before-delete: SafetyPanel.tsx confirmDialog usage.
//  - read-only lock note: DriversPanel.tsx.
//  - honest-disabled (dim + lock glyph + aria-disabled + title — NEVER the
//    native `disabled` attribute on a primary action, spec §11.8):
//    PreviewToolbar.tsx's Toggle helper.
//
// All per-kind validation / health-verdict / draft-default decisions live in
// the PURE lib/alertSinks.ts helpers (tested in isolation) — nothing is
// re-decided here; this file is render + wiring only.

import { useEffect, useState, type JSX, type ReactNode } from "react";
import type { AlertHealth, AlertSink, AlertSinkInput } from "../../types";
import { api, ApiError } from "../../api";
import { deleteAlert, getAlertHealth, testAlert, upsertAlert } from "../../api/alerts";
import {
  ALERT_KINDS, ALL_EVENTS, deadmanVerdict, defaultDraft, deriveSinkHealth,
  kindLabel, validateDraft, type HealthVerdict,
} from "../../lib/alertSinks";
import { uid } from "../../lib/ids";
import { useConfig, useStore } from "../../store";
import { accessPhrase, useCan } from "../../lib/caps";
import { confirmDialog } from "../ConfirmDialog";
import { Field, Panel, Toggle } from "../ui";
import { Segmented } from "../Segmented";
import { Icon, type IconName } from "../icons";

// No dedicated brand glyphs exist yet (icons.tsx has no discord/slack/mail) —
// reuse "link" for the url/webhook-shaped channels, per spec §4 decision 5.
const KIND_ICON: Record<AlertSink["kind"], IconName> = {
  ntfy: "link",
  webhook: "link",
  discord: "link",
  slack: "link",
  telegram: "key",
  email: "info",
};

const TONE_CLASS: Record<HealthVerdict["tone"], string> = {
  good: "text-good border-good/50",
  warn: "text-warn border-warn/50",
  bad: "text-bad border-bad/60",
  dim: "text-dim border-line2",
};

function Badge({ v }: { v: HealthVerdict }): JSX.Element {
  return (
    <span
      title={v.detail}
      className={`mono text-[9px] tracking-[0.16em] uppercase px-1.5 py-0.5 border ${TONE_CLASS[v.tone]}`}
    >
      {v.label}
    </span>
  );
}

/** Honest-disabled primary-action button (spec §11.8): a dim token + lock
 *  glyph + `aria-disabled` + `title` communicate the locked state — NEVER the
 *  native `disabled` attribute. The click is simply swallowed while locked or
 *  busy (mirrors PreviewToolbar.tsx's Toggle helper). */
function ActionButton({
  onClick, locked, lockedTitle, busy, tone = "default", children,
}: {
  onClick: () => void;
  locked: boolean;
  lockedTitle: string;
  busy?: boolean;
  tone?: "default" | "accent" | "danger";
  children: ReactNode;
}): JSX.Element {
  const inert = locked || !!busy;
  return (
    <button
      type="button"
      aria-disabled={inert || undefined}
      title={locked ? lockedTitle : undefined}
      onClick={inert ? undefined : onClick}
      className={`btn !py-1 text-xs inline-flex items-center gap-1
        ${!inert && tone === "accent" ? "btn-accent" : ""}
        ${!inert && tone === "danger" ? "!border-bad/60 !text-bad" : ""}
        ${inert ? "!text-dim cursor-not-allowed" : ""}`}
    >
      {locked && <Icon name="lock" size={11} />}
      {children}
    </button>
  );
}

function SecretField({
  label, value, onChange, tokenConfigured, canEdit, placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  tokenConfigured: boolean;
  canEdit: boolean;
  placeholder?: string;
}): JSX.Element {
  return (
    <Field label={`${label} — ${tokenConfigured ? "set" : "not set"}`}>
      <input
        className="field"
        type="password"
        value={value}
        disabled={!canEdit}
        onChange={(e) => onChange(e.target.value)}
        placeholder={tokenConfigured ? "(unchanged)" : (placeholder ?? "required")}
        aria-label={`${label} (write-only)`}
      />
    </Field>
  );
}

function EventChips({
  events, onChange, canEdit,
}: {
  events: string[];
  onChange: (next: string[]) => void;
  canEdit: boolean;
}): JSX.Element {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Alert events">
      {ALL_EVENTS.map((ev) => {
        const on = events.includes(ev);
        return (
          <button
            key={ev}
            type="button"
            aria-pressed={on}
            disabled={!canEdit}
            onClick={() => onChange(on ? events.filter((e) => e !== ev) : [...events, ev])}
            className={`text-[11px] px-2 py-1 border uppercase tracking-wide
              ${on ? "bg-accent2/40 border-accent text-accent" : "bg-raise border-line2 text-dim"}
              ${!canEdit ? "opacity-50" : ""}`}
          >
            {ev}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- sink form

function SinkForm({
  draft, setDraft, tokenConfigured, canEdit, saving, onSave, onCancel,
}: {
  draft: AlertSinkInput;
  setDraft: (d: AlertSinkInput) => void;
  tokenConfigured: boolean;
  canEdit: boolean;
  saving: boolean;
  onSave: () => void;
  onCancel: () => void;
}): JSX.Element {
  const changeKind = (kind: AlertSink["kind"]) => {
    // Each kind has a different field shape — reset to that kind's defaults,
    // keeping only the id + enabled bit an in-progress edit already has. The
    // secret (if any) must be re-entered: token_configured only described the
    // ORIGINAL kind's secret.
    setDraft({ ...defaultDraft(kind, draft.id), enabled: draft.enabled });
  };

  return (
    <div className="border border-line2 bg-raise/40 p-3 flex flex-col gap-3 mt-2">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <Field label="Channel">
          <Segmented
            options={ALERT_KINDS.map((k) => ({ value: k, label: kindLabel(k) }))}
            value={draft.kind}
            onChange={changeKind}
            ariaLabel="Alert channel kind"
            disabled={!canEdit}
          />
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <span className="label">Enabled</span>
          <Toggle
            checked={draft.enabled}
            onChange={(v) => setDraft({ ...draft, enabled: v })}
            disabled={!canEdit}
            label="Sink enabled"
          />
        </label>
      </div>

      {(draft.kind === "ntfy" || draft.kind === "webhook") && (
        <Field label={draft.kind === "ntfy" ? "ntfy topic URL" : "Webhook URL"}>
          <input
            className="field"
            value={draft.url}
            disabled={!canEdit}
            onChange={(e) => setDraft({ ...draft, url: e.target.value })}
            placeholder="https://…"
          />
        </Field>
      )}

      {draft.kind === "telegram" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Chat ID">
            <input
              className="field"
              value={draft.chat_id ?? ""}
              disabled={!canEdit}
              onChange={(e) => setDraft({ ...draft, chat_id: e.target.value })}
              placeholder="e.g. 123456789"
            />
          </Field>
          <SecretField
            label="Bot token"
            value={draft.token ?? ""}
            onChange={(v) => setDraft({ ...draft, token: v })}
            tokenConfigured={tokenConfigured}
            canEdit={canEdit}
          />
        </div>
      )}

      {(draft.kind === "discord" || draft.kind === "slack") && (
        <SecretField
          label={`${kindLabel(draft.kind)} webhook URL`}
          value={draft.token ?? ""}
          onChange={(v) => setDraft({ ...draft, token: v })}
          tokenConfigured={tokenConfigured}
          canEdit={canEdit}
          placeholder="https://…"
        />
      )}

      {draft.kind === "email" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="SMTP host">
            <input
              className="field"
              value={draft.smtp_host ?? ""}
              disabled={!canEdit}
              onChange={(e) => setDraft({ ...draft, smtp_host: e.target.value })}
              placeholder="smtp.example.com"
            />
          </Field>
          <Field label="SMTP port">
            <input
              className="field"
              inputMode="numeric"
              value={String(draft.smtp_port ?? 587)}
              disabled={!canEdit}
              onChange={(e) => setDraft({ ...draft, smtp_port: Number(e.target.value) || 0 })}
              placeholder="587"
            />
          </Field>
          <Field label="From address">
            <input
              className="field"
              value={draft.smtp_from ?? ""}
              disabled={!canEdit}
              onChange={(e) => setDraft({ ...draft, smtp_from: e.target.value })}
              placeholder="rig@example.com"
            />
          </Field>
          <Field label="To (comma-separated)">
            <input
              className="field"
              value={draft.smtp_to ?? ""}
              disabled={!canEdit}
              onChange={(e) => setDraft({ ...draft, smtp_to: e.target.value })}
              placeholder="me@example.com, backup@example.com"
            />
          </Field>
          <Field label="Login user (optional)">
            <input
              className="field"
              value={draft.smtp_user ?? ""}
              disabled={!canEdit}
              onChange={(e) => setDraft({ ...draft, smtp_user: e.target.value })}
              placeholder="usually the From address"
            />
          </Field>
          <SecretField
            label="SMTP password (optional)"
            value={draft.token ?? ""}
            onChange={(v) => setDraft({ ...draft, token: v })}
            tokenConfigured={tokenConfigured}
            canEdit={canEdit}
            placeholder="optional — open relays exist"
          />
          <label className="flex items-center justify-between gap-2 text-sm sm:col-span-2">
            <span className="label">STARTTLS</span>
            <Toggle
              checked={draft.smtp_starttls ?? true}
              onChange={(v) => setDraft({ ...draft, smtp_starttls: v })}
              disabled={!canEdit}
              label="SMTP STARTTLS"
              showState
            />
          </label>
        </div>
      )}

      <Field label="Minimum level (generic warning/error logs; state-change alerts always send)">
        <Segmented
          options={[
            { value: "warning" as const, label: "Warning" },
            { value: "error" as const, label: "Error" },
          ]}
          value={draft.min_level}
          onChange={(v) => setDraft({ ...draft, min_level: v })}
          ariaLabel="Minimum level"
          disabled={!canEdit}
        />
      </Field>

      <Field label="Events">
        <EventChips
          events={draft.events}
          onChange={(next) => setDraft({ ...draft, events: next })}
          canEdit={canEdit}
        />
      </Field>

      <Field label="Heartbeat (minutes, 0 = off)">
        <input
          className="field"
          inputMode="numeric"
          value={String(draft.heartbeat_min)}
          disabled={!canEdit}
          onChange={(e) => setDraft({ ...draft, heartbeat_min: Number(e.target.value) || 0 })}
          placeholder="0"
        />
      </Field>

      <div className="flex items-center justify-end gap-2 pt-1 border-t border-line">
        <button type="button" className="btn !py-1 text-xs" onClick={onCancel}>
          Cancel
        </button>
        <ActionButton
          onClick={onSave}
          locked={!canEdit}
          lockedTitle={`Saving needs ${accessPhrase("config.alerts")}`}
          busy={saving}
          tone="accent"
        >
          {saving ? "Saving…" : "Save sink"}
        </ActionButton>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------- panel

export default function AlertsPanel(): JSX.Element {
  const config = useConfig();
  const canEdit = useCan("config.alerts");

  const sinks = config?.alerts ?? [];
  const deadmanConfigured = !!config?.deadman_configured;

  const [health, setHealth] = useState<AlertHealth | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null); // sink id, or "__new__"
  const [draft, setDraft] = useState<AlertSinkInput | null>(null);
  const [draftTokenConfigured, setDraftTokenConfigured] = useState(false);
  const [saving, setSaving] = useState(false);
  // Per row, not per panel. This was a single id, and the Test handler bailed on
  // ANY row being busy — so while one channel was waiting on an SMTP timeout the
  // other rows' Test buttons pressed down, looked live, and did nothing: no dim,
  // no aria-disabled, no toast. The routes are independent (POST
  // /api/alerts/{id}/test), so the guard is too; it still stops a double-tap on
  // the SAME row.
  const [busyIds, setBusyIds] = useState<string[]>([]); // per-row test/delete
  const isBusy = (id: string) => busyIds.includes(id);
  const markBusy = (id: string, on: boolean) =>
    setBusyIds((ids) =>
      on ? (ids.includes(id) ? ids : [...ids, id]) : ids.filter((x) => x !== id),
    );
  const [deadmanUrl, setDeadmanUrl] = useState("");
  const [deadmanBusy, setDeadmanBusy] = useState(false);

  const refreshHealth = async () => {
    try {
      setHealth(await getAlertHealth());
    } catch {
      // best-effort — a failed poll just leaves the last snapshot on screen
    }
  };

  // Fetch on mount and whenever the config bounces (a save/test/delete here —
  // or anywhere else — broadcasts `config`, which loadConfig folds into
  // config.version, so re-polling health alongside it keeps the queue/deadman
  // badges fresh without a dedicated WS subscription).
  useEffect(() => {
    void refreshHealth();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config?.version]);

  const openAdd = () => {
    setEditingId("__new__");
    setDraft(defaultDraft("ntfy", uid()));
    setDraftTokenConfigured(false);
  };

  const openEdit = (s: AlertSink) => {
    setEditingId(s.id);
    setDraft({ ...s, token: "" }); // write-only: NEVER seeded from config
    setDraftTokenConfigured(!!s.token_configured);
  };

  const closeForm = () => {
    setEditingId(null);
    setDraft(null);
    setDraftTokenConfigured(false);
  };

  const save = async () => {
    if (!draft || saving) return;
    const err = validateDraft(draft, draftTokenConfigured);
    if (err) {
      useStore.getState().enqueueToast({ level: "error", title: err });
      return;
    }
    setSaving(true);
    try {
      await upsertAlert(draft);
      await useStore.getState().loadConfig();
      useStore.getState().enqueueToast({ level: "success", title: `${kindLabel(draft.kind)} sink saved` });
      closeForm();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await useStore.getState().loadConfig();
        useStore.getState().enqueueToast({
          level: "error",
          title: "Config changed elsewhere — reloaded, re-apply your edit",
        });
      } else if (e instanceof ApiError && e.status === 403) {
        useStore.getState().enqueueToast({ level: "error", title: "config.alerts required to change alert sinks" });
      } else {
        useStore.getState().enqueueToast({
          level: "error",
          title: e instanceof Error ? e.message : "Could not save alert sink",
        });
      }
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (id: string) => {
    if (isBusy(id)) return;
    markBusy(id, true);
    try {
      const res = await testAlert(id);
      useStore.getState().enqueueToast({
        level: res.ok ? "success" : "error",
        title: res.ok ? "Test alert delivered" : (res.error || "Test alert failed"),
      });
      await useStore.getState().loadConfig(); // refresh the verified badge
      await refreshHealth();
    } catch (e) {
      useStore.getState().enqueueToast({
        level: "error",
        title: e instanceof Error ? e.message : "Could not run the test",
      });
    } finally {
      markBusy(id, false);
    }
  };

  const removeSink = async (s: AlertSink) => {
    const ok = await confirmDialog({
      title: `Delete the ${kindLabel(s.kind)} sink?`,
      body: <>This stops alerts to this channel immediately. This can&apos;t be undone.</>,
      tone: "danger",
      mode: "confirm",
      confirmLabel: "Delete sink",
      cancelLabel: "Keep it",
    });
    if (!ok) return;
    markBusy(s.id, true);
    try {
      await deleteAlert(s.id);
      await useStore.getState().loadConfig();
      await refreshHealth();
      useStore.getState().enqueueToast({ level: "success", title: "Alert sink deleted" });
      if (editingId === s.id) closeForm();
    } catch (e) {
      useStore.getState().enqueueToast({
        level: "error",
        title: e instanceof Error ? e.message : "Could not delete the sink",
      });
    } finally {
      markBusy(s.id, false);
    }
  };

  const saveDeadman = async () => {
    if (deadmanBusy) return;
    const url = deadmanUrl.trim();
    if (!url) {
      useStore.getState().enqueueToast({ level: "error", title: "Enter a monitor URL first" });
      return;
    }
    setDeadmanBusy(true);
    try {
      await api.post("/api/config", { deadman_url: url });
      setDeadmanUrl("");
      await useStore.getState().loadConfig();
      useStore.getState().enqueueToast({ level: "success", title: "Dead-man's-switch URL saved" });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await useStore.getState().loadConfig();
        useStore.getState().enqueueToast({
          level: "error",
          title: "Config changed elsewhere — reloaded, re-apply your edit",
        });
      } else {
        useStore.getState().enqueueToast({
          level: "error",
          title: e instanceof Error ? e.message : "Could not save the dead-man's-switch URL",
        });
      }
    } finally {
      setDeadmanBusy(false);
    }
  };

  if (!config) {
    return (
      <Panel title="Alerts">
        <p className="text-xs text-dim">Loading alerts configuration…</p>
      </Panel>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Panel
        title="Alert sinks"
        right={
          <ActionButton
            onClick={openAdd}
            locked={!canEdit}
            lockedTitle={`Adding a sink needs ${accessPhrase("config.alerts")}`}
            tone="accent"
          >
            <Icon name="plus" size={12} /> Add sink
          </ActionButton>
        }
      >
        <p className="text-[12px] text-dim mb-3">
          Outbound push/webhook/email channels for run start/end, safety
          transitions, reconnects, and warning/error logs.
        </p>

        {/* UX review #31: the default state is `alerts: []` + `deadman_url: ""`
            — nothing is watching an unattended rig, and the old copy stated
            that as a neutral fact ("No alert sinks configured yet."). The rain
            abort in the review produced a beautiful red banner on a browser tab
            nobody was looking at. State the consequence, not the state. */}
        {sinks.length === 0 && (
          <div className="flex items-start gap-2 border border-warn/50 bg-warn/10 px-3 py-2.5">
            <Icon name="alert" size={14} className="text-warn shrink-0 mt-0.5" />
            <p className="text-xs text-ink leading-relaxed">
              <span className="text-warn">Nothing is watching this rig.</span>{" "}
              With no channel here, a cloud pause at 01:15 or a mount that stops
              tracking reaches a browser tab and nowhere else — you find out in
              the morning. Add one channel (ntfy is two taps and needs no
              account) and the rig can wake you.
            </p>
          </div>
        )}

        <div className="flex flex-col gap-2">
          {sinks.map((s) => {
            const v = deriveSinkHealth(s, health);
            const detail =
              s.kind === "email" ? (s.smtp_to || "no recipients")
                : s.kind === "telegram" ? (s.chat_id || "no chat id")
                : (s.url || "no url");
            return (
              <div key={s.id} className="flex flex-col gap-2">
                <div className="flex items-start gap-3 border border-line bg-bg/60 px-3 py-2.5">
                  <Icon name={KIND_ICON[s.kind]} size={16} className="mt-0.5 text-dim shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm text-ink">{kindLabel(s.kind)}</span>
                      {!s.enabled && <span className="mono text-[9px] text-dim">OFF</span>}
                      <Badge v={v} />
                    </div>
                    <p className="text-[11px] text-dim mt-0.5 truncate">{detail}</p>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0 flex-wrap justify-end">
                    <ActionButton
                      onClick={() => void runTest(s.id)}
                      locked={!canEdit}
                      lockedTitle={`Testing needs ${accessPhrase("config.alerts")}`}
                      busy={isBusy(s.id)}
                    >
                      Test
                    </ActionButton>
                    <ActionButton
                      onClick={() => openEdit(s)}
                      locked={!canEdit}
                      lockedTitle={`Editing needs ${accessPhrase("config.alerts")}`}
                    >
                      Edit
                    </ActionButton>
                    <ActionButton
                      onClick={() => void removeSink(s)}
                      locked={!canEdit}
                      lockedTitle={`Deleting needs ${accessPhrase("config.alerts")}`}
                      busy={isBusy(s.id)}
                      tone="danger"
                    >
                      Delete
                    </ActionButton>
                  </div>
                </div>
                {editingId === s.id && draft && (
                  <SinkForm
                    draft={draft}
                    setDraft={setDraft}
                    tokenConfigured={draftTokenConfigured}
                    canEdit={canEdit}
                    saving={saving}
                    onSave={() => void save()}
                    onCancel={closeForm}
                  />
                )}
              </div>
            );
          })}
        </div>

        {editingId === "__new__" && draft && (
          <SinkForm
            draft={draft}
            setDraft={setDraft}
            tokenConfigured={draftTokenConfigured}
            canEdit={canEdit}
            saving={saving}
            onSave={() => void save()}
            onCancel={closeForm}
          />
        )}

        {!canEdit && (
          <p className="text-[11px] text-dim inline-flex items-center gap-1.5 mt-3">
            <Icon name="lock" size={11} />
            Read-only — changing alerts needs {accessPhrase("config.alerts")}.
          </p>
        )}
      </Panel>

      <Panel
        title="Dead-man's-switch"
        right={<Badge v={deadmanVerdict(health)} />}
      >
        <p className="text-[11px] text-dim leading-relaxed mb-3">
          An external healthcheck (e.g. healthchecks.io, Uptime-Kuma) that
          AstroDeck pings periodically. Its ABSENCE — not a local error — is
          what pages you if the whole box goes dark.
        </p>
        <Field label={`Monitor ping URL — ${deadmanConfigured ? "set" : "not set"}`}>
          <div className="flex items-center gap-2">
            <input
              className="field"
              type="password"
              value={deadmanUrl}
              disabled={!canEdit}
              onChange={(e) => setDeadmanUrl(e.target.value)}
              placeholder={deadmanConfigured ? "(unchanged)" : "https://hc-ping.com/…"}
              aria-label="Dead-man's-switch URL (write-only)"
            />
            <ActionButton
              onClick={() => void saveDeadman()}
              locked={!canEdit}
              lockedTitle={`Changing this needs ${accessPhrase("config.alerts")}`}
              busy={deadmanBusy}
              tone="accent"
            >
              Save
            </ActionButton>
          </div>
        </Field>
        {!canEdit && (
          <p className="text-[11px] text-dim inline-flex items-center gap-1.5 mt-3">
            <Icon name="lock" size={11} />
            Read-only — changing the monitor URL needs {accessPhrase("config.alerts")}.
          </p>
        )}
      </Panel>
    </div>
  );
}
