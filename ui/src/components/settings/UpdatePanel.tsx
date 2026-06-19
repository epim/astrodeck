// UpdatePanel.tsx — Settings → Updates (self-update, Phase 3). Admin-only
// (system.update); the SettingsView tab is gated, and every mutating call is
// re-gated server-side. Shows the running version, a "Check now" control, the
// available-update card with release notes + a hold-to-confirm "Upgrade", live
// pipeline progress, the last apply result, and the update config (channel /
// auto-check / interval / signing public key).
//
// The Upgrade action passes the server's rig-idle SAFETY GATE: when a sequence is
// running or the mount is slewing/exposing, `can_apply` is false and the button is
// disabled with the reason shown — an update can never interrupt active work.

import { useEffect, useState, type JSX } from "react";
import type { UpdateConfig } from "../../types";
import {
  applyUpdate,
  checkUpdate,
  setUpdateConfig,
} from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useStore, useUpdate } from "../../store";
import { useCan } from "../../lib/caps";
import { confirmDialog } from "../ConfirmDialog";
import { Panel, Field, Toggle } from "../ui";
import { Segmented } from "../Segmented";
import { Icon } from "../icons";

const PHASE_LABEL: Record<string, string> = {
  checking: "Checking…",
  downloading: "Downloading…",
  verifying: "Verifying signature…",
  staging: "Staging…",
  applying: "Installing — the scope will restart…",
};

function fmtTime(ts: number | null | undefined): string {
  if (!ts) return "never";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return "—";
  }
}

export default function UpdatePanel(): JSX.Element {
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
    setSavedAt(null);
    setErr(null);
  }, [uc?.channel, uc?.auto_check, uc?.check_interval_hours, uc?.signing_pubkey]);

  const active = status?.phase && status.phase in PHASE_LABEL;
  const blockedReason = status?.apply_blocked_reason ?? "";

  const onCheck = async () => {
    if (checking || busy) return;
    setErr(null);
    setChecking(true);
    try {
      await checkUpdate();
      await useStore.getState().loadUpdate();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Check failed.");
    } finally {
      setChecking(false);
    }
  };

  const onUpgrade = async () => {
    if (busy || !status?.latest || !status.update_available) return;
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
      confirmLabel: "Download & install",
      cancelLabel: "Not now",
    });
    if (!ok) return;
    setErr(null);
    setBusy(true);
    try {
      await applyUpdate();
      // progress now streams over the `update` WS event into the store slice.
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.status === 409
            ? e.message || "Update can't start right now."
            : e.message || "Could not start the update."
          : "Could not start the update.";
      setErr(msg);
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
          ? e.status === 400
            ? e.message || "Invalid update settings."
            : e.message || "Could not save."
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

  return (
    <div className="flex flex-col gap-4">
      {/* ---------------------------------------------------------- status */}
      <Panel
        title="Software updates"
        right={
          <span className="inline-flex items-center gap-1.5 text-[11px] text-dim">
            <Icon name="check" size={14} />
            v{status?.current ?? "—"}
          </span>
        }
      >
        <p className="text-[11px] text-dim leading-relaxed max-w-xl mb-4">
          AstroDeck checks GitHub for signed releases. Updates are verified
          (Ed25519 + SHA256) before they run, applied only when you confirm, and
          never while the rig is imaging. A failed update rolls back automatically.
        </p>

        <div className="flex items-center justify-between gap-3 py-3 border-t border-line">
          <div className="min-w-0 text-xs text-dim">
            Last checked: <span className="text-ink">{fmtTime(status?.last_check_ts)}</span>
            {status?.channel ? ` · ${status.channel}` : ""}
            {status && status.supervised === false && (
              <span className="block text-warn mt-1">
                Not running under the supervisor — you can check for updates, but
                installing requires the supervised launcher.
              </span>
            )}
          </div>
          <button
            type="button"
            className="btn min-h-[44px] sm:min-h-0 inline-flex items-center gap-2 shrink-0"
            onClick={onCheck}
            disabled={checking || busy || !canUpdate}
          >
            <Icon name="refresh" size={15} />
            {checking ? "Checking…" : "Check now"}
          </button>
        </div>

        {/* ------------------------------------------------ available card */}
        {status?.update_available && status.latest && (
          <div className="border border-accent/50 bg-accent/10 px-3 py-3 mt-3">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div className="text-sm text-ink">
                Update available:{" "}
                <span className="mono text-accent">v{status.latest}</span>{" "}
                <span className="text-dim text-xs">(current v{status.current})</span>
              </div>
              <button
                type="button"
                className="btn btn-accent min-h-[44px] sm:min-h-0 inline-flex items-center gap-2"
                onClick={onUpgrade}
                disabled={busy || !!active || status.can_apply === false || !canUpdate}
              >
                <Icon name="arrow-down" size={15} />
                {busy ? "Starting…" : "Upgrade"}
              </button>
            </div>
            {status.notes_md && (
              <div className="mt-3 max-h-48 overflow-auto border border-line2 bg-base/40 p-2 text-[11px] text-ink whitespace-pre-wrap leading-relaxed">
                {status.notes_md}
              </div>
            )}
            {status.can_apply === false && blockedReason && (
              <p className="text-[11px] text-warn inline-flex items-center gap-1.5 mt-2">
                <Icon name="alert" size={13} className="shrink-0" />
                Can't install now: {blockedReason}
              </p>
            )}
          </div>
        )}

        {/* --------------------------------------------------- progress */}
        {active && (
          <div className="mt-3">
            <div className="flex items-center justify-between text-[11px] text-dim mb-1">
              <span>{PHASE_LABEL[status!.phase]}</span>
              <span>{Math.round((status!.progress || 0) * 100)}%</span>
            </div>
            <div className="h-1.5 w-full bg-raise overflow-hidden">
              <div
                className="h-full bg-accent transition-[width] duration-200"
                style={{ width: `${Math.round((status!.progress || 0) * 100)}%` }}
              />
            </div>
          </div>
        )}

        {/* ------------------------------------------------ last result */}
        {status?.last_result && !active && (
          <p
            className={`text-[11px] inline-flex items-center gap-1.5 mt-3 ${
              status.last_result.ok ? "text-good" : "text-bad"
            }`}
          >
            <Icon name={status.last_result.ok ? "check" : "alert"} size={13} />
            {status.last_result.ok
              ? `Updated to v${status.last_result.version}.`
              : `Last update to v${status.last_result.version} failed (${
                  status.last_result.reason || "rolled back"
                }).`}
          </p>
        )}

        {status?.error && !active && (
          <p className="text-[11px] text-bad inline-flex items-center gap-1.5 mt-2">
            <Icon name="alert" size={13} className="shrink-0" />
            {status.error}
          </p>
        )}
      </Panel>

      {/* ----------------------------------------------------- settings */}
      <Panel title="Update settings">
        <div className="grid gap-4">
          <Field label="Channel" hint="Stable installs final releases only; Pre-release also offers release candidates.">
            <Segmented
              options={[
                { value: "stable", label: "Stable" },
                { value: "prerelease", label: "Pre-release" },
              ]}
              value={channel}
              onChange={(v) => setChannel(v as "stable" | "prerelease")}
              ariaLabel="Update channel"
            />
          </Field>

          <div className="flex items-start justify-between gap-3 py-2 border-t border-line">
            <div className="min-w-0">
              <div className="text-sm text-ink">Check automatically</div>
              <p className="text-[11px] text-dim max-w-md">
                Poll GitHub on a schedule and surface an available update. Installing
                always still needs your confirmation.
              </p>
            </div>
            <Toggle
              checked={autoCheck}
              onChange={setAutoCheck}
              disabled={busy || !canUpdate}
              label="Check automatically"
              showState
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Check interval (hours)" hint="How often to poll when automatic checks are on.">
              <input
                className="field"
                type="number"
                min={1}
                max={720}
                step={1}
                value={intervalH}
                onChange={(e) =>
                  setIntervalH(Math.min(720, Math.max(1, Number(e.target.value) || 24)))
                }
                disabled={busy || !autoCheck || !canUpdate}
              />
            </Field>
          </div>

          <Field
            label="Release signing public key"
            hint="Base64 Ed25519 public key. Releases must verify against this key or they are rejected. Generate with scripts/gen_signing_key.py and keep the private key in CI only."
          >
            <input
              className="field mono text-[11px]"
              type="text"
              placeholder="base64 Ed25519 public key"
              value={pubkey}
              onChange={(e) => setPubkey(e.target.value)}
              disabled={busy || !canUpdate}
              spellCheck={false}
            />
          </Field>

          <div className="flex items-center gap-3">
            <button
              type="button"
              className="btn btn-accent min-h-[44px] sm:min-h-0 inline-flex items-center gap-2"
              onClick={onSave}
              disabled={busy || !dirty || !canUpdate}
            >
              <Icon name="check" size={15} />
              {busy ? "Saving…" : "Save settings"}
            </button>
            {savedAt && !busy && !err && (
              <span className="text-[11px] text-good inline-flex items-center gap-1.5">
                <Icon name="check" size={13} /> Saved
              </span>
            )}
          </div>

          {!pubkey.trim() && (
            <div className="flex items-start gap-3 border border-warn/50 bg-warn/10 px-3 py-2 text-xs">
              <Icon name="alert" size={14} className="text-warn shrink-0 mt-0.5" />
              <span className="text-ink">
                No signing key pinned — updates can't be installed until you set the
                release public key (this is what proves a release is genuinely yours).
              </span>
            </div>
          )}
        </div>
      </Panel>

      {err && (
        <p className="text-xs text-bad inline-flex items-center gap-1.5">
          <Icon name="alert" size={13} className="shrink-0" />
          {err}
        </p>
      )}
    </div>
  );
}
