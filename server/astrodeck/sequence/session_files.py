"""Per-session files index with grades (Session hub, task S5).

WHAT THIS EXISTS FOR. ``GET /api/sessions/{id}`` already returns the whole
ledger, but it is a ledger: one flat list of ``SessionFrame`` rows carrying
``step_id`` and an open metrics dict, with no filter name, no file size and no
verdict on it. Turning that into "how many L subs did I keep, and how much disk
did they cost" is a plan join plus a stat() per frame, and every client that
wanted the answer would have to do both -- including one that is not allowed to
see the plan's on-disk paths at all.

So the fold happens HERE, once, server-side, and what leaves is the answer:
per-filter counts, accepted counts, bytes and integration, each carrying the
frame rows underneath so the operator can regrade one. Two consequences are
deliberate:

* **No path leaves this module, in any form.** Not absolute, not
  capture-root-relative. ``bytes`` is the only thing the on-disk file
  contributes, and a thumbnail is offered as a URL onto the existing
  ``/api/sessions/{id}/frames/{frame_id}/thumb`` route rather than a location.
  ``_redact_session_for`` exists because the ledger route DID hand out paths;
  this payload never has one to redact.
* **The frame ``id`` is the ledger id**, so a row here is the argument to
  ``PATCH /api/sessions/{id}/frames/{frame_id}``. An index whose rows cannot be
  acted on would just be a second way to read the same numbers.

The verdict shown is ``SessionFrame.effective()`` -- override if set, else
``auto_accepted`` -- which is the SAME method ``Session.accepted_by_step()``
folds, so this index and the resume quota can never disagree about whether a
frame counted.
"""
from __future__ import annotations

import os
from statistics import median

from .models import ExposureStep
from .session import Session, SessionFrame, session_store

#: Filter bucket for frames whose step is no longer in the plan. A dormant
#: session's plan is editable (PATCH /api/sessions/{id} with a new plan), and
#: dropping a step does NOT delete the frames it already produced -- they stay
#: in the ledger with a dangling ``step_id``. They are still frames on disk that
#: cost bytes, so they are still listed; they just have no filter to list under.
#: Distinct from ``""``, which is a step that genuinely names no filter (a mono
#: rig with no wheel, or an OSC): that is a known answer, this is an unknown one.
UNKNOWN_FILTER = "?"


def _thumb_ids(session_id: str) -> set[str]:
    """Frame ids that have a rendered thumbnail on disk.

    One directory listing for the whole session rather than a stat per frame,
    and it asks the FILESYSTEM rather than trusting ``SessionFrame.thumb``:
    that field is stamped by a fire-and-forget render task, and a thumb dir
    removed by hand would otherwise put a URL in the payload that 404s."""
    try:
        tdir = session_store.thumbs_dir(session_id)
        return {p.stem for p in tdir.glob("*.jpg")}
    except (KeyError, OSError):
        return set()


def _frame_bytes(path: str) -> int:
    """Size on disk, or 0.

    0 covers every way a frame can have no measurable file: never saved
    (``path == ""``), saved by NINA on another host, moved to trash, or deleted
    since. None of those is an error worth failing the index over -- the index
    is a summary, and a summary that 500s because one frame was tidied away is
    useless the night it matters."""
    if not path:
        return 0
    try:
        return int(os.stat(path).st_size)
    except OSError:
        return 0


def _frame_exposure(step: ExposureStep | None, frame: SessionFrame) -> float:
    """Seconds this one frame integrated for.

    The ledger does not record an exposure per frame -- it records the step that
    produced it, and the step carries the exposure. When the step is gone the
    metrics dict is the only remaining place an exposure could be (it is an open
    float dict precisely so an external grader can add keys), and when it is not
    there either the honest answer is 0: unknown integration must not be counted
    as integration."""
    if step is not None:
        return float(step.exposure_s)
    value = frame.metrics.get("exposure_s")
    return float(value) if value is not None else 0.0


def _target_label(session: Session) -> str:
    """What this session is OF, in one line.

    One target names itself. A multi-target plan has no single answer, so the
    session's own name (which the engine seeds from the plan name) is used
    rather than picking one target and implying the others are not there."""
    names = [t.name for t in session.plan.targets if t.name]
    if len(names) == 1:
        return names[0]
    return session.name or session.plan.name or ", ".join(names)


def files_index(session: Session) -> dict:
    """The per-filter files index for one session (route payload, verbatim).

    Ordering is the plan's: filters appear in the order their first step does,
    with any dangling-step bucket last; frames within a filter are oldest-first,
    which is the order they were shot and the order a reviewer walks them.
    """
    steps: dict[str, ExposureStep] = {
        st.id: st for t in session.plan.targets for st in t.steps}

    # Seeded from the plan so a filter with zero frames so far still shows (an
    # index that hides the channel you have not started is not an index of the
    # plan), and so the ORDER is the plan's rather than whichever filter the
    # scheduler happened to shoot first.
    order: list[str] = []
    groups: dict[str, dict] = {}

    def _bucket(key: str, step: ExposureStep | None) -> dict:
        g = groups.get(key)
        if g is None:
            order.append(key)
            g = groups[key] = {"filter": key, "step": step,
                               "rows": [], "exposures": []}
        elif g["step"] is None and step is not None:
            g["step"] = step
        return g

    for t in session.plan.targets:
        for st in t.steps:
            _bucket(st.filter or "", st)

    thumbs = _thumb_ids(session.id)
    for frame in sorted(session.frames, key=lambda f: f.ts):
        step = steps.get(frame.step_id)
        key = (step.filter or "") if step is not None else UNKNOWN_FILTER
        g = _bucket(key, step)
        exposure = _frame_exposure(step, frame)
        accepted = frame.effective()
        stars = frame.metrics.get("stars")
        g["exposures"].append(exposure)
        g["rows"].append({
            "id": frame.id,
            "ts": frame.ts,
            "bytes": _frame_bytes(frame.path),
            "accepted": accepted,
            "override": frame.override,
            "hfr": frame.metrics.get("hfr"),
            "stars": int(stars) if stars is not None else None,
            "guide_rms": frame.metrics.get("guide_rms"),
            "thumb": (f"/api/sessions/{session.id}/frames/{frame.id}/thumb"
                      if frame.id in thumbs else None),
            "_integration": exposure if accepted else 0.0,
        })

    by_filter: list[dict] = []
    for key in order:
        g = groups[key]
        rows = g["rows"]
        step: ExposureStep | None = g["step"]
        if step is not None:
            # The FIRST step in plan order for this filter. A plan may hold two
            # steps on one filter at different exposures; this field is the
            # headline ("L, 120 s"), while integration_s below is summed from
            # each frame's OWN step and so stays right whatever the mix.
            exposure_s = float(step.exposure_s)
        else:
            # No step to ask, so the frames themselves are the only evidence;
            # median rather than mean so one mis-stamped frame cannot move it.
            exposure_s = float(median(g["exposures"])) if g["exposures"] else 0.0
        integration = float(sum(r.pop("_integration") for r in rows))
        by_filter.append({
            "filter": key,
            "count": len(rows),
            "accepted": sum(1 for r in rows if r["accepted"]),
            "exposure_s": exposure_s,
            "bytes": sum(r["bytes"] for r in rows),
            "integration_s": integration,
            "frames": rows,
        })

    return {
        "target": _target_label(session),
        "totals": {
            "frames": sum(g["count"] for g in by_filter),
            "accepted": sum(g["accepted"] for g in by_filter),
            "bytes": sum(g["bytes"] for g in by_filter),
            "integration_s": float(sum(g["integration_s"] for g in by_filter)),
        },
        "by_filter": by_filter,
    }


def active_session() -> Session | None:
    """The session a run is writing to right now, or None.

    ``active`` is set by ``SequenceEngine.start`` and cleared by the run's
    ending; ``SessionStore.boot_sweep`` demotes any left over from a crash, so
    a stale file cannot masquerade as a live run after a restart. Most recently
    updated wins defensively -- there should only ever be one.
    """
    live = [s for s in session_store.load_all() if s.status == "active"]
    live.sort(key=lambda s: s.updated_ts, reverse=True)
    return live[0] if live else None
