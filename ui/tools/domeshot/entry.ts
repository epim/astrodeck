// Bundle entry for the dome screenshot harness. Imports the SHIPPING modules,
// so what gets rendered is the code that runs on the rig rather than a copy of
// it that can drift.
//
// Relative paths, so this works from a clone anywhere.
import { drawDomeScope, TUBE_SCREEN } from "../../src/lib/domeScope";
import {
  DOME_TILT_DEG, domeExtent, projectAltAz,
} from "../../src/lib/domeProjection";

(window as unknown as { AD: unknown }).AD = {
  drawDomeScope, TUBE_SCREEN, DOME_TILT_DEG, domeExtent, projectAltAz,
};
