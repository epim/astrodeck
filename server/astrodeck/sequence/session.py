# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Multi-night Session entity + store (sessions spec §2).

One JSON file per session under ``CAPTURE_DIR/sessions/<id>.json`` (written
atomically, no ``.bak`` on those writes — the file churns every frame like the
retired resume file; the one ``.bak`` is ``backup``'s, taken before ADOPT
rewrites a ledger's step ids); thumbnails under
``CAPTURE_DIR/sessions/<id>/thumbs/<frame_id>.jpg``.
``SessionStore`` mirrors ``plans.PlanLibrary``: ``safe_id_path`` escape guard,
soft quota with oldest complete/abandoned pruned first (dormant/active NEVER
pruned). ``migrate_legacy_resume`` folds the retired single-slot
``.sequence_resume.json`` into a dormant Session exactly once at boot.
"""
from __future__ import annotations

import copy
import json
import math
import re
import shutil
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import pydantic_core
from pydantic import BaseModel, Field, ValidationError

from .. import hub as _hubmod
from ..events import night_key
from ..persist import (harden_private_file, list_json, read_json,
                       read_json_or, safe_id_path, write_private_text_atomic)
from .models import SequencePlan

SESSION_SCHEMA = 1
MAX_SESSIONS = 200

#: The local start stamp at the end of a report id, as
#: ``SessionReporter._make_id`` writes it (``<slug>-YYYYMMDD-HHMMSS``), with
#: the ``-2``, ``-3`` ``_mint_report_id`` adds for a second start in the same
#: second. Anchored at the end, so a plan name that itself ends in digits
#: shaped like a stamp is never read for the run's.
_REPORT_STAMP = re.compile(r"(\d{8}-\d{6})(?:-\d+)?$")


def report_night(report_id: str) -> str | None:
    """The observing night a run belongs to, from its report id: the
    ``events.night_key`` of the local instant the id's stamp names, which is
    the key ``captures/logs/<night>.jsonl`` is named by, or None for an id
    that carries no stamp (#430, S7 orchestrator ruling 7).

    FROM THE ID, SO EVERY SESSION ON DISK ANSWERS. ``engine.start`` mints the
    id from the run's start (``_mint_report_id``) and appends it to
    ``Session.nights``, and has done since sessions existed, so no session
    needs a new field or a migration for its nights to be counted.

    THROUGH ``night_key`` ITSELF, never the stamp's calendar date with noon
    subtracted by hand. The stamp is local wall-clock time; ``mktime`` turns
    it back into the instant in the same zone ``_make_id`` read it in, and
    ``night_key`` then applies its own rollover to that instant. A second
    copy of the rule would part from the night log's on the day the clocks
    change, when wall-clock noon and twelve hours after local midnight are an
    hour apart.

    None for an id with no stamp, or a stamp that is not a date: a hand-made
    test session, a legacy id. Never a guessed night."""
    m = _REPORT_STAMP.search(report_id or "")
    if m is None:
        return None
    try:
        parsed = time.strptime(m.group(1), "%Y%m%d-%H%M%S")
        return night_key(time.mktime(parsed))
    except (ValueError, OverflowError):
        return None


#: The set-aside kinds that say a panel was starved of what it needs to be
#: shot, as opposed to set aside for a reason that is the night's or the
#: sky's (``"floor"``, ``"rejects"``, ``"group"``, ``"angle"``): it could not
#: be centred (``group_rules.CENTRING``), could not start guiding
#: (``group_rules.GUIDE_START``), or failed in more than one of those ways
#: (``"deferred"``: ``GroupRun._count_failure``'s word for a mixed streak, and
#: the held-pass rule's for the mosaic's last live panel). Spelt out as words,
#: so the ledger takes no dependency on the driver's rules;
#: ``tests/test_w17_starved_panel.py`` pins the spellings to the constants
#: (#180 part A, backlog WP-131).
STARVING_KINDS = ("centring", "guide_start", "deferred")

#: How many consecutive nights a panel is set aside for one of
#: ``STARVING_KINDS`` before the progress answer and the Campaign call it
#: starved (#180 part A). Defined once, here, beside the count it is
#: compared with (``Session.set_aside_streak``).
STARVED_AFTER_NIGHTS = 3

#: Every value ``Session.status`` takes. Named so a caller asking "the newest
#: session of this flow, whatever became of it" can say so in one word.
SESSION_STATUSES = ("active", "dormant", "complete", "abandoned")


class SessionUnreadable(Exception):
    """A session file exists and is not JSON, or is not a valid Session, or
    states no status (#218, ``_stated_status``).

    Distinct from ``KeyError`` (no such session) because the two deserve
    different answers: one is "you asked for something that is not here", the
    other is "what is here is damaged", and calling the second one the first
    sends the user looking for a session they can see in the list, which is
    where ``SessionStore.list`` now shows every such file (#242)."""

    def __init__(self, session_id: str, reason: str = ""):
        # ``reason`` is one of the store's own words for the damage
        # (``NOT_JSON``, ``INVALID``, ``NO_STATUS``), never the parser's: the
        # detail of a validation failure is pydantic's, quotes the values it
        # refused (a frame's absolute path, say) and travels as ``__cause__``
        # only, because the reason is shown in a list viewers can read.
        # ``INVALID`` may go on to name the step that failed, in words the
        # store builds from the file itself (``_invalid_reason``, #416).
        message = f"session file is unreadable: {session_id}"
        super().__init__(f"{message} ({reason})" if reason else message)
        self.session_id = session_id
        self.reason = reason


def _sessions_dir() -> Path:
    # Resolved lazily (module-attribute lookup) so tests that monkeypatch
    # hub.CAPTURE_DIR are honored — same pattern as engine's old _resume_file().
    return _hubmod.CAPTURE_DIR / "sessions"


class SessionFrame(BaseModel):
    """One ledger entry (spec §2). ``metrics`` is an open float dict so a later
    PixInsight integration adds keys without a schema migration (spec §10)."""
    id: str = Field(default_factory=lambda: uuid4().hex)
    # 0.0 (the default) means "never stamped a real capture time" -- the
    # engine always stamps a banked frame with the exposure's own clock, so
    # 0.0 only ever appears on a frame this store SYNTHESIZED rather than
    # recorded (``migrate_legacy_resume``, #265). ``continuation._usable``
    # already reads it that way ("a frame the engine never stamped carries
    # 0.0") for ADOPT's ephemeris check, so a synthesized frame is left at
    # 0.0 rather than given a fabricated one: see ``migrate_legacy_resume``.
    ts: float = 0.0
    night: str = ""                 # report_id this frame was captured under
    target_id: str = ""
    step_id: str = ""
    path: str = ""                  # saved FITS path ("" if unsaved)
    thumb: str | None = None        # relative thumb path under the session dir
    metrics: dict[str, float] = Field(default_factory=dict)
    auto_accepted: bool = True
    override: str | None = None     # "accept" | "reject" | None

    def effective(self) -> bool:
        """Effective acceptance = override if set else auto_accepted."""
        if self.override is not None:
            return self.override == "accept"
        return self.auto_accepted


class Session(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    schema_version: int = SESSION_SCHEMA
    name: str = ""                  # defaults to plan.name (set by the engine)
    created_ts: float = 0.0
    updated_ts: float = 0.0
    status: str = "active"          # active | dormant | complete | abandoned
    plan: SequencePlan = Field(default_factory=SequencePlan)  # frozen snapshot WITH ids
    nights: list[str] = Field(default_factory=list)           # report ids, in order
    frames: list[SessionFrame] = Field(default_factory=list)
    auto_resume: bool = False
    # WHERE THIS WORK CAME FROM (#239 follow-up). A flow run and a Plan-editor
    # run compile to identical plans with identical names, so this cannot be
    # inferred after the fact - which is exactly why "what is the status of the
    # flow in progress" had no answer on any screen. "" means the session
    # predates the field and STAYS "": backfilling it would be an invention.
    #
    # Named origin/origin_id rather than source/source_id because
    # SessionReport.policy already uses {"source": "plan"|"rig"} for which LAYER
    # a setting won from, and two fields named "source" whose value "plan" means
    # different things is a trap laid for the next reader.
    origin: str = ""                # "" unknown | "plan" | "flow"
    origin_id: str = ""             # the flow id when origin == "flow"
    # CONSECUTIVE CRASHES OF THIS SESSION, and it lives here rather than in
    # ResumeArm because a crash can take the process with it - a counter in
    # memory would reset on exactly the restart it is meant to be counting.
    #
    # Incremented from BOTH halves of "the run died":
    #   * a run that ends with end_reason="error" while the server survives
    #     (`_finalize_report`), and
    #   * a session still `active` on disk at boot (`boot_sweep`), which can
    #     only mean the process itself died mid-run — the case that finalizes
    #     nothing and so used to count as nothing.
    # Reset to 0 by ANY other ending. A weather veto, a recovery hold or a
    # refusal to start is NOT a crash and must never land here: those are the
    # system working, and counting them would park the mount three cloudy holds
    # into a night that was going to clear.
    crash_resumes: int = 0
    # SET ASIDE FOR TONIGHT (#189 S2, #208; spec 3.4, 5.1, 6.7). Each record
    # is ``{target_id, step_id | None, reason, night}``: a panel the group
    # driver set aside whole (step_id None) or a step the reject guard set
    # aside, with the night key it happened under. Persisted because a crash
    # takes the engine's memory with it, and a same-night crash-resume that
    # forgot would retry, all over again, every panel it had just given up
    # on. ``set_aside_on`` reads one night's; a later night ignores the rest,
    # so every panel is retried tomorrow, as "set aside is not done" says.
    # Plain dicts rather than a model, as the spec has it: no record can make
    # a file fail validation, and a file that fails validation vanishes from
    # every scan (``load_all``). Written only through ``note_set_aside``.
    # Since H4 (#534, H4 orchestrator ruling 2) the engine also writes the
    # record's ``kind`` and ``ts``, and a centring set-aside that expires is
    # marked ``"expired": True`` (``note_set_aside_expired``), so a restart
    # tonight takes that panel up again and never expires it a second time.
    # All three are additive, with SESSION_SCHEMA still 1.
    set_aside: list[dict] = Field(default_factory=list)
    # LOCKED ANGLES (Revision 2, ruling 9): ``{target_id: {pa_deg, solved_at,
    # exposed_at, source}}``. An unframed TARGET takes the position angle its
    # first imaging-camera solve measures, and from then on that angle
    # behaves exactly like a planned one: every acquisition, resume, flip
    # re-centre and night commands the rotator to it, or on a fixed camera
    # checks against it, so frames from different nights stack. Keyed by
    # target id, and a re-frame re-anchors the block and so re-keys its ids
    # (spec 3.3, ruling 3), which is the one thing that clears a lock.
    # Written only through ``lock_angle``, where the first lock wins.
    locked_angles: dict[str, dict] = Field(default_factory=dict)
    # A GROUP'S PIER STATE FOR THE NIGHT (#312, S3 orchestrator ruling 4;
    # spec 3.4, 5.7): ``{group_id: {night, flipped, side, verified}}``, the
    # latest for each group. ``flipped``: the group made its one pier change
    # that night. ``side``: the pier side its hops measured ("east", "west",
    # or None when the change could not be read). ``verified``: that side was
    # read differing from one measured before the meridian, the only side a
    # later hop may disarm its flip latch on. The engine keeps all of this in
    # memory for the run, and a crash, a /recover or an auto-resume is a new
    # run: without the record a restart after the pier change started the
    # group unflipped, went back to a panel before the meridian, and changed
    # pier side a second time that night. ``group_pier_on`` reads one night's;
    # another night's record is history, and the group starts that night
    # unflipped. Plain dicts, as ``set_aside`` is, for the same reason, and
    # typed ``Any`` beneath the group id, looser still: a value that is not
    # a dict at all (a hand edit) is read as no record by ``group_pier_on``,
    # where ``dict`` would fail the whole file's validation and the session,
    # ledger and all, would vanish from every scan over one pier record.
    # Written only through ``note_group_pier``.
    group_pier: dict[str, Any] = Field(default_factory=dict)
    # WHEN THE FLOW'S FROZEN VERSION WAS SAVED (#473, S7 orchestrator ruling
    # 1; spec 5.9): the flow record's ``updated_ts`` for the version this
    # session's plan was compiled from. An armed auto-resume replays the
    # frozen plan, not the flow as the editor shows it now, and the editor
    # says so with this date ("the armed session will replay the version
    # from 2026-09-22; press CONTINUE to apply your edits"). Written by
    # ``run_flow`` when it makes a session and when CONTINUE replaces the
    # plan, and cleared by a PATCH that replaces the plan with one that is
    # not the flow's. None for a session made before it existed, for a
    # shipped Example (never saved: its ``updated_ts`` is the moment it was
    # read) and for a session no flow made: never a guessed time.
    plan_saved_ts: float | None = None
    # All four are additive with SESSION_SCHEMA still 1. There is no
    # ``extra="forbid"`` here, so a build that predates them loads this file
    # and ignores them (it then retries set-aside panels, today's behaviour),
    # and this build reads a file without them as empty.
    # THE SESSION THIS ONE WAITS BEHIND (#598, backlog ruling D-04,
    # owner-approved 2026-09-30): the id of a live or armed session A, set on a
    # DORMANT session B by ``PATCH {queue_next: true}``. Under the singleton,
    # "run A now, then armed B" leaves one of the two unprotected whichever way
    # it is arranged: arming B disarms A's restart resume (#595). A queued B is
    # deliberately NOT armed (``is_armed`` is unchanged: dormant plus
    # ``auto_resume``), so ``SessionStore.armed`` still answers A and A keeps
    # its protection. B is armed by exactly one event, A COMPLETING
    # (``SequenceEngine._promote_queued``), and never by a safety stop, an
    # operator stop or a dawn cut-off of A, which leave A owing frames and
    # armed itself. Cleared when B starts by any path, when B is abandoned,
    # on promotion, and by ``queue_next: false``. A queue of one: a second
    # session queued behind the same A replaces the first. Additive with
    # SESSION_SCHEMA still 1; a build that predates it ignores the key.
    queued_behind: str | None = None

    # ---- nights and arming (derived; nothing here is written) --------------
    def observing_nights(self) -> list[str]:
        """The observing nights this session has run, in the order it first
        ran on each: one entry per distinct ``report_night`` of its runs'
        report ids (#430, S7 orchestrator ruling 7).

        ``nights`` holds one report id per ``engine.start``, and every
        restart appends one: a crash-resume at 01:40, a /recover, a CONTINUE
        pressed twice in an evening. Its length is the RUN count, and read as
        a night count it called a restart the next night, on the progress
        card and in CONTINUE's answer alike. Keyed by night, a restart in the
        same night continues that night's entry.

        A run whose id carries no stamp (``report_night`` None) is keyed by
        its id, so it counts once and is never merged into another night:
        what it counted before #430, since nothing says which night it was."""
        out: list[str] = []
        for rid in self.nights:
            key = report_night(rid) or rid
            if key not in out:
                out.append(key)
        return out

    def night_at(self, now: float) -> int:
        """The night a run of this session started at ``now`` would be: the
        observing nights so far, plus one only when ``now``'s night is not
        already among them. CONTINUE's ``night``: a second press in the same
        evening is the night the log is already writing, not the next one."""
        nights = self.observing_nights()
        return len(nights) + (0 if night_key(now) in nights else 1)

    def is_armed(self) -> bool:
        """Whether ResumeArm would start this session: dormant, with
        ``auto_resume`` on. THE ONE RULE, which ``SessionStore.armed`` picks
        by and the progress route's ``armed`` answers with (#473), so the
        card can never call armed a session the tick would pass over.

        Dormant and not merely armed: ``engine.start`` arms every run it
        starts, so a live session is always ``auto_resume`` true, and a
        complete one may be; neither is a session auto-resume will start."""
        return self.status == "dormant" and self.auto_resume

    # ---- set aside and locks (run-owned; the engine writes, a resume reads) --
    def note_set_aside(self, target_id: str, reason: str, *, night: str,
                       step_id: str | None = None, kind: str | None = None,
                       ts: float | None = None) -> dict:
        """Record that ``target_id`` (or one of its steps) is set aside for the
        night ``night``, and return the record. Appended, never replacing: the
        second panel set aside tonight must not erase the first.

        ``night`` is the ``events.night_key()`` of the moment, the key a
        crash-resume asks with. An empty one is refused, because no night key
        is ever "": the record would be read by no night, and the set-aside
        would silently not survive the crash it is kept for.

        ``kind`` and ``ts`` (#534, H4 orchestrator ruling 2, amended by
        backlog WP-07 #564 2026-09-30): what set it aside, in a word
        (``"centring"`` is the one kind that expires,
        ``group_rules.set_aside_expiry``), and the clock time it was set
        aside at, which the expiry's 45 minutes run from. Written only when
        given, so the record keeps the spec's four keys for a caller that
        knows neither, and a reader treats a record without them as one that
        never expires, which is what every record written before them was.
        NOTHING SITE-DERIVED IS STORED: the record holds only the clock time
        it was set aside at, and the expiry it decides is time-only, so
        nothing here is, or was ever, an altitude (6.9)."""
        if not night:
            raise ValueError(
                f"a set-aside record needs the night it applies to "
                f"(events.night_key()), got {night!r}")
        record = {"target_id": target_id, "step_id": step_id,
                  "reason": reason, "night": night}
        if kind is not None:
            record["kind"] = str(kind)
        if ts is not None:
            t = float(ts)
            # The API renders JSON without NaN, and a record whose clock is
            # not a number could never expire by it anyway.
            if not math.isfinite(t):
                raise ValueError(f"a set-aside's ts must be a finite clock "
                                 f"time, got {ts!r}")
            record["ts"] = t
        self.set_aside.append(record)
        return record

    def note_set_aside_expired(self, target_id: str, *,
                               night: str) -> dict | None:
        """Mark ``target_id``'s standing CENTRING set-aside for ``night``
        expired (#534), and return the record, or None when it has none.

        The marker is ``"expired": True``, on the record itself: the record
        stays, as history and as the night's count of expiries, which is at
        most one per panel (``set_aside_expiries_on``), and ``set_aside_on``
        no longer reads it, so a restart tonight takes the panel up again
        instead of skipping it. A plain flag and no time: an expiry by the
        panel's rise happens at a moment the site decides, and the session is
        served to every role (6.9, the #19 class)."""
        for rec in reversed(self.set_aside):
            if (rec.get("target_id") == target_id
                    and rec.get("step_id") is None
                    and rec.get("night") == night
                    and rec.get("kind") == "centring"
                    and not rec.get("expired")):
                rec["expired"] = True
                return rec
        return None

    def note_set_aside_cleared(self, target_ids: Iterable[str], *,
                               night: str) -> list[dict]:
        """The operator brings ``target_ids``' set-aside panels back for
        ``night`` (#600, backlog ruling D-07, owner-approved 2026-09-30):
        mark every record tonight holds for those targets ``"cleared": True``,
        whole-panel and step-level alike, and return the ones that STOOD (not
        expired, not cleared already), in the order they were made.

        The marker is on the record itself, as ``"expired"`` is, and for the
        same reason: the record stays, as history of what the night did and
        of what the operator undid, and a plain flag beside it needs no
        migration (SESSION_SCHEMA stays 1; a build that predates it ignores
        the flag and reads the record as standing, which is what it always
        did). ``set_aside_on`` no longer reads a cleared record, so a restart,
        CONTINUE or an auto-resume tonight takes the panel up as if it had
        never been set aside.

        An EXPIRED record of the same target is marked too, though it is not
        returned: ``set_aside_expiries_on`` ignores a cleared one, which is
        what gives the panel its one expiry for the night back (#534). Kept
        counted, a centring set-aside that expired once and was set aside
        again for good would be retried by the operator and then stand for
        the rest of the night however it failed, with the one chance the
        first set-aside had already spent.

        Nothing site-derived is stored: a flag and no time (6.9)."""
        wanted = set(target_ids)
        standing: list[dict] = []
        for rec in self.set_aside:
            if (rec.get("night") != night or rec.get("cleared")
                    or rec.get("target_id") not in wanted):
                continue
            if not rec.get("expired"):
                standing.append(rec)
            rec["cleared"] = True
        return standing

    def set_aside_on(self, night: str) -> list[dict]:
        """The set-aside records for ``night`` that still stand, in the order
        they were made. A crash-resume passes ``events.night_key()`` and does
        not retry these; any other night's records are history, and so is a
        centring set-aside that has expired (``note_set_aside_expired``,
        #534): its panel is tried again tonight. So is one the operator
        cleared (``note_set_aside_cleared``, #600)."""
        return [r for r in self.set_aside
                if r.get("night") == night and not r.get("expired")
                and not r.get("cleared")]

    def set_aside_expiries_on(self, night: str) -> dict[str, int]:
        """How many times each target's set-aside expired on ``night`` (#534):
        the expired whole-target records, by target id. AT MOST ONE EXPIRY
        PER PANEL PER NIGHT is read from here, by the engine at a restart and
        by the resume arm, so the second set-aside of a night stands for the
        rest of it however often the run is restarted.

        A record the operator cleared is not counted (#600): a retry restores
        the panel's one expiry, as if its set-asides tonight had not been."""
        out: dict[str, int] = {}
        for r in self.set_aside:
            if (r.get("night") == night and r.get("expired")
                    and not r.get("cleared")
                    and r.get("step_id") is None and r.get("target_id")):
                out[r["target_id"]] = out.get(r["target_id"], 0) + 1
        return out

    def set_aside_streak(
            self, target_id: str, *, before: str | None = None,
            kinds: tuple[str, ...] = STARVING_KINDS) -> tuple[int, str | None]:
        """On how many CONSECUTIVE observing nights, ending with the newest
        this session ran, ``target_id`` was starved, and the kind of the
        newest record: ``(0, None)`` when it was not (#180 part A, backlog
        WP-131). The read behind "panel 1-3 has been set aside 4 nights
        running" (``flows.progress._starved``).

        ``before`` (#835) is a night key: the walk then ends with the night
        BEFORE it, as if the session had not run on ``before`` or after.
        "Starved on the nights before tonight" is what the group driver
        wants at its start, whether or not tonight has a record yet.
        ``kinds`` narrows which records count (default ``STARVING_KINDS``,
        the answer progress serves, unchanged); the walk stops at the first
        night the panel was reached on with none of them.

        A NIGHT COUNTS when the session holds a whole-panel record for the
        target on it (``step_id`` None: a step the reject guard set aside
        leaves the panel's other steps shot) of a kind in ``kinds``,
        AND the session banked no effective frame of the target that night.
        The records are keyed by ``events.night_key`` and the frames by their
        report id, so both are put through ``report_night``, the function
        ``observing_nights`` keys by, and a run whose id carries no stamp is
        its own night there and matches no record. The walk stops at the
        first night that does not count: a panel shot once since is not
        starved, however often it was set aside before.

        A NIGHT THAT NEVER REACHED THE PANEL IS STEPPED OVER (#970): it
        neither counts nor stops the walk. A night is REACHED when the
        session holds anything of the target from it, a set-aside record of
        any kind or step (expired and cleared included) or a frame of any
        grade (a rejected one was centred and exposed). A night that holds
        neither is one the panel was not tried on: clouded out before its
        turn, cut by the dawn, or spent on the mosaic's other panels. The
        ledger has no evidence about the panel from it, in either direction,
        so the walk goes on to the night before and the streak is a run of
        the nights the panel WAS tried. Read as a break it made a panel set
        aside on three nights answer ``(0, None)`` the night after a night
        that never got to it, which is when the Campaign should still name
        it and the group driver (``earlier_starved_nights``) should still
        give it the one held pass. A reached night that does not count
        (a frame banked, a kind outside ``kinds``, one step only) ends the
        streak as before. A night the panel was tried on whose set-aside had
        not landed when the run stopped looks like one it never reached, and
        is stepped over too.

        TONIGHT IS THE SAME CASE (#942). A run that has started has tonight
        in ``nights`` (``engine.start`` appends its report id) and, until the
        panel is set aside, no record of it: tonight is stepped over while it
        holds nothing of the panel and counted once when the record lands, so
        the earlier nights are not dropped as the run begins and not counted
        twice. No clock is needed to tell tonight from a night that closed
        untouched, because both are stepped over.

        EXPIRED AND CLEARED RECORDS COUNT. Both stay as history of what the
        night did (a centring set-aside that expired and struck out again, a
        panel the operator brought back); the night is a night the panel was
        set aside whichever way it went, and a retry that shot it is a
        banked frame, which ends the streak on its own account. A record
        with no ``kind`` (hand-edited, or from before kinds) is not starving:
        nothing says why it was set aside.

        ONLY THE KIND IS RETURNED, never the record's ``reason``: it is free
        text that can carry a solver's error, and what is built from this
        answer is served to a viewer.

        NOTHING HERE IS SITE-DERIVED: nights and kinds, no clock and no time.
        The night key is the server's own local noon-to-noon date."""
        banked = {report_night(f.night) or f.night for f in self.frames
                  if f.target_id == target_id and f.effective()}
        reached = {r.get("night") for r in self.set_aside
                   if r.get("target_id") == target_id}
        reached |= {report_night(f.night) or f.night for f in self.frames
                    if f.target_id == target_id}
        nights = self.observing_nights()
        if before is not None and before in nights:
            nights = nights[:nights.index(before)]
        streak, kind = 0, None
        for night in reversed(nights):
            if night not in reached:
                continue
            records = [r for r in self.set_aside
                       if r.get("target_id") == target_id
                       and r.get("step_id") is None
                       and r.get("night") == night
                       and r.get("kind") in kinds]
            if not records or night in banked:
                break
            if kind is None:
                kind = records[-1]["kind"]
            streak += 1
        return streak, kind

    def earlier_starved_nights(self, target_id: str, *, night: str,
                               kinds: tuple[str, ...]) -> int:
        """On how many consecutive observing nights BEFORE ``night`` the
        session set ``target_id`` aside whole for one of ``kinds`` and shot
        none of it (``set_aside_streak``), or 0 (#835). What the group driver
        reads at its start to give up sooner on a panel that has not centred
        for nights running. A night in between that never reached the panel
        is stepped over (#970), so a cloud-out does not give it the six
        passes back.

        ZERO WHEN THE OPERATOR BROUGHT THE PANEL BACK ON ``night``
        (``note_set_aside_cleared``, backlog ruling D-07): the retry is the
        operator's decision that the panel is worth a full try tonight, and a
        restart after it must not read the nights before as a reason to give
        it one pass.

        ZERO WHEN THE SESSION HOLDS A FRAME OF THE PANEL FROM ``night`` ITSELF.
        The driver drops its starved note the moment the panel shoots
        (``GroupRun._record``: ``exposures > 0``, a rejected frame included,
        since a rejected frame was centred and exposed), and this read is made
        again at every start, so a crash-resume, a CONTINUE or a /recover
        later the same night would otherwise note it starved a second time and
        give a panel that recovered tonight one held pass instead of the six.
        Any frame of the panel counts here, effective or not, and the report
        ids are put through ``report_night`` as ``set_aside_streak`` does;
        the NIGHTS BEFORE are still counted by the ledger's own rule, which
        needs an effective frame to end a night. Counts and nights, nothing
        site-derived (6.9)."""
        if any(r.get("cleared") and r.get("target_id") == target_id
               and r.get("night") == night for r in self.set_aside):
            return 0
        if any(f.target_id == target_id
               and (report_night(f.night) or f.night) == night
               for f in self.frames):
            return 0
        return self.set_aside_streak(target_id, before=night, kinds=kinds)[0]

    def lock_angle(self, target_id: str, pa_deg: float, *, solved_at: float,
                   exposed_at: float | None, source: str) -> dict:
        """Lock ``target_id``'s angle to ``pa_deg`` unless it is locked
        already, and return the lock IN FORCE, which is the angle the caller
        commands (ruling 9).

        THE FIRST LOCK WINS. A second call, a later night's first solve
        included, leaves the lock as it was and returns it. Overwriting would
        re-read the angle at every acquisition, and the angle would drift from
        night to night: the failure ruling 9 exists to prevent.

        ``solved_at`` is when the solve finished, ``exposed_at`` when its frame
        was exposed (None when that is not known), both unix seconds, and
        ``source`` says in words which solve measured it, for the flow editor
        that shows where the angle came from.

        A NaN or infinite angle (a failed solve can hand one back) is refused
        and nothing is written: locked, it would be the angle every later night
        commands, and the session could no longer be served, since the API's
        JSON rendering refuses NaN and every route returning it would 500."""
        held = self.locked_angles.get(target_id)
        if held is not None:
            return held
        if not math.isfinite(pa_deg):
            raise ValueError(
                f"cannot lock {target_id!r} to angle {pa_deg!r}: a locked angle "
                f"must be a finite number of degrees")
        record = {"pa_deg": float(pa_deg), "solved_at": float(solved_at),
                  "exposed_at": exposed_at, "source": source}
        self.locked_angles[target_id] = record
        return record

    def locked_angle(self, target_id: str) -> dict | None:
        """``target_id``'s lock, or None when it has none. Never a default
        angle: 0 is a real position angle."""
        return self.locked_angles.get(target_id)

    def note_group_pier(self, group_id: str, *, night: str, flipped: bool,
                        side: str | None, verified: bool) -> dict:
        """Record ``group_id``'s pier state for the night ``night`` and return
        the record (#312). It REPLACES the group's last record: the state is
        one fact that moves forward through a night (the first side measured,
        then the pier change), and a restart wants only the latest.

        ``night`` is the ``events.night_key()`` of the moment, as a set-aside
        record's is, and an empty one is refused for the same reason: no
        night would ever read it. ``side`` is "east", "west" or None (the
        side could not be read); anything else is refused, because a side
        the engine could not have measured is not a record of a measurement.
        A side that is None cannot be ``verified``."""
        if not night:
            raise ValueError(
                f"a group's pier record needs the night it applies to "
                f"(events.night_key()), got {night!r}")
        if side not in ("east", "west", None):
            raise ValueError(
                f"a pier side is 'east', 'west' or None (unread), got "
                f"{side!r}")
        record = {"night": night, "flipped": bool(flipped), "side": side,
                  "verified": bool(verified) and side is not None}
        self.group_pier[group_id] = record
        return record

    def group_pier_on(self, group_id: str, night: str) -> dict | None:
        """``group_id``'s pier record when it was written on the night
        ``night``, else None: a record from another night is history, and
        the group starts that night unflipped (#312). A record that is not
        a dict (the session is a JSON file anyone can edit) reads as none."""
        rec = self.group_pier.get(group_id)
        if not isinstance(rec, dict) or rec.get("night") != night:
            return None
        return rec

    # ---- derived helpers (mode-aware per the FROZEN plan's count_mode) -------
    def accepted_by_step(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.frames:
            if f.effective():
                out[f.step_id] = out.get(f.step_id, 0) + 1
        return out

    def recorded_by_step(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.frames:
            out[f.step_id] = out.get(f.step_id, 0) + 1
        return out

    def accepted(self, step_id: str) -> int:
        return self.accepted_by_step().get(step_id, 0)

    def total_accepted(self) -> int:
        return sum(self.accepted_by_step().values())

    def _counts(self) -> dict[str, int]:
        # getattr default keeps this module valid before Task 4 adds count_mode.
        mode = getattr(self.plan, "count_mode", "attempts")
        return self.accepted_by_step() if mode == "accepted" else self.recorded_by_step()

    def remaining(self) -> dict[str, int]:
        """Per-step frames still owed (mode-aware; floored at 0)."""
        counts = self._counts()
        out: dict[str, int] = {}
        for t in self.plan.targets:
            for s in t.steps:
                out[s.id] = max(0, s.count - counts.get(s.id, 0))
        return out

    def owed(self) -> int:
        """Frames still owed across every step (mode-aware, floored at 0).

        THE definition of "is this plan finished". It exists as one method
        rather than an expression at each call site because the run's ending
        and the session's status must never be able to answer it differently -
        which is exactly how #252 shipped: the session asked about unmet quota
        in accepted mode only, and the run did not ask at all.
        """
        return sum(self.remaining().values())

    def done_map(self) -> dict[str, int]:
        """Engine seeding map ``"<target_id>:<step_id>" -> completed`` (mode-
        aware), capped at the step count so a resumed loop never starts past
        its range."""
        counts = self._counts()
        out: dict[str, int] = {}
        for t in self.plan.targets:
            for s in t.steps:
                out[f"{t.id}:{s.id}"] = min(s.count, counts.get(s.id, 0))
        return out


#: Why ``SessionUnreadable`` refuses a file that states no status (#218).
NO_STATUS = "it has no status"

#: Why it refuses a file that does not parse, or is not UTF-8 text (#242): a
#: write cut short by a power cut, or a file copied in by hand. Writes are
#: atomic (``write_private_text_atomic``, the staging file and replace of
#: ``write_json_atomic``), so the store itself should never leave one.
NOT_JSON = "not valid JSON"

#: Why it refuses JSON that ``Session`` does not validate (#242).
INVALID = "fails validation"


def _stated_status(raw: dict) -> str | None:
    """The status a session FILE states, or None when it states none.

    THE ONE PLACE A READER ASKS A FILE ITS STATUS (#218, H2 orchestrator
    ruling 12, spec "Still waiting on the owner" item 9). ``Session.status``
    defaults to "active" so a session built in code gets one, and the engine
    passes it explicitly anyway. Read off disk, that default turned a file
    which says nothing about its session into a RUNNING session, and the
    readers disagreed about it: ``load`` answered active, the
    raw-dict scan behind ``active()`` did not find it, and ``boot_sweep`` swept
    it, counted a crash that never happened (three make ResumeArm stow the
    rig) and saved the default-filled model over the file. A missing fact read
    as a confident default is what ``Session.origin`` refuses to do
    ("backfilling it would be an invention"), and the status is the fact every
    reader of the store filters on. So there is one answer, and it is "none":
    the raw scans compare this, and ``_session_from_file`` refuses a file for
    which it is None."""
    return raw.get("status")


#: Longest quoted value a reason carries: a list line, and the value comes
#: from a file nothing validated.
_VALUE_MAX = 40


def _quoted(value) -> str:
    """``repr(value)``, cut to ``_VALUE_MAX`` characters."""
    text = repr(value)
    return text if len(text) <= _VALUE_MAX else text[:_VALUE_MAX - 3] + "..."


def _step_words(raw, error: dict) -> str | None:
    """What a validation error on one field of a plan step says, in the
    store's words, or None when the error is anywhere else (#416).

    ``target 'M42', step 2 of 2 (filter 'Ha'): frame_type 'DarkFlat' is not
    one of Light, Dark, Bias, Flat (in any case)``: the target, the step's
    place and filter, which is how an operator finds a step, then the field
    and the value. All of it is read off the raw file at the error's
    location, so it can quote only a step's own settings (its filter name,
    exposure, count, gain, offset, binning, frame type and the like), and
    none of them is a path or a place. An error anywhere else (a frame's
    metrics, whose input can be an absolute path) is not described at all,
    which is why pydantic's rendering of the error is never used: it quotes
    the refused input from wherever it sits.

    The rule broken is pydantic's word for it (``input should be greater
    than 0``), which describes the constraint and quotes nothing, except for
    the frame type, whose rule is the store's: the four spellings #334 made
    the only ones. A missing field is said to be missing rather than quoted
    as None, a step with no filter says so, and a target with a blank name
    is named by its place, counted from 1 like the step."""
    loc = error.get("loc", ())
    if (len(loc) != 6 or loc[0] != "plan" or loc[1] != "targets"
            or loc[3] != "steps" or not isinstance(loc[2], int)
            or not isinstance(loc[4], int) or not isinstance(loc[5], str)):
        return None
    try:
        target = raw["plan"]["targets"][loc[2]]
        steps = target["steps"]
        step = steps[loc[4]]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(steps, list) or not isinstance(step, dict):
        return None
    name = target.get("name")
    who = (f"target {_quoted(name.strip())}"
           if isinstance(name, str) and name.strip() else
           f"target {loc[2] + 1}")
    filt = step.get("filter")
    filt = "no filter" if filt is None else f"filter {_quoted(filt)}"
    field = loc[5]
    if error.get("type") == "missing":
        what = f"{field} is missing"
    elif field == "frame_type":
        # Lazy, as ``models._frame_type`` imports it: the four live with the
        # writer of IMAGETYP.
        from ..imaging.fitsio import FRAME_TYPES
        what = (f"frame_type {_quoted(step.get(field))} is not one of "
                f"{', '.join(FRAME_TYPES)} (in any case)")
    else:
        rule = str(error.get("msg", ""))
        what = (f"{field} {_quoted(step.get(field))}: "
                f"{rule[:1].lower()}{rule[1:]}")
    return f"{who}, step {loc[4] + 1} of {len(steps)} ({filt}): {what}"


def _invalid_reason(raw, exc: Exception) -> str:
    """``INVALID``, naming the first step field that failed when one did
    (#416), else ``INVALID`` alone.

    WHY A STEP IS NAMED. #334 made a step's frame type one of four
    spellings, and before it the field took any string, so a session on disk
    can hold a step saying ``DarkFlat``. That file became "fails
    validation" and nothing else: nothing told the operator what to repair,
    and DELETE was the only thing left to press on a campaign's ledger.
    Named, it is one field in a file they can edit.

    The first in pydantic's order is named and the rest are counted, since
    repairing the one named would not make the file load if another field
    fails too. A failure that is not a ``ValidationError`` names nothing."""
    errors = exc.errors() if isinstance(exc, ValidationError) else []
    for error in errors:
        words = _step_words(raw, error)
        if words is None:
            continue
        more = len(errors) - 1
        if more:
            words += f" (and {more} more error{'s' if more != 1 else ''})"
        return f"{INVALID}: {words}"
    return INVALID


def _session_from_file(raw, session_id: str) -> Session:
    """Validate a parsed session file, or raise :class:`SessionUnreadable`.

    Every ``SessionStore`` reader builds its ``Session`` here, so a file is
    readable by all of them or by none: ``load`` raises, the scanning
    readers skip it under the rule they already keep for a corrupt file, and
    ``list`` shows it as unreadable with the reason raised here (#242).

    Validation first, so a file that is damaged in some other way as well
    (``{"plan": "not a plan"}`` states no status either) reports the damage,
    with pydantic's error as the cause, rather than the missing status alone:
    adding a status to that file would not make it readable. JSON that is
    not an object at all (``[]``, ``"x"``) fails validation too. A failure
    on a step's field is named (``_invalid_reason``, #416)."""
    try:
        session = Session.model_validate(raw)
    except Exception as e:
        raise SessionUnreadable(session_id, _invalid_reason(raw, e)) from e
    if _stated_status(raw) is None:
        raise SessionUnreadable(session_id, NO_STATUS)
    return session


def _parsed(path: Path):
    """The JSON a session file holds, or raise: :class:`SessionUnreadable`
    (``NOT_JSON``) for a file that is there and does not parse, and
    ``OSError`` for one that is gone or that the OS will not open.

    THE ONE PLACE A STORE READER TELLS "DAMAGED" FROM "NOT THERE" (#242).
    ``read_json_or`` answers both with its default, so ``load`` called a
    truncated file "no such session" and ``DELETE`` answered 404 for a file
    on disk, and the scans skipped it without a word. A file held open by
    another process (an antivirus scan, a replace in flight on Windows) is
    an ``OSError`` and not damage: nothing about it says it will stay that
    way, so it is never offered for deletion."""
    try:
        return read_json(path)
    except ValueError as e:     # JSONDecodeError, UnicodeDecodeError
        raise SessionUnreadable(path.stem, NOT_JSON) from e


#: Longest name an unreadable row carries: it is a list line, and the name
#: comes from a file nothing validated.
_NAME_MAX = 120


def _backup_path(path: Path) -> Path:
    """``<id>.json.bak`` for ``<id>.json``: the one name ``backup`` writes,
    ``delete`` removes or keeps, and ``_unreadable_row`` reports (#266)."""
    return path.with_suffix(path.suffix + ".bak")


def _has_backup(path: Path) -> bool:
    """Whether a backup sits beside session file ``path``, for the list row.

    False when the OS will not say (a stat refused), the rule
    ``_unreadable_row`` keeps for the file itself: one file nobody can look
    at must not cost the whole list. The row then claims nothing, and
    ``delete`` still keeps a backup it finds there (#266)."""
    try:
        return _backup_path(path).is_file()
    except OSError:
        return False


def _raw_name(path: Path, raw) -> str:
    """The name an unreadable file gives its session, cut to ``_NAME_MAX``,
    or the file's stem when it gives none: the list row's name and the one
    ``armed``'s warning says (#416)."""
    name = raw.get("name") if isinstance(raw, dict) else None
    return (name.strip()[:_NAME_MAX]
            if isinstance(name, str) and name.strip() else path.stem)


#: What a backup's name ends in, and so how its session id is read back off
#: it: ``backup`` writes ``<id>.json.bak`` and nothing else (#280).
_BACKUP_SUFFIX = ".json.bak"

#: The ``reason`` of the row ``list`` gives a backup whose session file is
#: gone (#280). The UI shows it as sent.
ORPHAN_REASON = "the session file is gone; only its backup remains"

#: Why ``restore_backup`` refuses a backup that is another session's: the id
#: inside it is not the id of the file it sits beside (#280). The other id is
#: not quoted: it comes from a file nothing validated.
OTHER_SESSION = "it holds a different session id"


class LiveSessionReadable(Exception):
    """``restore_backup`` found a READABLE ``<id>.json`` and left it alone
    (#280).

    A restore is for a file that is missing or that nothing can read. A
    readable file is the current ledger, usually with frames the backup does
    not hold, and replacing it would be an undo nobody asked for: distinct
    from ``SessionUnreadable``, which is about the BACKUP, so the route can
    answer each in its own words."""

    def __init__(self, session_id: str):
        super().__init__(f"session file is readable: {session_id}")
        self.session_id = session_id


def _backup_id(bak: Path) -> str:
    """The session id a backup file is named for: ``<id>`` of
    ``<id>.json.bak`` (``Path.stem`` would say ``<id>.json``)."""
    return bak.name[:-len(_BACKUP_SUFFIX)]


def _orphan_backups() -> list[Path]:
    """Every ``<id>.json.bak`` in the sessions directory with no
    ``<id>.json`` beside it, by name (#280).

    WHAT #266 LEFT UNREACHABLE. ``DELETE`` of an unreadable file keeps its
    backup, and once the file was gone nothing listed the ``.bak``, so
    getting the session log back, or getting rid of the copy and its
    thumbnails, took a shell on the rig. ``list`` shows each of these as a row
    of its own, ``has_orphan_backup`` lets ``DELETE`` find one, and
    ``restore_backup`` brings one back.

    A backup beside a ``.json``, readable or not, is not an orphan: that
    session is listed already (an unreadable one says ``backup: true``).

    A stem the store's id guard refuses is skipped (``safe_id_path`` is the
    guard ``SessionStore._path`` is): no route could address it, and one
    stray file must not cost the list. So is a file whose stat the OS will
    not answer, the rule ``_has_backup`` keeps: nothing is claimed that was
    not seen."""
    directory = _sessions_dir()
    if not directory.is_dir():
        return []
    found: list[Path] = []
    for bak in sorted(directory.glob(f"*{_BACKUP_SUFFIX}")):
        try:
            live = safe_id_path(directory, _backup_id(bak))
            if bak.is_file() and not live.is_file():
                found.append(bak)
        except (KeyError, OSError):
            continue
    return found


def _orphan_row(bak: Path) -> dict | None:
    """The ``GET /api/sessions`` row for an orphaned backup (#280), or None
    when the file has gone, or the OS will not open it, since it was found.

    ``status`` is ``"unreadable"`` ON PURPOSE, though the row says nothing is
    wrong with the backup: that is the one word every reader that cannot take
    a session without a ledger already filters on (``isUnreadableRow``,
    ``listSessions``), so a row with no counts and no dates can never be drawn
    as a session with 0 of 0 frames or offered RESUME. ``orphan`` is what
    tells the two apart, for the UI that words its delete differently.

    The id is the backup's stem and the name the one inside it, read so the
    operator can tell which session this was, else the id. A backup that does
    not parse is listed all the same (so it can be deleted), and ``restore``
    is what says whether it would load. ``updated_ts`` is the BACKUP's mtime:
    the moment its ledger was copied, which is the date a restore brings back."""
    try:
        mtime = bak.stat().st_mtime
    except OSError:
        return None
    try:
        raw = _parsed(bak)
    except SessionUnreadable:
        raw = None
    except OSError:
        return None
    sid = _backup_id(bak)
    return {"id": sid, "name": _raw_name(Path(f"{sid}.json"), raw),
            "status": "unreadable", "unreadable": ORPHAN_REASON,
            "updated_ts": mtime, "backup": True, "orphan": True}


def _unreadable_row(path: Path, raw, reason: str | None) -> dict | None:
    """The ``GET /api/sessions`` row for a file the store cannot read (#242),
    or None when the file has gone since it was read.

    SEEN, AND NOTHING TO MISTAKE FOR A SESSION. The scans skip such a file,
    which is right for every reader that would count, sweep, start or save
    over it, and it made the file invisible: the only ways to remove it were
    a shell on the rig or a factory reset. So the list shows it, the way the
    flow library shows an unreadable flow (#153), and ``DELETE`` removes it.
    The row carries no ledger field (accepted, total, owed, nights,
    auto_resume): none of them was read from anything, and a 0 of 0 would
    be a count of a session that does not exist. ``status`` is
    "unreadable", which is none of ``SESSION_STATUSES``, so no client
    filtering on a real status picks it up.

    The id is the FILE's stem, because that is what ``DELETE`` addresses.
    The name inside is used when there is one, else the stem. ``updated_ts``
    is the file's mtime: the ``updated_ts`` inside, if any, was written by
    whatever damaged the file, and it sorts the row in among the rest.

    ``backup: true`` WHEN A BACKUP SITS BESIDE IT (#266). ``DELETE`` keeps
    that ``.bak`` for an unreadable file, since it can be the last good copy
    of the ledger, and the confirm has to say so before the tap. The key is
    left out when there is none, so that row is the #242 row unchanged. It
    says a file exists, not that it would load: whether it does is only
    known by reading it, and the delete keeps it either way."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    row = {"id": path.stem, "name": _raw_name(path, raw),
           "status": "unreadable", "unreadable": reason, "updated_ts": mtime}
    if _has_backup(path):
        row["backup"] = True
    return row


def session_text(session: Session) -> str:
    """The text ``SessionStore.save`` writes for ``session``: its
    ``model_dump()`` as JSON, indented two spaces, non-ASCII unescaped, and
    NaN and the infinities as the ``NaN`` / ``Infinity`` constants Python's
    ``json`` reads back (#514, spec risk 8).

    WHY NOT ``json.dumps``. The file is rewritten after every banked frame and
    holds every frame the project has banked, and the write runs on the event
    loop. At 10 000 frames ``json.dumps(indent=2)`` took 84.5 ms of the
    107.7 ms a save cost (medians on the development box, loaded).
    ``pydantic_core.to_json`` writes the same dump in 13.1 ms, and on a
    realistic 10 000-frame dump the text is the same byte for byte. The one
    spelling that differs is a float Python writes in exponent form below
    1e-4: ``5e-05`` comes out ``0.00005``, ``1e-07`` comes out ``1e-7``. Both
    parse to the same float, so no reader can tell.

    ``model_dump`` FIRST, NEVER ``model_dump_json``. The model's own JSON
    serialiser writes NaN as ``null``, and a metric read back as ``null``
    fails ``dict[str, float]``: one NaN HFR would make the whole ledger
    unreadable (``SessionUnreadable``), gone from every scan.
    ``inf_nan_mode="constants"`` keeps what ``json.dumps`` wrote.

    Public so the budget test (``test_s7_ledger_cost.py``) times the
    serialiser ``save`` actually uses."""
    return pydantic_core.to_json(session.model_dump(), indent=2,
                                 inf_nan_mode="constants").decode("utf-8")


def _file_stamp(path: Path) -> tuple[int, int, int] | None:
    """``(inode, mtime_ns, size)`` for the file at ``path``, or None when the
    OS will not say (it is gone, or the stat was refused). What
    ``SessionStore.save_run_state`` compares to tell the file this process
    last wrote from one somebody else has written since (#514)."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


class SessionStore:
    """uuid-keyed session store; one ``sessions/<id>.json`` per session
    (mirrors plans.PlanLibrary)."""

    def _path(self, session_id: str) -> Path:
        return safe_id_path(_sessions_dir(), session_id)

    def load(self, session_id: str) -> Session:
        """The stored session, or ``KeyError`` when there is no readable file.

        A file that parses as JSON but is not a ``Session`` raises
        :class:`SessionUnreadable`, NOT ``KeyError``: the six routes that load
        a session all answer ``KeyError`` with "session not found", and that is
        a false statement about a session that exists and is corrupt. It used
        to be an uncaught ``ValidationError``, i.e. a 500 with a traceback and
        no sentence naming the file.

        A file that states no status raises it too, naming that as the reason
        (#218): see ``_stated_status``. So does a file that is not JSON
        (#242): it is on disk, and ``GET /api/sessions`` lists it."""
        try:
            raw = _parsed(self._path(session_id))
        except OSError:
            raise KeyError(session_id) from None
        return _session_from_file(raw, session_id)

    def _scan_status(self, status: str) -> list[tuple[float, Path]]:
        """(updated_ts, path) for every stored session with ``status``, newest
        first, WITHOUT building a ``Session`` for any of them.

        ``load_all()`` fully pydantic-validates every archived session — 200
        sessions x 170 frames is 34 000 ``SessionFrame`` models — to read one
        string off each. Measured at the store's own soft cap, that scan was
        0.982 s while the fold it fed took 0.002 s, i.e. the whole cost of
        ``GET /api/sessions/current/files`` was finding the session. The status
        and the timestamp are two top-level scalars in a file we have already
        parsed, so ask the raw dict for them and validate only the winner."""
        rows: list[tuple[float, Path]] = []
        for path in list_json(_sessions_dir()):
            raw = read_json_or(path)
            if not isinstance(raw, dict) or _stated_status(raw) != status:
                continue
            ts = raw.get("updated_ts")
            rows.append((float(ts) if isinstance(ts, (int, float)) else 0.0,
                         path))
        rows.sort(key=lambda r: r[0], reverse=True)
        return rows

    def active(self) -> Session | None:
        """The session a run is writing to right now, or None.

        Most recently updated wins defensively — there should only ever be one.
        A file that no longer validates is SKIPPED rather than raised, the same
        rule ``load_all`` keeps: a corrupt archive must not make the live
        session unfindable."""
        for _ts, path in self._scan_status("active"):
            raw = read_json_or(path)
            if not isinstance(raw, dict):
                continue
            try:
                return _session_from_file(raw, path.stem)
            except SessionUnreadable:
                continue
        return None

    def _entries(self) -> Iterator[tuple[Path, object, Session | None,
                                         str | None]]:
        """One walk of the sessions directory, one judgment per file:
        ``(path, raw, session, None)`` for a session, and ``(path, raw, None,
        reason)`` for a file the store cannot read, ``raw`` None when it is
        not JSON (#242). A file that is gone since the listing, or that the
        OS will not open, yields nothing (``_parsed``).

        ``load_all`` keeps the sessions and ``list`` keeps both, so the two
        cannot disagree about which files are sessions: the judgment is
        ``_parsed`` and ``_session_from_file``, the same two ``load`` makes."""
        for path in list_json(_sessions_dir()):
            raw = None
            try:
                raw = _parsed(path)
                yield path, raw, _session_from_file(raw, path.stem), None
            except OSError:
                continue
            except SessionUnreadable as e:
                yield path, raw, None, e.reason

    def load_all(self) -> list[Session]:
        """Every readable session. ``boot_sweep``, ``recoverable``, the prune
        sweep and ``engine.start``'s disarm loop all read through here, so a
        file this skips is one none of them can sweep, count, start or save
        over (#218). ``list`` walks the same entries and shows the skipped
        files as what they are (#242), and ``armed`` walks them too and says
        an armed one it cannot start (#416); nothing else does."""
        return [s for _path, _raw, s, _why in self._entries()
                if s is not None]           # unreadable: skip, never raise

    def newest_for_flow(self, flow_id: str,
                        statuses: Iterable[str]) -> Session | None:
        """The most recently CREATED session this flow started whose status is
        one of ``statuses``, or None.

        CREATED, NOT UPDATED. ``engine.start`` disarms every other armed
        session and saves each one it touches, and ``save`` stamps
        ``updated_ts``, so a fresh start of flow B makes flow A's older session
        the "most recently updated" one on disk. Ordered by ``updated_ts`` this
        would hand CONTINUE whichever session last lost the auto-resume
        singleton rather than the flow's latest ledger. ``created_ts`` is
        written once, by the start that made the session, and nothing moves it.

        Filtered on the raw dicts like ``_scan_status``, so one flow's lookup
        does not build a ``Session`` for every archived session of every other
        flow. A candidate that no longer validates is skipped, the rule
        ``active`` and ``load_all`` keep, and the next newest is tried.
        """
        wanted = set(statuses)
        rows: list[tuple[float, Path, dict]] = []
        for path in list_json(_sessions_dir()):
            raw = read_json_or(path)
            if (not isinstance(raw, dict) or raw.get("origin") != "flow"
                    or raw.get("origin_id") != flow_id
                    or _stated_status(raw) not in wanted):
                continue
            ts = raw.get("created_ts")
            rows.append((float(ts) if isinstance(ts, (int, float)) else 0.0,
                         path, raw))
        rows.sort(key=lambda r: r[0], reverse=True)
        for _ts, path, raw in rows:
            try:
                return _session_from_file(raw, path.stem)
            except SessionUnreadable:
                continue
        return None

    def current_for_flow(self, flow_id: str) -> Session | None:
        """The session that is this flow's work, or None: the newest session
        the flow started, by ``created_ts`` and of ANY status, and None when
        that newest one was abandoned.

        ONE RULE, EVERY READER (#189 hardening A2). The card's progress chip
        and Run's CONTINUE both ask "which ledger is this flow's", and they
        used to answer it two ways: the chip took the newest session that was
        not abandoned, Run the newest of any status. They parted as soon as a
        flow held the session a START OVER leaves behind, which stays dormant
        and unarmed for good: abandon the newer session and the chip fell
        back to that old ledger while Run started fresh, so the card counted
        frames toward a session no button would continue. Callers decide
        what to do with the answer; only this method decides which session
        it is.

        NEVER PAST THE NEWEST. Whatever became of the newest session, an
        older one is a ledger the operator chose to leave, and reading it
        again (the chip) or continuing it (Run) would reopen it unasked.
        A complete newest is returned: the chip shows what it banked, and
        Run, which continues only a dormant session, starts fresh (reopening
        a complete one is I-30). An abandoned newest is None: the operator
        closed it, so there is nothing to show and nothing to continue.

        Through ``newest_for_flow``, so the order (created, not updated) and
        the skip over an unreadable file are that method's rules and not a
        second copy of them.
        """
        s = self.newest_for_flow(flow_id, SESSION_STATUSES)
        if s is None or s.status == "abandoned":
            return None
        return s

    #: Serialises read-modify-write against plain writes. RLock because
    #: ``save_run_state`` holds it across a ``load`` and a ``save``, and
    #: because ``write_locked`` callers go on to call ``save`` (directly, or
    #: through ``engine.start``) while they hold it.
    _write_lock = threading.RLock()

    @contextmanager
    def write_locked(self) -> Iterator[SessionStore]:
        """Hold the store's write lock across a caller's own read-check-start.

        ``save`` and ``save_run_state`` each take this lock for one write, which
        keeps a write whole but says nothing about what a caller READ before
        it. Run CONTINUE reads a dormant session, decides it may take it, and
        hands it to ``engine.start``; if anything can start or end that same
        session between the read and the start, the start persists a stale
        copy over the frames the other run banked (the one-starter race of
        2026-09-18, spec 5.9). Two things close it together. The engine's
        ledger writes, its finalize and ResumeArm all run on the event loop,
        and a section with no ``await`` in it cannot be interleaved by
        anything on that loop. Writers on WORKER threads - a route's
        ``asyncio.to_thread(session_store.save, ...)`` - are not stopped by
        that. ``save``, ``save_run_state``, ``backup`` and ``delete`` take
        this lock, so it holds them off until the section, ``engine.start``'s
        own ``save`` included, is done. The DELETE route re-reads and unlinks
        inside a section of its own (#212), so a delete cannot land between
        a section's read and its start either.

        Re-entrant, so ``save`` inside it does not deadlock. Hold it for
        synchronous work only: never ``await`` inside it. An ``await`` would
        let the loop's own writers in, which defeats the first half, and a
        coroutine suspended while holding a thread lock can stall every
        worker-thread writer behind a lock only it can release.
        """
        with self._write_lock:
            yield self

    #: What this process last wrote to each session file, keyed by the file's
    #: path: ``(stamp, operator_owned)``, where ``stamp`` is ``_file_stamp``
    #: read straight after the write and ``operator_owned`` the values of
    #: ``_OPERATOR_OWNED`` the write carried (#514). On the class, like
    #: ``_write_lock``, so every store in the process shares it, and read and
    #: written only under that lock.
    _written: dict[str, tuple[tuple[int, int, int], dict[str, Any]]] = {}

    def save(self, session: Session) -> None:
        """Atomic write, no .bak (churns every frame). A NEW id triggers the
        prune sweep; upserting an existing id never prunes.

        The text is ``session_text``'s, written through
        ``write_private_text_atomic``: the same private staging file, fsync,
        replace and hardening ``write_json_atomic(backup=False)`` gave it, with
        the serialiser that costs a sixth as much (#514). The file's stamp and
        the operator-owned values are then recorded in ``_written`` for
        ``save_run_state``. The record is made only after a write that
        returned: a write that failed before its replace left the file the
        previous record describes, and one that failed after it left a file
        whose stamp matches no record, which is read."""
        with self._write_lock:
            session.updated_ts = time.time()
            path = self._path(session.id)
            is_new = not path.exists()
            write_private_text_atomic(path, session_text(session))
            stamp = _file_stamp(path)
            if stamp is None:
                self._written.pop(str(path), None)
            else:
                self._written[str(path)] = (stamp, {
                    field: copy.deepcopy(getattr(session, field))
                    for field in self._OPERATOR_OWNED})
            if is_new:
                self._prune()

    #: Fields the OPERATOR owns while a run is live. The engine may not write
    #: these from the copy it captured at ``start()``.
    _OPERATOR_OWNED = ("auto_resume",)

    def save_run_state(self, session: Session) -> None:
        """Write a session the RUN owns, preserving operator-owned fields.

        The engine holds one ``Session`` from ``start()`` and writes the whole
        object back on every frame (the ledger write) and again from each
        thumbnail render. The API edits the SAME session by loading a fresh
        copy, mutating it and saving -- and ``auto_resume`` on an ACTIVE
        session is an edit the route allows deliberately. Two writers, two
        objects, and the engine's fires every couple of minutes, so a 200 from
        PATCH /api/sessions/<id> was undone within one exposure. Measured
        2026-08-24; first misread as the abort disarm failing.

        Re-reading before the write is not enough on its own: without the lock
        an in-flight frame whose read happened BEFORE the operator's write
        still puts the stale value back, and every later read then returns the
        engine's own stale value. The window is milliseconds at a two-minute
        cadence and reliable at test exposures -- a race, which is to say a
        bug that waits for a bad night. The lock closes it: the run's read and
        write are one critical section, and plain ``save`` takes the same lock.

        Mutates ``session`` as well as the file, so the caller's long-lived
        copy stops being stale rather than silently diverging again.

        THE RE-READ IS SKIPPED WHEN THE FILE IS THE ONE THIS PROCESS LAST
        WROTE (#514, spec risk 8). The re-read parsed and validated every frame
        the project holds to recover one bool: 41.7 ms of the 158.6 ms this
        call cost at 10 000 frames, after every banked frame, on the event
        loop. Every write this process makes goes through ``save``, which
        records the file's stamp and the operator-owned values it wrote
        (``_written``), the PATCH that disarms included. So when the file's
        stamp is still the recorded one, the values on disk are the recorded
        values, and they are taken from the record. A writer from outside
        (a hand edit, another process, a restore) changes the stamp: a
        replace gives the file a new inode, and an edit in place a new size or
        mtime. Then the file is read as before. The one write this cannot see
        is an in-place rewrite of the same size inside one tick of the
        filesystem's clock after this process's own write, which is no editor
        and no writer of this store.
        """
        with self._write_lock:
            path = self._path(session.id)
            written = self._written.get(str(path))
            if written is not None and written[0] == _file_stamp(path):
                for field, value in written[1].items():
                    setattr(session, field, copy.deepcopy(value))
            else:
                try:
                    stored = self.load(session.id)
                except (KeyError, Exception):
                    stored = None
                if stored is not None:
                    for field in self._OPERATOR_OWNED:
                        setattr(session, field, getattr(stored, field))
            self.save(session)

    def _prune(self) -> None:
        sessions = self.load_all()
        excess = len(sessions) - MAX_SESSIONS
        if excess <= 0:
            return
        prunable = sorted(
            (s for s in sessions if s.status in ("complete", "abandoned")),
            key=lambda s: s.updated_ts)
        # dormant/active are NEVER pruned — the store may exceed the soft cap
        # in the pathological all-live case (spec §2).
        for s in prunable[:excess]:
            self.delete(s.id)

    def backup(self, session_id: str) -> Path:
        """Copy ``<id>.json`` to ``<id>.json.bak`` and return the copy's path.

        For a rewrite a person asked for and may want back: ADOPT re-keys a
        pre-S1 ledger's frames onto the deterministic step ids, and a wrong
        match would otherwise leave no record of where each frame was counted
        before. Never on the per-frame writes, which churn too fast for a
        backup to mean anything.

        A copy, never a move, so the live file is never absent (the
        ``write_json_atomic`` rule), and hardened like the file it copies.
        Taken under the write lock so it cannot copy a half-finished write.
        ``<id>.json.bak`` does not match ``*.json``, so no listing ever reads
        it as a second session. Raises on failure: a caller that asked for a
        backup must not rewrite the ledger without one. ``delete`` keeps it
        when the live file has become unreadable (#266).
        """
        path = self._path(session_id)          # validates the id (KeyError)
        bak = _backup_path(path)
        with self._write_lock:
            shutil.copy2(path, bak)
            harden_private_file(bak)
        return bak

    def has_orphan_backup(self, session_id: str) -> bool:
        """Whether ``<id>.json.bak`` is there with no ``<id>.json`` beside it
        (#280): the one question ``DELETE`` asks of an id that ``load`` calls
        missing, to tell a backup it can remove from a session that is not
        there. False for an id the store's guard refuses, and when the OS will
        not say (as ``_has_backup``): nothing is claimed that was not seen."""
        try:
            path = self._path(session_id)
            return _backup_path(path).is_file() and not path.is_file()
        except (KeyError, OSError):
            return False

    def backup_mtime(self, session_id: str) -> float | None:
        """When ``<id>.json.bak`` was written, or None when there is none or
        the OS will not say (#280): the date a restore brings the session log
        back to, which the restore's answer reports."""
        try:
            return _backup_path(self._path(session_id)).stat().st_mtime
        except (KeyError, OSError):
            return None

    def restore_backup(self, session_id: str) -> Session:
        """Replace a MISSING or UNREADABLE ``<id>.json`` with ``<id>.json.bak``
        and return the session it holds (#280).

        THE REST OF #266. ``DELETE`` of an unreadable file keeps the backup
        because it can be the last good copy of the ledger, and nothing could
        bring it back short of a shell and a rename.

        THE BACKUP IS JUDGED BY THE JUDGMENT EVERY READER MAKES, ``_parsed``
        and ``_session_from_file``, so a restore can never put in place a file
        the store would then refuse: ``KeyError`` when there is no backup,
        ``SessionUnreadable`` (the store's own reason, naming the session) when
        it is damaged, and the same when the id inside it is not ``session_id``,
        because ``save`` writes the file named by the id inside, so a
        hand-edited backup would otherwise write a DIFFERENT session's file and
        leave this one missing.

        NEVER OVER A READABLE FILE (``LiveSessionReadable``). That file is the
        current ledger and usually holds frames the backup does not; a restore
        is a way back from damage, not an undo. The live file is judged here
        and not through ``load``, which calls a file the OS will not open
        MISSING: an ``OSError`` that is not "no such file" reaches the caller
        instead (a locked ledger is not a gone one, the rule ``_parsed`` keeps).

        THE RESTORED SESSION IS NEVER ARMED, AND NEVER ``active``. A backup
        taken mid-run says both, and a restored copy that kept them would join
        the auto-resume singleton unasked (#595) or read as a run the engine
        is not making. ``dormant`` and ``auto_resume`` False are what the
        operator can arm again deliberately. Any other status stays.

        The write is ``save``'s (atomic, replacing a damaged file whole: its
        bytes are gone afterwards). The ``.bak`` STAYS, byte for byte, as it
        does beside any readable session until its DELETE, and so do the
        thumbnails, which belong to the ledger it holds. All of it under the
        write lock, so no worker-thread write lands between the judgment and
        the write; the route holds ``write_locked`` around the call for the
        same reason as the delete's (#212), and the lock is re-entrant."""
        path = self._path(session_id)          # validates the id (KeyError)
        bak = _backup_path(path)
        with self._write_lock:
            try:
                raw = _parsed(bak)
            except FileNotFoundError:
                raise KeyError(session_id) from None
            except SessionUnreadable as e:
                raise SessionUnreadable(session_id, e.reason) from e
            session = _session_from_file(raw, session_id)
            if session.id != session_id:
                raise SessionUnreadable(session_id, OTHER_SESSION)
            try:
                _session_from_file(_parsed(path), session_id)
            except (FileNotFoundError, SessionUnreadable):
                pass                            # missing or damaged: restorable
            else:
                raise LiveSessionReadable(session_id)
            session.auto_resume = False
            if session.status == "active":
                session.status = "dormant"
            self.save(session)
            return session

    def delete(self, session_id: str, *,
               keep_backup: bool = False) -> Path | None:
        """Remove the session file, its ADOPT backup and its thumbs directory.
        NEVER touches FITS. Returns the backup it kept, or None.

        The backup goes with it: a ``.bak`` left behind is a copy of a ledger
        the operator deleted, which nothing lists and nothing would remove.

        EXCEPT WHEN THE FILE COULD NOT BE READ (#266): the route passes
        ``keep_backup`` for that file, and then a ``.bak`` beside it stays,
        byte for byte, and so does the thumbs directory, which belongs to the
        ledger the backup holds. The ``.bak`` is the copy ``backup`` took
        before an ADOPT rewrote the ledger, so for a ledger damaged since, it
        can be the last good record of which frames were accepted for which
        step, and the operator pressed DELETE on the damaged file, not on
        that. With no ``.bak`` there, the thumbnails belong to nothing that
        can be read, and go as before. The backup is looked for before
        anything is removed, so a stat that fails removes nothing.

        UNDER THE WRITE LOCK (#212), like every other write. Without it a
        worker-thread delete could unlink a session in the middle of a
        ``write_locked`` section that had just re-read it and decided to
        start it (Run CONTINUE, ResumeArm), and ``engine.start``'s save then
        put a deleted ledger back on disk, running. Re-entrant, so the prune
        sweep, which deletes from inside ``save``, still works, and so does
        the DELETE route, which calls this inside its own section.

        It never reads the file, so a file the store cannot read goes the
        same way (#242); whether it MAY go, and whether its backup stays, is
        the route's decision, since the route has read it."""
        path = self._path(session_id)          # validates the id (KeyError)
        bak = _backup_path(path)
        with self._write_lock:
            keep = keep_backup and bak.is_file()
            if path.exists():
                path.unlink()
            if keep:
                return bak
            if bak.exists():
                bak.unlink()
            side_dir = _sessions_dir() / session_id
            if side_dir.is_dir():
                shutil.rmtree(side_dir, ignore_errors=True)
            return None

    def thumbs_dir(self, session_id: str) -> Path:
        self._path(session_id)                 # id validation only (KeyError)
        return _sessions_dir() / session_id / "thumbs"

    def list(self) -> list[dict]:
        """Lightweight rows for GET /api/sessions (spec §6), newest first,
        with a row for every file the store cannot read (``_unreadable_row``,
        #242) sorted in among them by the file's mtime.

        ``nights`` IS THE OBSERVING NIGHTS, ``len(s.observing_nights())``
        (#430, S7 orchestrator ruling 7), the one reading the flow card and
        CONTINUE's answer give. It was ``len(s.nights)``, one report id per
        ``engine.start``, so a session restarted the same night read "2
        nights" in the Sessions panel beside "night 1" on its flow card.
        Display only: nothing that decides what runs reads these rows, the
        list route is their one reader.

        A BACKUP WHOSE FILE IS GONE GETS A ROW TOO (``_orphan_row``, #280),
        after the walk: it is not one of ``_entries``, so ``load_all`` and
        every scan still pass it by, and only this list says it is there."""
        rows: list[dict] = []
        for path, raw, s, why in self._entries():
            if s is None:
                row = _unreadable_row(path, raw, why)
                if row is not None:
                    rows.append(row)
                continue
            rows.append({
                "id": s.id, "name": s.name, "status": s.status,
                "created_ts": s.created_ts, "updated_ts": s.updated_ts,
                "nights": len(s.observing_nights()),
                "accepted": s.total_accepted(),
                "total": s.plan.total_frames(), "auto_resume": s.auto_resume,
                "owed": s.owed(), "origin": s.origin, "origin_id": s.origin_id,
                # The session this one waits behind (#598): the Sessions
                # panel's "next: ..." line reads it, and a viewer cannot tell
                # a queued session from an unarmed one without it.
                "queued_behind": s.queued_behind,
            })
        # An id that already has a row is not an orphan: a DELETE of its
        # unreadable file can land between the walk above and the scan here,
        # and the backup it kept would then be listed twice in one answer.
        seen = {row["id"] for row in rows}
        for bak in _orphan_backups():
            orphan = _orphan_row(bak) if _backup_id(bak) not in seen else None
            if orphan is not None:
                rows.append(orphan)
        rows.sort(key=lambda r: r["updated_ts"], reverse=True)
        return rows

    def boot_sweep(self) -> int:
        """Power-cut orphans: any ``active`` session on disk at boot (the engine
        is never running at boot) -> ``dormant`` (spec §4). Returns the count.

        AND IT COUNTS THE DEATH. Being ``active`` here is not just a stale
        status to tidy up, it is the only evidence that exists of a crash that
        took the whole process with it: ``_finalize_report`` never ran, so the
        in-process crash counter never saw it. Without this, the supervisor
        relaunches, auto-resume restarts the same run, it dies in the same
        place, and the loop goes round all night with ``crash_resumes`` still
        at 0 and the give-up ladder unreachable.

        A clean ending of ANY kind resets the counter, so one power blip a
        night never accumulates toward a stow — only deaths with no clean
        ending between them do.
        """
        n = 0
        for s in self.load_all():
            if s.status == "active":
                s.status = "dormant"
                s.crash_resumes += 1
                self.save(s)
                n += 1
        return n

    def recoverable(self) -> Session | None:
        """Most recently updated dormant session WITH frames (recover routes)."""
        dormant = [s for s in self.load_all()
                   if s.status == "dormant" and s.frames]
        dormant.sort(key=lambda s: s.updated_ts, reverse=True)
        return dormant[0] if dormant else None

    #: The unreadable armed files ``armed`` has said so about, by path: once
    #: per file per process (#416). On the class, like ``_write_lock``, so
    #: every store in the process shares it.
    _said_armed_unreadable: set[str] = set()

    def armed(self) -> Session | None:
        """The auto_resume-armed dormant session. PATCH enforces the singleton;
        most-recent wins defensively if files were hand-edited.

        AN ARMED FILE IT CANNOT READ IS SAID, ONCE (#416). The same walk as
        ``load_all`` (``_entries``), which skips such a file: a session
        whose plan holds a step #334 now refuses (``DarkFlat``) dropped out
        of auto-resume, and the tick saw exactly what "disarmed from the UI"
        looks like, so nothing on any screen or in the night log said the
        campaign would not resume (the 2026-08-11 shape, recorded at
        ``ResumeArm.tick``). The file is still not a session this can start,
        and it is never rewritten here: ``_say_armed_unreadable`` logs."""
        armed: list[Session] = []
        for path, raw, s, why in self._entries():
            if s is None:
                self._say_armed_unreadable(path, raw, why)
            elif s.is_armed():
                armed.append(s)
        armed.sort(key=lambda s: s.updated_ts, reverse=True)
        return armed[0] if armed else None

    def _say_armed_unreadable(self, path: Path, raw,
                              reason: str | None) -> None:
        """One warning for an unreadable file that auto-resume would have
        started, naming the session and the store's reason, the first time
        this process meets it; nothing for any other file (#416).

        ARMED IS ``auto_resume`` TRUE WITH A STATUS OF DORMANT OR ACTIVE, as
        the raw file states them. Dormant is what ``armed`` answers for a
        readable file. Active is what a deploy over a live run leaves: the
        engine was writing the file when the process stopped, and
        ``boot_sweep``, which turns an active session dormant so it can be
        resumed, reads through ``load_all`` and cannot sweep a file it
        cannot read, so that file never becomes dormant on disk. A file
        that is complete, disarmed or states no status (#218) was never going
        to be resumed, and one that is not JSON states nothing.

        ONCE, because ``armed`` runs on every ResumeArm tick and in the
        routes that ask about tonight, and the file stays as it is until
        somebody repairs or deletes it. Keyed by path, never cleared: a file
        repaired and broken again in the same process is not said twice. A
        log line that cannot be written never costs the scan its answer."""
        if (not isinstance(raw, dict) or raw.get("auto_resume") is not True
                or _stated_status(raw) not in ("dormant", "active")):
            return
        key = str(path)
        if key in self._said_armed_unreadable:
            return
        self._said_armed_unreadable.add(key)
        try:
            from ..events import bus
            bus.log("warning",
                    f"auto-resume: session '{_raw_name(path, raw)}' "
                    f"({path.stem}) is armed but its file cannot be read, so "
                    f"auto-resume will not start it ({reason}). Repair the "
                    f"file, or delete it from the sessions list.",
                    "sequence")
        except Exception:      # noqa: BLE001 - a log line never costs the scan
            pass


session_store = SessionStore()


def _safe_unlink(path: Path) -> None:
    """``path.unlink(missing_ok=True)`` that also swallows ``OSError`` (a
    locked/AV-held file on Windows) — legacy-file cleanup must never crash
    the boot migration, on any exit path."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def migrate_legacy_resume() -> Session | None:
    """One-shot boot migration of the retired ``.sequence_resume.json`` (spec
    §2): positional ``"ti:si"`` counts map onto the ids pydantic backfills
    during plan validation (position-preserving), synthesized as N accepted
    placeholder frames per step so ``done_map()`` seeds a resume at the exact
    same counts. The legacy file is deleted afterward on every exit path —
    even when unparseable — it is single-slot garbage either way, and this
    function must never raise on a corrupt legacy file. A bad top-level
    ``ts`` falls back to ``now()``; a bad individual ``done`` entry (bad key
    or non-numeric count) is skipped and the rest of the migration proceeds.

    SYNTHESIZED FRAMES CARRY NO ``ts`` (#265). The legacy file names only ONE
    instant for the whole run (its own top-level ``ts``, read above as
    ``created_ts``), never one per frame, so stamping each synthesized frame
    with ``now()`` — the migration's own clock, not a capture's — invented a
    capture time out of nothing. A body step's ADOPT match (H3 orchestrator
    ruling 7, ``continuation._capture_times``) reads a frame's ``ts`` as when
    it was taken and checks the old pointing against the ephemeris at that
    instant; fed the migration time instead, it almost always finds the body
    elsewhere and leaves the step unmatched for the wrong reason, and could in
    principle find it RIGHT by coincidence and match wrongly. Left at the
    model default (0.0, "never stamped"), ``_capture_times``'s existing
    ``_usable`` filter already treats these frames exactly like a frame the
    engine never got to stamp, so the step is unmatched by the rule that
    says "no real capture time", not by luck."""
    legacy = _hubmod.CAPTURE_DIR / ".sequence_resume.json"
    try:
        raw = json.loads(legacy.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        _safe_unlink(legacy)
        return None
    try:
        plan = SequencePlan.model_validate(raw.get("plan") or {})
    except Exception:
        _safe_unlink(legacy)
        return None
    report_id = str(raw.get("report_id") or "")
    now = time.time()
    try:
        created_ts = float(raw.get("ts") or now)
    except (TypeError, ValueError):
        created_ts = now
    s = Session(name=plan.name or "Tonight",
                created_ts=created_ts, updated_ts=now,
                status="dormant", plan=plan,
                nights=[report_id] if report_id else [])
    for key, count in (raw.get("done") or {}).items():
        try:
            ti, si = (int(x) for x in str(key).split(":", 1))
            target = plan.targets[ti]
            step = target.steps[si]
            n = int(count)
        except (TypeError, ValueError, IndexError):
            continue
        for _ in range(n):
            # NO ``ts`` (#265): see the "SYNTHESIZED FRAMES" note above. The
            # model default (0.0) is what marks a synthesized frame apart
            # from a captured one.
            s.frames.append(SessionFrame(
                night=report_id, target_id=target.id,
                step_id=step.id, path="", auto_accepted=True))
    session_store.save(s)
    _safe_unlink(legacy)
    return s
