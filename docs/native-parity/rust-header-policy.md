# Rust source header policy (`native/crates/*`)

This workspace hosts crates under two different licenses, split by crate, not
by file. `native/README.md` has the crate map; this doc has the exact header
text each crate's files must carry.

## `astro-guide` (Apache-2.0)

> **Amendment 2026-07-17**: `astro-guide` was originally planned as MPL-2.0
> (matching the rest of the `native/` workspace) but the user chose
> **Apache-2.0** as AstroDeck's project license (root `LICENSE`, commit
> `d2cb831`). `astro-guide`'s `Cargo.toml` sets `license = "Apache-2.0"` as a
> per-crate override of the workspace `license` field — the workspace default
> stays MPL-2.0 for the pre-existing crates below. BSD-3-Clause permits
> sublicensing under Apache-2.0, so this is compatible with the PHD2-derived
> logic `astro-guide` ports. See `THIRD-PARTY-NOTICES.md` for the full
> BSD-3-Clause text this crate's headers point back to.

Every `astro-guide` source file that ports logic expressed in PHD2's C++
source carries this exact header:

```
// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (<DOSSIER SECTIONS>).
// Derived from PHD2 <phd2 src file:lines> (BSD-3-Clause; see THIRD-PARTY-NOTICES.md).
// No code copied from PHD2.
```

- `<DOSSIER SECTIONS>` — the dossier `§` number(s) (e.g. `§1`, `§4`, `§7`)
  covering the module, taken from `docs/native-parity/algorithms/phd2-guiding.md`.
- `<phd2 src file:lines>` — the specific upstream PHD2 file(s) (and, where
  practical, line ranges) whose logic the module ports, taken from the
  dossier's §16 source map (dossier commit `4a13cf24`, i.e.
  `4a13cf245d7e485e79533697f87b032b304df952`).

Both placeholders are filled per-file when that file is written; they are not
left literal in shipped code.

### Non-derived `astro-guide` files

Files that don't port PHD2-expressed logic (e.g. the sim harness, generic
plumbing/glue) use the same Apache-2.0 SPDX line and provenance paragraph but
drop the `Derived from PHD2 ...` line — i.e. the `astro-focus` header form
(see below), adapted to Apache-2.0:

```
// SPDX-License-Identifier: Apache-2.0
//
// Provenance: <what this file implements, in the reimplemented-from-dossier
// or original-to-astro-guide sense>. No code copied from PHD2.
```

## `astro-star`, `astro-focus`, `astro-tppa`, `astrodeck-native` (MPL-2.0)

Unchanged by the Apache-2.0 amendment above — these remain MPL-2.0, matching
the existing NINA/Hocus-Focus/TPPA-derived workspace default declared in
`native/README.md`. Do not touch these crates' headers as part of the
`astro-guide` work. Their existing header form (see e.g.
`native/crates/astro-focus/src/backlash.rs`) is:

```
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/<dossier file>.md (<relevant §>). No code
// copied from NINA/Hocus Focus/TPPA.
```
