# AstroDeck Rust-native engine (`native/`)

Rust workspace implementing NINA-parity astronomy algorithms (star
detection/HFR/PSF, autofocus, three-point polar alignment) without depending
on NINA at runtime, exposed to the AstroDeck Python backend via a PyO3
extension module.

## Crate map

| Crate               | Purpose                                                                                   |
| -------------------- | ------------------------------------------------------------------------------------------ |
| `crates/astro-star`   | Star detection, HFR measurement, PSF fitting. Optional `parallel` feature (rayon).        |
| `crates/astro-focus`  | Autofocus sweep state machine + focus-curve fitting.                                      |
| `crates/astro-tppa`   | Three-point polar alignment math (axis fit, polar error decomposition).                   |
| `crates/astrodeck-native` | PyO3 `cdylib` bundling the three crates above into one Python extension module (`astrodeck_native`), built with maturin. |

All three algorithm crates (`astro-star`, `astro-focus`, `astro-tppa`) are
pure — no async, no I/O, no globals, deterministic (no `rand` in library
code). `astrodeck-native` is the only crate that touches Python/FFI.

## Provenance and licensing

The algorithms in `astro-star`, `astro-focus`, and `astro-tppa` are
**reimplemented from scratch** against the audited algorithm dossiers in
[`docs/native-parity/algorithms/`](../docs/native-parity/algorithms/) — exact
formulas, constants, and solver semantics extracted and verified from the
original NINA, Hocus Focus, and TPPA sources, not copied C#/C++ code. These
three crates (plus `astrodeck-native`) are licensed MPL-2.0, the workspace
default (see `license.workspace` in each crate's `Cargo.toml`). See the
repo-root [`THIRD-PARTY-NOTICES.md`](../THIRD-PARTY-NOTICES.md) for the full
NINA/Hocus-Focus MPL-2.0 attribution.

The `astro-guide` crate reimplements PHD2's guiding stack (BSD-3-Clause); see
the repo-root `THIRD-PARTY-NOTICES.md` for attributions. Because AstroDeck's
own project license is Apache-2.0 (root `LICENSE`), `astro-guide` carries
**Apache-2.0** file headers (a per-crate `license` override of the workspace
MPL-2.0 default, set in its `Cargo.toml`) with BSD-3 derivation notes naming
the specific PHD2 source each file ports — BSD-3-Clause permits Apache
sublicensing. See
[`docs/native-parity/rust-header-policy.md`](../docs/native-parity/rust-header-policy.md)
for the exact header text.

## Build instructions

Run tests for the whole workspace:

```sh
cd native
cargo test
```

Build and install the Python extension module into AstroDeck's backend venv
(from the repo root):

```sh
maturin develop --release -m native/crates/astrodeck-native/Cargo.toml
```

using the interpreter/venv at `server/.venv`.
