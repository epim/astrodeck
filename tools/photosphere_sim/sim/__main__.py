"""``python -m sim <command> ...``, from CONTRACT.md's Commands section.

``corrupt`` and ``report`` are Task 8's, and print a plain message and exit 2
rather than pretend to run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .cases import CASES_DIR, build_case, flat_renderer
from .ideal import make_ideal_result
from .score import score_case

_NOT_YET_IMPLEMENTED = ("corrupt", "report")

#: Where ``make-case`` writes and where ``score`` and ``ideal`` look, relative
#: to the working directory, as CONTRACT.md's case layout has it.
DEFAULT_CASE_ROOT = "cache/cases"


def _load_case_def(case_id: str) -> dict:
    path = CASES_DIR / f"{case_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _renderer(name: str):
    """The renderer ``make-case`` was asked for.

    ``sim.render`` is imported only when it is wanted, so that a flat build
    needs neither Playwright nor a browser on the machine.
    """
    if name == "flat":
        return flat_renderer

    from .render import CaseRenderer

    # A fresh one per run: it carries the versions the manifest records.
    return CaseRenderer()


def _make_case(args: argparse.Namespace) -> int:
    case_def = _load_case_def(args.case_id)
    out_dir = build_case(case_def, Path(args.out), _renderer(args.renderer))
    print(f"wrote {out_dir}")
    return 0


def _case_dir(args: argparse.Namespace) -> Path:
    case_dir = Path(args.cases) / args.case_id
    if not case_dir.is_dir():
        raise SystemExit(f"no case directory at {case_dir}")
    return case_dir


def _ideal(args: argparse.Namespace) -> int:
    case_dir = _case_dir(args)
    out_dir = Path(args.out) if args.out else case_dir / "ideal"
    print(f"wrote {make_ideal_result(case_dir, out_dir)}")
    return 0


def _score(args: argparse.Namespace) -> int:
    """Score a result directory, print the gates, and exit non-zero on a fail.

    The exit status is the gate verdict: a scorer that reports a failing case
    and still exits 0 is a green light nothing keeps.
    """
    case_dir = _case_dir(args)
    result_dir = Path(args.result) if args.result else case_dir / "result"
    if not result_dir.is_dir():
        # Not the same thing as a bad result: say which it is rather than
        # print an all-false report a reader would blame on the scanner.
        raise SystemExit(f"no result directory at {result_dir}: replay the case first")
    scores = score_case(case_dir, result_dir)

    landmarks, horizon = scores["landmarks"], scores["horizon"]
    print(f"case      {scores['case_id']}  profile {scores['profile']}")
    print(f"landmarks expected {landmarks['expected']} found {landmarks['found']} "
          f"omitted {len(landmarks['omitted'])} duplicated {len(landmarks['duplicated'])} "
          f"spurious {landmarks['spurious']}")
    print(f"          errors_deg {json.dumps(landmarks['errors_deg'])}")
    print(f"horizon   {horizon['measured_bins']} bins "
          f"({horizon['measured_resolution_deg']} deg) "
          f"signed {json.dumps(horizon['signed_error_deg'])}")
    print(f"          false_open_sr {horizon['false_open_sr']} "
          f"false_blocked_sr {horizon['false_blocked_sr']} "
          f"unresolved_sr {horizon['unresolved_sr']} "
          f"north_offset_deg {horizon['north_offset_deg']}")
    print(f"          missed_obstructions {json.dumps(horizon['missed_obstructions'])}")
    print(f"overlay   {json.dumps(scores['overlay'])}")
    print(f"capture   {json.dumps(scores['capture'])}")
    print(f"coverage  {json.dumps(scores['coverage'])}")
    print(f"gates     {json.dumps(scores['gates'], indent=2)}")
    return 0 if scores["gates"]["pass"] else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sim")
    subparsers = parser.add_subparsers(dest="command", required=True)

    make_case = subparsers.add_parser("make-case")
    make_case.add_argument("case_id")
    make_case.add_argument("--out", default=DEFAULT_CASE_ROOT)
    make_case.add_argument("--renderer", choices=["three", "flat"], default="three")

    score = subparsers.add_parser("score")
    score.add_argument("case_id")
    score.add_argument("--cases", default=DEFAULT_CASE_ROOT)
    score.add_argument("--result", default=None,
                       help="the result directory to score (default <case>/result)")

    ideal = subparsers.add_parser("ideal")
    ideal.add_argument("case_id")
    ideal.add_argument("--cases", default=DEFAULT_CASE_ROOT)
    ideal.add_argument("--out", default=None,
                       help="where to write the ideal result (default <case>/ideal)")

    for name in _NOT_YET_IMPLEMENTED:
        stub = subparsers.add_parser(name)
        stub.add_argument("rest", nargs=argparse.REMAINDER)

    args = parser.parse_args(argv)

    if args.command == "make-case":
        return _make_case(args)
    if args.command == "score":
        return _score(args)
    if args.command == "ideal":
        return _ideal(args)

    print(f"{args.command}: not implemented in this task", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
