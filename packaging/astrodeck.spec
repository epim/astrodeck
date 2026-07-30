# PyInstaller spec — AstroDeck as one file you run.
#
# Built by packaging/build_binary.py, which builds the UI first and copies it to
# server/astrodeck/webui so it is ordinary package data here.
#
# Three things this has to get right, each of which fails SILENTLY if missed —
# the binary starts, and then some part of the product is just absent:
#
#   1. The SPA. Without it the server answers the API and serves no interface.
#   2. Package METADATA. The device backends register through setuptools entry
#      points (pyproject: [project.entry-points."astrodeck.backends"]), and
#      entry-point discovery reads installed metadata, not imports. Drop the
#      metadata and every native driver quietly disappears — the app runs, finds
#      no hardware, and says nothing about why.
#   3. Astropy's data. It resolves config and coordinate data at import time.
#
# Everything reachable only by entry point also needs naming in hiddenimports:
# PyInstaller follows imports, and an entry point is a string in a metadata file.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

SERVER = Path(SPECPATH).resolve().parent / "server"

datas = []
datas += copy_metadata("astrodeck")          # entry points -> native backends
datas += collect_data_files("astropy")
datas += collect_data_files("astropy_healpix")

# The built SPA. Not optional: without it the binary is an API with no UI.
webui = SERVER / "astrodeck" / "webui"
if not (webui / "index.html").is_file():
    raise SystemExit(
        f"{webui}/index.html is missing — run packaging/build_binary.py, which "
        "builds the UI first. Building this spec directly produces a binary "
        "that serves no interface.")
datas.append((str(webui), "astrodeck/webui"))

# Vendored SDK libraries: Windows DLLs plus the per-platform Linux .so and
# macOS .dylib trees. Copied wholesale, so the binary carries the libraries for
# the platform it was built on (and harmlessly, the others).
vendor = SERVER / "astrodeck" / "vendor"
if vendor.is_dir():
    datas.append((str(vendor), "astrodeck/vendor"))

# Bundled survey pack, when the tree has one (it is large and optional).
pack = SERVER / "astrodeck" / "catalog" / "_bundled_pack"
if pack.is_dir():
    datas.append((str(pack), "astrodeck/catalog/_bundled_pack"))

hiddenimports = [
    # Registered by entry point, so nothing imports them statically.
    "astrodeck.devices.backends.zwo_am5",
    "astrodeck.devices.backends.zwo_usb",
    "astrodeck.devices.backends.wanderer_snowflake",
    "astrodeck.devices.backends.zwo_asi",
    "astrodeck.devices.backends.player_one",
    "astrodeck.devices.backends.asiair_backend",
    "astrodeck.devices.backends.ascom_local",
    "astrodeck.devices.backends.nina_backend",
    "astrodeck.devices.backends.phd2_backend",
    "astrodeck.devices.backends.native_backend",
    "astrodeck.devices.backends.sim_backend",
    # uvicorn picks its implementation at runtime by name.
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

a = Analysis(
    [str(Path(SPECPATH).resolve() / "entry.py")],
    pathex=[str(SERVER)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Test and plotting stacks that numpy/astropy pull in transitively and the
    # server never uses. Dropping them is most of the difference between a
    # ~120 MB binary and a ~350 MB one.
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "notebook",
              "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="astrodeck",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX off on purpose: it saves perhaps 30% and reliably trips antivirus
    # heuristics on Windows, which turns "download and run" into "explain to
    # your security software why this is fine".
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
