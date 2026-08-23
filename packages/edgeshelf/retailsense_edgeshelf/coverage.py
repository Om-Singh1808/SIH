"""Classical (no-ML) shelf coverage estimation.

Design rationale
----------------
A kirana shelf is watched by one fixed camera. We do not need to *recognise*
products to know whether the shelf is empty: product looks different from the
shelf backing. Two cheap cues capture this on both synthetic and real frames:

* **colour distance** - the CIE-Lab distance (ΔE) between a pixel and the shelf
  *backing* colour. Packaged goods are rarely the same colour as the board or
  the wall behind them.
* **local texture** - the standard deviation of Lab colour in a 5×5 window
  (root of the summed per-channel variances, so both print contrast and
  colour texture count).
  Products carry print, edges and specular highlights; an empty backing is
  flat. This cue catches products whose *mean* colour happens to match the
  backing.

Each pixel inside the shelf polygon is classified as *product* or *backing*
with a three-band rule (see :func:`productness`). The binary map is collapsed
into a **profile along the shelf's long axis** (columns for a horizontal shelf,
rows for a vertical one), because facings sit side by side along that axis. A
column is *covered* when more than ``covered_col_frac`` of its polygon pixels
are product; ``raw_coverage`` is the share of covered columns.

Calibration. ``raw_coverage`` of a *full* shelf is below 1.0 because of gaps
between facings, shadows and price strips. :meth:`calibrate` records that
value (and the backing colour) in a :class:`ShelfReference`; later estimates are
normalised by it so that ``coverage == 1.0`` means "as full as when the owner
calibrated". Without a reference the raw value is returned unchanged.

Facings. When ``facing_width_px`` is configured, facings are counted as runs
of covered columns: every run at least ``0.6 × facing_width_px`` long counts
as ``round(run / facing_width_px)`` facings (so adjacent facings without a
visible gap still count). Otherwise facings are ``round(coverage × capacity)``.

Everything is plain numpy - no OpenCV - so the estimator runs anywhere the
contracts run and behaves identically in tests and on the edge box.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from retailsense_contracts.config import ShelfPolygon, ShelfReference
from retailsense_contracts.geometry import polygon_bbox, polygon_long_axis, polygon_mask
from retailsense_contracts.interfaces import CoverageResult
from retailsense_contracts.synthetic import SyntheticPalette

METHOD = "classical"

# Minimum plausible "full shelf" raw coverage - protects the normalisation
# from a calibration taken on an (accidentally) empty shelf.
MIN_RAW_FULL = 0.05

# sRGB (D65) -> XYZ matrix, rows X, Y, Z; columns R, G, B.
_RGB2XYZ = np.array(
    [[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]],
    dtype=np.float32,
)
_D65 = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)


# ---------------------------------------------------------------------------
# pixel-level primitives (pure numpy)
# ---------------------------------------------------------------------------


def bgr_to_lab(image: np.ndarray) -> np.ndarray:
    """Convert a BGR uint8 image (HxWx3) to CIE-Lab float32 (L in 0..100)."""
    rgb = image[..., ::-1].astype(np.float32) / 255.0
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = lin @ _RGB2XYZ.T / _D65
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    lab = np.empty_like(f)
    lab[..., 0] = 116.0 * f[..., 1] - 16.0
    lab[..., 1] = 500.0 * (f[..., 0] - f[..., 1])
    lab[..., 2] = 200.0 * (f[..., 1] - f[..., 2])
    return lab


def bgr_tuple_to_lab(bgr: tuple[int, int, int] | list[int]) -> np.ndarray:
    """Lab triple of a single BGR colour."""
    px = np.asarray(bgr, dtype=np.uint8).reshape(1, 1, 3)
    return bgr_to_lab(px)[0, 0]


def local_std(x: np.ndarray, k: int = 5) -> np.ndarray:
    """Standard deviation of ``x`` in a k×k window around each pixel (edge-padded).

    Uses summed-area tables so the cost is O(pixels) regardless of ``k``.
    """
    r = k // 2
    xp = np.pad(x.astype(np.float64), r, mode="edge")
    ones = np.ones(xp.shape, dtype=np.float64)

    def _box(a: np.ndarray) -> np.ndarray:
        s = np.cumsum(np.cumsum(a, axis=0), axis=1)
        s = np.pad(s, ((1, 0), (1, 0)))
        h, w = x.shape
        return s[k : k + h, k : k + w] - s[:h, k : k + w] - s[k : k + h, :w] + s[:h, :w]

    n = _box(ones)
    mean = _box(xp) / n
    mean_sq = _box(xp * xp) / n
    var = np.maximum(mean_sq - mean * mean, 0.0)
    return np.sqrt(var)


def local_lab_std(lab: np.ndarray, k: int = 5) -> np.ndarray:
    """Combined L/a/b local standard deviation: sqrt(var_L + var_a + var_b) in a k×k window."""
    return np.sqrt(sum(local_std(lab[..., c], k) ** 2 for c in range(3)))


def productness(
    lab: np.ndarray,
    backing_lab: np.ndarray,
    *,
    std_tau: float,
    colour_tau: float,
    backing_tol: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-pixel product/backing classification.

    Three bands on the colour distance ΔE to the backing:

    * ``ΔE > colour_tau``  -> product (clearly a different colour);
    * ``ΔE <= backing_tol`` -> backing (indistinguishable from the board, even if
      the local window is textured because a neighbour is product);
    * in between -> product only if the 5×5 Lab std exceeds ``std_tau``.

    The middle band is what lets a textured product of roughly backing colour be
    seen, while the inner band keeps 2-px gaps between facings *uncovered* (their
    neighbours make the window textured but the pixel itself is pure backing).
    Returns ``(product_mask, delta_e, std)``.
    """
    delta_e = np.sqrt(((lab - backing_lab) ** 2).sum(axis=2))
    std = local_lab_std(lab)
    product = (delta_e > colour_tau) | ((delta_e > backing_tol) & (std > std_tau))
    return product, delta_e, std


# ---------------------------------------------------------------------------
# estimator
# ---------------------------------------------------------------------------


@dataclass
class _Crop:
    """Shelf pixels: the bbox crop of the frame plus the polygon mask inside it."""

    image: np.ndarray  # HxWx3 uint8
    mask: np.ndarray  # HxW bool
    axis: str  # "x" or "y": direction along which facings are laid out

    @property
    def empty(self) -> bool:
        return self.image.size == 0 or not self.mask.any()


def crop_shelf(image: np.ndarray, shelf: ShelfPolygon) -> _Crop:
    """Crop the polygon bbox (clipped to the frame) and rasterise the polygon mask."""
    h, w = image.shape[:2]
    x0, y0, x1, y1 = polygon_bbox(shelf.polygon)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return _Crop(np.zeros((0, 0, 3), dtype=np.uint8), np.zeros((0, 0), dtype=bool), "x")
    full_mask = polygon_mask(shelf.polygon, h, w)
    return _Crop(image[y0:y1, x0:x1], full_mask[y0:y1, x0:x1], polygon_long_axis(shelf.polygon))


def count_facings_from_runs(covered: np.ndarray, facing_width_px: float, min_run_frac: float = 0.6) -> int:
    """Count facings as runs of covered columns.

    A run shorter than ``min_run_frac × facing_width_px`` is noise (a price
    label, a shadow). A longer run contributes ``max(1, round(run / width))``
    so that two facings touching without a visible gap are still two.
    """
    if facing_width_px <= 0 or covered.size == 0:
        return 0
    min_run = min_run_frac * facing_width_px
    facings = 0
    run = 0
    for c in np.concatenate([covered.astype(np.int8), [0]]):
        if c:
            run += 1
        elif run:
            if run >= min_run:
                facings += max(1, int(round(run / facing_width_px)))
            run = 0
    return facings


class ClassicalCoverageEstimator:
    """Lab colour + local texture coverage estimator (see module docstring).

    Parameters
    ----------
    std_tau : Lab std (5×5 window) above which a pixel is textured.
    colour_tau : ΔE above which a pixel is clearly not backing.
    covered_col_frac : share of product pixels for a column to count as covered.
    backing_bgr : force a backing colour (overrides reference and auto-detect).
    backing_tol : ΔE below which a pixel is treated as pure backing.
    """

    def __init__(
        self,
        std_tau: float = 8.0,
        colour_tau: float = 28.0,
        covered_col_frac: float = 0.35,
        backing_bgr: list[int] | tuple[int, int, int] | None = None,
        backing_tol: float = 6.0,
    ) -> None:
        self.std_tau = float(std_tau)
        self.colour_tau = float(colour_tau)
        self.covered_col_frac = float(covered_col_frac)
        self.backing_bgr = tuple(int(v) for v in backing_bgr) if backing_bgr is not None else None
        self.backing_tol = float(backing_tol)

    # -- backing colour ----------------------------------------------------

    def _auto_backing(self, crop: _Crop, lab: np.ndarray) -> tuple[tuple[int, int, int], bool]:
        """Guess the backing colour from the shelf pixels themselves.

        Returns ``(bgr, is_synthetic_palette)``. The synthetic palette backing
        wins when a meaningful share (>= 5 %) of the *flat* pixels match it -
        that is the demo store. Otherwise the median of the flat (low-variance)
        pixels is used, which on a real photo is the visible board/wall. With
        no flat pixels at all (a packed, textured shelf) we fall back to the
        palette colour; texture alone will then classify the pixels as product.
        """
        palette = tuple(int(v) for v in SyntheticPalette.SHELF_BACKING)
        std = local_lab_std(lab)
        flat = crop.mask & (std <= self.std_tau)
        if not flat.any():
            return palette, True
        flat_px = crop.image[flat]
        flat_lab = lab[flat]
        d_pal = np.sqrt(((flat_lab - bgr_tuple_to_lab(palette)) ** 2).sum(axis=1))
        if (d_pal <= self.colour_tau).mean() >= 0.05:
            return palette, True
        med = np.median(flat_px, axis=0)
        return tuple(int(round(float(v))) for v in med), False

    def _resolve_backing(self, crop: _Crop, lab: np.ndarray, ref: ShelfReference | None) -> tuple[tuple[int, int, int], float]:
        """Backing colour precedence: constructor > reference > auto-detect.

        The second item is a debug code: 0 explicit, 1 reference, 2 auto-palette, 3 auto-median.
        """
        if self.backing_bgr is not None:
            return self.backing_bgr, 0.0
        if ref is not None and ref.backing_bgr:
            return tuple(int(v) for v in ref.backing_bgr[:3]), 1.0
        bgr, synthetic = self._auto_backing(crop, lab)
        return bgr, 2.0 if synthetic else 3.0

    # -- core ----------------------------------------------------------------

    def profile(self, image: np.ndarray, shelf: ShelfPolygon, ref: ShelfReference | None = None) -> tuple[np.ndarray, dict[str, float]]:
        """Per-column (long-axis) product fraction inside the polygon, plus debug info."""
        crop = crop_shelf(image, shelf)
        if crop.empty:
            return np.zeros(0, dtype=np.float64), {"backing_mode": -1.0}
        lab = bgr_to_lab(crop.image)
        backing, mode = self._resolve_backing(crop, lab, ref)
        product, _, _ = productness(
            lab, bgr_tuple_to_lab(backing), std_tau=self.std_tau, colour_tau=self.colour_tau, backing_tol=self.backing_tol
        )
        product &= crop.mask
        reduce_axis = 0 if crop.axis == "x" else 1  # collapse rows for an x-shelf -> one value per column
        counts = crop.mask.sum(axis=reduce_axis)
        hits = product.sum(axis=reduce_axis)
        valid = counts > 0
        prof = np.zeros(counts.shape, dtype=np.float64)
        prof[valid] = hits[valid] / counts[valid]
        debug = {
            "backing_mode": mode,
            "backing_b": float(backing[0]),
            "backing_g": float(backing[1]),
            "backing_r": float(backing[2]),
            "n_cols": float(valid.sum()),
        }
        return prof[valid], debug

    def _raw(self, image: np.ndarray, shelf: ShelfPolygon, ref: ShelfReference | None) -> tuple[float, np.ndarray, np.ndarray, dict[str, float]]:
        prof, debug = self.profile(image, shelf, ref)
        covered = prof > self.covered_col_frac
        raw = float(covered.mean()) if covered.size else 0.0
        debug["covered_cols"] = float(covered.sum())
        return raw, covered, prof, debug

    # -- protocol --------------------------------------------------------------

    def calibrate(self, image: np.ndarray, shelf: ShelfPolygon) -> ShelfReference:
        """Snapshot a *full* shelf: its raw coverage, backing colour and column profile."""
        raw, _, prof, debug = self._raw(image, shelf, None)
        backing = [int(debug.get("backing_b", 0)), int(debug.get("backing_g", 0)), int(debug.get("backing_r", 0))]
        return ShelfReference(
            shelf_id=shelf.shelf_id,
            calibrated_ts=time.time(),
            raw_coverage_full=max(raw, MIN_RAW_FULL),
            backing_bgr=backing,
            profile=[round(float(v), 3) for v in prof],
            method=METHOD,
        )

    def estimate(self, image: np.ndarray, shelf: ShelfPolygon, ref: ShelfReference | None) -> CoverageResult:
        """Coverage in ``[0, 1]`` (normalised by the reference when given) and a facings count."""
        raw, covered, _, debug = self._raw(image, shelf, ref)
        full = max(ref.raw_coverage_full, MIN_RAW_FULL) if ref is not None else 1.0
        coverage = float(min(1.0, max(0.0, raw / full)))
        if shelf.facing_width_px:
            facings = count_facings_from_runs(covered, float(shelf.facing_width_px))
            facings = min(facings, shelf.capacity_facings)
            debug["facings_method"] = 1.0
        else:
            facings = int(round(coverage * shelf.capacity_facings))
            debug["facings_method"] = 0.0
        debug["raw_full"] = full
        return CoverageResult(
            coverage=round(coverage, 3), facings=facings, raw_coverage=round(raw, 3), method=METHOD, debug=debug
        )


__all__ = [
    "METHOD",
    "MIN_RAW_FULL",
    "ClassicalCoverageEstimator",
    "bgr_to_lab",
    "bgr_tuple_to_lab",
    "count_facings_from_runs",
    "crop_shelf",
    "local_lab_std",
    "local_std",
    "productness",
]
