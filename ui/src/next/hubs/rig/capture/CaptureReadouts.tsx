// CaptureReadouts.tsx - the design's readout grid and its one dial, over the
// `capture` frame scope.
//
// EVERY VALUE HERE IS `frameSettings.capture`, not a private useState. On
// 2026-08-08 the operator set FILT=R on the Align screen and Capture said Oiii:
// every camera setting behind those screens was a private useState seeded from
// a constant, so no two surfaces could agree and a reload recovered none of them
// (types.ts:715-737). One home per (camera, purpose).
//
// THE TEXT DRAFTS ARE NOT DECORATION. `useFrameDraft` keeps the RAW STRING so
// that "", "1e9" and "-3" reach the guards: bind an input to a number and a
// blank box silently becomes a 0 s exposure, which is the blank frame plus the
// misleading "few stars" that CAP-02 was filed for. The commit happens on blur,
// on Enter, and once more before the shutter fires.
//
// AND THE HANDLES ARE PROPS, NOT `useFrameDraft` CALLS OF THIS FILE'S OWN.
// `useFrameDraft` returns per-CALLER state: a second call for the same field is
// a SECOND draft with its own text and its own commit, so a copy owned here
// could not be the copy the shutter reads. That is exactly what r4 #1 was - the
// gate was fed the committed store number instead, both fix-reasons became
// unreachable, and a typed-but-unblurred 180 shot the old 30 s. CaptureScreen
// owns the four handles now and passes them down; this file only renders them.
//
// The design shows four tiles (FILTER, EXPOSURE, GAIN, COUNT). OFFSET and
// BINNING are the two the rig has and the design has no room for, so they sit in
// the same scrollable grid rather than being dropped: nothing is lost.
//
// FILTER is the one control on this screen that OFFERS THE BLACKOUT SLOTS. Every
// other screen builds its filter list with `cameraDialCategories`, which drops
// them, because a focus or solve sweep through a carrier with no glass hangs.
// Capture is the deliberate exception - parking on the blackout slot is how you
// shoot darks on a rig with a wheel - so the slot list comes from
// `captureFilterDialCategory`, and each blackout slot says so on its face.

import { useId, useMemo, useState, type JSX } from "react";
import {
  EXPOSURE_PRESETS_S, GAIN_PRESETS, BIN_PRESETS, fmtExposure, filterFace,
} from "../../../../components/ui/CameraPickers";
import { captureFilterDialCategory } from "../../../../components/capture/captureFilterDial";
import { CAPTURE_PRESETS } from "../../../../lib/capturePresets";
import { subLengthVerdict } from "../../../../lib/photometry";
import { useFrameSettings, useStore } from "../../../../store";
import {
  ActionButton, Chip, Dial, Field, Label, Mono, ReadoutGrid, ReadoutTile, TextInput,
  type DialOption,
} from "../../../ui";
import {
  EXPOSURE_INVALID_MESSAGE, draftNumber, gainInvalidMessage, isGainInvalid,
} from "./captureGate";
import { isExposureInvalid } from "../../../../lib/exposure";
import type { RigStatus } from "../../../../types";

/** The COUNT stops. `/api/capture` takes no count, so this is a client-side
 *  batch and its ceiling is what an operator will sit through, not a protocol
 *  limit. */
export const COUNT_PRESETS = [1, 2, 3, 5, 10, 20, 50, 100];

export type TileId = "filter" | "exposure" | "gain" | "count" | "offset" | "binning";

/** One `useFrameDraft` handle, passed down rather than created here (see the
 *  header). Structural, so the store's own return type satisfies it and this
 *  file needs no import from the store to name it. */
export interface FrameDraftHandle {
  text: string;
  setText: (raw: string) => void;
  commit: () => void;
}

/** A labelled text box that COMMITS on blur and on Enter.
 *
 *  The primitive `TextInput` deliberately takes neither handler (it is the one
 *  text input and owns only value/change), and `next/ui` is not this task's to
 *  edit, so the two events are caught on the wrapper: React's `onBlur` is
 *  focusout and bubbles, and Enter is a keydown on the input. Without both, a
 *  typed value that was never blurred would be a number the shutter uses and no
 *  other surface can see. */
function DraftBox({ label, hint, value, onChange, commit, lockedReason, testid }: {
  label: string;
  hint?: string | null;
  value: string;
  onChange: (v: string) => void;
  commit: () => void;
  lockedReason: string | null;
  testid: string;
}): JSX.Element {
  const id = useId();
  return (
    <div
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") commit(); }}
    >
      <Field label={label} hint={hint ?? undefined} htmlFor={id}>
        <TextInput
          id={id}
          value={value}
          onChange={onChange}
          mono
          ariaLabel={label}
          lockedReason={lockedReason}
          data-testid={testid}
        />
      </Field>
    </div>
  );
}

/** Bytes a frame of this shape costs, from the camera's OWN sensor size at this
 *  binning, in MB. The prototype's `count * 52 / bin^2` is a fixture from one
 *  camera; this is the number the disk will actually see. Null when the camera
 *  has not said how big its sensor is - a fabricated size is worse than none. */
export function frameSizeMb(
  width: number | undefined, height: number | undefined, binning: number,
): number | null {
  const b = Math.max(1, Math.round(binning) || 1);
  if (!width || !height || width <= 0 || height <= 0) return null;
  return (Math.floor(width / b) * Math.floor(height / b) * 2) / 1e6;
}

/** The one-line summary under the grid (proto `capSummary`). */
export function captureSummary(opts: {
  count: number; exposureS: number; filter: string; gain: number; binning: number;
  coolerTargetC: number | null; coolerOn: boolean; sizeMb: number | null;
}): string {
  const bits = [
    `${opts.count} × ${opts.exposureS} s ${opts.filter}`,
    `gain ${opts.gain}`,
    `bin ${opts.binning}`,
    opts.coolerOn && opts.coolerTargetC != null ? `${opts.coolerTargetC}°C` : "cooler off",
  ];
  if (opts.sizeMb != null) bits.push(`${(opts.sizeMb * opts.count).toFixed(0)} MB`);
  return bits.join(" · ");
}

export interface CaptureReadoutsProps {
  status: RigStatus | null;
  count: number;
  setCount: (n: number) => void;
  /** The four `capture`-scope drafts, owned by CaptureScreen because the
   *  shutter's guard and the POST body are both built from these strings. */
  expDraft: FrameDraftHandle;
  gainDraft: FrameDraftHandle;
  offsetDraft: FrameDraftHandle;
  binDraft: FrameDraftHandle;
  /** Moves the wheel now, and pins that slot's saved settings (F.5). */
  moveFilterTo: (slot: number) => void;
  /** `filterMotion(...)`'s sentence while the carousel turns, or null. */
  filterNote: string | null;
  lockedReason: string | null;
  onExplain: (reason: string) => void;
  /** The slot-name editor - names, types, blackout, narrowband, per-filter
   *  exposure and gain, focus offsets and the offset auto-learn - all of which
   *  live in the Rig hub's filter-wheel sheet. This screen LINKS to it rather
   *  than growing a second copy whose `learnDisabledReason` chain could drift. */
  onOpenWheel: () => void;
  /** Sky-limited sub length from the last LINEAR preview, or null. */
  skyLimitedS: number | null;
  onSuggest: () => void;
  suggestReason: string | null;
}

export function CaptureReadouts(props: CaptureReadoutsProps): JSX.Element {
  const [tile, setTile] = useState<TileId>("exposure");
  const settings = useFrameSettings("capture");
  const setFrameSettings = useStore((s) => s.setFrameSettings);
  const setCapture = (patch: Parameters<typeof setFrameSettings>[1]) =>
    setFrameSettings("capture", patch);

  const { expDraft, gainDraft, offsetDraft, binDraft } = props;
  const [countText, setCountText] = useState(String(props.count));

  const cam = props.status?.camera;
  const wheel = props.status?.filterwheel;
  const maxGain = cam?.max_gain ?? null;
  // UX-27: offer bins 1..max_bin from the camera's reported ceiling instead of a
  // hardcoded list. Clamp to a sane 1-8 in case a backend reports garbage.
  const maxBin = Math.min(8, Math.max(1, cam?.max_bin ?? 4));

  const exposureBad = isExposureInvalid(expDraft.text);
  const gainBad = isGainInvalid(gainDraft.text, maxGain);

  /** The tiles read the DRAFTS, the same numbers the shutter will send. A tile
   *  showing the committed value beside a box holding a different one is the
   *  r4 #1 disagreement in miniature: "the field reads 180, the rig shoots 30,
   *  and nothing is said". */
  const shownExposureS = draftNumber(expDraft.text, settings.exposure_s);
  const shownGain = draftNumber(gainDraft.text, settings.gain);
  const shownOffset = draftNumber(offsetDraft.text, settings.offset);
  const shownBinning = Math.max(1, draftNumber(binDraft.text, settings.binning));

  const wheelName = typeof wheel?.position === "number"
    ? (wheel.names?.[wheel.position] ?? null) : null;
  const face = filterFace(wheelName, settings.filter);

  const filterCategory = useMemo(
    () => captureFilterDialCategory(wheel, props.moveFilterTo),
    [wheel, props.moveFilterTo],
  );
  /** The slot list, keyed by SLOT INDEX. Two slots may carry the same name and
   *  the wheel only knows positions - the 2026-08-02 offset bug shifted every
   *  frame's FILTER header by one slot, and a name-keyed control cannot even
   *  express which slot it meant. */
  const filterOptions: DialOption<number>[] = (filterCategory?.kind === "entry"
    ? []
    : filterCategory?.options ?? []
  ).map((o) => ({ value: Number(o.id), label: o.label }));

  const gainOptions = GAIN_PRESETS.filter((g) => !maxGain || g <= maxGain);
  const binOptions = BIN_PRESETS.filter((b) => b <= maxBin);
  const scaleAt1 = props.status?.optics?.image_scale_arcsec_px ?? null;

  const sizeMb = frameSizeMb(cam?.width, cam?.height, shownBinning);
  const verdict = subLengthVerdict(shownExposureS, props.skyLimitedS);
  const exposureSub = verdict === "unknown"
    ? "sky limit unknown"
    : verdict === "too_short" ? `under sky-limited ${Math.round(props.skyLimitedS ?? 0)}s`
      : verdict === "long" ? `well past sky-limited ${Math.round(props.skyLimitedS ?? 0)}s`
        : `sky-limited near ${Math.round(props.skyLimitedS ?? 0)}s`;

  /** Which preset the settings on screen ACTUALLY ARE - not which one was last
   *  tapped. Derived, so the mark can only ever name settings that are loaded;
   *  Suggest, "match last lights" and a hand edit all move it. */
  const activePreset = CAPTURE_PRESETS.find((p) =>
    settings.exposure_s === p.exposure_s && settings.gain === p.gain
    && settings.offset === p.offset && settings.binning === p.binning) ?? null;

  const commitCount = () => {
    const n = Math.round(Number(countText));
    if (!Number.isFinite(n) || n < 1 || n > 999) { setCountText(String(props.count)); return; }
    props.setCount(n);
    setCountText(String(n));
  };

  const dial = (): JSX.Element => {
    switch (tile) {
      case "filter":
        return (
          <Dial<number>
            label="FILTER"
            options={filterOptions.length ? filterOptions : [{ value: -1, label: "no wheel" }]}
            value={typeof wheel?.position === "number" ? wheel.position : -1}
            onChange={(slot) => { if (slot >= 0) props.moveFilterTo(slot); }}
            lockedReason={filterOptions.length ? props.lockedReason : "No filter wheel is connected"}
            onExplain={props.onExplain}
            data-testid="capture-dial-filter"
          />
        );
      case "gain":
        return (
          <Dial<number>
            label="GAIN"
            options={gainOptions.map((g) => ({ value: g, label: String(g) }))}
            value={settings.gain}
            onChange={(g) => setCapture({ gain: g })}
            lockedReason={props.lockedReason}
            onExplain={props.onExplain}
            data-testid="capture-dial-gain"
          />
        );
      case "count":
        return (
          <Dial<number>
            label="COUNT"
            options={COUNT_PRESETS.map((n) => ({ value: n, label: String(n) }))}
            value={props.count}
            onChange={(n) => { props.setCount(n); setCountText(String(n)); }}
            lockedReason={props.lockedReason}
            onExplain={props.onExplain}
            data-testid="capture-dial-count"
          />
        );
      case "offset":
        // A continuum, not a list (CameraPickers' OffsetPicker is `kind:"entry"`
        // for exactly this reason), so the dial is not the control - the box is.
        return (
          <DraftBox
            label="OFFSET"
            hint="a continuum, not a preset list - type it"
            value={offsetDraft.text}
            onChange={offsetDraft.setText}
            commit={offsetDraft.commit}
            lockedReason={props.lockedReason}
            testid="capture-entry-offset"
          />
        );
      case "binning":
        return (
          <Dial<number>
            label="BINNING"
            options={binOptions.map((b) => ({ value: b, label: `${b}x${b}` }))}
            value={settings.binning}
            onChange={(b) => setCapture({ binning: b })}
            lockedReason={props.lockedReason}
            onExplain={props.onExplain}
            data-testid="capture-dial-binning"
          />
        );
      default:
        return (
          <Dial<number>
            label="EXPOSURE"
            options={EXPOSURE_PRESETS_S.map((s) => ({ value: s, label: fmtExposure(s) }))}
            value={settings.exposure_s}
            onChange={(s) => setCapture({ exposure_s: s })}
            lockedReason={props.lockedReason}
            onExplain={props.onExplain}
            data-testid="capture-dial-exposure"
          />
        );
    }
  };

  const entry = (): JSX.Element | null => {
    if (tile === "filter" || tile === "offset") return null;
    const draft = tile === "exposure" ? expDraft
      : tile === "gain" ? gainDraft
        : tile === "binning" ? binDraft : null;
    if (draft) {
      const bad = tile === "exposure" ? exposureBad : tile === "gain" ? gainBad : false;
      const hint = tile === "exposure"
        ? (bad ? EXPOSURE_INVALID_MESSAGE : null)
        : tile === "gain" ? (bad ? gainInvalidMessage(maxGain) : null) : null;
      return (
        <DraftBox
          label={tile.toUpperCase()}
          hint={hint}
          value={draft.text}
          onChange={draft.setText}
          commit={draft.commit}
          lockedReason={props.lockedReason}
          testid={`capture-entry-${tile}`}
        />
      );
    }
    return (
      <DraftBox
        label="COUNT"
        hint="frames this press will take, one after the other"
        value={countText}
        onChange={setCountText}
        commit={commitCount}
        lockedReason={props.lockedReason}
        testid="capture-entry-count"
      />
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }} data-testid="capture-readouts">
      <div style={{ overflowX: "auto" }}>
        <ReadoutGrid cols={3}>
          <ReadoutTile
            label="FILTER"
            value={face.face}
            sub={face.pending ? "moving to the pin" : (wheel ? "in the beam" : "no wheel")}
            selected={tile === "filter"}
            onSelect={() => setTile("filter")}
            data-testid="tile-filter"
          />
          <ReadoutTile
            label="EXPOSURE"
            value={fmtExposure(shownExposureS)}
            sub={exposureSub}
            tone={exposureBad ? "bad" : undefined}
            selected={tile === "exposure"}
            onSelect={() => setTile("exposure")}
            data-testid="tile-exposure"
          />
          <ReadoutTile
            label="GAIN"
            value={String(shownGain)}
            sub={maxGain ? `100 = unity · max ${maxGain}` : "100 = unity"}
            tone={gainBad ? "bad" : undefined}
            selected={tile === "gain"}
            onSelect={() => setTile("gain")}
            data-testid="tile-gain"
          />
          <ReadoutTile
            label="COUNT"
            value={String(props.count)}
            sub={sizeMb != null ? `${(sizeMb * props.count).toFixed(0)} MB` : "size unknown"}
            selected={tile === "count"}
            onSelect={() => setTile("count")}
            data-testid="tile-count"
          />
          <ReadoutTile
            label="OFFSET"
            value={String(shownOffset)}
            sub="sets the black floor"
            selected={tile === "offset"}
            onSelect={() => setTile("offset")}
            data-testid="tile-offset"
          />
          <ReadoutTile
            label="BINNING"
            value={`${shownBinning}x${shownBinning}`}
            sub={scaleAt1 != null
              ? `${(scaleAt1 * shownBinning).toFixed(2)}" per pixel`
              : "scale needs optics"}
            selected={tile === "binning"}
            onSelect={() => setTile("binning")}
            data-testid="tile-binning"
          />
        </ReadoutGrid>
      </div>

      {dial()}
      {entry()}

      {tile === "filter" && (
        <ActionButton
          kind="ghost"
          onPress={props.onOpenWheel}
          data-testid="capture-open-wheel"
        >
          EDIT SLOT NAMES AND OFFSETS
        </ActionButton>
      )}

      {tile === "exposure" && (
        <ActionButton
          kind="ghost"
          onPress={props.onSuggest}
          lockedReason={props.lockedReason ?? props.suggestReason}
          onExplain={props.onExplain}
          data-testid="capture-suggest"
        >
          SUGGEST A SUB LENGTH
        </ActionButton>
      )}

      <div style={{ display: "flex", gap: 6, overflowX: "auto", paddingBottom: 2 }}>
        <Label>PRESET</Label>
        {CAPTURE_PRESETS.map((p) => (
          <Chip
            key={p.id}
            active={activePreset?.id === p.id}
            onClick={() => setCapture({
              exposure_s: p.exposure_s, gain: p.gain, offset: p.offset, binning: p.binning,
            })}
            lockedReason={props.lockedReason}
            onExplain={props.onExplain}
          >
            {p.label}
          </Chip>
        ))}
      </div>

      {props.filterNote && (
        <Mono size={10} tone="warn">{props.filterNote}</Mono>
      )}

      <Mono size={10.5} tone="dim">
        {captureSummary({
          count: props.count,
          exposureS: shownExposureS,
          filter: face.face,
          gain: shownGain,
          binning: shownBinning,
          coolerTargetC: cam?.cooler?.target_c ?? null,
          coolerOn: !!cam?.cooler?.on,
          sizeMb,
        })}
      </Mono>
    </div>
  );
}
