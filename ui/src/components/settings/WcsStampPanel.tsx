// WcsStampPanel.tsx — Settings → Connect: per-frame WCS solve + write-back.
//
// Progressive disclosure (spec §4.2): the novice surface is ONE off-by-default
// toggle in plain language. Everything else — solver choice, downsample, the
// star floor — hides behind a collapsed "Advanced" disclosure (the app's
// aria-expanded button idiom, FilterNamesModal precedent).
//
// Honest-disabled (idiom §11.8): without config.site_optics the controls stay
// visible and focusable with aria-disabled + a title naming the missing
// capability — never the native `disabled` attribute, which would hide the
// reason from assistive tech.
//
// All logic worth testing lives in lib/wcsStamp.ts; this file is a thin shell.
import { useEffect, useState, type JSX } from "react";
import { setWcsStampConfig } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useProviders, useStore } from "../../store";
import { accessPhrase, useCan } from "../../lib/caps";
import { Panel, Field, SegmentedControl, Toggle } from "../ui";
import { Icon } from "../icons";
import {
  DOWNSAMPLE_CHOICES, downsampleLabel, wcsStampAdvisory, wcsStampOrDefault,
  wcsStampSummary,
} from "../../lib/wcsStamp";
import type { WcsStampConfig } from "../../types";

export default function WcsStampPanel(): JSX.Element {
  const config = useConfig();
  const providers = useProviders();
  const canEdit = useCan("config.site_optics");
  const enabled = config?.solve_saved_lights ?? false;
  const stamp = wcsStampOrDefault(config?.wcs_stamp);

  // Named at RENDER time (accessPhrase reads the live role table) and phrased
  // as a sentence — never the raw capability string, which meant nothing to a
  // user who has never read the RBAC table.
  const LOCK = `Changing this needs ${accessPhrase("config.site_optics")}.`;

  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // min_stars is free text while typing; committed on blur so a half-typed
  // number never round-trips to the server.
  const [starsDraft, setStarsDraft] = useState(String(stamp.min_stars));
  useEffect(() => { setStarsDraft(String(stamp.min_stars)); }, [stamp.min_stars]);

  const advisory = wcsStampAdvisory(providers?.solve);

  const save = async (nextEnabled: boolean, next: WcsStampConfig) => {
    if (busy || !canEdit) return;
    setBusy(true); setErr(null);
    try {
      await setWcsStampConfig({ solve_saved_lights: nextEnabled, wcs_stamp: next });
      await useStore.getState().loadConfig();
    } catch (e) {
      setErr(e instanceof ApiError
        ? (e.status === 403 ? LOCK : e.message)
        : "Could not save.");
    } finally { setBusy(false); }
  };

  const patch = (p: Partial<WcsStampConfig>) => void save(enabled, { ...stamp, ...p });
  const commitStars = () => {
    const n = Math.max(0, Math.round(Number(starsDraft) || 0));
    setStarsDraft(String(n));
    if (n !== stamp.min_stars) patch({ min_stars: n });
  };

  // Honest-disabled wrapper: aria-disabled + a title naming the missing cap,
  // dimmed but never natively `disabled`.
  const lockProps = canEdit
    ? {}
    : { "aria-disabled": true as const, title: LOCK };
  const lockClass = canEdit ? "" : "opacity-50";

  return (
    <Panel title="Record where each photo points">
      <div className="flex flex-col gap-3">
        {/* ---------------------------------------------------------- novice */}
        <div className="flex items-start gap-3">
          <span {...lockProps} className={lockClass}>
            <Toggle
              checked={enabled}
              onChange={canEdit ? (v) => void save(v, stamp) : () => {}}
              label="Record where each photo points"
              showState
            />
          </span>
          <div className="min-w-0">
            <div className="text-[12px] text-ink">
              Write each photo&apos;s sky position into the file
            </div>
            <p className="text-[11px] text-dim leading-snug">
              After each photo is saved, AstroDeck works out exactly where the
              scope was pointing and stores it inside the file. Stacking
              software can then line your photos up without figuring it out
              again. Costs a little time per photo, so it is off by default.
            </p>
          </div>
        </div>

        {advisory && (
          <p className="text-[11px] text-warn leading-snug">{advisory}</p>
        )}

        {/* ------------------------------------------------- advanced (§4.2) */}
        <div className="border-t border-line pt-2">
          <div className="flex items-center gap-2 flex-wrap">
            <button
              type="button"
              className="btn !px-2 !py-1 text-[11px]"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}>
              {open ? "▾ Advanced" : "▸ Advanced"}
            </button>
            {!open && (
              <span className="text-[11px] text-dim truncate">
                {wcsStampSummary(enabled, stamp)}
              </span>
            )}
          </div>
          {open && (
            <div className="mt-2 flex flex-col gap-3">
              <Field label="Solver">
                <span {...lockProps} className={`inline-block ${lockClass}`}>
                  <SegmentedControl
                    ariaLabel="WCS solver"
                    value={stamp.solver}
                    onChange={canEdit ? (v) => patch({ solver: v }) : () => {}}
                    options={[
                      { value: "auto", label: "Auto" },
                      { value: "astap", label: "ASTAP" },
                    ]}
                  />
                </span>
              </Field>
              <p className="text-[11px] text-dim leading-snug -mt-1">
                Auto picks the best installed solver.
              </p>

              <Field label="Downsample">
                <span {...lockProps} className={`inline-block ${lockClass}`}>
                  <SegmentedControl
                    ariaLabel="Solver downsample"
                    value={String(stamp.downsample)}
                    onChange={canEdit ? (v) => patch({ downsample: Number(v) }) : () => {}}
                    options={DOWNSAMPLE_CHOICES.map((d) => ({
                      value: String(d), label: downsampleLabel(d),
                    }))}
                  />
                </span>
              </Field>
              <p className="text-[11px] text-dim leading-snug -mt-1">
                Higher = faster, less precise. 2x is a good speed/accuracy trade
                on a Pi.
              </p>

              <Field label="Only tag frames with enough stars">
                <input
                  className="field !w-24" type="number" min={0} step={1}
                  value={starsDraft} readOnly={!canEdit}
                  aria-disabled={!canEdit || undefined}
                  title={!canEdit ? LOCK : undefined}
                  aria-label="Minimum detected stars before solving"
                  onChange={(e) => setStarsDraft(e.target.value)}
                  onBlur={canEdit ? commitStars : undefined}
                />
              </Field>
              <p className="text-[11px] text-dim leading-snug -mt-1">
                0 turns this off. Skips the solve on cloud/trail frames that
                would fail anyway.
              </p>
            </div>
          )}
        </div>

        {err && <p className="text-[12px] text-warn">{err}</p>}

        <p className="text-[11px] text-dim leading-snug">
          One photo is worked out at a time in the background, so capturing
          never waits. With very short exposures it can fall behind — the
          oldest ones are then skipped and simply save without a position.
          Photos your imaging backend saves on its own machine are never
          tagged.
        </p>

        {!canEdit && (
          <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
            <Icon name="lock" size={11} /> Read-only — changing this needs{" "}
            {accessPhrase("config.site_optics")}.
          </p>
        )}
      </div>
    </Panel>
  );
}
