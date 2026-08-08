"""Every provider the product OFFERS is one the resolver will actually run.

THE PROMISE: if AstroDeck lets you pick X for a capability, X is what runs it.

THE DEFECT CLASS (broken promises, class B — an offered option the executor will
not honour). Both directions of this were live on the same day:

* the guide dropdown offered "AstroDeck native" whenever ANY camera was
  connected, while ``_resolve_guide`` computed ``native_ok`` from the
  ``guide_camera`` ROLE alone. The user picked native, the write succeeded, and
  the badge said PHD2 — with nothing on screen admitting the pick was discarded.
* the mirror image: the offer REFUSED the PHD2 bridge unless one was already
  connected, while the resolver honours ``backend`` unconditionally, so the
  screen printed "no bridge is connected" directly above "using the PHD2
  bridge".

``test_guide_provider_selection.py`` pins those two on the ``guide`` capability
against a handful of hand-built rigs. This file is the GENERALISATION the audit
plan asks for: the property swept over **every capability × every value the
write layer accepts × a rig matrix**, plus the second offer surface nobody
checked — the per-driver ``offers.tasks`` list that fills the Equipment Tasks
dropdowns.

The invariant, stated once:

    a value the product accepts for a capability must be a value the resolver
    has a branch for. If no rig in the matrix can make the resolver honour it,
    the control is inert: setting it does exactly what ``auto`` does, and
    nothing anywhere says so.

DELIBERATELY NOT ASSERTED: that a value is honoured on EVERY rig. Degrading a
pick whose prerequisites are absent is correct behaviour and the resolver says
why when it does (``"no guide camera connected — using the PHD2 bridge"``).
What is forbidden is a value that no rig can ever satisfy.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from astrodeck import drivers as drivers_mod
from astrodeck import providers
from astrodeck.config import (PROVIDER_CAPABILITIES, ConfigStore,
                              ProvidersConfig)
from astrodeck.devices.base import DeviceError

_DRIVERS_SRC = Path(drivers_mod.__file__)


# --------------------------------------------------------------------- fakes

class FakeDev:
    """A connected role device carrying only what the resolvers inspect."""

    def __init__(self, *, hardware: bool = True, native_af: bool = False):
        self.connected = True
        self.hardware = hardware
        self.backend = "alpaca" if hardware else ""
        self.supports_native_autofocus = native_af


class FakeHub:
    def __init__(self, devices: dict, *, nina: bool = False):
        self.devices = devices
        self.nina_client = object() if nina else None
        self.guider = None                 # no guider wired: the pure "what
        self.last_connect_result = None    # WOULD run" preview
        self.sim_rig = None

    def _active_profile(self):
        return None


def _roles(*names, hardware=True, native_af=False):
    return {n: FakeDev(hardware=hardware,
                       native_af=(native_af and n == "focuser"))
            for n in names}


#: The rig matrix. Each entry is (hub, astap_present). A value is "honourable"
#: when at least ONE of these makes the resolver return it — that is what
#: separates "your pick needs a guide camera" from "this control does nothing".
#:
#: Every rig here is one a user really has: a NINA bridge, an Alpaca/ASCOM rig,
#: the simulator, the default half-wired rig (imaging camera + mount, which is
#: what a first connect looks like), and a rig with nothing connected at all.
def _rig_matrix() -> dict[str, tuple[FakeHub, bool]]:
    real = ("camera", "guide_camera", "telescope", "focuser")
    return {
        "nina": (FakeHub(_roles(*real, native_af=True), nina=True), True),
        "alpaca": (FakeHub(_roles(*real)), True),
        "alpaca-no-astap": (FakeHub(_roles(*real)), False),
        "sim": (FakeHub(_roles(*real, hardware=False)), False),
        "imaging-only": (FakeHub(_roles("camera", "telescope")), True),
        "bare": (FakeHub({}), False),
    }


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """An isolated store bound into ``providers`` (which read ``config_store``
    at import time) so an override written here is the one the resolver reads,
    and the developer's real config can never decide a test."""
    s = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(providers, "config_store", s)
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    return s


def _honours(cap: str, value: str, hub: FakeHub, astap: bool,
             monkeypatch, store: ConfigStore) -> bool:
    """Would picking ``value`` for ``cap`` on this rig actually run ``value``?

    Compares the resolved ``kind`` against the FAMILY the value names, using the
    project's own ``_override_family`` mapping rather than a second copy of it.
    ``sim`` is accepted for a native family because the native engine badges
    itself ``sim`` when it runs over simulated devices (see ``_resolve_guide``)
    — the ENGINE is what the user picked, and the badge is only saying which
    devices it is driving."""
    store.set_providers(ProvidersConfig(**{cap: value}))
    monkeypatch.setattr(providers, "find_astap",
                        lambda: ("/usr/bin/astap" if astap else None))
    want = providers._override_family(value)
    try:
        kind = providers.resolve(cap, hub).kind
    except DeviceError:
        return False
    if want == "astrodeck":
        return kind in ("astrodeck", "sim")
    return kind == want


def _honourable_anywhere(cap: str, value: str, monkeypatch, store) -> bool:
    return any(_honours(cap, value, hub, astap, monkeypatch, store)
               for hub, astap in _rig_matrix().values())


# --------------------------------------------------- the write-layer vocabulary
#
# ``ConfigStore.set_providers`` validates a written value against ONE vocabulary
# for ALL FOUR capabilities — it has no per-capability notion of what can run
# what. So the API accepts ``providers.solve = "astrodeck"`` (there is no native
# solver) and ``providers.autofocus = "astap"`` (ASTAP does not focus), stores
# them, and resolves exactly as if the user had never touched the control.

#: Accepted by ``set_providers`` for this capability and honoured by NO rig.
#: Each entry is a FINDING, not a permission slip: the fix is either a
#: per-capability write vocabulary (422 at the route) or a resolver branch.
#: None of these is reachable from the UI today — the Tasks dropdowns are built
#: from ``offers.tasks`` (asserted below) and the guide row from
#: ``guide_provider_options`` — so they are latent, not live. The test below
#: refuses to let the list GROW, which is what stops the next one hiding here.
_ACCEPTED_BUT_INERT: dict[tuple[str, str], str] = {
    ("autofocus", "sim"):
        "there is no simulated autofocus; a sim rig focuses with the native "
        "V-curve over sim devices (the 'astrodeck' family)",
    ("autofocus", "astap"):
        "ASTAP is a plate solver; it has no focus routine",
    ("polar_align", "astap"):
        "ASTAP is the solver polar align USES, not a polar-align provider",
    ("solve", "astrodeck"):
        "the native Rust engine has no plate solver — solving is ASTAP or the "
        "simulator",
    ("solve", "backend"):
        "NINA can plate-solve, but _resolve_solve has no backend branch: solve "
        "is ASTAP-or-simulator by design (a faked solve near real motion is "
        "refused). Either add the branch or reject the write",
    ("guide", "astap"):
        "ASTAP is a plate solver; it does not guide",
    ("guide", "sim"):
        "documented on GUIDE_PROVIDER_VALUES: _resolve_guide has no sim branch, "
        "so pinning it behaves exactly like auto. Never offered, but still "
        "accepted by the write layer",
}


def _named_families(store: ConfigStore) -> list[str]:
    """The write vocabulary, minus the values that mean "resolver, you choose".

    A value whose family is ``auto`` is not a promise about WHO runs the
    capability, so it cannot be broken. ``auto`` itself is checked separately.
    """
    return sorted(v for v in store.valid_override_values()
                  if providers._override_family(v) not in ("auto",))


@pytest.mark.parametrize("cap", PROVIDER_CAPABILITIES)
def test_every_accepted_provider_value_is_one_some_rig_can_run(
        cap, store, monkeypatch):
    inert = [v for v in _named_families(store)
             if not _honourable_anywhere(cap, v, monkeypatch, store)]
    unexplained = [v for v in inert if (cap, v) not in _ACCEPTED_BUT_INERT]
    assert unexplained == [], (
        f"POST /api/config accepts these values for providers.{cap} and no rig "
        f"in the matrix makes the resolver honour any of them, so choosing one "
        f"silently does what 'auto' does: {unexplained}. Fix by adding the "
        f"resolver branch, or by rejecting the value at write time in "
        f"ConfigStore.set_providers — or, if it is genuinely inert and "
        f"unreachable, add it to _ACCEPTED_BUT_INERT with the reason.")


@pytest.mark.parametrize("cap", PROVIDER_CAPABILITIES)
def test_auto_always_resolves_to_something(cap, store, monkeypatch):
    """``auto`` is the default every rig ships with. On a fully-connected rig it
    must never raise — a capability that cannot resolve on the maximal rig is
    unreachable for everyone."""
    store.set_providers(ProvidersConfig(**{cap: "auto"}))
    monkeypatch.setattr(providers, "find_astap", lambda: "/usr/bin/astap")
    hub, _ = _rig_matrix()["alpaca"]
    choice = providers.resolve(cap, hub)
    assert choice.kind in ("backend", "astrodeck", "astap", "sim")
    assert choice.reason, f"providers.{cap} resolved with no stated reason"


def test_the_inert_list_does_not_grow():
    """Today's inert control must not hide among yesterday's. The count is the
    assertion: it may only move down, and only with a deliberate edit."""
    assert len(_ACCEPTED_BUT_INERT) <= 7, (
        "a newly-inert provider value was filed here instead of being given a "
        "resolver branch or rejected at write time")
    for (cap, value), why in _ACCEPTED_BUT_INERT.items():
        assert cap in PROVIDER_CAPABILITIES, cap
        assert why.strip(), f"{cap}/{value} is excused with no reason"


def test_the_predicate_can_actually_say_no(store, monkeypatch):
    """The detector's own positive control.

    Every assertion above is of the form "nothing is broken". If ``_honours``
    silently returned True the file would be a rubber stamp, so it is shown
    discriminating on a pair whose answer is known both ways.
    """
    hub, _ = _rig_matrix()["alpaca"]
    assert _honours("solve", "astap", hub, True, monkeypatch, store) is True
    assert _honours("solve", "astap", hub, False, monkeypatch, store) is False
    assert _honours("autofocus", "astap", hub, True, monkeypatch, store) is False


# ------------------------------------------------- offer surface 1: the drivers
#
# The Equipment "Tasks" dropdowns are built by ``eligibleTaskDrivers`` from each
# driver row's ``offers.tasks``. A row that offers a task the resolver cannot
# route to it is the class-B defect with a different dropdown: the user picks a
# driver by NAME and the resolver maps that id to a FAMILY — a NINA id becomes
# ``backend``, and every other configured type becomes ``auto``, i.e. discarded.

def _probe_task_offers() -> dict[str, list[str]]:
    """``driver type -> the tasks its probe advertises``, read out of the source.

    Read rather than executed because three of the four probes need a network
    peer (and one needs an ASIAIR). What matters here is the literal list the
    author wrote, which is exactly what the source carries.

    Parsed with ``ast`` rather than a regex: ``_ok(devices, tasks)`` takes two
    list arguments and the first line of this helper's first draft matched the
    DEVICES list, which produced a confident failure about a task named
    ``{"role": "guider``. A detector that misreads its own input is worse than
    no detector."""
    tree = ast.parse(_DRIVERS_SRC.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        m = re.fullmatch(r"_probe_(\w+)", node.name)
        if not m:
            continue
        tasks: list[str] = []
        for call in ast.walk(node):
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "_ok"
                    and len(call.args) >= 2
                    and isinstance(call.args[1], ast.List)):
                tasks += [e.value for e in call.args[1].elts
                          if isinstance(e, ast.Constant)]
        out[m.group(1)] = tasks
    return out


def test_the_probe_source_scan_still_finds_the_probes():
    """Positive control for the regex: a scanner that quietly stops matching
    would make the two assertions below pass on an empty set."""
    offers = _probe_task_offers()
    assert set(offers) >= {"nina", "alpaca", "phd2"}, offers
    assert offers["nina"] == ["autofocus", "polar_align"], (
        "the NINA probe's advertised tasks changed — re-check the mapping below")


def test_every_task_a_driver_offers_is_a_real_capability(store):
    """A typo'd task name is silently un-offerable: it matches no capability, so
    the dropdown for the capability the author meant simply never lights."""
    for dtype, tasks in _probe_task_offers().items():
        for t in tasks:
            assert t in PROVIDER_CAPABILITIES, (
                f"the {dtype} probe advertises task {t!r}, which is not one of "
                f"{PROVIDER_CAPABILITIES} — no dropdown will ever show it")
    for row in drivers_mod._implicit_rows():
        for t in row["offers"]["tasks"]:
            assert t in PROVIDER_CAPABILITIES, (row["id"], t)


def test_a_configured_driver_that_offers_a_task_can_be_routed_to_it(
        store, monkeypatch):
    """The seam: ``offers.tasks`` fills the dropdown, ``_override_family`` maps
    the chosen id to an implementation family, and only NINA ids map to one.

    So a driver type whose probe advertises a task while its family degrades to
    ``auto`` produces the original defect exactly: the pick is written, the
    resolver discards it, and the badge shows whatever auto picked."""
    for dtype, tasks in _probe_task_offers().items():
        if not tasks:
            continue
        entry = store.add_driver(dtype, host="10.0.0.9")
        for cap in tasks:
            assert _honourable_anywhere(cap, entry.id, monkeypatch, store), (
                f"a {dtype} driver advertises the {cap!r} task, so the "
                f"Equipment dropdown offers it — but providers._override_family"
                f"({entry.id!r}) maps it to "
                f"{providers._override_family(entry.id)!r}, which no rig "
                f"resolves to for {cap}. Either stop advertising the task or "
                f"give the family a resolver branch.")


def test_every_implicit_driver_task_offer_is_honoured_somewhere(
        store, monkeypatch):
    """The built-in rows (Simulator / AstroDeck native / ASTAP) are offered on
    every rig, with no probe and no reachability question, so a mismatch here is
    visible to every user rather than only to one with that hardware."""
    for row in drivers_mod._implicit_rows():
        for cap in row["offers"]["tasks"]:
            assert _honourable_anywhere(cap, row["id"], monkeypatch, store), (
                f"the built-in {row['id']!r} row offers {cap!r} and no rig "
                f"resolves {cap} to it — the Tasks dropdown lists a choice "
                f"that does nothing")


# ---------------------------------------------- offer surface 2: guide options
#
# ``guide_provider_options`` is the one offer surface with a per-rig predicate.
# test_guide_provider_selection pins it on four hand-built rigs; here it is swept
# over the whole matrix, in BOTH directions, which is the property that failed
# twice in one day.

@pytest.mark.parametrize("rig", sorted(_rig_matrix()))
def test_the_guide_offer_agrees_with_the_guide_resolver_on_every_rig(
        rig, store, monkeypatch):
    hub, astap = _rig_matrix()[rig]
    for opt in providers.guide_provider_options(hub):
        value = opt["value"]
        if value == "auto":
            continue
        honoured = _honours("guide", value, hub, astap, monkeypatch, store)
        assert opt["eligible"] == honoured, (
            f"[{rig}] the guide dropdown says {value!r} is "
            f"{'selectable' if opt['eligible'] else 'blocked'} and the resolver "
            f"{'honours' if honoured else 'discards'} it. The offer and the "
            f"resolver must be the SAME predicate — see "
            f"providers._guide_native_blocker.")


def test_the_resolver_reaches_its_answer_through_the_offer_predicate():
    """Structure, not just agreement.

    Once ``_resolve_guide`` decides the ``astrodeck`` override by CALLING
    ``_guide_native_blocker``, the two can no longer drift — the test above
    becomes true by construction, which is the outcome we want and also the
    outcome that would let it rot unnoticed. So the structure itself is the
    assertion: re-inlining the blocker's terms into the resolver is how this
    defect came back twice, and it is what this catches.
    """
    src = inspect.getsource(providers._resolve_guide)
    assert "_guide_native_blocker(hub)" in src, (
        "_resolve_guide decides the 'astrodeck' override with its own copy of "
        "the offer's terms again. Call _guide_native_blocker(hub) instead, so "
        "the dropdown and the badge cannot disagree about the same rig.")


@pytest.mark.parametrize("rig", sorted(_rig_matrix()))
def test_every_blocked_guide_option_says_what_to_do(rig):
    """A blocked row with no sentence is a dead control: the user cannot tell an
    unwired rig from a product that cannot do the thing at all — which is the
    conclusion a real user reached about guiding."""
    hub, _ = _rig_matrix()[rig]
    for opt in providers.guide_provider_options(hub):
        if opt["eligible"]:
            continue
        reason = opt.get("reason") or ""
        assert reason.strip(), f"[{rig}] {opt['value']} blocked with no reason"
        assert any(n in reason for n in ("AstroDeck", "NINA", "PHD2")), (
            f"[{rig}] {opt['value']}: the sentence is rendered without the "
            f"option's label, so it must name its own provider: {reason!r}")
