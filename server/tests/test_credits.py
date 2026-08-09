"""The credits screen must not be able to go stale.

A hand-maintained acknowledgements page is wrong the day after it ships, and
being wrong about a licence is not a cosmetic defect. So the page is generated
(``tools/gen_credits.py``) and this module is the gate that makes the generation
mandatory: **add a dependency without regenerating and these tests go red.**

The checks are deliberately NOT "re-run the generator and diff". That would need
a cargo registry, a node_modules tree and every optional extra installed on any
machine running the suite, and a check that cannot run is a check that gets
deleted. Instead each ecosystem is compared against the file that is already the
source of truth for it and is committed to the repo:

===================  =======================================================
Python               ``server/pyproject.toml`` closed over the INSTALLED env
JavaScript           ``ui/package-lock.json`` (non-dev entries)
Rust                 ``native/Cargo.lock``
Vendored binaries    the directories under ``server/astrodeck/vendor``
Shipped data         the files under ``server/astrodeck/catalog/data``
Network services     outbound hosts grepped out of the server source
External programs    executable names grepped out of the server source
===================  =======================================================

Version equality is enforced for JavaScript and Rust, whose lockfiles pin
exactly, and NOT for Python, whose pyproject declares ranges — a fresh CI
install legitimately resolves a newer astropy than this developer has, and a
test that reddens on somebody else's release is a test that gets muted. Name
coverage is enforced everywhere, in both directions.
"""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CREDITS = REPO / "ui" / "src" / "credits.generated.json"
TOOLS = REPO / "tools"

# APPENDED, not inserted at position 0. `tools/` holds a handful of scripts, and
# putting it ahead of the standard library means the day somebody adds
# `tools/json.py` the whole suite fails somewhere unrelated and mysterious.
# Appending lets stdlib and site-packages win every collision.
if str(TOOLS) not in sys.path:
    sys.path.append(str(TOOLS))
import licence_policy as policy  # noqa: E402
import credits_registry as registry  # noqa: E402

REGENERATE = "regenerate with: python tools/gen_credits.py"


@pytest.fixture(scope="module")
def credits() -> dict:
    assert CREDITS.is_file(), f"{CREDITS} does not exist — {REGENERATE}"
    return json.loads(CREDITS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def entries(credits) -> list[dict]:
    return [e for g in credits["groups"] for e in g["entries"]]


def _named(entries: list[dict]) -> dict[str, dict]:
    """Entries by normalised name, so `typing_extensions` matches `typing-extensions`."""
    return {_norm(e["name"]): e for e in entries}


def _norm(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


# ---------------------------------------------------------------------------
# The headline check, one per ecosystem: a dependency with no credit entry fails.
# ---------------------------------------------------------------------------

def _python_runtime_names() -> set[str]:
    import importlib.metadata as md
    from packaging.requirements import Requirement

    proj = tomllib.loads((REPO / "server" / "pyproject.toml").read_text())["project"]
    seeds = [Requirement(s) for s in proj["dependencies"]]
    for extra, specs in proj.get("optional-dependencies", {}).items():
        if extra != "dev":
            seeds += [Requirement(s) for s in specs]

    names: set[str] = set()
    stack = [(r.name, frozenset(r.extras)) for r in seeds]
    seen: set[tuple[str, frozenset]] = set()
    while stack:
        name, extras = stack.pop()
        if (key := (_norm(name), extras)) in seen:
            continue
        seen.add(key)
        names.add(_norm(name))
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue  # an optional extra that is not installed; still credited
        for spec in dist.requires or []:
            req = Requirement(spec)
            if req.marker and not any(req.marker.evaluate({"extra": e})
                                      for e in (extras or frozenset({""}))):
                continue
            stack.append((req.name, frozenset(req.extras)))
    return names


def test_every_python_dependency_has_a_credit_entry(entries):
    have = _named(entries)
    missing = sorted(n for n in _python_runtime_names() if n not in have)
    assert not missing, (
        f"{len(missing)} Python package(s) ship with no credit entry: {missing}. "
        f"{REGENERATE}")


def _npm_locked() -> dict[str, str]:
    lock = json.loads((REPO / "ui" / "package-lock.json").read_text(encoding="utf-8"))
    return {
        path.split("node_modules/")[-1]: meta.get("version", "")
        for path, meta in lock["packages"].items()
        if path and not meta.get("dev")
    }


def test_every_npm_dependency_has_a_credit_entry(entries):
    have = _named(entries)
    missing = sorted(n for n in _npm_locked() if _norm(n) not in have)
    assert not missing, (
        f"{len(missing)} npm package(s) ship with no credit entry: {missing}. "
        f"{REGENERATE}")


def test_npm_credit_versions_match_the_lockfile(entries):
    have = _named(entries)
    drift = [
        f"{name}: lockfile {version}, credits {have[_norm(name)]['version']}"
        for name, version in _npm_locked().items()
        if _norm(name) in have and have[_norm(name)]["version"] != version
    ]
    assert not drift, f"credits disagree with package-lock.json: {drift}. {REGENERATE}"


def _cargo_locked() -> dict[str, str]:
    lock = tomllib.loads((REPO / "native" / "Cargo.lock").read_text(encoding="utf-8"))
    workspace = tomllib.loads((REPO / "native" / "Cargo.toml").read_text(encoding="utf-8"))
    ours = {Path(m).name for m in workspace["workspace"]["members"]}
    return {p["name"]: p["version"] for p in lock["package"] if p["name"] not in ours}


def test_every_rust_crate_has_a_credit_entry(entries):
    have = _named(entries)
    missing = sorted(n for n in _cargo_locked() if _norm(n) not in have)
    assert not missing, (
        f"{len(missing)} Rust crate(s) ship with no credit entry: {missing}. "
        f"{REGENERATE}")


def test_cargo_credit_versions_match_the_lockfile(entries):
    have = _named(entries)
    drift = [
        f"{name}: Cargo.lock {version}, credits {have[_norm(name)]['version']}"
        for name, version in _cargo_locked().items()
        if _norm(name) in have and have[_norm(name)]["version"] != version
    ]
    assert not drift, f"credits disagree with Cargo.lock: {drift}. {REGENERATE}"


# ---------------------------------------------------------------------------
# Things with no package manager. Each gets its own detector rather than trust.
# ---------------------------------------------------------------------------

_BINARY = re.compile(r"\.(dll|dylib|exe|lib|a)$|\.so(\.|$)", re.I)


def test_every_vendored_binary_directory_is_credited(credits):
    """Redistributing a compiled binary is a stronger act than depending on a
    package. A new vendor directory must not be able to appear silently."""
    root = REPO / "server" / "astrodeck" / "vendor"
    shipping = {
        d.name for d in root.iterdir()
        if d.is_dir() and any(_BINARY.search(p.name) for p in d.rglob("*") if p.is_file())
    } if root.is_dir() else set()
    missing = sorted(shipping - set(registry.VENDORED))
    assert not missing, (
        f"vendor director{'y' if len(missing) == 1 else 'ies'} {missing} ship "
        "binaries with no entry in tools/credits_registry.py VENDORED")

    vendored = {e["name"] for g in credits["groups"] for e in g["entries"]
                if g["id"] in ("vendored", "flagged")}
    for spec in registry.VENDORED.values():
        assert spec["name"] in vendored, (
            f"{spec['name']} is registered as a vendored binary but does not "
            f"appear in the generated credits. {REGENERATE}")


def test_every_vendored_binary_ships_its_licence_beside_it(credits):
    """The notice has to travel with the binary in the INSTALLED package, not
    just live in the repo — a non-editable pip install that drops the licence
    file breaks MIT's one condition."""
    package_data = tomllib.loads(
        (REPO / "server" / "pyproject.toml").read_text()
    )["tool"]["setuptools"]["package-data"]["astrodeck"]
    root = REPO / "server" / "astrodeck" / "vendor"
    for name in registry.VENDORED:
        licences = [p for p in (root / name).iterdir()
                    if p.is_file() and p.name.upper().startswith(("LICENSE", "COPYING"))]
        assert licences, f"vendor/{name} ships binaries but no licence file"
        assert any(pat.startswith(f"vendor/{name}/") and
                   (pat.endswith(lic.name) or pat.endswith("*" + lic.suffix)
                    or pat.endswith("*"))
                   for lic in licences for pat in package_data), (
            f"vendor/{name}'s licence file is not matched by any "
            "[tool.setuptools.package-data] pattern, so `pip install` would ship "
            "the binary without its notice")


def test_every_shipped_data_file_is_credited(credits):
    root = REPO / "server" / "astrodeck" / "catalog" / "data"
    on_disk = {p.name for p in root.iterdir() if p.is_file()} if root.is_dir() else set()
    missing = sorted(on_disk - set(registry.DATA))
    assert not missing, (
        f"shipped data file(s) {missing} have no entry in credits_registry.DATA. "
        "Data carries attribution and sometimes share-alike obligations.")


#: Hosts that appear in the server source but are NOT services we call: licence
#: URLs, documentation links, upstream project pages, protocol examples. Adding
#: to this list is a deliberate act, which is the point — a genuinely new
#: outbound host cannot slip past by being mistaken for a doc link.
_NON_SERVICE_HOSTS = {
    "www.mozilla.org", "mozilla.org", "creativecommons.org", "spdx.org",
    "www.gnu.org", "opensource.org", "www.apache.org",
    "github.com", "raw.githubusercontent.com", "gist.github.com",
    "docs.python.org", "peps.python.org", "www.python.org",
    "developer.mozilla.org", "www.w3.org", "schemas.microsoft.com",
    "www.hnsky.org", "openphdguiding.org", "nighttime-imaging.eu",
    "ascom-standards.org", "www.ascom-standards.org",
    "player-one-astronomy.com", "www.zwoastro.com", "astronomy-imaging-camera.com",
    "www.cloudynights.com", "indilib.org", "www.astropy.org", "docs.astropy.org",
    "en.wikipedia.org", "www.iau.org", "exopla.net", "archive.stsci.edu",
    "irsa.ipac.caltech.edu", "www.cosmos.esa.int", "gea.esac.esa.int",
    "cds.unistra.fr", "leda.univ-lyon1.fr", "www.pas.rochester.edu",
    "example.com", "www.example.com", "hc-ping.com",
    "open-meteo.com", "www.astrospheric.com", "www.open-meteo.com",
    "eccc-msc.github.io", "core.telegram.org", "developers.google.com",
    "mesonet.agron.iastate.edu.", "doi.org",
}

_URL = re.compile(r"https?://([A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,})")


#: What counts as "the product" for host discovery. `scripts/` and `packaging/`
#: are in scope because a host contacted at build time still puts something into
#: the release — fetch_astap.py's SourceForge download is how the bundled solver
#: gets there. `references/` and the venv are not.
_PRODUCT_TREES = ("server/astrodeck", "scripts", "packaging", "supervisor", "relay")


def _product_sources() -> list[Path]:
    out: list[Path] = []
    for tree in _PRODUCT_TREES:
        root = REPO / tree
        if root.is_dir():
            out += [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]
    return out


def _outbound_hosts() -> dict[str, set[str]]:
    """Every real hostname reachable from a non-comment line of the product."""
    found: dict[str, set[str]] = {}
    for path in _product_sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for host in _URL.findall(line):
                found.setdefault(host.lower().rstrip("."), set()).add(
                    f"{path.relative_to(REPO)}:{lineno}")
    return found


def test_every_outbound_host_is_credited():
    """The detector that keeps the hand-written service list honest.

    Services have no manifest, so ``credits_registry.SERVICES`` is written by
    hand — which is exactly the shape of thing this codebase keeps getting wrong.
    This goes and looks at the source instead of trusting the list.
    """
    claimed = {h.lower() for s in registry.SERVICES for h in s.get("hosts", [])}
    uncredited = {
        host: sorted(where)
        for host, where in _outbound_hosts().items()
        if host not in claimed and host not in _NON_SERVICE_HOSTS
    }
    assert not uncredited, (
        "these hosts are contacted (or named) in the server source but are "
        "neither credited in credits_registry.SERVICES nor listed as "
        f"documentation-only in _NON_SERVICE_HOSTS: {uncredited}")


def test_credited_services_are_still_used():
    """The reverse direction: a service we stopped calling should stop being
    credited, or the page slowly becomes a museum."""
    seen = set(_outbound_hosts())
    stale = [
        s["name"] for s in registry.SERVICES
        if s.get("hosts") and not any(h.lower() in seen for h in s["hosts"])
    ]
    assert not stale, (
        f"credited services no longer referenced anywhere in the server: {stale}")


def test_credited_external_programs_are_still_invoked():
    source = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (REPO / "server" / "astrodeck").rglob("*.py"))
    stale = [
        p["name"] for p in registry.PROGRAMS
        if p.get("binaries") and not any(b in source for b in p["binaries"])
    ]
    assert not stale, f"credited programs no longer referenced: {stale}"


# ---------------------------------------------------------------------------
# Quality of the entries themselves. A credit that names a licence and
# reproduces nothing is the failure mode this whole exercise exists to prevent.
# ---------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\b(TODO|TBD|FIXME|XXX|placeholder|see above|coming soon)\b", re.I)


def test_entries_requiring_reproduced_text_actually_have_it(credits, entries):
    """MIT and BSD are not discharged by printing the word "MIT"."""
    pool = credits["licenses"]
    naked = []
    for entry in entries:
        if not set(entry["requires"]) & policy.NEEDS_TEXT:
            continue
        bodies = [pool.get(t["hash"], "") for t in entry["texts"]]
        if not bodies or not any(len(b.strip()) >= 200 for b in bodies):
            naked.append(f"{entry['name']} ({entry['spdx']})")
    assert not naked, (
        f"{len(naked)} entr{'y' if len(naked) == 1 else 'ies'} require a "
        f"reproduced licence notice but ship none: {naked}. {REGENERATE}")


def test_no_licence_text_is_a_placeholder(credits):
    bad = [
        h for h, body in credits["licenses"].items()
        if len(body.strip()) < 100 or _PLACEHOLDER.search(body)
    ]
    assert not bad, f"licence texts that are stubs rather than licences: {bad}"


def test_every_text_reference_resolves(credits, entries):
    pool = credits["licenses"]
    dangling = [
        f"{e['name']} -> {t['hash']}"
        for e in entries for t in e["texts"] if t["hash"] not in pool
    ]
    assert not dangling, f"credit entries point at missing licence text: {dangling}"
    used = {t["hash"] for e in entries for t in e["texts"]}
    assert not (orphans := set(pool) - used), (
        f"licence texts nothing references: {sorted(orphans)}. {REGENERATE}")


def test_copyleft_and_restricted_licences_are_surfaced_not_buried(credits):
    """Anything the policy could not clear belongs in its own group at the top.

    Burying an owner decision inside 46 Rust crates is the same as not raising
    it, and this is the check that says so.
    """
    flagged = [e for g in credits["groups"] for e in g["entries"] if e.get("flag")]
    group = next((g for g in credits["groups"] if g["id"] == "flagged"), None)
    if flagged:
        assert group is not None, "entries are flagged but there is no flagged group"
        assert group is credits["groups"][0], (
            "the flagged group must render first; an owner decision below the "
            "fold of a 123-entry page is an owner decision nobody makes")
        assert {e["name"] for e in flagged} == {e["name"] for e in group["entries"]}
    for entry in flagged:
        assert len(entry["flag"]) > 40, (
            f"{entry['name']} is flagged with no explanation of what to decide")


def test_every_entry_is_well_formed(entries):
    for entry in entries:
        assert entry["name"].strip(), "an entry has no name"
        assert entry["spdx"].strip(), f"{entry['name']} has no licence identifier"
        assert entry["spdx"] != "UNKNOWN" or entry.get("flag"), (
            f"{entry['name']} has an unknown licence and is not flagged")
        assert entry["requires"] or entry.get("flag") or entry["summaries"], (
            f"{entry['name']} states no obligation and no reason it has none")


def test_data_we_modified_says_so(credits):
    """CC BY and CC BY-SA both require an indication of whether we changed the
    work. We extracted a column subset from OpenNGC and cut the IAU star list,
    so both must say so in words a reader can find."""
    # Scoped to the CC family on purpose. Apache-2.0 also carries a
    # state-changes clause, but it bites only when you modify Apache-licensed
    # FILES -- our own generated tables are not that, and asserting over them
    # would make this guard fire on the wrong thing and get relaxed.
    data = next(g for g in credits["groups"] if g["id"] == "data")
    modified = [e for e in data["entries"]
                if e["spdx"].upper().startswith("CC-BY")
                and policy.STATE_CHANGES in e["requires"]]
    assert modified, "no CC-licensed data entry carries a state-changes obligation"
    for entry in modified:
        assert "WE CHANGED IT" in entry.get("notes", ""), (
            f"{entry['name']} must indicate whether it was modified")


def test_screen_covers_every_group_the_generator_produced(credits):
    """The UI renders `groups` in order and must not be handed an empty one."""
    assert credits["groups"], "no groups at all"
    for group in credits["groups"]:
        assert group["entries"], f"group {group['id']} would render empty"
        assert group["title"] and group["blurb"], f"group {group['id']} is unlabelled"
