"""Advisory comparisons with the frames already on disk. Never gates capture."""
from __future__ import annotations

import asyncio
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import gallery

FIELDS = ("width", "height", "bin_x", "bin_y", "exposure_s")


def positive_number(value, *, integer=False, allow_zero=False):
    """FITS cards are untrusted; missing/invalid values stay explicitly unknown."""
    try:
        number = float(value)
        if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
            return None
        if integer:
            return int(number) if number.is_integer() else None
        return number
    except (TypeError, ValueError, OverflowError):
        return None


def _name(value):
    return str(value or "").strip().casefold()


def geometry_groups(rows):
    """Full filtered-set counts, independent of the listing's page size."""
    groups = {}
    for row in rows:
        geometry = tuple(positive_number(row.get(f), integer=f != "exposure_s",
                                         allow_zero=f == "exposure_s") for f in FIELDS)
        key = (*(_name(row.get(f)) for f in ("target", "filter", "frame_type")), *geometry)
        if key not in groups:
            groups[key] = {f: str(row.get(f) or "").strip() for f in ("target", "filter", "frame_type")}
            groups[key].update(zip(FIELDS, geometry))
            groups[key].update(count=0, bytes=0)
        groups[key]["count"] += 1
        groups[key]["bytes"] += row.get("bytes", 0)
    return sorted(groups.values(), key=lambda g: (
        _name(g["target"]), _name(g["filter"]), _name(g["frame_type"]),
        -g["count"], *(g[f] or 0 for f in FIELDS)))


def describe(group):
    dims = f'{group["width"]} x {group["height"]} px' if group.get("width") and group.get("height") else "size unknown"
    bins = f'bin {group["bin_x"]} x {group["bin_y"]}' if group.get("bin_x") and group.get("bin_y") else "binning unknown"
    exposure = f'{group["exposure_s"]:g}s' if group.get("exposure_s") is not None else "exposure unknown"
    return f"{dims}, {bins}, {exposure}"


def _bank(groups, target, filt):
    return [g for g in groups if _name(g["target"]) == _name(target)
            and _name(g["filter"]) == _name(filt) and _name(g["frame_type"]) == "light"]


def plan_warnings(plan, groups):
    warnings = []
    seen = set()
    for target in plan.targets:
        if target.calibration:
            continue
        for step in target.steps:
            # None means KEEP the wheel's current position, not a named filter.
            if step.filter is None or _name(step.frame_type) != "light":
                continue
            key = (_name(target.name), _name(step.filter), step.binning, step.exposure_s)
            if key in seen:
                continue
            seen.add(key)
            bank = _bank(groups, target.name, step.filter)
            if not bank:
                continue
            modal = max(bank, key=lambda g: g["count"])
            mismatch = any(modal.get(f) is not None and modal[f] != value for f, value in (
                ("bin_x", step.binning), ("bin_y", step.binning), ("exposure_s", step.exposure_s)))
            unknown = any(g.get(f) is None for g in bank for f in FIELDS)
            if len(bank) > 1 or mismatch or unknown:
                reasons = []
                if len(bank) > 1:
                    reasons.append(f"{len(bank)} capture groups already exist")
                if mismatch:
                    reasons.append("the planned settings differ from the largest group")
                if unknown:
                    reasons.append("some saved frames have incomplete metadata")
                warnings.append(
                    f"{target.name} / {step.filter}: {'; '.join(reasons)}. "
                    f"Planned: bin {step.binning} x {step.binning}, {step.exposure_s:g}s. "
                    f"Largest saved group: {modal['count']} frames at {describe(modal)}. "
                    "Capture can continue; calibrate and stack incompatible groups separately. "
                    "Review saved frame sizes in the gallery.")
    return warnings


def frame_warning(groups, target, step, info):
    """Compare actual output dimensions; sensor/bin division is not reliable."""
    if target.calibration or step.filter is None or _name(step.frame_type) != "light":
        return None
    # A bridge may return a resized rendered preview instead of sensor pixels.
    # Its dimensions cannot establish the geometry of the saved FITS frame.
    if info.get("data_is_linear") is False or info.get("saved_local") is False:
        return None
    bank = _bank(groups, target.name, step.filter)
    if not bank:
        return None
    width = positive_number(info.get("data_width"), integer=True)
    height = positive_number(info.get("data_height"), integer=True)
    if width is None or height is None:
        return None
    same_settings = [g for g in bank if g["bin_x"] == step.binning and g["bin_y"] == step.binning
                     and g["exposure_s"] == step.exposure_s and g["width"] and g["height"]]
    if not same_settings:
        return None  # Binning/exposure differences were covered before acquisition.
    modal = max(same_settings, key=lambda g: g["count"])
    if (width, height) != (modal["width"], modal["height"]):
        return (f"{target.name} / {step.filter}: captured {width} x {height} px; "
                f"the largest saved group at these settings is {describe(modal)}. "
                "Capture continues. Keep different frame sizes in separate calibration and stacking groups.")
    return None


# One worker coalesces repeated editor compiles. Timing out a caller does not
# cancel the shared scan or enqueue another full-library scan. No disk I/O on
# the server's event loop, and no dependence on a connected camera.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="capture-geometry")
_jobs = {}
_lock = threading.Lock()
_TTL_S = 5.0
PENDING_NOTE = "Capture geometry check is still reading the library. Saved-frame compatibility has not been verified; check the gallery before combining frames."


def _read_inventory(root):
    rows, truncated = gallery.scan(root)
    return geometry_groups(rows), truncated, time.monotonic()


async def inventory(*, timeout=1.0, refresh=True):
    root = gallery.capture_root()
    with _lock:
        job = _jobs.get(root)
        if job is not None and job.done():
            if job.exception() is not None or (refresh and time.monotonic() - job.result()[2] > _TTL_S):
                job = None
        if job is None:
            for key in list(_jobs):
                if key != root and _jobs[key].done():
                    del _jobs[key]
            job = _executor.submit(_read_inventory, root)
            _jobs[root] = job
    try:
        wrapped = asyncio.wrap_future(job)
        # A timed-out caller leaves the shared scan running. Observe a later
        # failure as well, so asyncio does not report an unhandled exception.
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        groups, truncated, _ = await asyncio.wait_for(
            asyncio.shield(wrapped), timeout=timeout)
        note = "Capture geometry check covers only part of the library; the scan limit was reached." if truncated else None
        return groups, note
    except TimeoutError:
        return [], PENDING_NOTE
    except Exception:
        return [], "Capture geometry check could not read the library. Saved-frame compatibility has not been verified."
