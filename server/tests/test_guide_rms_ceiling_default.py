# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#854: the frame grader passed trailed frames because the guide-RMS ceiling
shipped OFF.

NGC 7331, night of 2026-10-07. Frame 0411 (guide RMS 1549") and frame 0415
(773") were ACCEPTED: `standards.max_guide_rms` was 0, so `_check_quality`
never compared the guide RMS, and the eccentricity gate passed both at 0.647
and 0.645 under its 0.65 ceiling (a dotted trail of capped-pulse steps reads as
round knots). The run report recorded the 0.

What this file pins:

* the default is now 5.0 arcsec (DEFAULT_MAX_GUIDE_RMS), reaching a bare plan
  through the standards layer, and the two runaway frames are rejected on the
  real grading path, directly and through the frame loop;
* healthy guiding (1.45", 2.3") is kept;
* a stored 0 written before schema 4 is raised once; a 0 written at schema 4,
  and any nonzero choice, are left alone; 0 still turns the gate off;
* a guider reporting raw PIXELS with no image scale is judged through a floor
  (0.1"/px, the smallest real guide scale), so a runaway is rejected whether or
  not the guide focal length is set, and the once-per-run warning says what to
  do first;
* the eccentricity sentence quotes the guide RMS, and both its forms fit the
  UI humanizer's 137 characters;
* on an UNGUIDED plan an idle guider's stale RMS rejects nothing;
* the stopped-guider rejection says what to do.

Every case drives the REAL `_check_quality` (or the real frame loop, or the
real `ConfigStore`); only the guider is a stand-in, and it is a real `Guider`
subclass. Each test's docstring names the production mutant that turns it RED
and the assertion text observed under it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from _simhub import a_real_site

import astrodeck.config as config_mod
import astrodeck.hub as hub_module
from astrodeck.config import CONFIG_SCHEMA, AppConfig, ConfigStore
from astrodeck.events import bus
from astrodeck.guide.base import GuideStats, Guider
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.policy import guide_rms_floor_arcsec, resolve_policy

#: The UI humanizer's word pairs (RULES.md, `ui/src/lib/humanize.ts`). A line
#: carrying one of these pairs is rewritten, so an operator line must not.
_HUMANIZER_PAIRS = (
    ("camera", ("not responding", "timeout", "disconnect")),
    ("nina", ("5", "http", "error")),
    ("plate", ("solve",)),
    ("guid", ("lost",)),
)


def _trips_humanizer(line: str) -> list[str]:
    low = line.lower()
    return [f"{a}+{b}" for a, bs in _HUMANIZER_PAIRS if a in low
            for b in bs if b in low]


class _Guider(Guider):
    """A real ``Guider`` subclass, so the hub's teardown and any isinstance
    check meet the contract, not a duck."""
    name = "stub"

    def __init__(self, **stats):
        self.s = GuideStats(**stats)
        self.connected = True

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def start_guiding(self):
        pass

    async def stop_guiding(self):
        pass

    async def dither(self, pixels: float = 3.0, settle=None):
        pass

    def stats(self):
        return self.s

    async def is_active(self):
        return self.s.guiding


async def _install(hub, stub):
    """Disconnect the guider the hub already has (the sim one), then put the
    stub in its place. The hub's own teardown disconnects whatever is
    installed, which is now the stub."""
    old = hub.guider
    if old is not None:
        await old.disconnect()
    hub.guider = stub


def _said(q) -> list[tuple[str, str]]:
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "log":
            out.append((str(ev.data.get("level", "")),
                        str(ev.data.get("message", ""))))
    return out


def _marks(n: int, ecc: float) -> list[dict]:
    return [{"x": 1.0, "y": 1.0, "hfr": 2.0, "ecc": ecc} for _ in range(n)]


def _info(ecc: float) -> dict:
    """`info` as the grader emits it for frames 0411/0415: HFR 4.29, 200 stars,
    a median under the ceiling and 20 round marks (so the fraction rule, which
    needs 8 marks, runs and passes)."""
    return {"hfr": 4.29, "stars": 200, "ecc": ecc, "star_list": _marks(20, 0.40)}


def _synthetic(median: float, n: int, above: int, hot: float = 0.85) -> dict:
    marks = _marks(above, hot) + _marks(n - above, 0.40)
    return {"ecc": median, "star_list": marks}


async def _bare(stub, **plan_kw) -> SequenceEngine:
    """A bare engine on a bare hub, its plan inheriting the rig's DEFAULT
    standards unless ``plan_kw`` says otherwise, with ``stub`` as the guider."""
    hub = Hub()
    await _install(hub, stub)
    eng = SequenceEngine(hub)
    eng.plan = SequencePlan(name="p", targets=[], **plan_kw)
    return eng


async def _grade(eng, info) -> tuple[bool, list[tuple[str, str]]]:
    q = bus.subscribe()
    try:
        got = eng._check_quality(info)
        return got, _said(q)
    finally:
        bus.unsubscribe(q)


# ------------------------------------------------------------- the default

@pytest.mark.parametrize("rms, ecc", [(1549.0, 0.647), (773.0, 0.645)])
async def test_frames_0411_and_0415_are_rejected_by_default(rms, ecc):
    """THE NIGHT, AS A TEST. Both frames passed the eccentricity gate under its
    0.65 ceiling; with the default ceiling the guide RMS rejects them first.

    Mutant M1 "default stays off" (`config.py`
    ``Field(DEFAULT_MAX_GUIDE_RMS, ...)`` -> ``Field(0.0, ...)``), observed:
    ``AssertionError: frame at guide RMS 1549.0" and ecc 0.647 was accepted
    with the default standards``."""
    eng = await _bare(_Guider(guiding=True, rms_total=rms, is_arcsec=True))
    got, said = await _grade(eng, _info(ecc))
    assert got is False, (
        f'frame at guide RMS {rms}" and ecc {ecc} was accepted with the '
        f"default standards")
    want = f'guide RMS {rms:.2f}" above ceiling 5.00"'
    assert any(want in m for lv, m in said if lv == "warning"), (
        f"no warning carrying {want!r}: {said}")


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    """The `test_flip_before_the_limit.py` sim hub: its own config store under
    tmp_path, the legacy sim guider, a real site."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store, "_path",
                        tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module.config_store, "_cfg", None)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    a_real_site(monkeypatch)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _one_frame_plan(**plan_kw) -> SequencePlan:
    step = ExposureStep(filter="L", exposure_s=0.05, count=1)
    return SequencePlan(name="p", targets=[
        Target(name="T", ra_hours=5.5881, dec_deg=-5.3911, center=False,
               autofocus_first=False, steps=[step])], **plan_kw)


async def test_the_frame_loop_rejects_a_runaway_frame_by_default(sim_hub):
    """The same verdict through the real frame loop, with the rig standard from
    a config store that never set the field.

    Mutant M1, observed: ``AssertionError: a frame shot at guide RMS 1549"
    was banked: rejected=0``."""
    await _install(sim_hub, _Guider(guiding=True, rms_total=1549.0,
                                    is_arcsec=True))
    eng = SequenceEngine(sim_hub)
    eng.start(_one_frame_plan(guide=False, dither_every=0, autofocus_every=0,
                              meridian_flip=False))
    await eng._task
    assert eng._rejected == 1, (
        f'a frame shot at guide RMS 1549" was banked: rejected={eng._rejected}')


@pytest.mark.parametrize("rms", [1.45, 2.3])
async def test_healthy_guiding_is_kept_by_default(rms):
    """1.45" is this rig's measured healthy guiding; 2.3" is the GN-03 walking
    field (a fault, but not a trail). Both are kept at the default.

    Mutant M2 "default too tight" (``DEFAULT_MAX_GUIDE_RMS = 1.0``), observed:
    ``AssertionError: healthy guiding at 1.45" was rejected: [('warning',
    'guide RMS 1.45" above ceiling 1.00"')]``."""
    eng = await _bare(_Guider(guiding=True, rms_total=rms, is_arcsec=True))
    got, said = await _grade(eng, _info(0.45))
    assert got is True, f'healthy guiding at {rms}" was rejected: {said}'


# ----------------------------------------------------------- the migration

def _write(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body), encoding="utf-8")


def _config_lines(monkeypatch) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []

    def _log(level, message, source="hub", **_kw):
        if source == "config":
            seen.append((level, message))

    monkeypatch.setattr(config_mod.bus, "log", _log)
    return seen


def test_a_stored_zero_is_raised_once_on_the_way_past_3(tmp_path, monkeypatch):
    """Every rig on disk carries the old built-in 0. The schema stamp is what
    tells that 0 from an operator's, so the raise happens once, on 3 -> 4, is
    persisted, says so in one line, and a second load says nothing. This is
    also the one place the number 4 is pinned.

    Mutant M3 "no migration" (the ``if stored < 4 ...`` block deleted),
    observed: ``AssertionError: assert 0.0 == 5.0``."""
    seen = _config_lines(monkeypatch)
    path = tmp_path / "astrodeck.json"
    _write(path, {"schema_version": 3, "version": 7,
                  "standards": {"max_guide_rms": 0.0, "min_stars": 40}})
    cfg = ConfigStore(path=path).cfg()
    assert cfg.standards.max_guide_rms == 5.0
    assert cfg.standards.min_stars == 40, "the migration touched a neighbour"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == CONFIG_SCHEMA == 4
    assert on_disk["standards"]["max_guide_rms"] == 5.0, (
        "the raise has to be persisted, or every boot re-migrates for ever")
    lines = [m for _lv, m in seen if "guide RMS rejection is now on" in m]
    assert len(lines) == 1, seen
    assert [lv for lv, m in seen if m in lines] == ["info"], seen
    assert len(lines[0]) <= 137, (len(lines[0]), lines[0])

    seen.clear()
    assert ConfigStore(path=path).cfg().standards.max_guide_rms == 5.0
    assert not [m for _lv, m in seen if "guide RMS rejection" in m], (
        f"the migration ran a second time: {seen}")


def test_a_zero_written_at_schema_4_is_the_operators(tmp_path):
    """From schema 4 on, a 0 is the operator turning the gate off.

    Mutant M4 "migration ignores the stamp" (``if stored < 4 and ...`` ->
    ``if cfg.standards.max_guide_rms == 0:``), observed:
    ``AssertionError: an operator's 0 written at schema 4 was overwritten:
    5.0``."""
    path = tmp_path / "astrodeck.json"
    _write(path, {"schema_version": 4, "version": 7,
                  "standards": {"max_guide_rms": 0.0}})
    got = ConfigStore(path=path).cfg().standards.max_guide_rms
    assert got == 0.0, (
        f"an operator's 0 written at schema 4 was overwritten: {got}")


def test_a_real_choice_survives_the_migration(tmp_path):
    """A nonzero value under schema 3 was somebody's choice.

    Mutant M5 "migration overwrites" (``and cfg.standards.max_guide_rms == 0``
    dropped), observed: ``AssertionError: the migration overwrote a chosen
    1.5: 5.0``."""
    path = tmp_path / "astrodeck.json"
    _write(path, {"schema_version": 3, "version": 7,
                  "standards": {"max_guide_rms": 1.5}})
    got = ConfigStore(path=path).cfg().standards.max_guide_rms
    assert got == 1.5, f"the migration overwrote a chosen 1.5: {got}"


async def test_zero_still_turns_the_gate_off():
    """A plan's explicit 0 is a choice about tonight and beats the rig default.

    Mutant M6 "0 ignored" (``ceiling = self._policy.max_guide_rms or
    DEFAULT_MAX_GUIDE_RMS`` and the outer ``> 0`` test removed), observed:
    ``AssertionError: max_guide_rms=0 still rejected on guide RMS: [('warning',
    'guide RMS 1549.00" above ceiling 5.00"')]``."""
    eng = await _bare(_Guider(guiding=True, rms_total=1549.0, is_arcsec=True),
                      max_guide_rms=0.0)
    got, said = await _grade(eng, _info(0.45))
    assert got is True, f"max_guide_rms=0 still rejected on guide RMS: {said}"


# --------------------------------------------- a guider that reports pixels

async def test_a_runaway_in_raw_pixels_is_rejected_without_a_guide_scale():
    """No guide focal length: the guider reports pixels and `_guide_rms` cannot
    convert. 480 px is at least 48" at ANY real guide scale (0.1"/px floor), so
    it is a runaway whatever the scale is.

    Mutant M7 "no floor" (`_guide_rms_judged` returns ``(None, False)`` instead
    of the floor), observed: ``AssertionError: a 480 px runaway was accepted
    because the guider reports pixels``."""
    eng = await _bare(_Guider(guiding=True, rms_total=480.0, is_arcsec=False,
                              image_scale=0.0))
    got, said = await _grade(eng, _info(0.45))
    assert got is False, (
        "a 480 px runaway was accepted because the guider reports pixels")
    want = '480.0 px is at least 48.0"'
    assert any(want in m for lv, m in said if lv == "warning"), said


async def test_healthy_raw_pixels_pass_and_the_unit_warning_says_what_to_do():
    """1.2 px is healthy at any scale. The once-per-run warning leads with the
    action and fits the humanizer's 137 characters.

    Mutant M8 "floor too high" (``MIN_GUIDE_SCALE_ARCSEC_PX = 10.0``),
    observed: ``AssertionError: healthy 1.2 px guiding was rejected:
    [('warning', 'Set the guide scope focal length in Settings > Optics: the
    guider reports pixels, so guide RMS rejects only a runaway over 0 px.'),
    ('warning', 'guide RMS 1.2 px is at least 12.0" at any guide scale, above
    ceiling 5.00"'), ...]`` (the same reject again on the second call)."""
    eng = await _bare(_Guider(guiding=True, rms_total=1.2, is_arcsec=False,
                              image_scale=0.0))
    got1, said1 = await _grade(eng, _info(0.45))
    got2, said2 = await _grade(eng, _info(0.45))
    assert got1 is True and got2 is True, (
        f"healthy 1.2 px guiding was rejected: {said1 + said2}")
    unit = [m for lv, m in said1 + said2 if lv == "warning"
            and m.startswith("Set the guide scope focal length in Settings > "
                             "Optics")]
    assert len(unit) == 1, f"want exactly one unit warning: {said1 + said2}"
    assert len(unit[0]) <= 137, (len(unit[0]), unit[0])
    assert "over 50 px" in unit[0], unit[0]
    assert not _trips_humanizer(unit[0]), unit[0]


# ---------------------------------------------- the eccentricity sentence

async def test_the_eccentricity_sentence_quotes_the_guide_rms():
    """The engine holds the guide RMS one call away; the sentence carries it,
    so the morning can tell focus (low RMS) from the mount (high RMS).

    Mutant M9 "not passed" (E7 back to ``eccentricity_reject_reason(info)``),
    observed: ``AssertionError: no eccentricity warning quotes the guide RMS:
    [('warning', 'frame eccentricity median 0.71 above ceiling 0.65, 10% of
    20 stars above 0.80 - defocus or trailing')]``."""
    eng = await _bare(_Guider(guiding=True, rms_total=0.82, is_arcsec=True))
    got, said = await _grade(eng, _synthetic(0.71, 20, 2))
    assert got is False
    assert any('defocus or trailing; guide RMS 0.8"' in m
               for lv, m in said if lv == "warning"), (
        f"no eccentricity warning quotes the guide RMS: {said}")


async def test_the_eccentricity_sentence_quotes_no_stale_rms():
    """Fix round 1: an idle guider's RMS is stale (PHD2 keeps its last samples
    after it stops), so the E6b arm refuses to judge it on an unguided plan.
    The eccentricity sentence must not quote it either: '... defocus or
    trailing; guide RMS 773.0"' would send the operator to the mount for a
    fault in focus.

    Mutant F11 "stale RMS quoted" (E7 back to
    ``guide_rms=self._guide_rms()`` without the ``if self._guiding_now()``
    test), observed: ``AssertionError: an idle guider's stale RMS was quoted:
    'frame eccentricity median 0.71 above ceiling 0.65, 10% of 20 stars above
    0.80 - defocus or trailing; guide RMS 773.0"'``."""
    eng = await _bare(_Guider(guiding=False, rms_total=773.0, is_arcsec=True),
                      guide=False)
    got, said = await _grade(eng, _synthetic(0.71, 20, 2))
    assert got is False
    ecc = [m for lv, m in said if lv == "warning" and "eccentricity" in m]
    assert ecc, f"no eccentricity rejection: {said}"
    for m in ecc:
        assert "guide RMS" not in m, (
            f"an idle guider's stale RMS was quoted: {m!r}")


@pytest.mark.parametrize("stats, want", [
    (GuideStats(rms_total=480.0, is_arcsec=False, image_scale=0.0), 48.0),
    (GuideStats(rms_total=480.0, is_arcsec=False, image_scale=3.23), None),
    (GuideStats(rms_total=480.0, is_arcsec=True), None),
    (GuideStats(rms_total=float("nan"), is_arcsec=False), None),
    (GuideStats(rms_total=-1.0, is_arcsec=False), None),
], ids=["pixels-no-scale", "pixels-with-scale", "arcsec", "nan", "negative"])
def test_the_pixel_floor_is_only_for_pixels_it_cannot_convert(stats, want):
    """Fix round 1. The floor is the smallest arcsec a PIXEL figure can be at
    any guide scale, and only for the case the exact conversion cannot do: a
    scale or an arcsec figure belongs to the exact path, and a NaN or
    negative figure is no figure at all (a NaN floor would compare False
    against every ceiling, and a negative one would read as healthy).

    Mutant F12 "floor ignores NaN/negatives" (``if scale > 0:`` alone),
    observed on the nan and negative cases: ``AssertionError:
    GuideStats(..., rms_total=nan, ...)`` with ``assert ((nan is None) ==
    (None is None))``. Mutant F13 "floor ignores scale" (``if px != px or px
    < 0:``), observed on the pixels-with-scale case: ``assert ((48.0 is None)
    == (None is None))``."""
    got = guide_rms_floor_arcsec(stats)
    assert (got is None) == (want is None) and (
        want is None or got == pytest.approx(want)), (
        f"{stats}: want {want}, got {got}")


def test_both_eccentricity_sentences_fit_the_humanizer():
    """Worst case: 400 marks (the `star_marks` cap) and a five-digit RMS.

    Mutant M10 "old fraction wording" (restore ``, over the 25% limit
    (median 0.64 is under the 0.65 ceiling)``), observed: ``AssertionError:
    fraction rule sentence is 149 characters: 'frame eccentricity 100% of 400
    stars above 0.80, over the 25% limit (median 0.64 is under the 0.65
    ceiling) - defocus or trailing; guide RMS 12345.6"'``."""
    pol = resolve_policy(SequencePlan(), AppConfig())
    median = pol.eccentricity_reject_reason(
        {"ecc": 0.99, "star_list": _marks(400, 0.99)}, guide_rms=12345.6)
    frac = pol.eccentricity_reject_reason(
        {"ecc": 0.64, "star_list": _marks(400, 0.95)}, guide_rms=12345.6)
    assert median and "median 0.99 above ceiling" in median, median
    assert frac and "under the 0.65 ceiling" in frac, frac
    for name, s in (("median rule", median), ("fraction rule", frac)):
        assert len(s) <= 137, f"{name} sentence is {len(s)} characters: {s!r}"
        assert 'guide RMS 12345.6"' in s, s
        assert not _trips_humanizer(s), (name, s)


# ---------------------------------------- a stale RMS on an unguided plan

@pytest.mark.parametrize("stats, want", [
    ({"guiding": False, "rms_total": 773.0, "is_arcsec": True}, True),
    ({"guiding": False, "rms_total": 4800.0, "is_arcsec": False,
      "image_scale": 0.0}, True),
    ({"guiding": True, "rms_total": 773.0, "is_arcsec": True}, False),
], ids=["idle-arcsec", "idle-pixels", "control-guiding"])
async def test_a_stale_rms_from_an_idle_guider_rejects_nothing_on_an_unguided_plan(
        stats, want):
    """PHD2 keeps its last samples after it stops, and `stats()` keeps
    returning their RMS with guiding=False. On a plan that does not guide,
    judging that figure would reject every frame of the night. A guider that IS
    guiding on such a plan is still judged (the control).

    Mutant M20 "stale RMS judged" (the E6b ``elif`` deleted), observed on both
    idle cases: ``AssertionError: guide=False, guider {...guiding=False...}:
    want True, got False``. Mutant M20b "unguided never judged" (``elif not
    (plan and plan.guide):`` without ``and not self._guiding_now()``),
    observed on the control: ``AssertionError: guide=False, guider
    {'guiding': True, ...}: want False, got True``."""
    eng = await _bare(_Guider(**stats), guide=False)
    got, said = await _grade(eng, _info(0.45))
    assert got is want, f"guide=False, guider {stats}: want {want}, got {got}"


# ------------------------------------------- the stopped-guider sentence

#: 2026-06-14 12:00 UTC. The Sun is then 28.7 deg from M42 (RA 5.5881h,
#: Dec -5.3911), inside the 30 deg default cone.
_JUNE_NOON = 1781438400.0


def _sun_on_m42_every_day(monkeypatch) -> None:
    """Pin the Sun to its June place, only inside the real
    ``Hub._check_solar``; ``sun_altaz`` and the scheduler keep the clock."""
    from astrodeck.catalog import coords
    june = coords.sun_radec(_JUNE_NOON)
    cone = hub_module.config_store.cfg().safety.solar_exclusion_deg
    assert coords.angular_sep_deg(5.5881, -5.3911, *june) < cone, (
        "the pinned Sun no longer sits in the cone, so the pin proves nothing "
        "about the calendar")
    real = Hub._check_solar

    def _check_solar_in_june(self, ra_hours, dec_deg, *, force=False):
        with monkeypatch.context() as m:
            m.setattr(coords, "sun_radec", lambda unix_time=None: june)
            return real(self, ra_hours, dec_deg, force=force)

    monkeypatch.setattr(Hub, "_check_solar", _check_solar_in_june)


async def test_the_stopped_guider_sentence_says_what_to_do(tmp_path,
                                                           monkeypatch):
    """With the gate on by default, a guided plan's frame shot with the guider
    stopped is rejected on a rig whose operator never set a ceiling, so the
    sentence names the action rather than a choice nobody made.

    Mutant M21 "old sentence" (restore ``"guide RMS ceiling is set, but this
    frame was shot with the guider stopped — rejected"``), observed:
    ``AssertionError: no stopped-guider sentence: [...]``, the list being the
    run's log lines with the old sentence among them.

    The plan's target is M42, which sits in the 30 deg sun-exclusion cone
    from about June 4 to June 23. The cone is disarmed here as the file's
    ``sim_hub`` fixture does, and the real ``_check_solar`` sees the June Sun
    every day, so the case cannot pass or fail by the calendar.

    Mutant C-M2 "cone armed" (the ``solar_avoidance`` line below deleted),
    observed on any date: ``AssertionError: rejected=0``, the setup slew
    refused by the cone before any frame was shot."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store, "_path",
                        tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module.config_store, "_cfg", None)
    _sun_on_m42_every_day(monkeypatch)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    hub = Hub()
    await hub.connect_sim()
    try:
        eng = SequenceEngine(hub)

        async def _never_guided() -> bool:
            return False
        monkeypatch.setattr(eng, "_frame_was_guided", _never_guided)

        q = bus.subscribe()
        try:
            eng.start(_one_frame_plan(guide=True, dither_every=0,
                                      autofocus_every=0, meridian_flip=False))
            await eng._task
            said = _said(q)
        finally:
            bus.unsubscribe(q)
    finally:
        await hub.disconnect_all()
    assert eng._rejected == 1, f"rejected={eng._rejected}"
    lines = [m for lv, m in said if lv == "warning"
             and m.startswith("Rejected: shot with the guider stopped. Start "
                              "guiding")]
    assert lines, f"no stopped-guider sentence: {said}"
    assert len(lines[0]) <= 137, (len(lines[0]), lines[0])
    assert not _trips_humanizer(lines[0]), lines[0]
