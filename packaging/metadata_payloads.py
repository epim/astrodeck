# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Keep distributable payload rows: wheel metadata without installer-local PEP 610
URLs, and no astropy.wcs data (WCSLIB is not shipped, #638)."""
from pathlib import Path, PurePosixPath

WCSLIB_PARTS = ("astropy/wcs/", "astropy/visualization/wcsaxes/")
WCSLIB_LICENSE = "WCSLIB_LICENSE.rst"


def is_wcslib_payload(*names):
    """True when any name sits under astropy.wcs or is WCSLIB's licence file.

    The server no longer imports astropy.wcs, so none of its data may ship.
    Names are compared as text; nothing is opened.
    """
    for name in names:
        normal = str(name).replace("\\", "/")
        if any(part in normal for part in WCSLIB_PARTS):
            return True
        if normal.rsplit("/", 1)[-1] == WCSLIB_LICENSE:
            return True
    return False


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


def filter_installer_urls(toc, metadata_rows):
    """Remove only the selected roots' installer URL after Analysis hooks.

    TOC rows are destination, source, type. Compare destination names only;
    never open the installer metadata. Nested and other distributions' files
    remain unchanged, including their original row order and source paths.
    """
    excluded = {str(PurePosixPath(str(destination).replace("\\", "/")) / "direct_url.json")
                for source, destination in metadata_rows}
    return [row for row in toc if row[0].replace("\\", "/") not in excluded]


def filter_final_datas(toc, metadata_rows):
    """Apply every post-Analysis omission to the final data TOC.

    PyInstaller hooks run during Analysis and can add back what the spec left
    out: the selected roots' installer URLs and the astropy.wcs data. TOC rows
    are destination, source, type; both names are checked, never opened.
    """
    rows = filter_installer_urls(toc, metadata_rows)
    return [row for row in rows if not is_wcslib_payload(row[0], row[1])]
