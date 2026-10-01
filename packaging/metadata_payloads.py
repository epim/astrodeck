"""Keep distributable wheel metadata, excluding installer-local PEP 610 URLs."""
from pathlib import Path, PurePosixPath


def distributable_metadata(rows):
    """Expand copy_metadata directory pairs without copying direct_url.json.

    Only the root PEP 610 file is omitted. RECORD, license texts, native source,
    SBOM and nested files all retain their original bytes and destination.
    """
    result = []
    for source, destination in rows:
        root = Path(source).resolve()
        if not root.is_dir():
            raise ValueError("metadata source must be a distribution directory")
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if not path.resolve().is_relative_to(root):
                raise ValueError("metadata source escapes distribution directory")
            relative = path.relative_to(root)
            if relative.as_posix() == "direct_url.json":
                continue
            target = PurePosixPath(destination.replace("\\", "/")) / PurePosixPath(relative.parent.as_posix())
            result.append((str(path), str(target)))
    return result
