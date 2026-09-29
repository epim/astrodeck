"""The mosaic panel order: which panel a group visits next (mosaic spec 5.2,
5.9, 2.3; U-01, #189).

ONE FUNCTION FOR EVERY CALLER ON THE SERVER. The S2 scheduler re-sorts a
group's slice of ``remaining`` at the run's start and at every pass boundary
(5.1), and ResumeArm re-centres on the panel it picks (5.9). Each sorting for
itself would drift, and the panel the Monitor calls next would not be the one
the mount goes to. So they share this function, and it decides nothing but
the order. The progress route does not call it yet (spec Revision 6, row 4).

THE TARGET MODAL COPIES THE RULE (#412 item 1). S4 built the modal's run-order
numbering (2.3, 2.4 PANELS) as a TypeScript sort that never reaches this
function: ``ui/src/components/flows/framing/sections/PanelsSection.tsx``,
``snakeIndex`` and ``panelRows``. It copies two of the keys here, the snake
index and least complete first, and orders "Grid order" by the snake alone,
because the modal has the progress route's counts and neither visit times nor
the site, as ResumeArm has no visit times either; "Setting first" it lists in
grid order and says so. The two copies are held to one table,
``tests/fixtures/panel_order_cases.json``, which
``test_panel_order_fixture.py`` grades against this module and
``ui/src/components/flows/framing/__tests__/panelOrderFixture.test.ts``
against ``panelRows``: a change to either copy's snake or least-complete
rule turns its own side red. A change here that the modal should follow
belongs in the fixture first.

PURE, ON ONE SNAPSHOT PER PASS. The function walks no ledger and reads no
clock. The caller takes one :class:`OrderSnapshot`: the fraction banked per
panel, when each was last visited, how long until each reaches its floor, and
where the last visit was. Counts are snapshotted once per pass (5.2) because
``Session.accepted_by_step`` walks every frame in the ledger, and a mosaic's
ledger holds thousands by the third night. A clock read here would make the
answer depend on when it was asked, so two callers asking about one pass could
be told two orders.

A POLICY ONLY REORDERS. Every panel passed in comes out exactly once, whatever
the snapshot says about it: complete, never visited, never setting. Removing a
panel (complete, set aside, not eligible) is the scheduler's decision and is
made before the call. An order function that could drop a panel could starve
it, and 5.2's promise that every eligible panel gets one visit per pass rests
on this one.

THE SNAKE INDEX is the panel's position in ``framing.compute_mosaic``'s panel
list: row by row, even rows by ascending column and odd rows by descending
column, which keeps each hop to a neighbour. It is computed from
``(panel_row, panel_col, cols)`` and never from a position in some list,
because a group's live members are a subset of the grid (skipped panels are
dropped at compile, complete ones leave ``remaining``), and a list index would
shift as they go. ``cols`` is the layout's column count, not the live members'.

EVERY TIE-BREAK ENDS IN THE SNAKE INDEX, which is unique per panel, so the
order is total: no two panels compare equal, and the output does not depend
on the order the caller listed them in. With nothing banked, nothing visited
and nothing setting, every policy runs the snake, and a 3x2 is
``1-1, 1-2, 1-3, 2-3, 2-2, 2-1`` (5.2's worked example).

LABELS are 1-based ``row-col`` in the server convention (2.3): row 1 is the
north edge and col 1 the west edge at angle 0, the numbers ``to_plan`` puts in
a panel's name.

NaN NEVER SORTS. A NaN compares false with everything, so a sort would put
that panel anywhere and say nothing, and the order would be wrong without
looking wrong. A non-finite number in the snapshot, or a position outside the
grid, is a caller bug and raises.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypeVar

Policy = Literal["least_complete", "setting_first", "grid"]

#: The policies ``TargetGroup.order`` may name (spec 3.4), the default first.
POLICIES: tuple[str, ...] = ("least_complete", "setting_first", "grid")


class PanelLike(Protocol):
    """What the order reads from a group member. A ``sequence.models.Target``
    of a mosaic group has all three: ``to_plan`` sets ``panel_row`` and
    ``panel_col`` (0-based) on every member, a 1x1 block included (3.3)."""

    @property
    def id(self) -> str: ...

    @property
    def panel_row(self) -> int | None: ...

    @property
    def panel_col(self) -> int | None: ...


P = TypeVar("P", bound=PanelLike)


@dataclass(frozen=True)
class OrderSnapshot:
    """One pass's worth of what the order depends on, taken once by the caller.

    Each map is keyed by target id. A panel missing from a map takes the value
    a panel with no record has: nothing banked, never visited, does not set
    tonight. So a caller building the maps from the ledger may leave out the
    panels the ledger has never seen.
    """

    #: Fraction of the panel's owed frames banked, 0 to 1 (accepted frames
    #: when the plan counts accepted subs). Missing: 0.0.
    fraction_banked: Mapping[str, float] = field(default_factory=dict)
    #: Wall-clock time of the panel's last visit, from ledger timestamps (5.2).
    #: Missing or None: never visited.
    last_visit_ts: Mapping[str, float | None] = field(default_factory=dict)
    #: Seconds until the panel reaches its floor or the horizon mask. Missing
    #: or None: it does not set tonight.
    time_to_floor_s: Mapping[str, float | None] = field(default_factory=dict)
    #: 0-based ``(panel_row, panel_col)`` of the panel visited last, for
    #: ``grid``. It need not be among the panels ordered: a panel completed on
    #: that visit has left the list, and the walk still resumes after its
    #: place. None: no visit yet.
    last_visited: tuple[int, int] | None = None


def snake_index(row: int, col: int, cols: int) -> int:
    """The panel's index in ``framing.compute_mosaic``'s panel list, for a
    0-based ``(row, col)`` in a grid ``cols`` wide.

    Raises ``ValueError`` for a position outside the grid. Computed anyway it
    would collide with a real panel's index (``(0, 2)`` in a grid 2 wide is 2,
    which is also ``(1, 1)``), and two panels would tie on the one key that is
    meant to be unique.
    """
    for name, value in (("row", row), ("col", col), ("cols", cols)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"panel {name} must be an integer, not {value!r}")
    if cols < 1:
        raise ValueError(f"a mosaic has at least one column, not {cols}")
    if row < 0 or not 0 <= col < cols:
        raise ValueError(
            f"panel ({row}, {col}) is outside a grid {cols} columns wide")
    return row * cols + (col if row % 2 == 0 else cols - 1 - col)


def panel_label(row: int, col: int) -> str:
    """The 1-based ``row-col`` label of a 0-based panel position (spec 2.3)."""
    return f"{row + 1}-{col + 1}"


def _number(value: object, what: str, panel: PanelLike,
            *, none_ok: bool) -> float | None:
    """``value`` as a finite float, None where the snapshot may say "no
    record", and a ``ValueError`` naming the panel for anything else."""
    if value is None and none_ok:
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(
            f"{what} of panel {panel.id} must be a number, not {value!r}"
        ) from None
    if not math.isfinite(f):
        raise ValueError(f"{what} of panel {panel.id} is {value!r}")
    return f


def order_panels(
    panels: Iterable[P],
    *,
    cols: int,
    policy: Policy = "least_complete",
    snapshot: OrderSnapshot | None = None,
) -> list[P]:
    """The panels in the order the group visits them this pass.

    ``panels`` are the members to order, each with its grid position; ``cols``
    is the layout's column count; ``snapshot`` is the pass's one snapshot
    (empty: nothing banked, nothing visited, nothing setting). Returns a new
    list holding every panel passed in exactly once, as the same objects.

    * ``least_complete`` (the default): fraction banked, ascending. Ties go to
      the least recently visited, never-visited first, then the snake.
    * ``setting_first``: time to floor, ascending, with a panel that does not
      set tonight last. Ties go to fraction banked, then the snake. The visit
      time is not read: 5.2 names no such tie-break for this policy.
    * ``grid``: the snake, rotated to start after the panel visited last.

    Raises ``ValueError`` for an unknown policy, a member or last-visited
    position outside the grid, or a snapshot number that is not finite.
    """
    if policy not in POLICIES:
        raise ValueError(
            f"unknown panel order {policy!r}; expected one of {POLICIES}")
    snap = snapshot if snapshot is not None else OrderSnapshot()

    last: int | None = None
    if snap.last_visited is not None:
        last = snake_index(snap.last_visited[0], snap.last_visited[1], cols)

    def key(p: P) -> tuple:
        snake = snake_index(p.panel_row, p.panel_col, cols)  # type: ignore[arg-type]
        # Every number is checked whichever policy reads it: the snapshot is
        # one object for the pass, and a NaN in it is the caller's bug under
        # any policy.
        fraction = _number(snap.fraction_banked.get(p.id, 0.0),
                           "fraction banked", p, none_ok=False)
        visited = _number(snap.last_visit_ts.get(p.id),
                          "last visit time", p, none_ok=True)
        floor = _number(snap.time_to_floor_s.get(p.id),
                        "time to floor", p, none_ok=True)
        if policy == "least_complete":
            # False sorts before True, so never-visited leads, then the oldest.
            return (fraction, visited is not None,
                    0.0 if visited is None else visited, snake)
        if policy == "setting_first":
            return (floor is None, 0.0 if floor is None else floor,
                    fraction, snake)
        # grid: the panels after the last visit first, then the wrap.
        return (last is not None and snake <= last, snake)

    return sorted(panels, key=key)
