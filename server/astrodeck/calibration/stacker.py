"""PRO-1 master stacking — pure numpy reduction (no I/O). Operates on in-memory
frame lists; the bounded-memory streaming wrapper lives in library.py."""
from __future__ import annotations

import numpy as np

DEFAULT_SIGMA = 3.0


def _as_stack(frames: list[np.ndarray]) -> np.ndarray:
    if not frames:
        raise ValueError("stack needs at least one frame")
    shapes = {f.shape for f in frames}
    if len(shapes) != 1:
        raise ValueError(f"ragged stack: shapes {sorted(shapes)}")
    return np.stack([np.asarray(f, dtype=np.float32) for f in frames], axis=0)


def median_stack(frames: list[np.ndarray]) -> np.ndarray:
    """Per-pixel median over a uniform-shape stack. Returns float32."""
    return np.median(_as_stack(frames), axis=0).astype(np.float32)


def sigma_clip_mean(frames: list[np.ndarray], sigma: float = DEFAULT_SIGMA) -> np.ndarray:
    """Per-pixel sigma-clipped mean: one rejection pass at ``|x - median| >
    sigma * (1.4826 * MAD)``; mean of survivors, median where all rejected.
    Returns float32. ``len < 3`` degrades to ``median_stack`` (clipping is
    meaningless below three samples)."""
    stack = _as_stack(frames)
    if stack.shape[0] < 3:
        return np.median(stack, axis=0).astype(np.float32)
    med = np.median(stack, axis=0)                          # (H,W) robust center
    mad = np.median(np.abs(stack - med), axis=0) * 1.4826   # (H,W) robust sigma
    lo = med - sigma * mad
    hi = med + sigma * mad
    keep = (stack >= lo) & (stack <= hi)                    # (N,H,W)
    ssum = np.where(keep, stack, 0.0).sum(axis=0)
    cnt = keep.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = ssum / cnt
    # A pixel whose values are all rejected (cnt==0; only reachable at a zero-MAD
    # edge) falls back to the median so no NaN ever escapes into a master.
    out = np.where(cnt > 0, mean, med)
    return out.astype(np.float32)


def stack_frames(frames: list[np.ndarray], method: str = "sigma_clip",
                 sigma: float = DEFAULT_SIGMA) -> np.ndarray:
    """Dispatch median|sigma_clip. Raises ValueError on empty or ragged shapes."""
    if method == "median":
        return median_stack(frames)
    if method == "sigma_clip":
        return sigma_clip_mean(frames, sigma)
    raise ValueError(f"unknown stack method: {method!r}")
