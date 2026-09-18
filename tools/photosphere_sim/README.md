# Photosphere test simulator

An independent test rig for the photosphere scanner: it renders a synthetic
world, feeds the real scanner only what a phone could have supplied, and
scores the scanner's output against truth it never saw. Nothing here imports
production code, and production code never imports anything here. The shared
conventions live in [CONTRACT.md](CONTRACT.md); read it before changing a
frame, a unit or a file layout.

Design: `docs/ui-rebuild/16-photosphere-calibration-simulator.md`.

## What exists so far

`sim/geometry.py` only: the coordinate contract in code (sky vectors, camera
attitude, the pinhole camera, and the W3C device-orientation conversions),
plus the test that keeps the simulator independent. The scene, the truth
ray-caster, the renderer, the replay driver and the scorer arrive in later
tasks, so three of the four commands below do not run yet.

## Commands

From the repository root:

    python -m unittest discover -s tools/photosphere_sim/tests -t tools/photosphere_sim -v

The rest, from CONTRACT.md's Commands section (not implemented yet):

    # inside tools/photosphere_sim
    python -m sim make-case <case_id>
    python -m sim score <case_id>          # and: python -m sim corrupt <case_id> <corruption>

    # inside ui
    node --import tsx src/next/hubs/sky/sheets/__sim__/replay.ts <abs case dir>

## Interpreter and toolchain

- The system `python` (3.12) runs the tests: numpy, Pillow and Playwright are
  already present. There is no pytest, so the tests are stdlib `unittest` and
  the discover command above is the whole runner. Do not install into it.
- Node 24 with `three` 0.186.0 as a devDependency here: `npm install` inside
  this directory, which writes `node_modules/` and `package-lock.json` (both
  git-ignored, as is `cache/`).

## One convention worth stating twice

Two camera frames appear in `sim/geometry.py` and they differ by a sign.
`project`, `ray`, `to_camera` and `to_world` use the pinhole frame, where `z`
runs along `forward` and anything in front of the lens has `z > 0`.
`Basis.matrix_cam_to_world` is the renderer's matrix, whose third column is
`-forward` because an OpenGL camera looks down its own `-z`. Both are proper
rotations, so a determinant check will not tell them apart; passing one where
the other belongs flips the image front to back.
