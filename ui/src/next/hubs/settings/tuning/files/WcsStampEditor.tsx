// WcsStampEditor.tsx - "Record where each photo points", rebuilt in the
// design's own vocabulary (wave R7, T-R7-13; replaces the mounted
// `components/settings/WcsStampPanel.tsx`, which is NOT edited and keeps
// serving `#/classic`).
//
// ONE COMPONENT, TWO MOUNT POINTS, exactly as the legacy panel was: the
// PLATE-SOLVE STAMP sheet (`settings/sheets/WcsSheet.tsx`) and the Optics
// sheet's own plate-solve card (`settings/sheets/OpticsSheet.tsx`). It renders
// NO sheet chrome and no outer card of its own so both hosts can frame it their
// own way, and a fork would be two answers to one setting.
//
// PROGRESSIVE DISCLOSURE IS THE SHAPE OF THE FEATURE. The novice surface is one
// off-by-default switch in plain language; solver, downsample and the star
// floor live behind the `Disclosure` primitive, whose summary line says what is
// set without opening it. That is the legacy panel's own structure, now in the
// wave's shared primitive rather than a hand-rolled `aria-expanded` button.
//
// THE STAR FLOOR IS A `NumberField`, WHICH IS THE POINT. `Number("")` is 0, and
// 0 means "no floor - solve every frame": a blank box committed as a real zero
// would silently turn the gate off. `NumberField` rejects a value that does not
// parse and restores the last committed number, which is the behaviour the
// legacy panel's own `commitStars` did NOT have (`WcsStampPanel.tsx:65` reads
// `Number(starsDraft) || 0`, so a blank field wrote 0).

import { useState, type JSX } from "react";
import { ApiError } from "../../../../../api";
import { setWcsStampConfig } from "../../../../../api/backends";
import { accessPhrase } from "../../../../../lib/caps";
import {
  DOWNSAMPLE_CHOICES, downsampleLabel, wcsStampAdvisory, wcsStampOrDefault,
  wcsStampSummary,
} from "../../../../../lib/wcsStamp";
import type { WcsStampConfig } from "../../../../../types";
import { useConfig, useProviders, useStore } from "../../../../../store";
import { useLock } from "../../../../lib/gateHook";
import { isLocalOnly, LOCAL_ONLY_REASON } from "../../../../lib/gate";
import {
  Disclosure, LockNote, NumberField, Segmented, Switch,
} from "../../../../ui";
import {
  filesLockSentence, plainDashes, WCS_BLURB, WCS_DOWNSAMPLE_NOTE, WCS_FOOTNOTE,
  WCS_MINSTARS_NOTE, WCS_SOLVER_NOTE, WCS_SUBJECT, WCS_TITLE,
} from "./filesModel";
import "./files.css";

export function WcsStampEditor(): JSX.Element {
  const config = useConfig();
  const providers = useProviders();
  const enabled = config?.solve_saved_lights ?? false;
  const stamp = wcsStampOrDefault(config?.wcs_stamp);

  // Named at RENDER time (`accessPhrase` reads the live role table) and phrased
  // as a sentence - never the raw capability string, which means nothing to a
  // user who has never read the RBAC table.
  // `needsLan`: every write here is `POST /api/config/wcs`, on the LAN-only fence.
  const gate = useLock({ cap: "config.site_optics", needsLan: true });
  const lock = filesLockSentence(
    gate.lockedReason, accessPhrase("config.site_optics"), WCS_SUBJECT,
  );
  const explain = gate.onExplain;

  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const advisory = wcsStampAdvisory(providers?.solve);

  const save = async (nextEnabled: boolean, next: WcsStampConfig): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      // The master enable and the advanced block travel together: the server
      // takes them in one atomic version bump, so sending half would leave the
      // other half of the user's intent unrecorded.
      await setWcsStampConfig({ solve_saved_lights: nextEnabled, wcs_stamp: next });
      await useStore.getState().loadConfig();
    } catch (e) {
      // `isLocalOnly` FIRST: the fence answers 403 for every role.
      setErr(isLocalOnly(e) ? LOCAL_ONLY_REASON
        : e instanceof ApiError
          ? (e.status === 403 ? `Refused - ${accessPhrase("config.site_optics")} is needed here.`
            : e.message)
          : "Could not save.");
    } finally { setBusy(false); }
  };

  const patch = (p: Partial<WcsStampConfig>): void => void save(enabled, { ...stamp, ...p });

  return (
    <div className="nx-files-col" data-testid="wcs-editor">
      <Switch
        checked={enabled}
        onChange={(v) => void save(v, stamp)}
        label={WCS_TITLE}
        note={WCS_BLURB}
        lockedReason={lock}
        onExplain={explain}
        data-testid="wcs-enabled"
      />

      {advisory && (
        <p className="nx-files-warn" data-testid="wcs-advisory">{plainDashes(advisory)}</p>
      )}

      <Disclosure
        summary="ADVANCED"
        sub={plainDashes(wcsStampSummary(enabled, stamp))}
        open={open}
        onToggle={setOpen}
        data-testid="wcs-advanced"
      >
        <Segmented
          label="Solver"
          value={stamp.solver}
          onChange={(v) => patch({ solver: v })}
          options={[
            { value: "auto" as const, label: "AUTO" },
            { value: "astap" as const, label: "ASTAP" },
          ]}
          lockedReason={lock}
          onExplain={explain}
          data-testid="wcs-solver"
        />
        <p className="nx-files-note">{WCS_SOLVER_NOTE}</p>

        <Segmented
          label="Downsample"
          value={String(stamp.downsample)}
          onChange={(v) => patch({ downsample: Number(v) })}
          options={DOWNSAMPLE_CHOICES.map((d) => ({
            value: String(d), label: downsampleLabel(d).toUpperCase(),
          }))}
          lockedReason={lock}
          onExplain={explain}
          data-testid="wcs-downsample"
        />
        <p className="nx-files-note">{WCS_DOWNSAMPLE_NOTE}</p>

        <NumberField
          label="Only tag frames with enough stars"
          ariaLabel="Minimum detected stars before solving"
          value={stamp.min_stars}
          onCommit={(n) => patch({ min_stars: n })}
          unit="stars"
          min={0}
          integer
          hint={WCS_MINSTARS_NOTE}
          zeroMeans="no floor, every frame is solved"
          lockedReason={lock}
          onExplain={explain}
          data-testid="wcs-minstars"
        />
      </Disclosure>

      <p className="nx-files-note">{WCS_FOOTNOTE}</p>

      {err && <p className="nx-files-err" data-testid="wcs-error">{err}</p>}
      <LockNote reason={lock} data-testid="wcs-lock" />
    </div>
  );
}
