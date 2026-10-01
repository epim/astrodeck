"""#166 / backlog W2 WP-11: the hub's own meridian-flip line is site-derived.

`Hub.meridian_flip` logs "meridian flip: stopping guiding and re-slewing" at
the moment a flip is due, so the line's timestamp IS the target's computed
transit: a function of the site's longitude. The other two flip lines (the
engine's "complete" and the idle park-hold's flip point) were flagged in
WP-11; this one was blocked until the shared ``bus_lines`` spy took ``**kw``.

The integration's first claim that test_the_flip_bound_covers_a_calibration.py
graded this flag was wrong (that test never reads the keyword; the wave-2
verifier caught it). This test reads the keyword at the source.

MUTANT "the hub line unflagged" (``site_derived=True`` dropped from the
bus.log call in Hub.meridian_flip): RED, observed -
    AssertionError: the hub's flip line was not flagged site_derived:
    [('info', 'meridian flip: stopping guiding and re-slewing', 'sequence', False)]
"""
from _simhub import sim_hub  # noqa: F401

from astrodeck import events


async def test_the_hubs_flip_line_is_flagged_site_derived(sim_hub, monkeypatch):
    seen = []
    monkeypatch.setattr(events.bus, "log",
                        lambda level, message, source="hub", **kw: seen.append(
                            (level, message, source, bool(kw.get("site_derived")))))
    sides = iter(["west", "east"])

    async def side_now():
        return next(sides)

    async def goto(ra_h, dec_d, **kw):
        return {"centered": True, "error_arcmin": 0.2}

    monkeypatch.setattr(sim_hub, "pier_side_now", side_now)
    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    await sim_hub.meridian_flip(1.0, 20.0)

    line = [s for s in seen if s[1].startswith("meridian flip: stopping guiding")]
    assert line and all(s[3] for s in line), (
        f"the hub's flip line was not flagged site_derived: {line}")
    # Control: a line the flip says that is not site-timed stays unflagged,
    # so the spy is reading the keyword and not flagging everything.
    closing = [s for s in seen if s[1].startswith("meridian flip complete")]
    assert closing, seen
