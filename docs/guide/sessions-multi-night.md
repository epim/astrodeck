# Keep progress across nights and download frames

These steps use the alternative interface. Open `#/next`, then open **SESSION** > **GALLERY** (`#/session/gallery`). The server root opens the classic interface; see [Interface routes](next-ui.md) if your screen looks different.

<a id="what-a-session-is"></a>

<a id="accepted-frame-quotas-count-modes"></a>

## Understand the session

A session records the plan and captured-frame progress across nights. A dormant session can be resumed. New target blocks count accepted subs, so rejected frames can remain on disk without satisfying the quota. Older saved targets can retain the previous count mode.

<a id="resuming--manual-and-auto-at-dusk"></a>

<a id="resuming-manual-and-auto-at-dusk"></a>

<a id="manual-resume"></a>

<a id="reviewing-and-regrading-frames"></a>

## Review and resume

1. Select the session in the gallery and inspect its status and accepted counts. Use **MORE** for its available actions; unavailable actions show their reason.

2. Use **REPORT** when a report exists. For per-session frame review, open **PLAN EDITOR** from the flow library, find **SESSIONS**, then choose **review frames**. Review rejected frames before changing a quota or restarting.

3. Choose **RESUME** on a dormant session to continue its remaining work. This is different from re-running a plan from the start.

<a id="update-from-plan"></a>

## Apply a changed plan

Load and edit the intended plan first. On its dormant session, choose **UPDATE FROM PLAN** and read the merge preview: matched steps keep progress, new steps start at zero, and dropped steps stop counting toward quotas while their frames remain in the session log.

<a id="auto-resume-at-dusk"></a>

<a id="sessions--multi-night-imaging"></a>

## Arm a later night

Use **AUTO-RESUME OFF** to arm a dormant session; the enabled action reads **AUTO-RESUME ON**. Read the warning if no safety monitor is connected. Complete [Unattended nights](unattended-nights.md) before leaving it armed.

<a id="getting-your-frames-out-the-stacking-bundle"></a>

<a id="advanced-bundle-options"></a>

## Get your frames out

1. Open the files sheet at `#/session/gallery/files?src=manual` for manual captures, or use the current run's files action. Choose the session or manual source before selecting files.

2. Choose **FITS originals** for the saved data or **JPEG previews** for display images. Select the required files and use the download button, whose label gives the selected count and size.

3. **DOWNLOAD BUNDLE** provides the report-backed manifest and build script; the photos stay on the rig. Read its missing-file and calibration warnings. It does not replace downloading the FITS originals.

4. Keep originals backed up. A materialized server-side bundle can use hard links, so editing a linked FITS file in place also edits the original. Deleting an export link does not by itself delete the original file's other link. Treat materialized trees as read-only.

<a id="what-survives-a-reboot"></a>

## After a reboot or cleanup

Session logs and saved files survive a server restart on persistent storage. Check the recovered session and equipment before resuming; an interrupted run is not proof that the hardware returned to a safe position.

**ABANDON** removes a session from the active panel but keeps its log and thumbnails. **DELETE** removes the session log and thumbnails; saved FITS frames are not deleted by that action. Read the confirmation before using either.

## Related

[Flows and mosaics](flows-and-mosaics.md) · [Monitor](monitor.md) · [Unattended nights](unattended-nights.md)

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
