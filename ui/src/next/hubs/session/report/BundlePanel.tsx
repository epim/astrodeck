// BundlePanel.tsx - the stacking bundle: what tonight would export, the .zip,
// and the advanced options behind a disclosure.
//
// PROGRESSIVE DISCLOSURE ON PURPOSE. A novice never sees a layout picker, a
// weight cutoff or a folder-on-the-rig button: the one-click .zip is the whole
// novice path and its URL is unchanged, because `bundleQuery` omits every
// default. Everything else lives inside `ADVANCED`.
//
// FOUR DIFFERENT TRUTHS, FOUR DIFFERENT SENTENCES, and the reason each control
// is locked is the one that applies:
//   * you lack `control.capture`            -> a LockNote, the RBAC shape
//   * the preview request failed            -> a warn note, with the RETRY
//   * no frames were captured               -> `bundleDisabledReason`
//   * the FITS are not on this machine      -> `materializeDisabledReason`
// Collapsing them was how the legacy panel ended up saying "Still loading this
// session's bundle preview." forever after one dropped request.

import type { JSX } from "react";
import {
  ActionButton, Card, Disclosure, Label, LockNote, Mono, NumberField, Pill,
  Segmented, Switch,
} from "../../../ui";
import { NxIcon } from "../../../icons";
import { u } from "../../../../lib/base";
import { useCanControlCapture } from "../../../../lib/caps";
import { explainLock } from "../../../shell/explain";
import {
  bundleDisabledReason, bundleQuery, layoutOptions, masterChips,
  materializeSummary, relayoutDirs, type BundleLayout,
} from "../../../../lib/bundleView";
import type { BundleGroupSummary, BundleMaterializeResult, BundlePreview } from "../../../../types";
import type { BundleOptionsState } from "./useReportData";
import {
  groupCountLine, groupLine, keptLine, MATERIALIZE_CAP_REASON,
  materializePreviewReason, materializeReason, REPORT_COPY,
} from "./reportModel";

/** One group line: identity, counts, and a chip per calibration master. The
 *  chip carries a GLYPH and the WORD, never the tone alone - "flat" missing is
 *  the difference between a stack that calibrates and one that does not. */
function BundleGroupRow({ g, testid }: { g: BundleGroupSummary; testid: string }): JSX.Element {
  return (
    <div className="nx-report-group" data-testid={testid}>
      <Mono size={10.5}>{groupLine(g)}</Mono>
      <Mono size={10} tone="dim">{groupCountLine(g)}</Mono>
      <span className="nx-report-masters">
        {masterChips(g.masters).map((c) => (
          <Pill
            key={c.kind}
            tone={c.ok ? "good" : "dim"}
            glyph={<NxIcon name={c.ok ? "check" : "x"} size={11} />}
            ariaLabel={c.ok ? `${c.kind} master matched` : `no ${c.kind} master`}
          >
            {c.kind}
          </Pill>
        ))}
      </span>
    </div>
  );
}

/** The advanced options. Rendered only when the bundle itself is meaningful -
 *  a layout picker over a session with no frames is a control with a promise. */
function BundleAdvanced({
  preview, previewLoading, previewErr, retryPreview, bundle,
  framesCaptured, canCapture, materializing, matResult, onMaterialize,
}: {
  preview: BundlePreview | null;
  previewLoading: boolean;
  previewErr: string | null;
  retryPreview: () => void;
  bundle: BundleOptionsState;
  framesCaptured: number;
  canCapture: boolean;
  materializing: boolean;
  matResult: BundleMaterializeResult | null;
  onMaterialize: () => void;
}): JSX.Element {
  const dirs = relayoutDirs(preview, bundle.layout);
  const hint = layoutOptions().find((o) => o.value === bundle.layout)?.hint ?? "";
  const capReason = canCapture ? null : MATERIALIZE_CAP_REASON;
  const matReason = capReason
    ?? (previewErr
      ? materializePreviewReason(previewErr)
      : materializeReason(framesCaptured, preview));

  return (
    <Disclosure
      summary={REPORT_COPY.advancedSummary}
      sub={REPORT_COPY.advancedSub}
      data-testid="report-bundle-advanced"
    >
      <div className="nx-report-fields">
        {/* ----------------------------------------------------------- layout */}
        <Segmented<BundleLayout>
          label={REPORT_COPY.layoutLabel}
          data-testid="report-bundle-layout"
          value={bundle.layout}
          onChange={bundle.setLayout}
          options={layoutOptions().map((o) => ({ value: o.value, label: o.label, sub: o.hint }))}
        />
        <p className="nx-report-note">{hint}</p>
        {dirs.length > 0 && (
          <Mono size={10} tone="dim" data-testid="report-bundle-dir">{`${dirs[0]}/...`}</Mono>
        )}

        {/* ------------------------------------------------------- weighting */}
        <Switch
          data-testid="report-bundle-weightalt"
          checked={bundle.weightAlt}
          onChange={bundle.setWeightAlt}
          label={REPORT_COPY.weightAltLabel}
          note={REPORT_COPY.weightAltNote}
        />

        {/* ---------------------------------------------------- keep cutoff */}
        <Switch
          data-testid="report-bundle-keepon"
          checked={bundle.keepOn}
          onChange={bundle.setKeepOn}
          label={REPORT_COPY.keepLabel}
          note={REPORT_COPY.keepNote}
        />
        {bundle.keepOn && (
          <NumberField
            data-testid="report-bundle-keep"
            label={REPORT_COPY.keepFieldLabel}
            ariaLabel="Keep threshold, normalized weight"
            value={bundle.keepThreshold}
            onCommit={bundle.setKeepThreshold}
            min={0}
            max={1}
            step={0.05}
            hint={previewLoading ? REPORT_COPY.recounting : undefined}
          />
        )}

        {/* ------------------------------------------------------ materialize */}
        <p className="nx-report-note">{REPORT_COPY.materializeBlurb}</p>
        <p className="nx-report-note" data-tone="warn">{REPORT_COPY.materializeDanger}</p>
        {/* The RBAC sentence is the panel's one `LockNote`, above this group.
            What can still be true HERE is a data truth - no frames, no local
            subs, a preview that failed - and that is not read-only-ness. */}
        {capReason == null && matReason && (
          <p className="nx-report-note" data-tone="warn" data-testid="report-bundle-matreason">
            {matReason}
          </p>
        )}
        <div className="nx-report-actions">
          {previewErr && (
            <ActionButton
              kind="ghost"
              onPress={retryPreview}
              data-testid="report-bundle-retry"
              glyph={<NxIcon name="refresh" size={14} />}
            >
              {REPORT_COPY.retryPreview}
            </ActionButton>
          )}
          <ActionButton
            kind="secondary"
            data-testid="report-bundle-materialize"
            busy={materializing}
            lockedReason={matReason}
            onExplain={explainLock}
            onPress={onMaterialize}
            glyph={<NxIcon name="download" size={14} />}
          >
            {materializing ? REPORT_COPY.materializeBusy : REPORT_COPY.materializeLabel}
          </ActionButton>
        </div>
        {matResult && (
          <div className="nx-report-block" data-testid="report-bundle-result">
            <p className="nx-report-note" data-tone="good">{materializeSummary(matResult)}</p>
            <Mono size={10} tone="dim" className="nx-report-break">{matResult.export_dir}</Mono>
            {matResult.failed.map((f, i) => (
              <p className="nx-report-note nx-report-break" data-tone="warn" key={i}>
                {`${f.src}: ${f.reason}`}
              </p>
            ))}
          </div>
        )}
      </div>
    </Disclosure>
  );
}

export function BundlePanel({
  reportId, framesCaptured, preview, previewLoading, previewErr, retryPreview,
  bundle, materializing, matResult, onMaterialize,
}: {
  reportId: string;
  framesCaptured: number;
  preview: BundlePreview | null;
  previewLoading: boolean;
  previewErr: string | null;
  retryPreview: () => void;
  bundle: BundleOptionsState;
  materializing: boolean;
  matResult: BundleMaterializeResult | null;
  onMaterialize: () => void;
}): JSX.Element {
  const canCapture = useCanControlCapture();
  const zipReason = bundleDisabledReason(framesCaptured, preview);
  const kept = keptLine(preview);
  const query = bundleQuery({
    layout: bundle.layout, weightAlt: bundle.weightAlt, keepThreshold: bundle.keepParam,
  });
  const zipHref = u(`/api/reports/${encodeURIComponent(reportId)}/bundle.zip${query}`);

  return (
    <Card data-testid="report-bundle">
      <div className="nx-report-block">
        <Label size={11}>STACKING BUNDLE</Label>
        <p className="nx-report-note">{REPORT_COPY.bundleBlurb}</p>
        {/* One read-only sentence for the whole panel, rendered where a viewer
            sees it without opening ADVANCED. `LockNote` returns null when the
            reason is null, so there is no ternary and no stray `false`. */}
        <LockNote
          reason={canCapture ? null : MATERIALIZE_CAP_REASON}
          data-testid="report-bundle-lock"
        />

        {preview && preview.groups.length > 0 && (
          // These rows still carry the counts computed for the PREVIOUS
          // options while a new preview is in flight. The dimming is paired
          // with the word below, never left as the only signal.
          <div className="nx-report-stale" data-stale={previewLoading ? "true" : "false"}>
            {preview.groups.map((g, i) => (
              <BundleGroupRow key={`${g.dir}-${i}`} g={g} testid={`report-bundle-group-${i}`} />
            ))}
          </div>
        )}

        {previewErr && (
          <>
            <p className="nx-report-note" data-tone="warn" data-testid="report-bundle-previewerr">
              {REPORT_COPY.previewError(previewErr)}
            </p>
            <div className="nx-report-actions">
              <ActionButton
                kind="ghost"
                onPress={retryPreview}
                data-testid="report-bundle-retry-top"
                glyph={<NxIcon name="refresh" size={14} />}
              >
                {REPORT_COPY.retryPreview}
              </ActionButton>
            </div>
          </>
        )}

        {preview?.warnings.map((w, i) => (
          <p className="nx-report-note" data-tone="warn" key={i} data-testid={`report-bundle-warning-${i}`}>
            {w}
          </p>
        ))}

        {kept && (
          // Informational, not a problem report - an amber line here read as
          // "photos were thrown away".
          <p className="nx-report-note nx-report-stale" data-stale={previewLoading ? "true" : "false"}
            data-testid="report-bundle-kept">
            {previewLoading ? `${kept} (${REPORT_COPY.recounting})` : kept}
          </p>
        )}

        {zipReason == null && (
          <BundleAdvanced
            preview={preview}
            previewLoading={previewLoading}
            previewErr={previewErr}
            retryPreview={retryPreview}
            bundle={bundle}
            framesCaptured={framesCaptured}
            canCapture={canCapture}
            materializing={materializing}
            matResult={matResult}
            onMaterialize={onMaterialize}
          />
        )}

        <div className="nx-report-actions">
          {zipReason ? (
            <ActionButton
              kind="secondary"
              data-testid="report-bundle-zip"
              lockedReason={zipReason}
              onExplain={explainLock}
              onPress={() => { /* locked: the press states the reason */ }}
              glyph={<NxIcon name="download" size={14} />}
            >
              {REPORT_COPY.zipLabel}
            </ActionButton>
          ) : (
            <a className="nx-btn" data-kind="secondary" data-testid="report-bundle-zip"
              href={zipHref} download>
              <span className="nx-btn-glyph"><NxIcon name="download" size={14} /></span>
              <span className="nx-btn-label">{REPORT_COPY.zipLabel}</span>
            </a>
          )}
        </div>
        {!canCapture && (
          // The lock above could be read as locking the download too. It does
          // not: the .zip is a read, and every signed-in role can take it.
          <Mono size={10} tone="dim" data-testid="report-bundle-zip-note">
            the .zip is a download; only writing a folder on the rig is locked
          </Mono>
        )}
      </div>
    </Card>
  );
}
