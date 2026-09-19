"""``python -m sim <command> ...``, from CONTRACT.md's Commands section.

``score`` writes ``report.html`` beside ``scores.json``; ``report`` re-renders
that page from a ``scores.json`` that is already there, without scoring again;
``corrupt`` writes a deliberately broken copy of a result directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import report as report_module
from .cases import CASES_DIR, build_case, flat_renderer
from .corrupt import CORRUPTIONS, apply as apply_corruption
from .ideal import make_ideal_result
from .score import score_case

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
    """The case directory, or exit 2.

    Exit 2 is "I could not run", distinct from exit 1, "I ran and the case
    failed". `SystemExit` with a string exits 1, which would have reported a
    missing case as a failing one.
    """
    case_dir = Path(args.cases) / args.case_id
    if not case_dir.is_dir():
        print(f"no case directory at {case_dir}", file=sys.stderr)
        raise SystemExit(2)
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
        print(f"no result directory at {result_dir}: replay the case first",
              file=sys.stderr)
        raise SystemExit(2)
    try:
        scores = score_case(case_dir, result_dir)
    except ValueError as error:
        # A case the scorer cannot score is "I could not run", not "the case
        # failed": a truth built before the per-obstacle silhouette has no
        # profile to score an obstacle against and must be rebuilt. Exit 2
        # with the message, not exit 1 with a traceback, because exit 1 is
        # what a failing result looks like.
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
    report_module.render(case_dir, scores, result_dir / "report.html",
                         result_dir=result_dir)

    landmarks, horizon = scores["landmarks"], scores["horizon"]
    print(f"case      {scores['case_id']}  profile {scores['profile']}")
    print(f"panorama  {json.dumps(scores['panorama'])}")
    print(f"landmarks expected {landmarks['expected']} found {landmarks['found']} "
          f"omitted {len(landmarks['omitted'])} duplicated {len(landmarks['duplicated'])} "
          f"slivers {landmarks['slivers']} spurious {landmarks['spurious']}")
    print(f"          errors_deg {json.dumps(landmarks['errors_deg'])}")
    print(f"horizon   {horizon['measured_bins']} bins "
          f"({horizon['measured_resolution_deg']} deg) "
          f"signed {json.dumps(horizon['signed_error_deg'])}")
    print(f"          false_open_sr {horizon['false_open_sr']} "
          f"false_blocked_sr {horizon['false_blocked_sr']} "
          f"unresolved_sr {horizon['unresolved_sr']} "
          f"north_offset_deg {horizon['north_offset_deg']}")
    print(f"          missed_obstructions {json.dumps(horizon['missed_obstructions'])}")
    for obstacle in horizon["obstacles"]:
        print(f"          {json.dumps(obstacle)}")
    print(f"overlay   {json.dumps(scores['overlay'])}")
    print(f"capture   {json.dumps(scores['capture'])}")
    print(f"coverage  {json.dumps(scores['coverage'])}")
    print(f"gates     {json.dumps(scores['gates'], indent=2)}")
    print(f"report    {result_dir / 'report.html'}")
    return 0 if scores["gates"]["pass"] else 1


def _parse_params(pairs: list) -> dict:
    """``--param deg=5`` into ``{"deg": 5}``, JSON first, then a plain string.

    JSON first so that ``offset_m=[1,0,0]`` and ``scale=1.05`` arrive as the
    list and the float they look like; a value that is not JSON (a bare word)
    is passed through as text rather than refused.
    """
    params = {}
    for pair in pairs:
        if "=" not in pair:
            print(f"--param wants KEY=VALUE, got {pair!r}", file=sys.stderr)
            raise SystemExit(2)
        key, _, value = pair.partition("=")
        try:
            params[key] = json.loads(value)
        except json.JSONDecodeError:
            params[key] = value
    return params


def _corrupt(args: argparse.Namespace) -> int:
    """Write a corrupted copy of a result directory and say where it went."""
    case_dir = _case_dir(args)
    result_dir = Path(args.result) if args.result else case_dir / "result"
    if not result_dir.is_dir():
        print(f"no result directory at {result_dir}: replay the case first",
              file=sys.stderr)
        raise SystemExit(2)
    out_dir = Path(args.out) if args.out else case_dir / "corrupt" / args.name
    try:
        written = apply_corruption(case_dir, result_dir, args.name, out_dir,
                                   **_parse_params(args.param))
    except (TypeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
    print(f"wrote {written}")
    return 0


def _report(args: argparse.Namespace) -> int:
    """Re-render ``report.html`` from a ``scores.json`` that is already there."""
    case_dir = _case_dir(args)
    result_dir = Path(args.result) if args.result else case_dir / "result"
    scores_path = result_dir / "scores.json"
    if not scores_path.is_file():
        print(f"no scores.json at {scores_path}: score the case first",
              file=sys.stderr)
        raise SystemExit(2)
    scores = json.loads(scores_path.read_text(encoding="utf-8"))
    written = report_module.render(case_dir, scores, result_dir / "report.html",
                                   result_dir=result_dir)
    print(f"wrote {written}")
    return 0


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

    corrupt = subparsers.add_parser("corrupt")
    corrupt.add_argument("case_id")
    corrupt.add_argument("name", choices=sorted(CORRUPTIONS))
    corrupt.add_argument("--cases", default=DEFAULT_CASE_ROOT)
    corrupt.add_argument("--result", default=None,
                         help="the result directory to corrupt (default <case>/result)")
    corrupt.add_argument("--out", default=None,
                         help="where to write it (default <case>/corrupt/<name>)")
    corrupt.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                         help="a corruption parameter, for example deg=5")

    report = subparsers.add_parser("report")
    report.add_argument("case_id")
    report.add_argument("--cases", default=DEFAULT_CASE_ROOT)
    report.add_argument("--result", default=None,
                        help="the scored result directory (default <case>/result)")

    args = parser.parse_args(argv)

    if args.command == "make-case":
        return _make_case(args)
    if args.command == "score":
        return _score(args)
    if args.command == "ideal":
        return _ideal(args)
    if args.command == "corrupt":
        return _corrupt(args)
    return _report(args)


if __name__ == "__main__":
    raise SystemExit(main())
