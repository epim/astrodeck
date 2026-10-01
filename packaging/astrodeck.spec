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

ROOT = Path(SPECPATH).resolve().parent
SERVER = ROOT / "server"
sys.path.insert(0, str(ROOT / "packaging"))
from distribution_policy import load_policy, selected_files, check_package_data
from metadata_payloads import distributable_metadata
policy = load_policy(ROOT)
check_package_data(ROOT, policy)

datas = []
datas += distributable_metadata(copy_metadata("astrodeck-native"))   # native version, real notices, source archive
datas += distributable_metadata(copy_metadata("astrodeck"))          # entry points -> native backends
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

# Select individual payload files through the shared owner policy. A wholesale
# directory copy would bypass both fetch-only SDK choices and the DSS2 refusal.
for source, relative in selected_files(SERVER / "astrodeck", "frozen", policy):
    if relative.startswith(("vendor/", "catalog/_bundled_pack/")):
        datas.append((str(source), "astrodeck/" + str(Path(relative).parent).replace("\\", "/")))
for name in ("LICENSE", "THIRD-PARTY-NOTICES.md"):
    datas.append((str(ROOT / name), "astrodeck-notices"))
datas.append((str(ROOT / "packaging/distribution-policy.json"), "astrodeck-notices"))

hiddenimports = [
    "astrodeck_native",
    "native_probe",
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
    pathex=[str(SERVER), str(ROOT / "packaging")],
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
