"""CONTINUE: carrying a flow's dormant session onto tonight's compile (#189 S1,
spec 5.9 and D6).

ONE LEDGER PER FLOW. Pressing Run on night two hands the flow's own dormant
session to ``engine.start(session=)`` with the fresh compile as its plan, the
same call a resume makes, so ``Session.owed()`` stays the only definition of
finished and there is no second ledger to disagree with it. That works because
``to_sequence_plan(flow_id=)`` names a step the same way on every compile and
the ledger counts frames by step id alone.

What this module answers is whether carrying the ledger over is safe to do
without asking first, and it answers from the session and the new plan alone:
no store, no engine, no clock, no devices. ``run_flow`` in ``api/app.py`` owns
the write lock, the refusals and the start.

* ``plan_replace_report`` - which steps carry over, which are new, and which
  steps that hold frames the new plan no longer has. It is also what
  ``PATCH /api/sessions/{id}`` reports for a plan edit, which is why it lives
  here and both call it: two copies of "what counts as dropped" would drift.
* ``saved_before_s1`` / ``adopt_matches`` / ``apply_adoption`` - a session
  saved before S1 has uuid4 step ids that no compile will ever produce again,
  so none of its frames count toward anything tonight. ADOPT re-keys the
  frames whose step matches exactly one step of the new plan, on a target
  within ``ADOPT_MAX_SEPARATION_ARCMIN`` of the old one, and leaves the rest
  where they are. It is offered for such a session only: a session
  compiled since S1 that shares no step id with tonight's compile was
  re-framed, and that is the dropped-steps question.
* ``recount`` - a ledger is counted by its FROZEN plan's ``count_mode``, so
  continuing under a different mode recounts every banked frame at once. The
  operator is shown both totals before that happens.
"""
from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field

from ..catalog.coords import angular_sep_deg
from ..sequence.models import SequencePlan
from ..sequence.session import Session


@dataclass(frozen=True)
class ReplaceReport:
    """What replacing a session's plan does to its steps.

    ``kept`` and ``new`` are step ids, sorted. ``dropped`` lists only the old
    steps that HOLD FRAMES and that the new plan lacks: a dropped step with no
    frames loses nothing, so nobody needs to be told about it.
    ``dropped_frames`` is how many ledger entries sit on those steps. They are
    not deleted - the frames list is never edited by a plan replace - they
    simply stop counting toward anything.
    """
    kept: list[str]
    new: list[str]
    dropped: list[str]
    dropped_frames: int

    def merge(self) -> dict[str, list[str]]:
        """The ``merge`` object ``PATCH /api/sessions/{id}`` has always
        answered, key for key."""
        return {"kept": self.kept, "new": self.new, "dropped": self.dropped}


def plan_replace_report(session: Session,
                        new_plan: SequencePlan) -> ReplaceReport:
    """Compare ``session``'s plan with ``new_plan`` by step id.

    A REPORT, NOT A MERGE. Neither caller merges anything: both replace
    ``session.plan`` wholesale afterwards. The refusal on dropped steps is
    CONTINUE's alone (spec 5.9); a PATCH keeps reporting and never refusing.
    """
    old_ids = {st.id for t in session.plan.targets for st in t.steps}
    new_ids = {st.id for t in new_plan.targets for st in t.steps}
    with_frames = {f.step_id for f in session.frames}
    dropped = sorted((old_ids - new_ids) & with_frames)
    gone = set(dropped)
    return ReplaceReport(
        kept=sorted(old_ids & new_ids),
        new=sorted(new_ids - old_ids),
        dropped=dropped,
        dropped_frames=sum(1 for f in session.frames if f.step_id in gone))


# ------------------------------------------------------------------ ADOPT

def _minted(step_id: str) -> bool:
    """True for an id ``flows/identity`` minted: every one of those is a
    uuid5, and ``SequencePlan`` mints uuid4s."""
    try:
        return uuid.UUID(hex=str(step_id)).version == 5
    except ValueError:
        return False


def saved_before_s1(session: Session) -> bool:
    """True when no step of ``session``'s plan carries an id
    ``to_sequence_plan(flow_id=)`` minted: a flow session saved before S1,
    whose uuid4 ids no compile produces again. The one kind of session ADOPT
    is for (spec 5.9).

    ASKED OF THE IDS, NOT OF "NOTHING IS SHARED". A session compiled since S1
    can share no step id with tonight's compile too: a single TARGET is keyed
    on the geometry it is at (spec 3.3), so moving it, or setting its angle,
    re-keys every step. That is a re-frame, whose counts restart (D5), and
    spec 5.9 puts it in the dropped-steps row. Read as pre-S1 it drew the
    adopt question, whose sentence says the session was "saved before flows
    kept their ids" - false - and ADOPT matches on target NAME and recipe, so
    a yes credited the old field's frames to the moved one: the flaw D5
    exists to remove.
    """
    return not any(_minted(st.id)
                   for t in session.plan.targets for st in t.steps)


def _step_key(target, step) -> tuple:
    """What ADOPT matches a pre-S1 step on (spec 5.9): target name, frame
    type, filter, exposure, gain, binning.

    ``count`` is deliberately absent, for the reason the step id leaves it out:
    raising a quota must keep the frames already banked. A None filter and ""
    are one key, because the engine treats both as "do not move the wheel"
    and ``flows/identity.py`` spells both "" in the step id."""
    return (target.name, step.frame_type or "Light", step.filter or "",
            float(step.exposure_s), int(step.gain), int(step.binning))


def _describe(target, step, frames: int, reason: str,
              separation_arcmin: float | None = None) -> dict:
    """One step left as it is. ``separation_arcmin`` is how far its one
    match is on the sky, and null when there is no one match to measure."""
    return {"step_id": step.id, "target": target.name,
            "frame_type": step.frame_type or "Light", "filter": step.filter,
            "exposure_s": step.exposure_s, "gain": step.gain,
            "binning": step.binning, "frames": frames, "reason": reason,
            "separation_arcmin": separation_arcmin}


#: The two fixed reasons a step that holds frames is left as it is. The third,
#: a match on another field, names its distance (``_moved``).
NO_MATCH = "no step in this flow matches it"
AMBIGUOUS = "more than one step matches it"

#: How far apart on the sky an old target and a new one may be for ADOPT to
#: carry frames between them (#189 A4). The match key is a NAME and a recipe,
#: and a name is a label, not a place: #190's wizard wrote the typed name
#: "M16" onto M31's coordinates, so a session filed as "M16" holds frames of
#: Andromeda, and once the flow is corrected to M16 the key still matches,
#: 103 degrees away. Carrying those frames onto M16 is the flaw D5 removes for
#: a re-frame; ``saved_before_s1`` keeps a re-framed S1 session out of ADOPT,
#: and this keeps a moved pre-S1 one from walking back in.
#:
#: 10 arcmin is a first figure, like ``REFRAME_CARRY_FRACTION``: an order of
#: magnitude above the run's centring tolerance (1.2 arcmin), so one field
#: typed twice, or re-entered from a catalogue, is the same field; and the
#: size of the carry threshold spec 3.3 computes for its 3x2 example grid
#: (10.0 arcmin), so ADOPT carries no further than a re-frame would.
ADOPT_MAX_SEPARATION_ARCMIN = 10.0


def _separation_arcmin(old, new) -> float:
    """Great-circle distance between two targets, in arcmin. RA is in HOURS;
    ``angular_sep_deg`` turns it into an angle, so a step in RA shrinks by
    cos(dec) and wraps at 0 h / 24 h as the sky does."""
    return angular_sep_deg(old.ra_hours, old.dec_deg,
                           new.ra_hours, new.dec_deg) * 60.0


def _moved(separation_arcmin: float) -> str:
    return (f"the step in this flow with its name and recipe points "
            f"{separation_arcmin:.1f} arcmin from where these frames were "
            f"taken, more than the {ADOPT_MAX_SEPARATION_ARCMIN:g} arcmin "
            f"ADOPT carries frames across")


@dataclass(frozen=True)
class AdoptMatches:
    """The outcome of matching a pre-S1 session against a fresh compile.

    ``mapping`` is old step id -> (new target id, new step id), for every old
    step that matched exactly one new step and was itself the only old step
    with that key. ``unmatched`` and ``ambiguous`` describe the old steps that
    HOLD FRAMES and did not match; a frameless step has nothing to carry.
    ``frames_matched`` is how many ledger entries the mapping re-keys.
    """
    mapping: dict[str, tuple[str, str]]
    unmatched: list[dict] = field(default_factory=list)
    ambiguous: list[dict] = field(default_factory=list)
    frames_matched: int = 0

    def rest(self) -> list[dict]:
        """Every step left as it is, the ambiguous ones first: those are the
        ones a person can resolve by editing the flow."""
        return [*self.ambiguous, *self.unmatched]


def adopt_matches(session: Session, new_plan: SequencePlan) -> AdoptMatches:
    """Match ``session``'s steps to ``new_plan``'s on ``_step_key``.

    UNIQUE ON BOTH SIDES. A key that two old steps share, or that two new
    steps share, maps nothing: picking one would credit one step's frames to
    the other, which is the #77 fault the step ids exist to prevent. Pre-S1
    flows carry no mosaics, so in practice a key is one TARGET node's one
    recipe, and a collision means the flow itself repeats a recipe.

    ON THE SAME FIELD. A unique match maps only when the two targets are
    within ``ADOPT_MAX_SEPARATION_ARCMIN`` of each other; further apart, the
    old step is listed with its separation (when it holds frames) and nothing
    is re-keyed. The name in the key is a label, and the separation is what
    says the label still points where the frames were taken.
    """
    old_by_key: dict[tuple, list] = {}
    for t in session.plan.targets:
        for st in t.steps:
            old_by_key.setdefault(_step_key(t, st), []).append((t, st))
    new_by_key: dict[tuple, list] = {}
    for t in new_plan.targets:
        for st in t.steps:
            new_by_key.setdefault(_step_key(t, st), []).append((t, st))
    frames_by_step = Counter(f.step_id for f in session.frames)
    mapping: dict[str, tuple[str, str]] = {}
    unmatched: list[dict] = []
    ambiguous: list[dict] = []
    matched = 0
    for key, olds in old_by_key.items():
        news = new_by_key.get(key, [])
        for t, st in olds:
            n = frames_by_step.get(st.id, 0)
            if len(olds) == 1 and len(news) == 1:
                nt, nst = news[0]
                apart = _separation_arcmin(t, nt)
                if apart <= ADOPT_MAX_SEPARATION_ARCMIN:
                    mapping[st.id] = (nt.id, nst.id)
                    matched += n
                elif n:
                    unmatched.append(_describe(t, st, n, _moved(apart),
                                               round(apart, 3)))
            elif n:
                if news:
                    ambiguous.append(_describe(t, st, n, AMBIGUOUS))
                else:
                    unmatched.append(_describe(t, st, n, NO_MATCH))
    return AdoptMatches(mapping=mapping, unmatched=unmatched,
                        ambiguous=ambiguous, frames_matched=matched)


def apply_adoption(session: Session, matches: AdoptMatches) -> int:
    """Re-key ``session`` IN PLACE through ``matches.mapping``; return how many
    frames moved.

    Each matched frame takes the new target id and step id. The old plan's
    matched steps take the new step id too, so ``plan_replace_report`` run
    afterwards reads them as kept rather than dropped - the plan itself is
    about to be replaced, and this is what makes the report describe the
    adoption honestly. Nothing is written: the caller persists the session,
    through ``engine.start``, only once every refusal has passed.
    """
    moved = 0
    for f in session.frames:
        hit = matches.mapping.get(f.step_id)
        if hit is not None:
            f.target_id, f.step_id = hit
            moved += 1
    for t in session.plan.targets:
        for st in t.steps:
            hit = matches.mapping.get(st.id)
            if hit is not None:
                st.id = hit[1]
    return moved


def adopt_detail(frames: int, matched: int) -> str:
    rest = frames - matched
    return (f"this flow's session holds {frames} sub{_s(frames)} under step "
            f"ids no compile produces any more (it was saved before flows kept "
            f"their ids). ADOPT re-keys the {matched} that match exactly one "
            f"step of this flow and leaves {rest} as {_they(rest)} {_are(rest)}"
            f"; START OVER begins a new session and leaves this one on disk")


# ------------------------------------------------------------------ recount

def _counted(session: Session, mode: str) -> int:
    """Frames ``mode`` counts, over the WHOLE ledger and uncapped: the number
    the session itself shows, not the part of it tonight's quota can use."""
    if mode == "accepted":
        return sum(1 for f in session.frames if f.effective())
    return len(session.frames)


def recount(session: Session, new_plan: SequencePlan) -> tuple[int, int]:
    """``(before, after)``: the ledger counted by the session's frozen plan's
    ``count_mode``, and by ``new_plan``'s."""
    return (_counted(session, session.plan.count_mode),
            _counted(session, new_plan.count_mode))


_MODE_WORDS = {"attempts": "every sub taken", "accepted": "accepted subs"}


def recount_detail(old_mode: str, new_mode: str, before: int,
                   after: int) -> str:
    return (f"this session counted {_MODE_WORDS.get(old_mode, old_mode)} "
            f"({before}); counting {_MODE_WORDS.get(new_mode, new_mode)} "
            f"makes it {after}")


def dropped_detail(frames: int) -> str:
    if frames == 1:
        return ("1 sub belongs to a step this flow no longer has; it stays "
                "on disk")
    return (f"{frames} subs belong to steps this flow no longer has; they "
            f"stay on disk")


def _s(n: int) -> str:
    return "" if n == 1 else "s"


def _they(n: int) -> str:
    return "it" if n == 1 else "they"


def _are(n: int) -> str:
    return "is" if n == 1 else "are"
