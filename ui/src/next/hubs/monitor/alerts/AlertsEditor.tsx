// AlertsEditor.tsx - the sink list and the dead-man's-switch, rebuilt in the
// design's own vocabulary for wave R7 (T-R7-10). Replaces the mounted
// `components/settings/AlertsPanel.tsx`, which is NOT edited and keeps serving
// `#/classic`.
//
// WHAT THIS OWNS, AND THE DEFECTS IT CARRIES FORWARD ON PURPOSE:
//
//   PER-ROW BUSY, NOT PER-PANEL. The legacy panel began with a single busy id
//   and its Test handler bailed on ANY row being busy, so while one channel sat
//   on an SMTP timeout the other rows' buttons pressed down, looked live and
//   did nothing. The routes are independent (`POST /api/alerts/{id}/test`), so
//   the guard is too. (`AlertsPanel.tsx:371-382`.)
//
//   THE SECRET IS NEVER SEEDED FROM THE CONFIG. `openEdit` spreads the sink and
//   then forces `token: ""`. The server already blanks tokens outbound, so a
//   seeded box could only ever paint an empty string as if it were the stored
//   secret - and a save would then send that empty string, which the server
//   reads as "unchanged". Two ways to be wrong, one line to prevent both.
//
//   HEALTH IS FETCHED ONCE, BY THE SCREEN. `GET /api/alerts/health` is a pure
//   read that re-polls on the config broadcast; `AlertsScreen` owns that poll
//   and hands the snapshot down with a `refresh` callback. The legacy pairing
//   ran two independent pollers over the same route on the same screen.
//
//   SIX REFUSALS, NOT ONE. Add / test / edit / delete / save / dead-man each
//   name their own verb (`alertsModel.ts`), because a reason read while
//   standing on the delete button has to say what would have been deleted.
//
// Nothing decision-shaped lives here: `defaultDraft`, `validateDraft`,
// `deriveSinkHealth` and `deadmanVerdict` are `lib/alertSinks.ts`'s, shared
// with the legacy panel so the two renderings cannot disagree.

import { useState, type JSX } from "react";
import { api, ApiError } from "../../../../api";
import { deleteAlert, testAlert, upsertAlert } from "../../../../api/alerts";
import {
  deadmanVerdict, defaultDraft, deriveSinkHealth, kindLabel, validateDraft,
  type HealthVerdict,
} from "../../../../lib/alertSinks";
import { uid } from "../../../../lib/ids";
import { useConfig, useStore } from "../../../../store";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import { useLock } from "../../../lib/gateHook";
import type { AlertHealth, AlertSink, AlertSinkInput } from "../../../../types";
import { ActionButton, Card, Field, Label, LockNote, Mono, Pill, TextInput } from "../../../ui";
import { NxIcon } from "../../../icons";
import { SinkForm } from "./SinkForm";
import {
  ADD_VERB, CONFLICT_TOAST, DEADMAN_LOCK_NOTE, DEADMAN_NOTE, DEADMAN_VERB, DELETE_VERB,
  EDIT_VERB, LOADING_CONFIG, NOTHING_WATCHING_BODY, NOTHING_WATCHING_LEAD, SECRET_UNCHANGED,
  SINKS_INTRO, SINKS_LOCK_NOTE, TEST_VERB, hyphens, lockSentence, setMarker, sinkDestination,
} from "./alertsModel";

/** `HealthVerdict.tone` is already the primitive library's tone vocabulary. */
function VerdictPill({ v, testId }: { v: HealthVerdict; testId?: string }): JSX.Element {
  return <Pill tone={v.tone} data-testid={testId}>{v.label}</Pill>;
}

const toastError = (e: unknown, fallback: string): void => {
  useStore.getState().enqueueToast({
    level: "error",
    title: hyphens(e instanceof Error ? e.message : fallback),
  });
};

export function AlertsEditor({ health, onRefreshHealth }: {
  health: AlertHealth | null;
  onRefreshHealth: () => void;
}): JSX.Element {
  const config = useConfig();
  const { lockedReason, onExplain } = useLock({ cap: "config.alerts" });
  const canEdit = lockedReason == null;

  const [editingId, setEditingId] = useState<string | null>(null); // a sink id, or "__new__"
  const [draft, setDraft] = useState<AlertSinkInput | null>(null);
  const [draftTokenConfigured, setDraftTokenConfigured] = useState(false);
  const [saving, setSaving] = useState(false);
  // Per ROW. See the header note.
  const [busyIds, setBusyIds] = useState<string[]>([]);
  const isBusy = (id: string) => busyIds.includes(id);
  const markBusy = (id: string, on: boolean) =>
    setBusyIds((ids) => (on ? (ids.includes(id) ? ids : [...ids, id]) : ids.filter((x) => x !== id)));
  const [deadmanUrl, setDeadmanUrl] = useState("");
  const [deadmanBusy, setDeadmanBusy] = useState(false);

  const sinks: AlertSink[] = config?.alerts ?? [];
  const deadmanConfigured = !!config?.deadman_configured;

  const openAdd = () => {
    setEditingId("__new__");
    setDraft(defaultDraft("ntfy", uid()));
    setDraftTokenConfigured(false);
  };

  const openEdit = (s: AlertSink) => {
    setEditingId(s.id);
    // WRITE-ONLY: the secret is never seeded from the config payload.
    setDraft({ ...s, token: "" });
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
      useStore.getState().enqueueToast({ level: "error", title: hyphens(err) });
      return;
    }
    setSaving(true);
    try {
      await upsertAlert(draft);
      await useStore.getState().loadConfig();
      useStore.getState().enqueueToast({
        level: "success", title: `${kindLabel(draft.kind)} sink saved`,
      });
      closeForm();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await useStore.getState().loadConfig();
        useStore.getState().enqueueToast({ level: "error", title: CONFLICT_TOAST });
      } else if (e instanceof ApiError && e.status === 403) {
        useStore.getState().enqueueToast({
          level: "error", title: "config.alerts is required to change alert sinks",
        });
      } else {
        toastError(e, "Could not save alert sink");
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
        title: res.ok ? "Test alert delivered" : hyphens(res.error || "Test alert failed"),
      });
      await useStore.getState().loadConfig();   // the verified badge
      onRefreshHealth();
    } catch (e) {
      toastError(e, "Could not run the test");
    } finally {
      markBusy(id, false);
    }
  };

  const removeSink = async (s: AlertSink) => {
    const ok = await confirmDialog({
      title: `Delete the ${kindLabel(s.kind)} sink?`,
      body: `This stops alerts to ${sinkDestination(s)} immediately, and it cannot be undone.`,
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
      onRefreshHealth();
      useStore.getState().enqueueToast({ level: "success", title: "Alert sink deleted" });
      if (editingId === s.id) closeForm();
    } catch (e) {
      toastError(e, "Could not delete the sink");
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
      onRefreshHealth();
      useStore.getState().enqueueToast({
        level: "success", title: "Dead-man's-switch URL saved",
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await useStore.getState().loadConfig();
        useStore.getState().enqueueToast({ level: "error", title: CONFLICT_TOAST });
      } else {
        toastError(e, "Could not save the dead-man's-switch URL");
      }
    } finally {
      setDeadmanBusy(false);
    }
  };

  if (!config) {
    return (
      <Card padding={12} data-testid="alerts-sinks">
        <div className="nx-alerts-col">
          <Label size={11}>ALERT SINKS</Label>
          <Mono size={10.5} tone="dim">{LOADING_CONFIG}</Mono>
        </div>
      </Card>
    );
  }

  const form = draft && (
    <SinkForm
      draft={draft}
      setDraft={setDraft}
      tokenConfigured={draftTokenConfigured}
      lockedReason={lockedReason}
      onExplain={onExplain}
      saving={saving}
      onSave={() => void save()}
      onCancel={closeForm}
    />
  );

  return (
    <div className="nx-alerts-stack">
      {/* ------------------------------------------------------ ALERT SINKS */}
      <Card padding={12} data-testid="alerts-sinks">
        <div className="nx-alerts-col">
          <div className="nx-alerts-head">
            <Label size={11}>ALERT SINKS</Label>
            <ActionButton
              kind="primary"
              glyph={<NxIcon name="plus" size={14} />}
              onPress={openAdd}
              lockedReason={lockSentence(ADD_VERB, lockedReason)}
              onExplain={onExplain}
              data-testid="alerts-sink-add"
            >
              ADD SINK
            </ActionButton>
          </div>
          <Mono size={10.5} tone="dim">{SINKS_INTRO}</Mono>

          {sinks.length === 0 && (
            <Card tone="accent" padding={12} data-testid="alerts-empty">
              <div className="nx-alerts-col">
                <Mono size={11} tone="warn">{NOTHING_WATCHING_LEAD}</Mono>
                <Mono size={10.5} tone="dim">{NOTHING_WATCHING_BODY}</Mono>
              </div>
            </Card>
          )}

          {sinks.map((s) => {
            const v = deriveSinkHealth(s, health);
            return (
              <div key={s.id} className="nx-alerts-col">
                <div className="nx-alerts-row" data-testid="alerts-sink-row">
                  <span className="nx-alerts-rowtile" aria-hidden="true">
                    <NxIcon name="share" size={16} />
                  </span>
                  <span className="nx-alerts-rowtext">
                    <span className="nx-alerts-rowhead">
                      <Label size={11}>{kindLabel(s.kind)}</Label>
                      {!s.enabled && <Mono size={10} tone="dim">OFF</Mono>}
                      <VerdictPill v={v} />
                    </span>
                    <Mono size={10.5} tone="dim">{sinkDestination(s)}</Mono>
                    <Mono size={10} tone="dim">{hyphens(v.detail)}</Mono>
                  </span>
                  <span className="nx-alerts-rowacts">
                    <ActionButton
                      kind="secondary"
                      onPress={() => void runTest(s.id)}
                      busy={isBusy(s.id)}
                      lockedReason={lockSentence(TEST_VERB, lockedReason)}
                      onExplain={onExplain}
                      data-testid="alerts-sink-test"
                    >
                      TEST
                    </ActionButton>
                    <ActionButton
                      kind="secondary"
                      onPress={() => openEdit(s)}
                      lockedReason={lockSentence(EDIT_VERB, lockedReason)}
                      onExplain={onExplain}
                      data-testid="alerts-sink-edit"
                    >
                      EDIT
                    </ActionButton>
                    <ActionButton
                      kind="danger"
                      onPress={() => void removeSink(s)}
                      busy={isBusy(s.id)}
                      lockedReason={lockSentence(DELETE_VERB, lockedReason)}
                      onExplain={onExplain}
                      data-testid="alerts-sink-delete"
                    >
                      DELETE
                    </ActionButton>
                  </span>
                </div>
                {editingId === s.id && form}
              </div>
            );
          })}

          {editingId === "__new__" && form}

          <LockNote reason={canEdit ? null : SINKS_LOCK_NOTE} data-testid="alerts-lock-note" />
        </div>
      </Card>

      {/* -------------------------------------------------- DEAD-MAN'S-SWITCH */}
      <Card padding={12} data-testid="alerts-deadman">
        <div className="nx-alerts-col">
          <div className="nx-alerts-head">
            <Label size={11}>DEAD-MAN&apos;S-SWITCH</Label>
            <VerdictPill v={deadmanVerdict(health)} testId="alerts-deadman-verdict" />
          </div>
          <Mono size={10.5} tone="dim">{DEADMAN_NOTE}</Mono>
          <Field
            label={`Monitor ping URL - ${setMarker(deadmanConfigured)}`}
            hint={deadmanConfigured
              ? "Stored on the rig. Leave this empty to keep the one already saved."
              : "The URL can carry a secret in its path, so the rig never sends it back."}
          >
            <div className="nx-alerts-deadrow">
              <TextInput
                type="password"
                value={deadmanUrl}
                onChange={setDeadmanUrl}
                placeholder={deadmanConfigured ? SECRET_UNCHANGED : "https://hc-ping.com/..."}
                ariaLabel="Dead-man's-switch URL (write-only)"
                lockedReason={lockedReason}
                className="nx-alerts-deadinput"
                data-testid="alerts-deadman-input"
              />
              <ActionButton
                kind="primary"
                onPress={() => void saveDeadman()}
                busy={deadmanBusy}
                lockedReason={lockSentence(DEADMAN_VERB, lockedReason)}
                onExplain={onExplain}
                data-testid="alerts-deadman-save"
              >
                SAVE
              </ActionButton>
            </div>
          </Field>
          <LockNote
            reason={canEdit ? null : DEADMAN_LOCK_NOTE}
            data-testid="alerts-deadman-lock-note"
          />
        </div>
      </Card>
    </div>
  );
}
