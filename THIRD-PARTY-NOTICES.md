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

## ASTAP — plate solver (MPL-2.0), bundled binary

AstroDeck's releases bundle the ASTAP command-line solver (`astap_cli`) and one
Gaia-derived star database, so that plate solving works without the user
installing anything. ASTAP is by Han Kleijn — https://www.hnsky.org/astap.htm,
source at https://github.com/han-k59/astap.

ASTAP is licensed under the **Mozilla Public License, Version 2.0**. A copy is
at https://mozilla.org/MPL/2.0/. MPL-2.0 is file-level copyleft covering ASTAP's
own source; AstroDeck invokes `astap_cli` as a **separate process over its
command-line interface** and does not link against it, so no MPL obligation
extends to AstroDeck's code. Our obligation is to say so and to point at the
upstream source, which is what this section does.

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
