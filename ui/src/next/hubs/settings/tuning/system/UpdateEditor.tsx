// UpdateEditor.tsx - Settings > UPDATE, rebuilt in the design vocabulary
// (wave R7, T-R7-12; parity table 3.F7).
//
// The legacy `components/settings/UpdatePanel.tsx` is not edited, not deleted
// and not imported here; it still serves `#/classic`. What is re-implemented is
// the PRESENTATION. What is carried across unchanged, because it was expensive
// to learn, is:
//
//   THE APPLY SAFETY GATE, IN TWO HALVES. `can_apply` / `apply_blocked_reason`
//   / `supervised` are stamped on by GET /api/update/status. They are NOT part
//   of `update_state.snapshot()`, which is what the `update` WS event carries -
//   and the store REPLACES the whole slice on that event. So every phase frame
//   arrived with those three keys absent, and reading them straight off
//   `status` re-armed UPGRADE in the middle of a sequence. Half one: remember
//   the last frame that CARRIED the gate and re-ask the route once per partial.
//   Half two: the only precondition that can change on its own is the rig one,
//   and both its inputs ride the 2 s status frame, so derive it live
//   (`rigBlocker`) and block on it first.
//
//   A "SAVED" CHIP IS NOT A STATE, IT IS A CLAIM. `savedAt` is never cleared by
//   a local edit, so the honest gate on showing it is `!dirty`. Editing the
//   signing key with a green "Saved" still beside the Save button is the panel
//   claiming the settings are stored and offering to store them in the same
//   breath - on the one field that decides whether a release is genuinely yours.
//
// Honest-disabled throughout: no native `disabled` anywhere in this file. The
// capability is `system.update`; the rig-idle gate and the pipeline are
// separate reasons and each is stated in its own words.

import { useEffect, useRef, useState, type JSX } from "react";
import { ApiError } from "../../../../../api";
import { applyUpdate, checkUpdate, setUpdateConfig } from "../../../../../api/backends";
import { useCan } from "../../../../../lib/caps";
import { appVersion } from "../../../../lib/versions";
import { useBusyOrPending } from "../../../../../lib/useBusy";
import { useConfig, useSequence, useStatus, useStore, useUpdate } from "../../../../../store";
import { confirmDialog } from "../../../../../components/ConfirmDialog";
import type { UpdateConfig } from "../../../../../types";
import {
  ActionButton, Bar, Card, Field, Label, LockNote, Mono, NumberField, Pill,
  Segmented, Switch, TextInput,
} from "../../../../ui";
import { NxIcon } from "../../../../icons";
import { explainLock } from "../../../../shell/explain";
import {
  AUTO_CHECK_NOTE, CHANNEL_HINT, NO_PUBKEY_NOTE, PUBKEY_HINT, UNSUPERVISED_NOTE,
  UPDATE_INTRO, UPDATE_LOCK_NOTE, fmtTime, isPipelineActive, phaseLabel,
  rigBlocker, staleRigBlock,
} from "./systemModel";
import "./system.css";

export function UpdateEditor(): JSX.Element {
  const config = useConfig();
  const status = useUpdate();
  const canUpdate = useCan("system.update");
  const uc = config?.update;

  const [channel, setChannel] = useState<"stable" | "prerelease">("stable");
  const [autoCheck, setAutoCheck] = useState(false);
  const [intervalH, setIntervalH] = useState(24);
  const [pubkey, setPubkey] = useState("");
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    if (!uc) return;
    setChannel(uc.channel === "prerelease" ? "prerelease" : "stable");
    setAutoCheck(!!uc.auto_check);
    setIntervalH(uc.check_interval_hours ?? 24);
    setPubkey(uc.signing_pubkey ?? "");
    setErr(null);
    // NOT `setSavedAt(null)`. This effect's deps are the SERVER's update block,
    // so the one moment it is guaranteed to fire is immediately after our own
    // save changed that block - and nulling `savedAt` there raced the
    // `setSavedAt` at the end of onSave.
  }, [uc?.channel, uc?.auto_check, uc?.check_interval_hours, uc?.signing_pubkey]);

  const active = isPipelineActive(status?.phase);

  // --------------------------------------------------- the gate, remembered
  const [gate, setGate] = useState<{
    supervised?: boolean; canApply?: boolean; reason: string;
  } | null>(null);
  const askedRef = useRef(false);
  useEffect(() => {
    if (!status) return;
    if (status.can_apply !== undefined) {
      askedRef.current = false;
      setGate({
        supervised: status.supervised,
        canApply: status.can_apply,
        reason: status.apply_blocked_reason ?? "",
      });
      return;
    }
    // A partial frame. Ask the one route that computes the gate - but only once
    // per partial, so a server that never sends it cannot start a fetch loop.
    if (askedRef.current) return;
    askedRef.current = true;
    void useStore.getState().loadUpdate();
  }, [status]);

  // Re-read the gate when the sheet opens: it is computed per-request, so at
  // mount the newest answer is whatever WS-connect fetched, possibly hours ago.
  useEffect(() => { void useStore.getState().loadUpdate(); }, []);

  const rig = useStatus();
  const seqState = useSequence().state;
  const rigNow = rigBlocker(seqState, rig?.busy);
  const supervised = status?.supervised ?? gate?.supervised;
  const serverCanApply = status?.can_apply ?? gate?.canApply;
  const serverReason = status?.apply_blocked_reason ?? gate?.reason ?? "";
  const serverBlocked =
    serverCanApply === false
      ? serverReason || "the server will not install an update right now"
      : null;
  // Live first - but never DROP the server's block just because this client
  // cannot see the run it names (a client that connected mid-sequence has no
  // sequence event yet). An out-of-date rig reason is retired by the refetch
  // below instead, so the button can go stale-blocked but never stale-armed.
  const blockedReason = rigNow ?? serverBlocked ?? null;
  const stale = serverBlocked !== null && staleRigBlock(serverReason);
  useEffect(() => {
    if (rigNow === null && stale) void useStore.getState().loadUpdate();
  }, [rigNow, stale]);

  const capNote = canUpdate ? null : UPDATE_LOCK_NOTE;

  // The one version fact no other screen carries. An apply restarts the server
  // on the new build while THIS browser keeps the bundle it already downloaded,
  // so "updated" and "what you are looking at" can genuinely disagree until a
  // reload. `appVersion()` is "dev" when the build-time define never ran (a dev
  // server, a test), which is not a mismatch worth a sentence.
  const uiBuild = appVersion();
  const uiStale =
    uiBuild !== "dev" && !!status?.current && uiBuild !== status.current;

  // A REF, not the `checking` state, because two taps inside one React batch
  // share a render and therefore share the closure: both would read the old
  // `checking === false` and both would poll GitHub. The state still drives the
  // button (busy, and a lock reason that states why a second tap does nothing);
  // the ref is what actually stops the second request.
  const checkingRef = useRef(false);

  const onCheck = async () => {
    if (checkingRef.current || busy) return;
    checkingRef.current = true;
    setErr(null);
    setChecking(true);
    try {
      await checkUpdate();
      await useStore.getState().loadUpdate();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Check failed.");
    } finally {
      checkingRef.current = false;
      setChecking(false);
    }
  };

  // POST /api/update/apply `_spawn`s the pipeline and returns the moment the
  // task is CREATED, so its promise resolving means "accepted", not
  // "installed". `arm()` covers the <=2 s before the next status frame; the
  // `system.update` lane covers the rest of the pipeline.
  const { busy: applying, arm } = useBusyOrPending("system.update");

  const onUpgrade = async () => {
    if (busy || applying || !status?.latest || !status.update_available) return;
    const ok = await confirmDialog({
      title: `Update to ${status.latest}?`,
      body: (
        <span style={{ whiteSpace: "pre-wrap" }}>
          {`The scope will download and cryptographically verify the signed release, then RESTART to install it. The UI will briefly disconnect and reconnect on the new version. If the new build fails to start, the supervisor automatically rolls back to the current version.${
            status.notes_md ? `\n\nRelease notes:\n${status.notes_md}` : ""
          }`}
        </span>
      ),
      tone: "warn",
      mode: "hold",
      confirmLabel: "Download and install",
      cancelLabel: "Not now",
    });
    if (!ok) return;
    setErr(null);
    setBusy(true);
    try {
      await applyUpdate();
      arm();
    } catch (e) {
      setErr(
        e instanceof ApiError
          ? e.message || "Could not start the update."
          : "Could not start the update.",
      );
    } finally {
      setBusy(false);
    }
  };

  const onSave = async () => {
    if (busy) return;
    setErr(null);
    setBusy(true);
    try {
      const body: UpdateConfig = {
        enabled: uc?.enabled ?? true,
        auto_check: autoCheck,
        check_interval_hours: Math.min(720, Math.max(1, intervalH || 24)),
        channel,
        repo: uc?.repo ?? "epim/astrodeck",
        signing_pubkey: pubkey.trim(),
        health_timeout_s: uc?.health_timeout_s ?? 60,
        last_check_ts: uc?.last_check_ts ?? null,
      };
      await setUpdateConfig(body);
      await useStore.getState().loadConfig();
      await useStore.getState().loadUpdate();
      setSavedAt(Date.now());
    } catch (e) {
      setErr(
        e instanceof ApiError
          ? e.message || "Could not save."
          : "Could not save.",
      );
    } finally {
      setBusy(false);
    }
  };

  const dirty =
    !!uc &&
    (channel !== (uc.channel === "prerelease" ? "prerelease" : "stable") ||
      autoCheck !== !!uc.auto_check ||
      intervalH !== (uc.check_interval_hours ?? 24) ||
      pubkey.trim() !== (uc.signing_pubkey ?? ""));

  const upgradeReason =
    capNote
    ?? (active ? `${phaseLabel(status?.phase).replace(/\.\.\.$/, "")} - the update is already running` : null)
    ?? (blockedReason ? `cannot install now: ${blockedReason}` : null);

  return (
    <div className="nx-sys-stack">
      {/* --------------------------------------------------------- STATUS */}
      <Card padding={12} data-testid="update-status">
        <div className="nx-sys-stack">
          <div className="nx-sys-head">
            <Label size={11}>Software updates</Label>
            {/* The version itself is the sheet's live line; what this says is
                which stream of releases the rig is watching, which nothing
                else on the card repeats. */}
            <Mono size={10.5} data-testid="update-channel-now">
              {`${status?.channel || "stable"} channel`}
            </Mono>
          </div>
          <p className="nx-sys-note">{UPDATE_INTRO}</p>

          <div className="nx-sys-row">
            <span className="nx-sys-grow">
              <Mono size={10.5}>{`Last checked: ${fmtTime(status?.last_check_ts)}`}</Mono>
              {supervised === false && (
                <p className="nx-sys-warn" data-testid="update-unsupervised">{UNSUPERVISED_NOTE}</p>
              )}
              {uiStale && (
                <p className="nx-sys-warn" data-testid="update-ui-stale">
                  {`This browser is still running UI build ${uiBuild}. Reload the page to `
                    + `pick up the one the rig is serving.`}
                </p>
              )}
            </span>
            <ActionButton
              kind="secondary"
              glyph={<NxIcon name="refresh" size={15} />}
              onPress={() => void onCheck()}
              busy={checking}
              lockedReason={capNote ?? (checking ? "a check is already running" : null)}
              onExplain={explainLock}
              data-testid="update-check"
            >
              {checking ? "CHECKING..." : "CHECK NOW"}
            </ActionButton>
          </div>

          {status?.update_available && status.latest && (
            <Card tone="accent" padding={12} data-testid="update-available">
              <div className="nx-sys-stack">
                <div className="nx-sys-row">
                  <span className="nx-sys-grow">
                    <Label size={11}>Update available</Label>
                    <Mono size={12} tone="accent" data-testid="update-latest">v{status.latest}</Mono>
                    <Mono size={10}>{` running v${status.current}`}</Mono>
                  </span>
                  <ActionButton
                    kind="primary"
                    glyph={<NxIcon name="download" size={15} />}
                    onPress={() => void onUpgrade()}
                    busy={busy || applying}
                    lockedReason={upgradeReason}
                    onExplain={explainLock}
                    data-testid="update-apply"
                  >
                    {busy || applying ? "STARTING..." : "UPGRADE"}
                  </ActionButton>
                </div>
                {status.notes_md && (
                  <pre className="nx-sys-pre" data-testid="update-notes">{status.notes_md}</pre>
                )}
                {blockedReason && (
                  <p className="nx-sys-warn" data-testid="update-blocked">
                    {`Cannot install now: ${blockedReason}.`}
                  </p>
                )}
              </div>
            </Card>
          )}

          {active && status && (
            <div className="nx-sys-stack" data-testid="update-progress">
              <div className="nx-sys-progress">
                <Mono size={10.5}>{phaseLabel(status.phase)}</Mono>
                <Mono size={10.5}>{`${Math.round((status.progress || 0) * 100)}%`}</Mono>
              </div>
              <Bar value={status.progress || 0} height={6} />
            </div>
          )}

          {status?.last_result && !active && (
            <p
              className={status.last_result.ok ? "nx-sys-good" : "nx-sys-bad"}
              data-testid="update-last-result"
            >
              {status.last_result.ok
                ? `Updated to v${status.last_result.version}.`
                : `Last update to v${status.last_result.version} failed (${
                  status.last_result.reason || "rolled back"
                }).`}
            </p>
          )}

          {status?.error && !active && (
            <p className="nx-sys-bad" data-testid="update-error">{status.error}</p>
          )}
        </div>
      </Card>

      {/* ------------------------------------------------------- SETTINGS */}
      <Card padding={12} data-testid="update-settings">
        <div className="nx-sys-stack">
          <Label size={11}>Update settings</Label>

          <Field label="Channel" hint={CHANNEL_HINT}>
            <Segmented<"stable" | "prerelease">
              options={[
                { value: "stable", label: "STABLE" },
                { value: "prerelease", label: "PRE-RELEASE" },
              ]}
              value={channel}
              onChange={(v) => setChannel(v)}
              label="Update channel"
              lockedReason={capNote}
              onExplain={explainLock}
              data-testid="update-channel"
            />
          </Field>

          <Switch
            checked={autoCheck}
            onChange={setAutoCheck}
            label="Check automatically"
            note={AUTO_CHECK_NOTE}
            lockedReason={capNote}
            onExplain={explainLock}
            data-testid="update-auto"
          />

          <NumberField
            label="Check interval (hours)"
            value={intervalH}
            onCommit={setIntervalH}
            min={1}
            max={720}
            integer
            hint="How often to poll while automatic checks are on."
            ariaLabel="Check interval, hours"
            lockedReason={capNote ?? (autoCheck ? null : "automatic checks are off")}
            onExplain={explainLock}
            data-testid="update-interval"
          />

          <Field label="Release signing public key" hint={PUBKEY_HINT}>
            <TextInput
              value={pubkey}
              onChange={setPubkey}
              placeholder="base64 Ed25519 public key"
              mono
              ariaLabel="Release signing public key"
              lockedReason={capNote}
              data-testid="update-pubkey"
            />
          </Field>

          <div className="nx-sys-actions">
            <ActionButton
              kind="primary"
              glyph={<NxIcon name="check" size={15} />}
              onPress={() => void onSave()}
              busy={busy}
              lockedReason={capNote ?? (dirty ? null : "nothing has changed since the last save")}
              onExplain={explainLock}
              data-testid="update-save"
            >
              {busy ? "SAVING..." : "SAVE SETTINGS"}
            </ActionButton>
            {/* `!dirty` is the load-bearing clause: the two chips are mutually
                exclusive, because a green "Saved" beside a live Save button is
                the panel claiming and offering the same thing at once. */}
            {dirty && !busy && <Pill tone="warn" data-testid="update-dirty">UNSAVED CHANGES</Pill>}
            {savedAt && !dirty && !busy && !err && (
              <Pill tone="good" data-testid="update-saved">SAVED</Pill>
            )}
          </div>

          {!pubkey.trim() && (
            <p className="nx-sys-warn" data-testid="update-nokey">{NO_PUBKEY_NOTE}</p>
          )}

          <LockNote reason={capNote} data-testid="update-lock-note" />
          {err && <p className="nx-sys-bad" data-testid="update-save-error">{err}</p>}
        </div>
      </Card>
    </div>
  );
}

export default UpdateEditor;
