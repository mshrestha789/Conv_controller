"""Per-sample autofocus and bounded full-frame selection.

Only the scoring calculation uses a region. The selected camera request is
saved whole, with its metadata, and all other requests are released.
"""

import math
import time

import numpy as np


class FocusError(RuntimeError):
    """No acceptable focused frame; caller must leave the belt stopped."""


def validate_settings(roi, candidates, minimum):
    if len(roi) != 4 or not all(math.isfinite(float(v)) for v in roi):
        raise ValueError("Focus ROI must contain four finite numbers.")
    x, y, w, h = map(float, roi)
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1 or y + h > 1:
        raise ValueError("Focus ROI must fit inside the normalized sensor view.")
    if isinstance(candidates, bool) or not isinstance(candidates, int) or not 1 <= candidates <= 5:
        raise ValueError("Focus candidate count must be an integer from 1 to 5.")
    if minimum is not None and (not math.isfinite(float(minimum)) or minimum < 0):
        raise ValueError("Minimum sharpness must be None or a finite nonnegative value.")


def sensor_window(sensor_rect, roi):
    """Map normalized focus region to sensor coordinates, including offsets."""
    x, y, w, h = map(int, sensor_rect)
    rx, ry, rw, rh = roi
    left, top = x + round(rx * w), y + round(ry * h)
    right, bottom = x + round((rx + rw) * w), y + round((ry + rh) * h)
    if right - left < 2 or bottom - top < 2:
        raise ValueError("Focus window is too small.")
    return left, top, right - left, bottom - top


def image_window(window, scaler_crop, image_size):
    """Map the sensor AF window into this request's actual output field."""
    x, y, w, h = window
    cx, cy, cw, ch = map(int, scaler_crop)
    iw, ih = image_size
    if cw <= 0 or ch <= 0:
        raise FocusError("Invalid ScalerCrop metadata.")
    left = max(0, min(iw, math.floor((x - cx) * iw / cw)))
    top = max(0, min(ih, math.floor((y - cy) * ih / ch)))
    right = max(0, min(iw, math.ceil((x + w - cx) * iw / cw)))
    bottom = max(0, min(ih, math.ceil((y + h - cy) * ih / ch)))
    if right - left < 8 or bottom - top < 8:
        raise FocusError("Stem focus area is outside the image or too small.")
    return left, top, right, bottom


def sharpness_score(luminance):
    """Noise-smoothed, brightness-normalized gradient energy.

Compare only the same stationary scene with identical ROI and resolution.
Texture, illumination, noise and holder edges can still affect this score.
"""
    a = np.asarray(luminance, dtype=np.float32)
    if a.ndim != 2 or min(a.shape) < 8 or not np.isfinite(a).all():
        raise FocusError("Invalid luminance data in the stem focus area.")
    # A small binomial blur reduces the influence of individual noisy pixels.
    b = (a[:-2, :-2] + 2*a[:-2, 1:-1] + a[:-2, 2:]
         + 2*a[1:-1, :-2] + 4*a[1:-1, 1:-1] + 2*a[1:-1, 2:]
         + a[2:, :-2] + 2*a[2:, 1:-1] + a[2:, 2:]) / 16
    gx = b[1:-1, 2:] - b[1:-1, :-2]
    gy = b[2:, 1:-1] - b[:-2, 1:-1]
    return float(np.mean(gx*gx + gy*gy) / max(float(b.mean()), 16.0)**2)


def capture_best(camera, save_path, *, window, candidates, minimum,
                 timeout_sec, focused_state, score_request,
                 clock=time.monotonic):
    """One AF sweep, then fresh focused frames; encode only the best request.

The parent QProcess timer remains the hard limit for stalled camera calls,
encoding or disk I/O. On a timed-out asynchronous job the worker must exit,
rather than reusing a camera that still has outstanding jobs.
"""
    if not math.isfinite(float(timeout_sec)) or timeout_sec <= 1:
        raise ValueError("Capture timeout must exceed one second.")
    deadline = clock() + timeout_sec - 1.0  # reserve time for reporting/commit

    def remaining():
        value = deadline - clock()
        if value <= 0:
            raise TimeoutError("Autofocus/frame selection exceeded the capture timeout.")
        return value

    def wait_for(job):
        return camera.wait(job, timeout=remaining())

    if not wait_for(camera.autofocus_cycle(wait=False)):
        raise FocusError(
            "Autofocus failed. Check sample distance, lighting and focus area; "
            "no image was saved. Press RESET SYSTEM before retrying."
        )

    best = None
    best_score = -1.0
    best_index = None
    best_metadata = None
    scores = []
    try:
        for index in range(candidates):
            remaining()
            # Flush queued/exposing frames: never rank a pre-focus frame.
            request = wait_for(camera.capture_request(wait=False, flush=True))
            try:
                metadata = request.get_metadata()
                if metadata.get("AfState") != focused_state:
                    scores.append(None)
                    continue
                score = float(score_request(request, window))
                scores.append(score if math.isfinite(score) else None)
                if not math.isfinite(score) or score <= 0:
                    continue
                if minimum is not None and score < minimum:
                    continue
                if score > best_score:
                    if best is not None:
                        best.release()
                    best = request
                    request = None
                    best_score, best_index = score, index + 1
                    best_metadata = metadata
            finally:
                if request is not None:
                    request.release()

        if best is None:
            raise FocusError(
                "No focused frame passed the sharpness check. Check the stem "
                "position, lighting and distance; no image was saved."
            )
        remaining()
        # Save the SAME request that won, not a later capture with unknown focus.
        best.save("main", str(save_path))
        remaining()
        return {
            "selected_frame": best_index,
            "candidate_scores": scores,
            "sharpness_score": best_score,
            "lens_position": best_metadata.get("LensPosition"),
            "exposure_time_us": best_metadata.get("ExposureTime"),
            "analogue_gain": best_metadata.get("AnalogueGain"),
        }
    finally:
        if best is not None:
            best.release()
