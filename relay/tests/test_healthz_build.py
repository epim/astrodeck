"""/healthz answers the build identity baked into the image (#462 item 2).

Before this, ``GET /healthz`` answered only ``{"ok": true, "ts": ...}``, so
nothing on the running relay said which code it was. The 2026-09-05 security
memo found it had run July code for two months without anyone noticing, and
the #399 deploy (Fly release v9) could be told apart from v8 only by the build
context it came from.

The identity is a pair of Dockerfile build args passed to env
(``RELAY_BUILD_VERSION``, ``RELAY_BUILD_COMMIT``), which
``scripts/deploy_relay.ps1`` sets and then reads back from ``/healthz``. An
image built without them has each variable set to the EMPTY string (an ARG
with no value expands to ""), so empty must read as unset: ``unknown``, never a
guess. The package's own ``__version__`` would be a guess -- it has read 0.1.0
since the relay was written, so it says nothing about which build is live.

These run off-wire through Starlette's TestClient; nothing is deployed.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")

from starlette.testclient import TestClient  # noqa: E402

from relay.config import RelayConfig  # noqa: E402
from relay.server import create_app  # noqa: E402

_DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"
_BUILD_VARS = ("RELAY_BUILD_VERSION", "RELAY_BUILD_COMMIT")


def _healthz(monkeypatch, **env: str) -> dict:
    """GET /healthz from an app built under exactly ``env`` for the two build
    variables, with no device-token map (none is needed to answer it)."""
    for name in ("RELAY_DEVICE_TOKENS_FILE", "RELAY_DEVICE_TOKENS", *_BUILD_VARS):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    app = create_app(RelayConfig(bind_host="127.0.0.1", origin="relay.test"))
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200, r.text
    return r.json()


def test_healthz_answers_the_version_and_commit_the_image_was_built_with(
        monkeypatch):
    """The deployed case: both build args set. Both values are computed per
    run, so no constant in the code can match them by accident.

    MUTATION "healthz ignores the build env" (``create_app`` hands the
    route a fixed ``{"version": BUILD_UNKNOWN, "commit": BUILD_UNKNOWN}`` in
    place of ``build_identity()``). Observed, run from a private copy on
    2026-09-28:
      AssertionError: /healthz does not report the commit baked into the
      image: {'ok': True, 'ts': 1790644451.6189148, 'version': 'unknown',
      'commit': 'unknown'}
      assert 'unknown' == '91c18299734b...a99a553516d67'
    (and ``test_each_half_of_the_identity_is_read_on_its_own`` red with it).
    """
    commit = hashlib.sha1(uuid.uuid4().bytes).hexdigest()
    version = "0.3." + str(uuid.uuid4().int % 1000)

    body = _healthz(monkeypatch, RELAY_BUILD_VERSION=version,
                    RELAY_BUILD_COMMIT=commit)

    assert body["ok"] is True, body
    assert isinstance(body["ts"], float), body
    assert body["commit"] == commit, (
        f"/healthz does not report the commit baked into the image: {body}")
    assert body["version"] == version, (
        f"/healthz does not report the version baked into the image: {body}")


@pytest.mark.parametrize("raw", [None, "", "   "],
                         ids=["unset", "empty", "blank"])
def test_healthz_answers_unknown_when_the_image_carries_no_identity(
        monkeypatch, raw):
    """An image built without the args. [empty] is the one Fly actually
    runs in that case: ``ENV X=$X`` with no build arg sets X to "".

    Control for "healthz ignores the build env": green under it, since the
    mutant answers "unknown" for everything.

    MUTATION "invent a version" (``build_identity`` falls back to the
    package's ``__version__`` for an empty RELAY_BUILD_VERSION). Observed,
    all three cases red:
      AssertionError: an image with no identity must say so, not guess:
      {'ok': True, 'ts': 1790644463.2317536, 'version': '0.1.0',
      'commit': 'unknown'}
    MUTATION "empty is a value" (``_read`` -> ``env.get(name,
    BUILD_UNKNOWN)``, so a set variable is echoed as read). Observed,
    [empty] and [blank] red, [unset] green (the default still answers):
      AssertionError: an image with no identity must say so, not guess:
      {'ok': True, 'ts': 1790644463.8258202, 'version': '', 'commit': ''}
      AssertionError: an image with no identity must say so, not guess:
      {'ok': True, 'ts': 1790644463.862922, 'version': '   ', 'commit': '   '}
    """
    env = {} if raw is None else {n: raw for n in _BUILD_VARS}

    body = _healthz(monkeypatch, **env)

    assert (body["version"], body["commit"]) == ("unknown", "unknown"), (
        f"an image with no identity must say so, not guess: {body}")
    assert body["ok"] is True, body


def test_each_half_of_the_identity_is_read_on_its_own(monkeypatch):
    """A commit with no version (or the reverse) reports the half it has and
    "unknown" for the other, never the one value in both places.

    MUTATION "one variable for both" (``version`` read from
    ``RELAY_BUILD_COMMIT``). Observed:
      AssertionError: {'commit': 'e624438c80a5746383e3897e3b9b2888a1e7bc3a',
      'ok': True, 'ts': 1790644464.5519373, 'version':
      'e624438c80a5746383e3897e3b9b2888a1e7bc3a'}
    Also red under "invent a version" (``'0.1.0' != 'unknown'``).
    """
    commit = hashlib.sha1(uuid.uuid4().bytes).hexdigest()

    body = _healthz(monkeypatch, RELAY_BUILD_COMMIT=commit)

    assert (body["version"], body["commit"]) == ("unknown", commit), body


def _stages(dockerfile: str) -> list[list[str]]:
    """The Dockerfile's instructions, grouped by FROM, with line continuations
    joined and comments dropped."""
    joined = re.sub(r"\\\r?\n", " ", dockerfile)
    stages: list[list[str]] = []
    for line in joined.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"(?i)FROM\s", line):
            stages.append([])
        if stages:
            stages[-1].append(line)
    return stages


def test_the_runtime_stage_declares_the_build_args_and_passes_them_to_env():
    """The Dockerfile half. A build arg is scoped to the stage that declares
    it: one declared before a FROM, or in the build stage, expands to "" in
    the runtime stage, and every deploy would then answer "unknown" while the
    script passed the right commit. The runtime stage (the last) must declare
    each ARG and set the same-named ENV from it.

    MUTATION "ARG in the build stage" (both ARG lines moved into the first
    stage, the ENV left in the runtime one). Observed:
      AssertionError: the runtime stage does not declare ARG
      RELAY_BUILD_VERSION, so the ENV reads it as ""
    MUTATION "ARG never reaches ENV" (the ENV instruction deleted).
    Observed:
      AssertionError: the runtime stage never sets ENV
      RELAY_BUILD_VERSION=$RELAY_BUILD_VERSION, so /healthz cannot see the
      build arg
    MUTATION "ARG default" (``ARG RELAY_BUILD_VERSION`` ->
    ``ARG RELAY_BUILD_VERSION=0.1.0``, an identity a bare ``fly deploy``
    would report). It survived this case until the default check was added
    by the S7-RELAY verifier; observed after, from a private copy on
    2026-09-28:
      AssertionError: the runtime stage gives ARG RELAY_BUILD_VERSION a
      default (['ARG RELAY_BUILD_VERSION=0.1.0']), so an image built
      without the arg reports that invented value, not "unknown"
    Control: ``ARG RELAY_BUILD_VERSION=""`` (the same as no default) stays
    green.
    Docker was not running on this box, so no image was built: this case
    grades the Dockerfile's text against the scoping rule, and the deploy
    script's /healthz poll is what grades a real build.
    """
    stages = _stages(_DOCKERFILE.read_text(encoding="utf-8"))
    assert len(stages) >= 2, "expected a build stage and a runtime stage"
    runtime = stages[-1]
    for name in _BUILD_VARS:
        args = [i for i in runtime
                if re.fullmatch(rf"(?i)ARG\s+{name}(=\S*)?", i)]
        assert args, (f"the runtime stage does not declare ARG {name}, so the "
                      f"ENV reads it as \"\"")
        # A default is an identity the build never supplied: a bare
        # `fly deploy` would then report it instead of "unknown". Only an
        # explicitly empty default means the same as none.
        defaulted = [i for i in args
                     if not re.fullmatch(rf"(?i)ARG\s+{name}(=|=\"\"|='')?", i)]
        assert not defaulted, (
            f"the runtime stage gives ARG {name} a default ({defaulted}), so "
            f"an image built without the arg reports that invented value, "
            f"not \"unknown\"")
        env_idx = [n for n, i in enumerate(runtime)
                   if re.match(r"(?i)ENV\s", i)
                   and re.search(rf"(?<!\S){name}=\$\{{?{name}\}}?(?!\S)", i)]
        assert env_idx, (f"the runtime stage never sets ENV {name}=${name}, "
                         f"so /healthz cannot see the build arg")
        assert runtime.index(args[0]) < env_idx[0], (
            f"ENV {name} is set before ARG {name} is declared")


def test_the_test_client_import_avoids_the_deprecated_httpx_shim():
    """#629: under the pinned starlette==1.7.0, ``starlette.testclient``
    tries ``import httpx2 as httpx`` first and only falls back to the real
    ``httpx`` -- with a StarletteDeprecationWarning -- when httpx2 is
    missing. That probe runs once, at module-import time: by the time any
    test function here executes, this file's own ``from
    starlette.testclient import TestClient`` above (and every other test
    file's) has already imported the module and cached the result, so a
    plain ``pytest.warns`` in-process can no longer observe it. A fresh
    subprocess is the only way to see the real, once-per-process check.

    MUTATION "drop httpx2 from the dev requirement" (the ``httpx2`` line
    removed from relay/requirements-dev.txt, reproduced by uninstalling it
    from an otherwise-identical venv). Observed:
      starlette.exceptions.StarletteDeprecationWarning: Using `httpx` with
      `starlette.testclient` is deprecated; install `httpx2` instead.
    """
    result = subprocess.run(
        [sys.executable, "-W", "error::UserWarning", "-c",
         "import starlette.testclient"],
        capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, (
        "starlette.testclient still falls back to the deprecated httpx "
        f"shim (relay/requirements-dev.txt should pin httpx2): "
        f"{result.stderr.strip()}")
