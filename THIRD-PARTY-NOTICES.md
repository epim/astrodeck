# Third-Party Notices

AstroDeck includes components derived from or bundled with third-party
open-source software. The notices below are mandatory attributions preserved
per each component's license terms.

## AstroDeck native crates (`native/crates/*`)

The `astro-star`, `astro-focus`, `astro-tppa`, and `astrodeck-native` crates
in the `native/` Rust workspace are licensed under the Mozilla Public
License, v. 2.0 (MPL-2.0). A copy of the MPL-2.0 is available at
https://mozilla.org/MPL/2.0/. Each of those crates' source files carries the
MPL-2.0 header.

The `astro-guide` crate is licensed under Apache-2.0 (AstroDeck's project
license; see the repo-root `LICENSE`), with BSD-3 derivation notices in its
file headers for the PHD2-ported algorithms per the PHD2 section below.

## PHD2 — guiding algorithms (BSD-3-Clause)

The `astro-guide` crate is a clean-room Rust reimplementation of PHD2's guiding
stack, produced from the source-mapped algorithm dossier
`docs/native-parity/algorithms/phd2-guiding.md` (extracted from PHD2 at commit
4a13cf245d7e485e79533697f87b032b304df952). Because the port derives from PHD2's
expressed logic, PHD2's BSD-3-Clause notice is preserved here (licensing report
§4, Path (a)):

```
This software includes code derived from PHD2
(https://github.com/OpenPHDGuiding/phd2), used under the following license:

Copyright (c) 2013-2019, Open PHD Guiding development team
Copyright (c) 2014-2015, Max Planck Society
Copyright (c) 2012, Bret McKee            [hysteresis, resist-switch, multi-star guider, mount/calibration]
Copyright (c) 2006-2010, Craig Stark      [star centroid, multi-star guider base]
Copyright (c) 2018, Ken Self              [Z-filter guide algorithm]
Copyright (c) 2020, Bruce Waddington      [multi-star guider extensions]
Copyright (c) 2023, Bruce Waddington      [calibration assistant / backlash tool]
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software without
   specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
OF THE POSSIBILITY OF SUCH DAMAGE.
```

### Predictive PEC / Gaussian-process guider (BSD-3-Clause, additionally)

The GP/PPEC algorithm (`astro-guide/src/algorithms/gaussian_process.rs`) is
ported from PHD2's `contributions/MPI_IS_gaussian_process` (Max Planck Institute
for Intelligent Systems, Tübingen). Its BSD-3-Clause notice is preserved:

```
Copyright 2014-2017, Max Planck Society.
Authors: Edgar D. Klenske, Stephan Wenninger, Raffi Enficiaud
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software without
   specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
OF THE POSSIBILITY OF SUCH DAMAGE.
```

Algorithm reference (academic citation, per licensing report §4 Path (b)):
Edgar D. Klenske, Melanie N. Zeilinger, Bernhard Schölkopf, Philipp Hennig,
"Gaussian Process Based Predictive Control for Periodic Error Correction,"
IEEE Transactions on Control Systems Technology, vol. 24, no. 1, pp. 110-121, 2016.

The GP port uses `nalgebra` for linear algebra; PHD2's C++ used Eigen (MPL-2.0),
which is a separable build dependency of PHD2 and does NOT travel to this port
(licensing report §3).

## NINA / Hocus Focus — autofocus + star detection (Mozilla Public License 2.0)

`astro-star` and `astro-focus` are clean-room reimplementations from the audited
dossiers `docs/native-parity/algorithms/nina-autofocus.md` and
`hocusfocus-autofocus-tilt.md`. No code was copied from NINA or Hocus Focus; the
upstream projects are MPL-2.0 and this reimplementation is likewise MPL-2.0.

Hocus Focus, by George Hilios (https://github.com/ghilios/hocus-focus), remains
the template AstroDeck's autofocus follows, and its published design decisions
keep shaping ours. Read from its documentation and release notes rather than its
source: the hyperbolic V-curve; excluding starless positions at the extremes of
a wide sweep from the fit instead of letting them poison it, and saying so when
that happens; statistical outlier rejection within a frame's star population;
and its finding that loose detection settings admit noise and donut fragments
which skew autofocus on wide or defocused sweeps. That last one named a failure
we had measured ourselves on narrowband on 2026-08-17 and had diagnosed only
half of (#219).

## ASTAP — plate solver (MPL-2.0), bundled binary

AstroDeck's release tooling can bundle the ASTAP command-line solver (`astap_cli`)
and a Gaia-derived star database. Check each artifact's manifest: the local
October audit tarball explicitly omits ASTAP, and that omission is not evidence
about a complete published release. ASTAP is by Han Kleijn — https://www.hnsky.org/astap.htm,
source at https://github.com/han-k59/astap.

ASTAP is licensed under the **Mozilla Public License, Version 2.0**. A copy is
at https://mozilla.org/MPL/2.0/. MPL-2.0 is file-level copyleft covering ASTAP's
own source; AstroDeck invokes `astap_cli` as a **separate process over its
command-line interface** and does not link against it, so no MPL obligation
extends merely through that command-line invocation. A binary distribution must
carry the applicable licence and tell recipients how to obtain corresponding
source; preserve the exact distributed version and its notices. The generated
credits contain the full MPL text. This source link alone does not establish
compliance for an uninspected binary or database.

### Star databases — ESA/Gaia/DPAC

The bundled star database is derived from the ESA Gaia mission. Per the Gaia
terms of use:

> The Gaia data are open and free to use, provided credit is given to
> 'ESA/Gaia/DPAC'.

We give that credit here. This applies to every database AstroDeck may bundle
(W08, D05, G05, D20, D50, D80).

### Deliberately NOT bundled

ASTAP's own installer additionally ships deep-sky and variable-star catalogue
CSVs. AstroDeck does **not** fetch or redistribute those, and `scripts/
fetch_astap.py` takes only the solver and the star database. They carry
non-commercial terms which AstroDeck has no reason to inherit:

* Wolfgang Steinicke's revised NGC/IC — *"Any non-commercial use of my data is
  free! If a commercial use is planned, please contact me!"*
* HyperLEDA — *"available in open-source for non-commercial purposes."*

AstroDeck has its own object catalogue, so nothing is lost by excluding them.


## Components verified inside the October 2026 Windows artifact

The local Windows executable inspected on 2026-10-01 contains additional
components inside dependency wheels, interpreter libraries and compiled output.
Their licences are not replaced by the top-level Python package's licence.
The exact versions, artifact hashes, upstream text sources and scope are in
[the bundled-component record](tools/licence/bundled-components.json).
Full notices are linked below and reproduced in the generated credits.

This inventory is not distribution clearance. WCSLIB and the GCC runtime retain
explicit owner decisions under #638. Player One binaries are present in the
executable even though the source tarball excludes them (#632). No written
redistribution confirmation was found. The nebula background's provenance
remains unresolved (#637). These findings are recorded in
[the artifact audit](tools/licence/dependency-artifact-audit.md).

| Component in the inspected artifact | Licence | Full text and notices |
| --- | --- | --- |
| WCSLIB (inside Astropy) 8.6 in Astropy 8.0.1 | LGPL-3.0-or-later | [bundled-wcslib-8.6-notice.txt](tools/licence_texts/bundled-wcslib-8.6-notice.txt); [bundled-wcslib-lgpl-3.0.txt](tools/licence_texts/bundled-wcslib-lgpl-3.0.txt); [bundled-gpl-3.0.txt](tools/licence_texts/bundled-gpl-3.0.txt) |
| OpenBLAS (inside NumPy) 0.3.34.106.0 in NumPy 2.5.3 | BSD-3-Clause | [bundled-numpy-2.5.3-notices.txt](tools/licence_texts/bundled-numpy-2.5.3-notices.txt) |
| LAPACK (inside NumPy OpenBLAS) Bundled with OpenBLAS 0.3.34.106.0 / NumPy 2.5.3; separate LAPACK revision not established | BSD-3-Clause-Open-MPI | [bundled-numpy-2.5.3-notices.txt](tools/licence_texts/bundled-numpy-2.5.3-notices.txt) |
| GCC runtime library (inside NumPy OpenBLAS) Bundled with NumPy 2.5.3; separate GCC runtime revision not established | GPL-3.0-or-later WITH GCC-exception-3.1 | [bundled-numpy-2.5.3-notices.txt](tools/licence_texts/bundled-numpy-2.5.3-notices.txt) |
| CPython bundled interpreter 3.12.10 | PSF-2.0 | [bundled-cpython-3.12.10.txt](tools/licence_texts/bundled-cpython-3.12.10.txt) |
| OpenSSL (CPython runtime libraries) 3.0.16 | Apache-2.0 | [bundled-openssl-3.0.16.txt](tools/licence_texts/bundled-openssl-3.0.16.txt) |
| OpenSSL (inside cryptography) 4.0.3 in cryptography 50.0.2 | Apache-2.0 | [bundled-openssl-4.0.3.txt](tools/licence_texts/bundled-openssl-4.0.3.txt) |
| setuptools (retained in frozen runtime) 84.0.0 | MIT | [bundled-setuptools-84.0.0.txt](tools/licence_texts/bundled-setuptools-84.0.0.txt) |
| setuptools vendor: backports.tarfile 1.2.0 | MIT | [bundled-setuptools-backports-tarfile-1.2.0-license.txt](tools/licence_texts/bundled-setuptools-backports-tarfile-1.2.0-license.txt); [bundled-setuptools-backports-tarfile-1.2.0-lars-notice.txt](tools/licence_texts/bundled-setuptools-backports-tarfile-1.2.0-lars-notice.txt) |
| setuptools vendor: jaraco.context 6.1.0 | MIT | [bundled-setuptools-jaraco-context-6.1.0-license.txt](tools/licence_texts/bundled-setuptools-jaraco-context-6.1.0-license.txt) |
| setuptools vendor: jaraco.functools 4.4.0 | MIT | [bundled-setuptools-jaraco-functools-4.4.0-license.txt](tools/licence_texts/bundled-setuptools-jaraco-functools-4.4.0-license.txt) |
| setuptools vendor: jaraco.text 4.0.0 | MIT | [bundled-setuptools-jaraco-text-4.0.0-license.txt](tools/licence_texts/bundled-setuptools-jaraco-text-4.0.0-license.txt) |
| setuptools vendor: more-itertools 10.8.0 | MIT | [bundled-setuptools-more-itertools-10.8.0-license.txt](tools/licence_texts/bundled-setuptools-more-itertools-10.8.0-license.txt) |
| setuptools vendor: packaging 26.0 | Apache-2.0 OR BSD-2-Clause | [bundled-setuptools-packaging-26.0-license.txt](tools/licence_texts/bundled-setuptools-packaging-26.0-license.txt); [bundled-setuptools-packaging-26.0-license-apache.txt](tools/licence_texts/bundled-setuptools-packaging-26.0-license-apache.txt); [bundled-setuptools-packaging-26.0-license-bsd.txt](tools/licence_texts/bundled-setuptools-packaging-26.0-license-bsd.txt) |
| setuptools vendor: tomli 2.4.0 | MIT | [bundled-setuptools-tomli-2.4.0-license.txt](tools/licence_texts/bundled-setuptools-tomli-2.4.0-license.txt) |
| setuptools vendor: wheel 0.46.3 | MIT | [bundled-setuptools-wheel-0.46.3-license-txt.txt](tools/licence_texts/bundled-setuptools-wheel-0.46.3-license-txt.txt) |

Vite's module/preload helpers and bundled CommonJS helper occur in the emitted
JavaScript. Tailwind's preflight rules occur in the emitted CSS. Their MIT texts,
including Vite's bundled plugin notices, are reproduced in generated credits.
Classifying a tool as a development dependency does not remove a notice duty
for its code in the delivered output.

## Data and services, October 2026 corrections

OpenNGC extracts retain CC BY-SA 4.0 and the stated extraction changes. IAU-CSN
2022-04-04 says Creative Commons Attribution without a version; a 4.0 grant has
not been established and that uncertainty remains visible. Constellation
assignments are generated by AstroDeck using Astropy's Roman 1987 boundary
table. The inspected tarball contains no survey pack, horizon dataset, NOAA
granules or downloaded TLE cache. Dependency data in executables have a
different scope.

Current CDS DSS2 and 2MASS colour records declare ODbL 1.0 for the HiPS database.
The [full ODbL text](tools/licence_texts/bundled-odbl-1.0.txt), database attribution
and original-image acknowledgements are in generated credits. The database
grant does not replace original-image rights or establish permission to bundle
DSS2. Operator-local downloads remain subject to provider terms.

Open-Meteo data attribution and free-service access conditions are separate.
Astrospheric public-client use still needs clarification beyond an operator's
personal-project acknowledgement. CelesTrak's stop-on-error policy is not
satisfied by the current repeated-retry behavior. NOAA and MPC descriptions
preserve their actual terms rather than infer public-domain dedication.
See the dated [service/data audit](tools/licence/service-data-audit.md) for primary
sources, quoted wording, our reading and the unresolved remedies.
