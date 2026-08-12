"""Is it cloudy? — the debounced, aged answer the sequencer is allowed to act on.

``imaging.clouds.cloud_score`` judges ONE frame, and every linear sub already
carries its verdict (``hub._publish_preview`` puts it in ``info["cloud"]``). This
turns that stream of per-frame opinions into a single state a run can be steered
by, and it exists as its own module because acting on a raw per-frame verdict
would be wrong in three separate ways:

* **A single frame is not weather.** A satellite, a passing branch, a bad
  autoguider excursion or one genuinely thin patch can make a clear night score
  cloudy. Holding a run on one frame would stop the night for a bird.
* **The answer decays.** During a hold the camera is not taking science frames,
  so the newest verdict gets older every second. Past a budget it stops being
  evidence and becomes a memory, and a run must not resume on a memory.
* **"I don't know" is a real answer, and it is not "clear".** Everything here is
  tri-state. ``None`` means no one can currently say, and the instruction
  evaluator treats that as indeterminate: it fires nothing and re-arms nothing.

Pure and clock-injectable. The engine owns one of these; nothing in it touches a
device, a bus or a wall clock it was not handed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: How many consecutive frames must agree before the state flips.
#:
#: Two, not one, and not five. One frame flips on a satellite. Five, at 180 s a
#: sub, is fifteen minutes of shooting through cloud before anything reacts -
#: which is most of what the feature was meant to save.
DEFAULT_CONSECUTIVE = 2

#: How long a verdict stays evidence, in seconds.
#:
#: 900 s is deliberately generous next to a typical 180-300 s sub: it survives a
#: dither, an autofocus run and a meridian flip without going blind, and still
#: expires well inside the 45-minute default hold. The number that matters is
#: not this one but the RULE - past the budget the answer becomes None, never
#: "clear".
DEFAULT_MAX_AGE_S = 900.0


@dataclass
class CloudState:
    """The rolling verdict. One per run; fed by the engine, read by the trigger."""

    consecutive: int = DEFAULT_CONSECUTIVE
    max_age_s: float = DEFAULT_MAX_AGE_S

    #: The last agreed answer, or None until enough frames have agreed once.
    _state: bool | None = field(default=None, repr=False)
    #: The candidate the recent frames are voting for, and how many have voted.
    _pending: bool | None = field(default=None, repr=False)
    _votes: int = field(default=0, repr=False)
    #: When the most recent OBSERVATION landed - not when the state last changed.
    #: Age is about how long ago somebody looked, not how long ago the sky moved.
    _last_obs_ts: float = field(default=0.0, repr=False)
    #: Kept for the log line and the status payload, so a human can see WHY.
    last_reason: str = field(default="", repr=False)
    last_score: float | None = field(default=None, repr=False)

    # ------------------------------------------------------------------ write

    def observe(self, cloudy: bool, ts: float, *, score: float | None = None,
                reason: str = "") -> bool | None:
        """Record one frame's verdict. Returns the state AFTER this observation.

        The vote is reset by DISAGREEMENT, not by time: two cloudy frames either
        side of a clear one are not two consecutive cloudy frames, because the
        clear one is evidence too.
        """
        self._last_obs_ts = ts
        self.last_score = score
        self.last_reason = reason
        if cloudy == self._state:
            # Already there. Clear any half-formed vote for the other side, so a
            # single contrary frame during a settled state cannot accumulate.
            self._pending = None
            self._votes = 0
            return self._state
        if cloudy != self._pending:
            self._pending = cloudy
            self._votes = 1
        else:
            self._votes += 1
        if self._votes >= self.consecutive:
            self._state = cloudy
            self._pending = None
            self._votes = 0
        return self._state

    def forget(self) -> None:
        """Drop the state without dropping the clock.

        Used when the thing being judged changes out from under the state - a
        new target, a filter change - so the next verdict starts a fresh vote
        rather than being compared against a sky judged through a different
        filter. A narrowband sub and an L sub do not score the same, and the
        state must not treat that difference as weather.
        """
        self._state = None
        self._pending = None
        self._votes = 0

    # ------------------------------------------------------------------- read

    def age_s(self, now: float) -> float | None:
        """Seconds since the last observation, or None if there has never been one."""
        return None if self._last_obs_ts <= 0 else max(0.0, now - self._last_obs_ts)

    def cloudy(self, now: float) -> bool | None:
        """The tri-state answer, or None when nobody can currently say.

        None on ALL of: never observed, not yet enough agreeing frames, and the
        newest observation older than the budget. Callers must never coerce this
        to a bool - a `not cloudy` on None reads as "clear", which is the exact
        misreading this module exists to prevent.
        """
        if self._state is None:
            return None
        age = self.age_s(now)
        if age is None or age > self.max_age_s:
            return None
        return self._state

    def describe(self, now: float) -> str:
        """One line for a log or a status payload, honest about not knowing."""
        v = self.cloudy(now)
        age = self.age_s(now)
        if v is None:
            if self._state is None:
                return ("no cloud reading yet"
                        if age is None else
                        f"cloud reading undecided ({self._votes} frame(s) so far)")
            return (f"cloud reading is {age:.0f}s old — too stale to act on "
                    f"(budget {self.max_age_s:.0f}s)")
        word = "cloudy" if v else "clear"
        detail = f" — {self.last_reason}" if self.last_reason else ""
        return f"{word}, from a reading {age:.0f}s old{detail}"


def verdict_from_info(info: dict) -> tuple[bool, float | None, str] | None:
    """Pull a frame's cloud verdict out of what ``hub.capture`` returned.

    Returns ``(cloudy, score, reason)`` or None when the frame carried no
    verdict at all - which happens for a stretched frame, because the contrast
    metric needs unstretched pixels (``hub._publish_preview`` gates on
    ``data_is_linear``). A frame nobody could judge is not a clear one, so the
    caller must feed None to nothing rather than defaulting it.
    """
    cloud = (info or {}).get("cloud")
    if not isinstance(cloud, dict) or "cloudy" not in cloud:
        return None
    score = cloud.get("score")
    return (bool(cloud["cloudy"]),
            float(score) if isinstance(score, (int, float)) else None,
            str(cloud.get("reason") or ""))
