"""A robust example controller that keeps the car near the road centre.

Uses both cameras but guards against contradictory readings (e.g. one camera
seeing the lane while the other picks up a guardrail reflection).

Pure NumPy — no OpenCV dependency.
"""

from typing import Optional, Tuple
import numpy as np


IMG_H, IMG_W = 480, 640

# ── Road ROI ───────────────────────────────────────────────────────────
ROI_TOP = int(IMG_H * 0.55)
ROI_BOT = int(IMG_H * 0.88)

# ── White detection ────────────────────────────────────────────────────
BRIGHT_MIN = 165
SAT_MAX    = 35
MIN_PX     = 80

# ── Steering memory & rate limiting ────────────────────────────────────
_steer_memory: float = 0.0
MAX_STEER_CHANGE = 0.18   # max change per frame (prevents sudden flips)


def _lane_offset(bgr: np.ndarray) -> Optional[float]:
    """Normalised horizontal offset of white pixels (−1=left … +1=right).

    Returns None when lane is invisible in this camera.
    Also returns None when white pixels are spread too wide (likely noise).
    """
    roi = bgr[ROI_TOP:ROI_BOT, :, :]
    v = roi.max(axis=2)
    spread = v - roi.min(axis=2)
    white = (v >= BRIGHT_MIN) & (spread <= SAT_MAX)

    col = white.sum(axis=0).astype(np.float64)
    total = col.sum()
    if total < MIN_PX:
        return None

    xs = np.arange(IMG_W, dtype=np.float64)
    cx = (xs * col).sum() / total

    # Reject if the white blob is too spread out (std > 120 px → noise)
    var = (xs * xs * col).sum() / total - cx * cx
    if var > 120.0 * 120.0:
        return None

    return float((cx - IMG_W / 2.0) / (IMG_W / 2.0))


def control(
    left_img: np.ndarray,
    right_img: np.ndarray,
    timestamp: float,
) -> Tuple[float, float]:
    """Keep the car centred using both cameras.

    When both cameras agree on direction, their average drives steering.
    When they disagree, only the camera that agrees with the current
    steering direction is trusted (prevents guardrail-induced flips).
    """
    global _steer_memory

    if right_img.shape != (IMG_H, IMG_W, 3):
        return 0.0, 0.0

    try:
        off_l = _lane_offset(left_img)
        off_r = _lane_offset(right_img)

        # ── Build candidate list ────────────────────────────────────
        candidates: list[float] = []
        if off_l is not None:
            candidates.append(off_l)
        if off_r is not None:
            candidates.append(off_r)

        if len(candidates) == 2:
            # Two readings — check for disagreement
            if candidates[0] * candidates[1] < -0.04:
                # Opposite signs: only keep the one whose sign matches the
                # current steering or is closer to frame centre
                use_l = abs(candidates[0]) <= abs(candidates[1])
                if abs(_steer_memory) > 0.05:
                    # Prefer the camera whose direction we're already turning toward
                    if (candidates[0] * _steer_memory) > (candidates[1] * _steer_memory):
                        use_l = True
                    else:
                        use_l = False
                if use_l:
                    candidates = [candidates[0]]
                else:
                    candidates = [candidates[1]]

        if len(candidates) >= 1:
            target = float(np.clip(candidates[0] * 2.2, -1.0, 1.0))
            trust = 0.9 if len(candidates) == 2 else 0.7
        else:
            # Both blind — hold memory with gentle centering bias
            target = _steer_memory * 0.85 + (-0.08) * 0.15
            target = float(np.clip(target, -1.0, 1.0))
            trust = 0.3

        raw_steer = _steer_memory * (1.0 - trust * 0.70) + target * (trust * 0.70)

        # ── Rate-limit steering changes ─────────────────────────────
        delta = raw_steer - _steer_memory
        if abs(delta) > MAX_STEER_CHANGE:
            delta = MAX_STEER_CHANGE * (1.0 if delta > 0 else -1.0)
        steering = _steer_memory + delta

        steering = float(np.clip(steering, -1.0, 1.0))
        _steer_memory = steering

        speed = 0.85 * (1.0 - 0.45 * abs(steering))
        speed = float(max(0.30, min(0.95, speed)))
    except Exception:
        steering, speed = 0.0, 0.0

    return steering, speed


if __name__ == "__main__":
    l = np.zeros((IMG_H, IMG_W, 3), dtype=np.uint8)
    r = np.zeros((IMG_H, IMG_W, 3), dtype=np.uint8)
    print(control(l, r, 0.0))
