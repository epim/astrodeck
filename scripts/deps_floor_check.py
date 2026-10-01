"""Compare the running venv's installed packages with a release's declared
dependency floors (#609).

    python deps_floor_check.py <release>/server/pyproject.toml [--report]

Reads only the ``[project]`` table's ``dependencies`` list, checks each
requirement against ``importlib.metadata`` in THIS interpreter, and prints one
line per requirement. Exits 1 if any requirement is missing or below its floor,
unless ``--report`` is given (then it only prints, and exits 0).

A rig deploy runs it twice: once before anything is stopped, to say what the
wheelhouse must fix, and once after the offline install, as the gate that
refuses to start the new release on a venv that still falls short. Without it,
every floor raised in pyproject since the venv was built silently never
reached the rig (#609: fastapi 0.138 under a >=0.141.1 floor, and sgp4 absent).
"""
import re
import sys
from importlib.metadata import PackageNotFoundError, version

from packaging.requirements import Requirement
from packaging.version import Version


def project_dependencies(path):
    """The [project] table's dependencies list, as requirement strings."""
    deps, in_project, in_deps = [], False, False
    for raw in open(path, encoding="utf-8"):
        line = raw.split("#", 1)[0].rstrip()
        table = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if table:
            in_project, in_deps = table.group(1).strip() == "project", False
            continue
        if not in_project:
            continue
        if re.match(r"^\s*dependencies\s*=\s*\[", line):
            in_deps = True
            line = line.split("[", 1)[1]
        if in_deps:
            deps += re.findall(r'"([^"]+)"', line)
            # The list ends at a ']' OUTSIDE quotes: "uvicorn[standard]" has one inside.
            if "]" in re.sub(r'"[^"]*"', "", line):
                in_deps = False
    return deps


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    report_only = "--report" in argv
    short = 0
    for spec in project_dependencies(argv[1]):
        req = Requirement(spec)
        if req.marker is not None and not req.marker.evaluate():
            continue
        try:
            have = version(req.name)
        except PackageNotFoundError:
            print("   MISSING  %-20s needs %s" % (req.name, req.specifier or "any"))
            short += 1
            continue
        ok = req.specifier.contains(Version(have), prereleases=True)
        print("   %-8s %-20s has %-12s needs %s" % ("ok" if ok else "BELOW", req.name, have,
                                                   req.specifier or "any"))
        short += 0 if ok else 1
    print("   %d requirement(s) short" % short)
    return 0 if (report_only or short == 0) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
