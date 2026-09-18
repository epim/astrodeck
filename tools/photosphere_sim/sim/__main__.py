"""``python -m sim <command> ...``, from CONTRACT.md's Commands section.

Only ``make-case`` is implemented in this task; ``score``, ``corrupt``,
``ideal`` and ``report`` are Tasks 7 and 8's, and print a plain message and
exit 2 rather than pretend to run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .cases import CASES_DIR, build_case, flat_renderer

_NOT_YET_IMPLEMENTED = ("score", "corrupt", "ideal", "report")


def _load_case_def(case_id: str) -> dict:
    path = CASES_DIR / f"{case_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _make_case(args: argparse.Namespace) -> int:
    if args.renderer != "flat":
        # The default is "three", Task 4's renderer. Until it lands, this
        # command must refuse rather than silently render mid-grey frames
        # under a name that promises something else.
        print("renderer not available: 'three' is wired up in Task 4; "
              "pass --renderer flat", file=sys.stderr)
        return 1
    case_def = _load_case_def(args.case_id)
    out_dir = build_case(case_def, Path(args.out), flat_renderer)
    print(f"wrote {out_dir}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sim")
    subparsers = parser.add_subparsers(dest="command", required=True)

    make_case = subparsers.add_parser("make-case")
    make_case.add_argument("case_id")
    make_case.add_argument("--out", default="cache/cases")
    make_case.add_argument("--renderer", choices=["three", "flat"], default="three")

    for name in _NOT_YET_IMPLEMENTED:
        stub = subparsers.add_parser(name)
        stub.add_argument("rest", nargs=argparse.REMAINDER)

    args = parser.parse_args(argv)

    if args.command == "make-case":
        return _make_case(args)

    print(f"{args.command}: not implemented in this task", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
