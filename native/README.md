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
original NINA, Hocus Focus, and TPPA sources, not copied C#/C++ code. NINA and
Hocus Focus are MPL-2.0 licensed; TPPA is likewise MPL-2.0. This workspace is
licensed MPL-2.0 to match (see `license.workspace` in each crate's
`Cargo.toml`).

PHD2 (BSD-3) is reserved for a future guiding crate and is not part of this
workspace yet.

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

using the interpreter/venv at `C:/Users/bear/astro/server/.venv`.
