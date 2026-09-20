# Photosphere alignment audit

## Result

The coordinate convention passes independent landmark tests. Two capture-registration errors were corrected. Real-world stitching accuracy remains unqualified because lens geometry and heading are not calibrated on the Samsung S25 Ultra.

## Real-device failure after the audit

A portrait-only S25 Ultra scan in Brave produced a visibly scrambled panorama, reported as upside down. The screenshot is evidence of failure, not a successful calibration. The earlier synthetic tests did not establish that actual camera pixels and sensor orientations agree on this device. The inversion's cause is still unresolved; a blanket flip of the output would not establish correct sky coordinates.

Capture previously accepted images between target dots, including moving timestamped frames. It now requires a continuous steady hold even when timestamps exist, and only captures an uncompleted cell when its dot is within the aiming ring. A textured-overlap correlation check rejects strong disagreements before they can alter the image or advance coverage. It tolerates overall exposure changes, but is only a rejection heuristic: it does not align photographs, and cannot validate a first image or featureless sky. A consistent inversion across all frames can still escape it.

The review now offers an optional local alignment report: at most 16 small original camera images, projection bases, lens estimates, recent sensor readings, video dimensions and browser version. The first sample is retained; subsequent samples are bounded. The sensor reading is the latest received reading; the projection basis is the actual one used for that frame. It contains no location, authentication details or camera device IDs. Nothing is uploaded. These samples are needed to reproduce the device failure and distinguish sensor, lens, image-rotation and projection errors.

## First real-frame diagnosis

The supplied report contained two upright 180 × 320 camera images and upright sensor bases. SIFT matching followed by homography RANSAC retained 44 shared landmarks. Reprojecting them with the existing 60° short-axis assumption gave 36.6 px RMS error. Holding the reported sensor rotation fixed and fitting a shared focal length reduced error to 2.8 px at a 41.14° short-axis angle. Allowing relative rotation to vary instead gave 0.54 px at 36.52° with a 2.81° rotation correction. These are fits to one pair, not an intrinsic camera calibration: compass error and camera translation can confound the estimate. This pair did not reproduce the earlier upside-down full scan.

Camera view angle is now configurable before recording and saved locally per camera and crop. The dome, overlap comparison and mosaic receive the same lens model. A `cameraFov` setup-link parameter only offers a trial correction; it does not apply one automatically. A 41.1° correction is suitable for the next diagnostic trial on the same lens and zoom, not a new default for all hardware.

The overlap check now also compares projected local edges. Broad regions of similar color had yielded a misleading 0.748 correlation at the wrong angle; local-edge correlation was only 0.196. At 41.1°, those scores were 0.898 and 0.596. The incorrect pair is now rejected. This remains a heuristic and not visual stitching or proof of global alignment. Private images and full reports stay in the ignored local probe directory; only anonymous landmark coordinates are included in regression tests.

## Corrected

- **Image timing:** the old timer paired the currently displayed image with the newest phone orientation. Those can describe different directions during movement. Capture now runs on video-frame callbacks where supported and matches capture times to orientation history. When capture time is absent, a stable 500 ms interval is required before sampling. Movement includes roll, not only pointing direction. Old frames, stale sensor data and screen-rotation transitions are rejected.
- **Mixed sensor poses:** new relative tilt could be combined with an older absolute orientation. Capture now uses one coherent pose for image projection, bearing, altitude and coverage. Relative orientation can locate the shared zenith but cannot place azimuth-dependent image areas.
- **Overhead pixel:** the fallback used the centre pixel even when the camera was tilted several degrees from vertical. It now projects the zenith into that image and samples there. The resulting zenith row cannot be overwritten by a later side view. A manually aimed capture with no usable tilt still relies on the user's explicit upward aim.
- **Sampling aspect:** the original video aspect ratio now supplies lens geometry even after the image is reduced to a bounded canvas, avoiding small changes caused by rounding its dimensions.

## Independently checked

The expected axes and landmark image locations are specified separately from the production projection helpers.

- Upright portrait, both landscape orientations and upside-down portrait put east on image-right and elevation on image-top when looking north.
- East-facing, pitched, rolled and overhead poses remain orthonormal.
- Coloured landmarks at known image coordinates reach their expected panorama bearing and altitude.
- A landmark spanning north appears on both ends of the panorama without mirroring.
- Every dome cell fits inside a centred portrait or landscape capture.
- A delayed video frame registers at its historical direction, even when the latest phone pose faces elsewhere. The displayed dome uses that matched pose too.
- Unknown regions remain transparent and cannot become open sky through an empty sample.

The panorama and horizon editor both map azimuth horizontally from 0° to 360° and altitude vertically from 90° to −10°. The editor's image and point geometry share that mapping, to within image-pixel sampling precision.

Verification: 74 targeted automated checks passed, along with the production build. A Chrome check using a synthetic camera stream exercised actual video-frame callbacks: coverage advanced from 0% to 15%, captured cells turned green, and the partial panorama reached the editor with the northeast building on the correct side of north. This checks the browser capture path, not physical lens calibration. The private phone preview was verified to serve the rebuilt assets.

## Limits still requiring a real camera

The default 60° short-axis field of view is an estimate, not a camera calibration. The browser does not supply the calibrated focal length and distortion parameters used by this implementation. Different lenses, crop modes and stabilization can therefore stretch or shift overlapping views. The bounded rigid registration described below does not replace lens calibration, feature-based loop closure or global bundle adjustment. Nearby objects can also move relative to each other when the phone is translated around the observer.

Before treating this as an accurate surveying tool, capture a known roof edge or marked vertical pole with the S25 in both orientations, including overlapping views and a return to the starting direction. Compare the panorama against those references. Lens calibration and image-based registration remain separate work if that test shows displaced or doubled edges.

## Recovery and drift follow-up

The phone trial still rejected too many frames and showed a drifting grid. Rejection alone was not registration. A bounded visual registration step now searches small rigid rotations against the existing panorama before rejecting a textured overlap. It does not stretch the image or silently change the lens. The search has at most 72 evaluations, requires a material improvement in both edge and brightness agreement, and cannot change the existing panorama. The accepted correction is also applied to the displayed dome. Returning to a captured green patch can re-establish image alignment without inventing coverage. Corrections more than 10° from the sensor frame are not accepted.

Where both orientation streams are available, relative motion is anchored to a nearby absolute reading once. Subsequent absolute compass changes do not repeatedly rotate the scan. Loss of the relative stream results in stale-pose waiting rather than a silent switch to a different reference. This reduces one source of drift; it is not SLAM or a guarantee of a perfectly anchored overlay. Without a capture timestamp, display latency and remaining lens error can still make references slide during motion.

Replaying the supplied real pair at 41.1° improved edge agreement from 0.596 to 0.790 after about 2° of rigid correction. Adding synthetic ±3° yaw and ±1° pitch offsets to the same recorded pose converged to the same match. The bounded search took roughly 15–27 ms on the development PC; Android performance still needs measurement. Synthetic tests recover yaw/pitch/roll errors, reject an unrelated image, leave blank sky unregistered, and verify relative motion does not follow later compass jumps.

The editor previously compressed 360° horizontally to the phone width while retaining a fixed tall image. It now uses equal horizontal and vertical angular scale at the default zoom (1040 px wide, 300 px tall including the editor margins). A Look around slider pans the view on touchscreens; zoom scales both dimensions. Spherical unrolling can still curve straight-looking scene edges and cannot remove near-object parallax.

## References

- [W3C Device Orientation and Motion](https://www.w3.org/TR/orientation-event/): device axes and Z-X-Y rotations.
- [W3C Screen Orientation](https://www.w3.org/TR/screen-orientation/): display rotation relative to natural device orientation.
- [Video frame callback specification](https://wicg.github.io/video-rvfc/): frame metadata and optional capture timestamps.
