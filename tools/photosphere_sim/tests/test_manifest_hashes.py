# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A case's manifest hashes must name the bytes on disk (#91).

The manifests record ``hashes.frames``, ``hashes.observations`` and
``hashes.truth``, and until now nothing recomputed any of them. `score.py`
copies `input_hash` forward on trust, so a case whose inputs had changed since
it was recorded scored exactly as if they had not, and said nothing.

That silence has already been load-bearing twice. A committed fixture went
through a Windows clone without the `-text` rule and nine of its twenty-one
text files came back CRLF, which moves both the observations and truth hashes
off the bytes on disk. And when an unguarded delete removed both 42 MB
recordings (#88), the only reason anyone could say the restoration was
faithful is that a person recomputed the digests by hand against the baseline
document.

The recipe here is CONTRACT.md's, not a reimplementation of the writer: the
frames hash is the SHA-256 of the concatenated per-file digests in sorted
name order, observations is the SHA-256 of the file's bytes, and truth is the
same concatenation over every file under ``truth/`` sorted by relative path.
Reading it from the contract rather than importing the writer's helper is
deliberate -- a test that calls the same function the writer called would
agree with it however wrong both were.
"""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = _ROOT / "fixtures"
CACHE_CASES = _ROOT / "cache" / "cases"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _concat_digests(paths: list[Path]) -> str:
    """CONTRACT.md: the SHA-256 of the concatenation of each file's own
    SHA-256 hex digest, in the order given."""
    joined = "".join(_sha256(p.read_bytes()) for p in paths)
    return _sha256(joined.encode("ascii"))


def _recompute(case_dir: Path) -> dict:
    frames_dir = case_dir / "input" / "frames"
    truth_dir = case_dir / "truth"
    observations = case_dir / "input" / "observations.jsonl"

    out: dict[str, str] = {}
    if frames_dir.is_dir():
        names = sorted(p.name for p in frames_dir.glob("*.png"))
        out["frames"] = _concat_digests([frames_dir / n for n in names])
    if observations.is_file():
        out["observations"] = _sha256(observations.read_bytes())
    if truth_dir.is_dir():
        files = sorted((p for p in truth_dir.rglob("*") if p.is_file()),
                       key=lambda p: p.relative_to(truth_dir).as_posix())
        out["truth"] = _concat_digests(files)
    return out


def _cases(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir()
                  if (p / "manifest.json").is_file())


class ManifestHashesNameTheBytesOnDisk(unittest.TestCase):
    """MUTATION: flip one byte in a fixture's observations.jsonl (for example
    change a single digit in the first record). Observed: the fixtures case
    below fails naming `observations` with the two digests, instead of the
    silence the scorer gives today."""

    def _check(self, case_dir: Path) -> None:
        manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
        recorded = manifest.get("hashes") or {}
        self.assertTrue(recorded, f"{case_dir.name}: manifest records no hashes at all")
        actual = _recompute(case_dir)
        self.assertTrue(actual, f"{case_dir.name}: nothing on disk to hash")
        for key, value in actual.items():
            with self.subTest(case=case_dir.name, hash=key):
                self.assertIn(key, recorded,
                              f"{case_dir.name}: manifest has no {key} hash but the "
                              f"files it covers are present")
                self.assertEqual(
                    recorded[key], value,
                    f"{case_dir.name}: the manifest's {key} hash does not name the "
                    f"bytes on disk. Either the inputs changed since the case was "
                    f"recorded, or the recording was restored imperfectly. A score "
                    f"taken now would carry the OLD input_hash forward and read as "
                    f"if nothing had moved.")

    def test_every_committed_fixture(self):
        """Unconditional: this is the half that protects a fresh clone, and the
        CRLF instance that prompted this issue was a fixture."""
        cases = _cases(FIXTURES)
        self.assertTrue(cases, f"no fixture cases under {FIXTURES} - this test "
                               f"would otherwise pass by having nothing to check")
        for case_dir in cases:
            self._check(case_dir)

    def test_every_recording_present_in_the_cache(self):
        """The recordings are git-ignored, so this half skips loudly when the
        cache is absent, the way the replay cases do."""
        cases = _cases(CACHE_CASES)
        if not cases:
            raise unittest.SkipTest(
                f"no recordings under {CACHE_CASES}; render them to check their "
                f"hashes (README: sim render)")
        for case_dir in cases:
            self._check(case_dir)


if __name__ == "__main__":      # pragma: no cover
    unittest.main()
