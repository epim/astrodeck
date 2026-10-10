// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The shipped scanner, `PhotosphereSweep`, behind the replay's seam.
//
// Everything `replay.ts` used to read off the sweep directly is read here
// instead, through the sweep's public surface and in the order it was read
// before: the per-frame report, the horizon from the columns, the `columns.json`
// rows, the cell count for `summary.json`, and the mapping of the sweep's
// capture log onto the snake_case records of `captures.jsonl`. Moving them
// changes where the code lives and nothing about what it writes - the replay's
// proof is that the legacy result files stay byte for byte what they were.
//
// The options a case carries (`focal_prior_scale`, `sensor_only`,
// `declination_deg`) are the panorama scanner's. The sweep has no such
// settings, so they are not applied, and replaying a case that sets them
// through `legacy` measures the old scanner on that case as it always would.
import { PANO_H, PANO_W } from '../pano/types';
import { PhotosphereSweep, traceSweep } from '../photosphere';
import type {
  Diagnostics, FrameReport, HorizonOutV1, LiveSnapshot, ScannerFactory, ScannerUnderTest,
} from './scannerUnderTest';

class LegacyScanner implements ScannerUnderTest {
  private readonly sweep = new PhotosphereSweep();

  start(video: HTMLVideoElement, canvas: HTMLCanvasElement): Promise<void> {
    return this.sweep.start(video, canvas);
  }

  /** `begin()` refuses until the sweep has a bearing, and this is the same gate
   *  the Start button is behind in the UI. */
  get canBegin(): boolean { return this.sweep.compassReady; }

  begin(): void { this.sweep.begin(); }

  get isRecording(): boolean { return this.sweep.isRecording; }

  frameReport(): FrameReport {
    const basis = this.sweep.cameraBasis;
    return {
      basis: basis && { right: basis.right, up: basis.up, forward: basis.forward },
      cue: this.sweep.captureCue,
      aim: this.sweep.aimTarget?.id ?? null,
      compassReady: this.sweep.compassReady,
      tiltReady: this.sweep.tiltReady,
      frameCount: this.sweep.frameCount,
    };
  }

  /** The sweep has no finish-time work: its mosaic is current after every frame. */
  finishScan(): void { }

  panorama(): { width: 1080; height: 300; pixels: Uint8ClampedArray } | null {
    const mosaic = this.sweep.panoramaPixels;
    if (!mosaic) return null;
    // A mosaic of another size would still encode, and the scorer's raster
    // mapping would then read every direction wrong while the file looked fine.
    if (mosaic.width !== PANO_W || mosaic.height !== PANO_H)
      throw new Error(`the scanner's mosaic is ${mosaic.width} x ${mosaic.height}, not the `
        + `${PANO_W} x ${PANO_H} raster the result contract defines`);
    return { width: PANO_W, height: PANO_H, pixels: mosaic.pixels };
  }

  horizon(): HorizonOutV1 {
    const columns = this.sweep.columns();
    // The same entry the app saves through (issue #129): a scan whose lens was
    // in doubt publishes nothing certain.
    const trace = traceSweep({ columns: () => columns, lensDoubtedThisScan: this.sweep.lensDoubtedThisScan });
    return { bins: columns.length, points: trace.points, uncertain_bins: trace.uncertainBins };
  }

  firstSeen(): Uint16Array | null { return null; }
  diagnostics(): Diagnostics | null { return null; }
  liveSnapshot(): LiveSnapshot | null { return null; }

  captureLog(): Record<string, unknown>[] {
    return this.sweep.captureLog.map(record => ({
      at: record.at,
      outcome: record.outcome,
      ...(record.cell === undefined ? {} : { cell: record.cell }),
      ...(record.basis === undefined ? {} : { basis: record.basis }),
      ...(record.sensorBasis === undefined ? {} : { sensor_basis: record.sensorBasis }),
      ...(record.adjusted === undefined ? {} : { adjusted: record.adjusted }),
      // The four fields an `alignment-wait` carries (issue #76): which term
      // refused, which source was missing when that term was `no-pose`, the
      // separation the `separation` term measured, and the magnitude of the
      // carried visual anchor. Written only where the scanner set them, like
      // every field above, so a record that measured nothing claims nothing.
      ...(record.wait === undefined ? {} : { wait: record.wait }),
      ...(record.separation === undefined ? {} : { separation: record.separation }),
      ...(record.anchor === undefined ? {} : { anchor: record.anchor }),
      ...(record.gap === undefined ? {} : { gap: record.gap }),
      // What an `overlap-wait` decided on (issue #130): which term refused,
      // the two correlations, the sample count, and whether registration
      // searched before refusing.
      ...(record.overlapTerm === undefined ? {} : { overlap_term: record.overlapTerm }),
      ...(record.correlation === undefined ? {} : { correlation: record.correlation }),
      ...(record.featureCorrelation === undefined ? {} : { feature_correlation: record.featureCorrelation }),
      ...(record.samples === undefined ? {} : { samples: record.samples }),
      ...(record.searched === undefined ? {} : { searched: record.searched }),
    }));
  }

  cells(): { total: number; covered: number } {
    const cells = this.sweep.cells;
    return { total: cells.length, covered: cells.filter(cell => cell.captured).length };
  }

  /** The contract's `columns.json` is the bin-centre luminance column, one
   *  row per degree - the array issue #58 quotes. The tracer now reads five
   *  columns across each bin, in two channels; recording that instead would
   *  silently change what every cached column in the cache means. */
  legacyColumns(): (number | null)[][] {
    return this.sweep.centreColumns().map(column => column.map(value => (Number.isFinite(value) ? value : null)));
  }

  stop(): void { this.sweep.stop(); }
}

export const legacyFactory: ScannerFactory = { create: () => new LegacyScanner() };
