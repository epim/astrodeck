"""The version lives in two files and they must agree.

Found during the 0.2.71 deploy. ``pyproject.toml`` was bumped and
``astrodeck/__init__.py`` was not, so ``/healthz`` reported 0.2.70 while
0.2.71 was genuinely running. That cost a long detour: the honest signal a
deploy has landed is the version it reports, and when that signal lies the next
move is to distrust the deploy — which is exactly the wrong direction.

``__version__`` is what the API reports (``/healthz``, ``/api/version``, the
FastAPI app title); ``pyproject.toml`` is what the release tarball and the
install are built from. Neither is redundant, so the fix is not to delete one —
it is to refuse to let them drift.
"""
from __future__ import annotations

import re
from pathlib import Path

import astrodeck

_PYPROJECT = Path(astrodeck.__file__).resolve().parents[1] / "pyproject.toml"


def test_pyproject_and_dunder_version_agree():
    text = _PYPROJECT.read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert m, f"no version line in {_PYPROJECT}"
    assert m.group(1) == astrodeck.__version__, (
        f"pyproject.toml says {m.group(1)!r} and astrodeck.__version__ says "
        f"{astrodeck.__version__!r}. /healthz reports the dunder, so a release "
        f"built from pyproject would report the wrong number and a deploy that "
        f"HAD landed would look like it had not.")
