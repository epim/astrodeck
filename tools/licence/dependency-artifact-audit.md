# Dependency and artifact licence audit

Audit date: 2026-10-01. Source base: `bf3eaddd`, branch `codex/licence-audit`. This independent audit changes only this report and `dependency-*` evidence files. No application or packaging source was changed, no shared packages were installed, and neither executable was run.

The August statement, **"No GPL, LGPL, AGPL or SSPL anywhere in the stack"**, is refuted by actual Windows artifact contents. The fresh executable contains WCSLIB under LGPL, and NumPy's OpenBLAS DLL includes GCC runtime code under GPL with its runtime exception. Those are different cases from an unqualified GPL application dependency. No AGPL or SSPL component was identified in the inspected package graphs; unavailable platform artifacts and unexamined internals of proprietary binaries prevent a universal absence claim.

## Artifact coverage and reproducibility

| Surface | Evidence obtained | Limit |
| --- | --- | --- |
| Current Windows x86_64 executable | Fresh private Python 3.12.10 environment, AstroDeck 0.3.39, PyInstaller 6.22.3; 887 CArchive entries and 2,384 PYZ modules; complete file hashes and PE import names | Local reproduction, not a downloaded signed release. Minimum-version dependencies resolve as of audit date. No executable execution/smoke test |
| Historical local Windows executable | September 8 file; 751 CArchive entries and 2,291 PYZ modules | Source revision not established; corroborating evidence only |
| Linux x86_64, Linux arm64, macOS arm64 executables | Release matrix and packaging recipe inspected | No artifacts built or obtained; their wheel/native-library payloads are not certified by the Windows result |
| Native wheel | Fresh offline release build with maturin 1.14.1, Cargo 1.94.0; Windows `cp311-abi3-win_amd64` wheel, six files | Other OS wheels not built. Rust package metadata is not a substitute for their system-library inventory |
| UI | 229 npm lock entries; parent's actual Vite chunk-input inventory; 58 shipped font files matched byte-for-byte to locked Fontsource files | Build/development resolution alone does not prove retention after compilation. CSS/plugin output requires separate tracing |
| Source/UI release tarball | Parent-owned `tarball-inventory.json` | Tarball conclusions remain in the parent audit; Windows executable has a different vendor/data surface |
| Root container / relay container | Dockerfiles, pinned base digest, relay requirements and runtime copies inspected | Docker daemon unavailable; no image layers, package database or final OCI manifest inventoried |
| Orange Pi appliance | Armbian Trixie documentation and rootfs provisioning installer inspected | No SD/eMMC/rootfs image inspected and no real device contacted |

Current executable: 64,061,492 bytes, SHA-256 `f68acf1fbcce3f3787d1c18b9fc3a127dfcde74d5391863482d7091228f47525`.
Native wheel: 423,803 bytes, SHA-256 `e17a748cfd32a3c9dd56f612c7d63a1ee44dc8849aa6d897e447aa50b1cdd73c`.
Historical executable: SHA-256 `c09b534670a207736b44b32c0aa9a6a32be7913a684dd0c7b38a5bf6b6a13382`.

Private scratch is `.probe/licence-deps/`. The first build using the shared interpreter failed because its Astropy 7.2 visualization hook encountered `pytest.importorskip` without matplotlib. That environment also contained AstroDeck 0.3.28 and an existing native extension; it was not used for the successful result. A new venv installed `pyinstaller>=6.0` and `./server`, without development or optional extras. The shared venv was read-only. Available disk space before the build was 137 GiB.

The successful build used the current spec directly, with separate `--workpath`, `--distpath`, `PYINSTALLER_CONFIG_DIR`, `PIP_CACHE_DIR`, config and capture paths. `ui/dist` was copied into the ignored worktree `server/astrodeck/webui`. The resulting executable was read with `PyInstaller.archive.readers.CArchiveReader`; PE files were inspected with pefile. No application initialization or device connection was performed.

Representative reproduction, using a new private directory and the normal prebuilt UI:

```text
python -m venv .probe/licence-deps/build-venv
<private-python> -m pip install "pyinstaller>=6.0" ./server --report <private-pip-report>
<private-python> -m PyInstaller --noconfirm --clean --distpath <private-dist> --workpath <private-work> packaging/astrodeck.spec
cargo metadata --locked --offline --format-version 1 --manifest-path native/Cargo.toml
maturin build --release --locked --offline --manifest-path native/crates/astrodeck-native/Cargo.toml --out <private-wheels> --interpreter <private-python>
```

`packaging/build_binary.py` installs the **base** server package. Its code does not install `[comhost]`, despite the comment in `server/pyproject.toml`; this audit likewise does not include comhost, cloudmap or asiair extras. It does not build/install `astrodeck_native`, which is absent from the fresh executable. Native-wheel evidence is a separate artifact.

## Findings requiring disposition

### A1. Player One binaries really ship in the executable

**Release-blocking owner decision.** The current CArchive contains `astrodeck/vendor/playerone/PlayerOneCamera.dll`, four Linux `.so.3.10.0` files and the macOS `.dylib`, plus the vendor licence. This agrees with the spec's wholesale copy of `astrodeck/vendor`, irrespective of platform. The historical archive has the same six-file surface. The parent's tarball excludes them; that does not protect PyInstaller or a server wheel/container containing package data.

The vendor text grants development use and requires its notice in copies, but does not explicitly grant redistribution. This is a reading of the supplied terms, not a conclusion that redistribution is definitely prohibited. Obtain a rights-owner clarification or exclude these binaries from every release format. A notice entry by itself cannot supply missing permission.

### A2. WCSLIB LGPL is an actual compiled runtime component

**Owner-required compliance review.** Current `astropy/wcs/_wcs.pyd` is 1,540,096 bytes, SHA-256 `d4fd275aaf79e6d4e96770fc8048e177a1c3dba7bebfe5884414a245e526d074`. Its PE imports include Python and Windows runtime DLLs but no separate WCSLIB DLL. The executable also carries Astropy's `WCSLIB_LICENSE.rst` (LGPL v3). Astropy's [build documentation](https://docs.astropy.org/en/latest/development/development_details.html) identifies bundled WCSLIB as the default; [upstream WCSLIB source](https://github.com/astropy/astropy/blob/main/cextern/wcslib/C/wcs.c) grants LGPL version 3 or later. Combined evidence supports compiled-in WCSLIB, rather than a licence file that merely mentions GPL.

Register the subcomponent separately from Astropy's BSD licence. Establish the exact matching source/version and compliant source/relink route for the delivered binary, including the relevant GPL/LGPL texts and notices. This report does not certify those obligations as discharged. Claude's later ruling permits a narrow `LGPL-3.0-or-later` owner-required policy entry; it does not imply release approval.

### A3. NumPy's runtime includes GPL with a specific exception

**Owner-required, narrow exception.** Current NumPy 2.5.3 supplies `numpy.libs/libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll`. Its actual wheel notice names OpenBLAS, LAPACK and GCC runtime; the GCC section identifies `GPL-3.0-or-later WITH GCC-exception-3.1` and states that GCC runtime code is statically linked into the OpenBLAS DLL. The archive includes that notice. The historical artifact corroborates it.

The [GCC runtime licence explanation](https://gcc.gnu.org/onlinedocs/libstdc++/manual/license.html) distinguishes the exception from ordinary GPL linking obligations. Preserve the full notices and review the exact exception conditions. Do not allow arbitrary GPL components merely because this specific runtime is acceptable. Claude explicitly authorized only the narrowly surfaced owner-required expression.

NumPy's top-level `License-Expression` omits this binary-wheel subcomponent. A metadata-only GPL search is therefore insufficient. Pillow's notice also includes alternative GPL text for FreeType and references licences of xz source portions; this alone does not prove GPL-only code is used in the Pillow runtime. Preserve its complete notice and select/record applicable alternatives rather than treating any occurrence of "GPL" as an automatic licence verdict.

### A4. Native wheel omits applicable notices and source information

**Release-blocking packaging gap for standalone wheel distribution.** The new wheel contains only `__init__.py`, `astrodeck_native.pyd`, METADATA, WHEEL, RECORD and a CycloneDX SBOM. METADATA says `License: MPL-2.0`; there is no licence-text file, third-party notice bundle, source-location notice or homepage. The linked native graph includes Apache/MIT/BSD/Zlib-licensed dependencies. An SPDX name/SBOM does not reproduce their required notices.

Ship the applicable full licence/notice texts and a durable corresponding-source route with the wheel. The [MPL text, sections 3.1 and 3.2](https://www.mozilla.org/en-US/MPL/2.0/) covers source availability and informing executable recipients how to obtain it. The source tree's MPL headers and repository notices are useful but are absent from this six-file distribution. Root-owned remediation must be reviewed against a newly built wheel before treating the gap as closed.

### A5. Runtime inventory must include compiler and interpreter contributions

**Notice and inventory gap.** Actual Vite output includes `vite/modulepreload-polyfill.js`, `vite/preload-helper.js` and CommonJS helpers; emitted CSS contains Tailwind preflight. The old credits scope says Vite/Tailwind never put code into artifacts, which is false. Vite's `LICENSE.md` includes the bundled `@rollup/plugin-commonjs` MIT notice. React, React DOM, scheduler and Zustand occur in actual JS chunk inputs; js-tokens and loose-envify do not appear in those retained inputs. All 58 font assets are exact Fontsource byte matches under OFL-1.1. The checked UI uses repository inline glyphs, not an installed external icon library; this is source inspection, not a claim of exhaustive originality research.

The frozen Python application also contains 127 setuptools modules and a subset of its vendored packages. Exact PYZ source-path matches identify backports.tarfile 1.2.0, jaraco.context 6.1.0, jaraco.functools 4.4.0, jaraco.text 4.0.0, more-itertools 10.8.0, packaging 26.0, tomli 2.4.0 and wheel 0.46.3. Installed but unretained vendors are not automatically runtime credits. See the dedicated vendor evidence for exact text paths.

The executable ships CPython 3.12.10 (`python312.dll`) and its extension/native libraries. CPython's own `LICENSE.txt` is available from the read-only base interpreter, but does not cover OpenSSL. Actual binary strings identify OpenSSL 3.0.16 in `libcrypto-3.dll` and OpenSSL 4.0.3 in cryptography 50.0.2's `_rust.pyd`. Official [3.0.16](https://github.com/openssl/openssl/blob/openssl-3.0.16/LICENSE.txt) and [4.0.3](https://github.com/openssl/openssl/blob/openssl-4.0.3/LICENSE.txt) licence files were retrieved verbatim and hashed; neither tag's root listing provides a NOTICE file. Add the exact shipped versions and texts, not just the Python wrapper's licence. The [CPython licence documentation](https://docs.python.org/3.12/license.html) also identifies incorporated software as a separate licence surface.

Microsoft `VCRUNTIME140*.dll`, UCRT API-set DLLs and NumPy's `msvcp140` DLL are actual bundled files. Their redistribution basis is a separate platform-runtime review, not Apache/MIT inferred from the calling Python package. PE imports and hashes are preserved; this audit does not grant or certify those rights. Likewise, the complete static Rust/C dependency contents of every third-party Python extension were not independently rebuilt. Unknown native internals remain an explicit limit.

PyInstaller is another qualified GPL case: the application contains its bootloader and runtime hooks. Its [official licence explanation](https://pyinstaller.org/en/stable/license.html) expressly permits generated application bundles and says a PyInstaller notice is not required for them, while dependency licences still apply. Do not incorrectly demand a generic GPL source release for the AstroDeck application merely because PyInstaller was used.

### A6. Existing notice files are not the only valid notice channel

**Verified positive evidence, with version drift.** Astropy-IERS-data, Pillow, certifi, pyerfa and pyserial do not all have standalone licence files in the archive. The frozen UI nevertheless contains 22 complete licence texts checked for those packages and NumPy, as decoded data string literals in the actual archived JS. The check parsed JavaScript syntax and nested JSON; it never executed the app. The current IERS-data licence is identical to the complete embedded BSD text, despite the credits displaying an older IERS-data version. Missing standalone files alone are not a demonstrated notice failure.

The current archive contains IERS `eopc04.1962-now`, `finals2000A.all`, `Leap_Second.dat` and their readmes. Their sizes/hashes are in the file inventory. The service/data auditor owns upstream IERS data provenance separately from the Python package's BSD notice. Re-resolve and generate notices from the same environment used to build each artifact: the current frozen credits include older dependency versions because the release path does not regenerate them from its newly resolved dependencies.

## Unverified platform surfaces

The release matrix has four distinct PyInstaller targets. Linux/macOS wheels can contain different bundled Fortran, image, crypto and system libraries. The Windows result cannot clear them. For each target, retain a resolved dependency report, complete artifact file list, native linkage information, upstream licence files and final bundled credits. Reject unclassified payloads instead of assuming all platform wheels inherit their package's top-level SPDX value.

Both Dockerfiles retain the pinned `python:3.12-slim` base in the runtime and copy a venv. Root Docker also explicitly installs `libusb-1.0-0`; upstream [libusb COPYING](https://github.com/libusb/libusb/blob/master/COPYING) is LGPL 2.1. This is a declared image input, not an inspected image finding. Relay pins four direct Python packages, but its transitive resolution and upgraded pip are not fully locked. No final image was available because the Docker daemon was absent. Inspect OCI layers, the distribution package database/copyright files, venv packages, SDKs and bundled data before claiming either image's licence set is complete. Do not classify all OS/runtime files as Apache because that is the image's application label.

Orange Pi provisioning writes AstroDeck scripts/services into an existing Armbian Trixie rootfs and depends on its system services. A source installer and a redistributed appliance image have different obligations. No image manifest, kernel/firmware package set or corresponding-source inventory was available here. A clean source tarball does not clear a separately redistributed SD/eMMC image; owner review needs the exact image and distribution plan.

## Evidence index

- [Cargo inventory](dependency-cargo-inventory.json): 40 resolved packages, 28 runtime-path and 12 build-only; exact local licence-file hashes. The graph includes five workspace crates and 35 external crates. No GPL/LGPL/AGPL/SSPL expression in that resolved crate graph; MPL is explicit, and Rust/platform runtime materials are a separate scope.
- [npm inventory](dependency-npm-inventory.json): 229 entries, licence evidence and runtime/build classification; all 58 font hashes map to source files; compiler-output caveat recorded.
- [Current executable](dependency-current-windows-archive.json): every CArchive file hash, PE import list and PYZ module name; source recipe hashes.
- [Resolved Python environment](dependency-python-windows-inventory.json): package versions/licences and actual file/module matches. Module names are evidence of inclusion, not a complete static C/Rust bill of materials.
- [Setuptools vendors](dependency-setuptools-vendors.json): stronger exact PYZ source-path to RECORD matches and full notice locations.
- [Native wheel](dependency-native-wheel-inventory.json): every file hash, PE imports and embedded CycloneDX contents; absolute workspace references normalized in this evidence copy only.
- [Frozen UI notice proof](dependency-frozen-ui-notice-proof.json): full normalized notice matches from archived JS data, not substring checks.
- [Historical executable](dependency-historical-windows-archive.json): complete corroborating file list and hash, explicitly not current-source certification.
- [OpenSSL sources](dependency-openssl-sources.json): authoritative tag URLs, full-text hashes and root NOTICE absence. Both fetched LICENSE files are 10,175 bytes with SHA-256 `7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a`.

## Disposition

The narrow LGPL/GCC policy changes, bundled-component credits, and artifact gates are parent-owned work in progress. This report records observations before those corrections and does not mark them complete. Player One redistribution rights, native-wheel notices/source availability, unverified platform artifacts and unknown platform-runtime rights require explicit disposition. Re-run the final artifact checks after corrections; a registry edit alone does not change the bytes already examined.
