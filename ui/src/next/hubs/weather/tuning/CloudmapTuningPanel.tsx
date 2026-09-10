// CloudmapTuningPanel.tsx - the body of the `cloudmap` sheet, rebuilt in the
// design's own vocabulary (wave R7, T-R7-15; plan section 3.F19 and the cutover
// table in section 7).
//
// It replaces `components/settings/CloudmapPanel.tsx` at this sheet's mount.
// The legacy file is NOT edited and NOT deleted: `#/classic` still renders it.
//
// WHAT CHANGED.
//
// 1. NO SAVE BUTTON. Same reason as the weather panel next door: a draft held
//    behind one press means a satellite the user picked is not the satellite the
//    rig is polling, and the only sign of it was a grey "Unsaved changes" line.
//    Each control writes its one field and echoes the whole block, which is
//    what `setCloudmapConfig` requires anyway (the server swaps `cloudmap`
//    wholesale; there is no /api/config/cloudmap and no merge).
//
// 2. THE SATELLITE IS A SEGMENTED CONTROL, NOT A `<select>`. Three options, all
//    three worth seeing at once, and the mispin warning below it only makes
//    sense when the alternative is visible. A native select on a phone opens a
//    system sheet over the whole screen to choose between three words.
//
// 3. SIX NATIVE `disabled` ATTRIBUTES ARE GONE (`CloudmapPanel.tsx` :146, :165,
//    :196, :214, :226, :268 - the toggle, the select, the "switch to Automatic"
//    link, both numbers and Save). Every one is `lockedReason` + `onExplain`.
//
// 4. `Number("")` CANNOT REACH THE RIG: `NumberField` rejects a blank instead of
//    committing a real zero, and clamps to the server's own bounds, so the 422
//    the legacy validator existed to pre-empt is unreachable.
//
// 5. THE BLOCK IS NOT INVENTED WHEN IT IS ABSENT. The legacy panel seeded "10"
//    and "100" whether or not the rig had sent a cloudmap block - and a server
//    older than 0.3.10 does not have one. With no block this says so.
//
// WHAT DID NOT CHANGE: the advisory caveat leads the card, because a model that
// gates nothing must never be mistaken for one that can stop a run; and the
// mispin note stays, because a pin written by a Pydantic default is
// indistinguishable from a deliberate one and only the operator can tell them
// apart (see `cloudmapModel.mispinLine`).

import { useEffect, useState, type JSX } from "react";
import { ApiError } from "../../../../api";
import { getCloudmap, setCloudmapConfig } from "../../../../api/cloudmap";
import { accessPhrase } from "../../../../lib/caps";
import { useConfig, useStore } from "../../../../store";
import { useLock } from "../../../lib/gateHook";
import {
  ActionButton, Card, EmptyCard, Label, LockNote, Mono, NumberField, Segmented, Switch,
} from "../../../ui";
import {
  CM_CONFLICT, CM_DEFAULT_SITE_WARN, CM_EMPTY_HINT, CM_EMPTY_TITLE, CM_ENABLED_NOTE, CM_INTRO,
  CM_SAVE_FAILED, CM_TITLE, costLine, cloudmapLockSentence, HALF_MAX, HALF_MIN, mispinLine,
  PLATFORM_COVERAGE, PLATFORM_LABEL, PLATFORM_NOTE, PLATFORM_ORDER, PLATFORM_SUB, POLL_MAX,
  POLL_MIN, SWITCH_TO_AUTO, type Platform,
} from "./cloudmapModel";

interface CloudmapEdit {
  enabled?: boolean;
  platform?: Platform;
  poll_minutes?: number;
  half_px?: number;
}

export function CloudmapTuningPanel(): JSX.Element {
  const config = useConfig();
  const c = config?.cloudmap ?? null;
  const lock = useLock({ cap: "config.site_optics" });

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  // `suggested_platform` is NOT in the config - it is what the geometry makes
  // of the config - and it is the only thing that can tell an operator their
  // pinned bird is the wrong one. Read once per config version, and a failure
  // is silent: the pin is still editable without it, there is just nothing to
  // say about which one is nearer.
  const [suggested, setSuggested] = useState<Platform | null>(null);

  const version = config?.version ?? null;
  useEffect(() => {
    let alive = true;
    getCloudmap()
      .then((s) => { if (alive) setSuggested(s.suggested_platform ?? null); })
      .catch(() => { /* no suggestion to make; the pin still edits */ });
    return () => { alive = false; };
  }, [version]);

  const write = async (edit: CloudmapEdit): Promise<void> => {
    if (busy || !c) return;
    setBusy(true);
    setErr(null);
    try {
      // Wholesale replace: the server swaps the whole block, so a partial body
      // would drop the fields this edit does not name.
      await setCloudmapConfig({
        enabled: edit.enabled ?? c.enabled,
        platform: edit.platform ?? (c.platform ?? "auto"),
        poll_minutes: edit.poll_minutes ?? c.poll_minutes,
        half_px: edit.half_px ?? c.half_px,
      });
      await useStore.getState().loadConfig();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await useStore.getState().loadConfig();
        setErr(CM_CONFLICT);
      } else if (e instanceof ApiError && e.status === 403) {
        setErr(`Refused - ${accessPhrase("config.site_optics")} is needed here.`);
      } else {
        setErr(e instanceof Error ? (e.message || CM_SAVE_FAILED) : CM_SAVE_FAILED);
      }
      setNonce((n) => n + 1);
    } finally {
      setBusy(false);
    }
  };

  const busyReason = busy ? "a change is still being saved" : null;
  const gate = lock.lockedReason ?? busyReason;
  const explain = lock.onExplain;
  const noteReason = lock.lockedReason ? cloudmapLockSentence(lock.lockedReason) : null;

  if (!c) {
    return (
      <div className="nx-wxtune-stack" data-testid="cloudmap-tuning">
        <Card>
          <div className="nx-wxtune-col">
            <Label size={11}>{CM_TITLE}</Label>
            <EmptyCard
              title={CM_EMPTY_TITLE}
              hint={CM_EMPTY_HINT}
              data-testid="cloudmap-tuning-empty"
            />
          </div>
        </Card>
        <LockNote reason={noteReason} data-testid="cloudmap-tuning-lock" />
      </div>
    );
  }

  const platform: Platform = c.platform ?? "auto";
  const mispin = mispinLine(platform, suggested);

  return (
    <div className="nx-wxtune-stack" data-testid="cloudmap-tuning">
      <Card data-testid="cloudmap-tuning-card">
        <div className="nx-wxtune-col">
          <div className="nx-wxtune-head">
            <Label size={11}>{CM_TITLE}</Label>
            <Mono size={10} tone="dim">{c.enabled ? "POLLING" : "NOT POLLING"}</Mono>
          </div>
          <p className="nx-wxtune-intro">{CM_INTRO}</p>

          <Switch
            checked={c.enabled}
            onChange={(v) => void write({ enabled: v })}
            label="Cloud model enabled"
            note={CM_ENABLED_NOTE}
            lockedReason={gate}
            onExplain={explain}
            data-testid="cloudmap-enabled"
          />

          {c.enabled && config?.site?.is_default && (
            <p className="nx-wxtune-warn" data-testid="cloudmap-default-site">
              {CM_DEFAULT_SITE_WARN}
            </p>
          )}
        </div>
      </Card>

      <Card data-testid="cloudmap-satellite-card">
        <div className="nx-wxtune-col">
          <Label size={11}>Satellite</Label>
          <Segmented<Platform>
            options={PLATFORM_ORDER.map((p) => ({
              value: p,
              label: PLATFORM_LABEL[p],
              sub: PLATFORM_SUB[p],
            }))}
            value={platform}
            onChange={(v) => void write({ platform: v })}
            label="GOES satellite"
            lockedReason={gate}
            onExplain={explain}
            data-testid="cloudmap-satellite"
          />
          <p className="nx-wxtune-note" data-testid="cloudmap-platform-note">
            {`${PLATFORM_NOTE[platform]} ${PLATFORM_COVERAGE}`}
          </p>
          {mispin && (
            <>
              <p className="nx-wxtune-warn" data-testid="cloudmap-mispin">{mispin}</p>
              <div className="nx-wxtune-actions">
                <ActionButton
                  kind="secondary"
                  onPress={() => void write({ platform: "auto" })}
                  lockedReason={gate}
                  onExplain={explain}
                  data-testid="cloudmap-to-auto"
                >
                  {SWITCH_TO_AUTO}
                </ActionButton>
              </div>
            </>
          )}
        </div>
      </Card>

      <Card data-testid="cloudmap-cost-card">
        <div className="nx-wxtune-col">
          <div className="nx-wxtune-pair">
            <NumberField
              key={`poll-${nonce}`}
              label="Poll every (min)"
              value={c.poll_minutes}
              onCommit={(v) => void write({ poll_minutes: v })}
              unit="min"
              min={POLL_MIN}
              max={POLL_MAX}
              integer
              hint="The satellite publishes every 5"
              ariaLabel="Poll interval, minutes"
              lockedReason={gate}
              onExplain={explain}
              data-testid="cloudmap-poll"
            />
            <NumberField
              key={`half-${nonce}`}
              label="Window half-width (cells)"
              value={c.half_px}
              onCommit={(v) => void write({ half_px: v })}
              unit="cells"
              min={HALF_MIN}
              max={HALF_MAX}
              integer
              ariaLabel="Window half-width, satellite grid cells"
              lockedReason={gate}
              onExplain={explain}
              data-testid="cloudmap-half"
            />
          </div>
          <p className="nx-wxtune-note" data-testid="cloudmap-cost">
            {costLine(c.half_px, c.poll_minutes)}
          </p>
        </div>
      </Card>

      {err && <Mono size={10.5} tone="bad" data-testid="cloudmap-tuning-error">{err}</Mono>}
      <LockNote reason={noteReason} data-testid="cloudmap-tuning-lock" />
    </div>
  );
}
