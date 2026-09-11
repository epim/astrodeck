// SinkForm.tsx - one alert channel, edited. Rebuilt in the design's own
// vocabulary for wave R7 (T-R7-10); replaces the `SinkForm` inside
// `components/settings/AlertsPanel.tsx:152-355`, which is NOT edited and keeps
// serving `#/classic`.
//
// WHAT CHANGED, AND WHY IT IS NOT A RE-SKIN.
//
//   EIGHTEEN NATIVE `disabled` ATTRIBUTES ARE GONE. The legacy form greyed out
//   every input, every toggle, every event chip and both segmented controls
//   (`AlertsPanel.tsx` :110, :180, :188, :198, :212, :246, :254, :264, :272,
//   :281, :299, :316, :333 and the `disabled={!canEdit}` inside `EventChips`).
//   A `disabled` element leaves the accessibility tree, and the REASON goes
//   with it: a viewer got a dead grey form and no way to ask why. Every one is
//   now `lockedReason` - dim, focusable, `aria-disabled`, and a press states
//   the reason.
//
//   THE TWO NUMBERS ARE `NumberField`, NOT `Number(e.target.value) || 0`. SMTP
//   port and the heartbeat both read `Number(raw) || 0` on every keystroke
//   (:255, :334), so clearing the port box wrote a real 0 and clearing the
//   heartbeat box silently switched the heartbeat OFF. `NumberField` holds the
//   raw text, commits at blur and Enter, and REJECTS a blank rather than
//   reading it as zero.
//
//   THE SECRET IS STILL WRITE-ONLY, and now says so in one place. The server
//   blanks every token outbound (`api/alerts.ts`), `token_configured` is the
//   only fact available about it, and the box is seeded from the DRAFT - never
//   from the config payload.
//
// Everything decision-shaped is `lib/alertSinks.ts`: `ALERT_KINDS`,
// `ALL_EVENTS`, `kindLabel`, `defaultDraft`. Nothing about a channel's shape is
// decided here.

import { useId, type JSX } from "react";
import type { AlertSink, AlertSinkInput } from "../../../../types";
import { ALERT_KINDS, ALL_EVENTS, defaultDraft, kindLabel } from "../../../../lib/alertSinks";
import {
  ActionButton, Chip, Field, Label, Mono, NumberField, Segmented, Switch, TextInput,
} from "../../../ui";
import {
  HEARTBEAT_HINT, MIN_LEVEL_HINT, SECRET_UNCHANGED, SAVE_VERB, eventLabel, lockSentence,
  setMarker,
} from "./alertsModel";

/** A write-only box. The value shown is ALWAYS the draft's own text, which
 *  starts empty on every open; `configured` is the only thing the server can
 *  say about the stored secret, and leaving the box blank means "keep it". */
function SecretInput({
  label, value, onChange, configured, lockedReason, placeholder, testId,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  configured: boolean;
  lockedReason: string | null;
  placeholder?: string;
  testId: string;
}): JSX.Element {
  const id = useId();
  return (
    <Field
      label={`${label} - ${setMarker(configured)}`}
      htmlFor={id}
      hint={configured
        ? "Stored on the rig. Leave this empty to keep the one already saved."
        : "Sent once and never read back - the rig stores it and the app cannot show it again."}
    >
      <TextInput
        id={id}
        type="password"
        value={value}
        onChange={onChange}
        placeholder={configured ? SECRET_UNCHANGED : (placeholder ?? "required")}
        ariaLabel={`${label} (write-only)`}
        lockedReason={lockedReason}
        data-testid={testId}
      />
    </Field>
  );
}

/** One labelled plain-text row. `TextInput` needs an id to bind the visible
 *  label to, and every field in this form wants the same three lines. */
function TextRow({
  label, value, onChange, placeholder, hint, lockedReason, testId,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  hint?: string;
  lockedReason: string | null;
  testId: string;
}): JSX.Element {
  const id = useId();
  return (
    <Field label={label} htmlFor={id} hint={hint}>
      <TextInput
        id={id}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        ariaLabel={label}
        lockedReason={lockedReason}
        data-testid={testId}
      />
    </Field>
  );
}

export function SinkForm({
  draft, setDraft, tokenConfigured, lockedReason, onExplain, saving, onSave, onCancel,
}: {
  draft: AlertSinkInput;
  setDraft: (d: AlertSinkInput) => void;
  /** Does the rig already hold a secret for THIS sink? Only meaningful for the
   *  kind the sink had when the form opened - see `changeKind`. */
  tokenConfigured: boolean;
  /** Null when this principal may edit. One reason for the whole form; the
   *  SAVE button re-words it with its own verb. */
  lockedReason: string | null;
  onExplain: (reason: string) => void;
  saving: boolean;
  onSave: () => void;
  onCancel: () => void;
}): JSX.Element {
  const changeKind = (kind: AlertSink["kind"]) => {
    // Each kind has a different field shape - reset to that kind's defaults,
    // keeping only the id and the enabled bit an in-progress edit already has.
    // The secret must be re-entered: `token_configured` described the ORIGINAL
    // kind's secret and means nothing about this one.
    setDraft({ ...defaultDraft(kind, draft.id), enabled: draft.enabled });
  };

  const toggleEvent = (ev: string) => {
    const on = draft.events.includes(ev);
    setDraft({ ...draft, events: on ? draft.events.filter((e) => e !== ev) : [...draft.events, ev] });
  };

  const isUrlKind = draft.kind === "ntfy" || draft.kind === "webhook";
  const isHook = draft.kind === "discord" || draft.kind === "slack";

  return (
    <div className="nx-alerts-form" data-testid="alerts-sink-form">
      <div className="nx-alerts-formrow">
        <div className="nx-alerts-col">
          <Label size={11}>CHANNEL</Label>
          <Segmented
            options={ALERT_KINDS.map((k) => ({ value: k, label: kindLabel(k) }))}
            value={draft.kind}
            onChange={changeKind}
            label="Alert channel kind"
            lockedReason={lockedReason}
            onExplain={onExplain}
            data-testid="alerts-kind"
          />
        </div>
        <Switch
          checked={draft.enabled}
          onChange={(v) => setDraft({ ...draft, enabled: v })}
          label="Sink enabled"
          note="Off keeps the channel here but sends nothing to it."
          lockedReason={lockedReason}
          onExplain={onExplain}
          data-testid="alerts-sink-enabled"
        />
      </div>

      {isUrlKind && (
        <TextRow
          label={draft.kind === "ntfy" ? "ntfy topic URL" : "Webhook URL"}
          value={draft.url}
          onChange={(v) => setDraft({ ...draft, url: v })}
          placeholder="https://ntfy.sh/your-topic"
          hint={draft.kind === "ntfy"
            ? "Anyone who knows the topic can read it - pick a name nobody would guess."
            : "The rig POSTs a JSON body to this URL."}
          lockedReason={lockedReason}
          testId="alerts-url"
        />
      )}

      {draft.kind === "telegram" && (
        <div className="nx-alerts-grid">
          <TextRow
            label="Chat ID"
            value={draft.chat_id ?? ""}
            onChange={(v) => setDraft({ ...draft, chat_id: v })}
            placeholder="e.g. 123456789"
            hint="The conversation the bot posts into, not your username."
            lockedReason={lockedReason}
            testId="alerts-chatid"
          />
          <SecretInput
            label="Bot token"
            value={draft.token ?? ""}
            onChange={(v) => setDraft({ ...draft, token: v })}
            configured={tokenConfigured}
            lockedReason={lockedReason}
            testId="alerts-secret-token"
          />
        </div>
      )}

      {isHook && (
        <SecretInput
          label={`${kindLabel(draft.kind)} webhook URL`}
          value={draft.token ?? ""}
          onChange={(v) => setDraft({ ...draft, token: v })}
          configured={tokenConfigured}
          lockedReason={lockedReason}
          placeholder="https://..."
          testId="alerts-secret-token"
        />
      )}

      {draft.kind === "email" && (
        <div className="nx-alerts-grid">
          <TextRow
            label="SMTP host"
            value={draft.smtp_host ?? ""}
            onChange={(v) => setDraft({ ...draft, smtp_host: v })}
            placeholder="smtp.example.com"
            lockedReason={lockedReason}
            testId="alerts-smtp-host"
          />
          <NumberField
            label="SMTP port"
            value={draft.smtp_port ?? 587}
            onCommit={(v) => setDraft({ ...draft, smtp_port: v })}
            min={1}
            max={65535}
            integer
            hint="587 with STARTTLS, 465 for implicit TLS, 25 unencrypted."
            ariaLabel="SMTP port"
            lockedReason={lockedReason}
            onExplain={onExplain}
            data-testid="alerts-smtp-port"
          />
          <TextRow
            label="From address"
            value={draft.smtp_from ?? ""}
            onChange={(v) => setDraft({ ...draft, smtp_from: v })}
            placeholder="rig@example.com"
            lockedReason={lockedReason}
            testId="alerts-smtp-from"
          />
          <TextRow
            label="To (comma-separated)"
            value={draft.smtp_to ?? ""}
            onChange={(v) => setDraft({ ...draft, smtp_to: v })}
            placeholder="me@example.com, backup@example.com"
            lockedReason={lockedReason}
            testId="alerts-smtp-to"
          />
          <TextRow
            label="Login user (optional)"
            value={draft.smtp_user ?? ""}
            onChange={(v) => setDraft({ ...draft, smtp_user: v })}
            placeholder="usually the From address"
            lockedReason={lockedReason}
            testId="alerts-smtp-user"
          />
          <SecretInput
            label="SMTP password (optional)"
            value={draft.token ?? ""}
            onChange={(v) => setDraft({ ...draft, token: v })}
            configured={tokenConfigured}
            lockedReason={lockedReason}
            placeholder="optional - open relays exist"
            testId="alerts-secret-token"
          />
          <div className="nx-alerts-wide">
            <Switch
              checked={draft.smtp_starttls ?? true}
              onChange={(v) => setDraft({ ...draft, smtp_starttls: v })}
              label="STARTTLS"
              note="Upgrades the connection before the password is sent. Off only for a relay that refuses it."
              lockedReason={lockedReason}
              onExplain={onExplain}
              data-testid="alerts-starttls"
            />
          </div>
        </div>
      )}

      <div className="nx-alerts-col">
        <Label size={11}>MINIMUM LEVEL</Label>
        <Mono size={10} tone="dim">{MIN_LEVEL_HINT}</Mono>
        <Segmented
          options={[
            { value: "warning" as const, label: "WARNING" },
            { value: "error" as const, label: "ERROR" },
          ]}
          value={draft.min_level}
          onChange={(v) => setDraft({ ...draft, min_level: v })}
          label="Minimum level"
          lockedReason={lockedReason}
          onExplain={onExplain}
          data-testid="alerts-minlevel"
        />
      </div>

      <div className="nx-alerts-col">
        <Label size={11}>EVENTS</Label>
        <Mono size={10} tone="dim">
          {draft.events.length === 0
            ? "None selected - this channel would stay silent all night."
            : `${draft.events.length} of ${ALL_EVENTS.length} selected`}
        </Mono>
        <div
          className="nx-alerts-events"
          role="group"
          aria-label="Alert events"
          data-testid="alerts-events"
        >
          {ALL_EVENTS.map((ev) => (
            <Chip
              key={ev}
              active={draft.events.includes(ev)}
              onClick={() => toggleEvent(ev)}
              lockedReason={lockedReason}
              onExplain={onExplain}
              data-testid={`alerts-event-${ev}`}
            >
              {eventLabel(ev)}
            </Chip>
          ))}
        </div>
      </div>

      <NumberField
        label="Heartbeat"
        value={draft.heartbeat_min}
        onCommit={(v) => setDraft({ ...draft, heartbeat_min: v })}
        unit="min"
        min={0}
        integer
        zeroMeans="off"
        hint={HEARTBEAT_HINT}
        ariaLabel="Heartbeat, minutes"
        lockedReason={lockedReason}
        onExplain={onExplain}
        data-testid="alerts-heartbeat"
      />

      <div className="nx-alerts-formfoot">
        <ActionButton kind="ghost" onPress={onCancel} data-testid="alerts-sink-cancel">
          CANCEL
        </ActionButton>
        <ActionButton
          kind="primary"
          onPress={onSave}
          busy={saving}
          lockedReason={lockSentence(SAVE_VERB, lockedReason)}
          onExplain={onExplain}
          data-testid="alerts-sink-save"
        >
          {saving ? "SAVING" : "SAVE SINK"}
        </ActionButton>
      </div>
    </div>
  );
}
