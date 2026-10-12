"""Synthetic Sonoptix ECHO frames.

A frame is what the real sonar sends on rtsp://<ip>:8554/raw once decoded: a
uint8 matrix with one column per beam (256, port to starboard) and one row per
range sample (nearest at the top). Sizes and rates follow what was measured on
the real unit (firmware 1.5.0):
  * about 67 samples per metre, capped at 1024 rows (3 m -> 200, 6 m -> 404, 15 m -> 1012)
  * about 15.5 frames/s at 700 kHz (HF) and 12.6 at 400 kHz (LF)
  * a 120 deg sector at 700 kHz and 90 deg at 400 kHz (Blue Robotics spec)
In air the real unit shows only a faint ring-down (values ~10) in the first few
centimetres; the scene below adds what it would see in water.
"""

import math
import time

import numpy as np
from pydantic import BaseModel, Field

BEAMS = 256
MAX_SAMPLES = 1024
SAMPLES_PER_M = 67.4
MEASURED_SAMPLES = {3: 200, 6: 404, 15: 1012}  # range (m) -> rows, from the real unit
HF_MAX_RANGE_M = 30                            # 700 kHz up to 30 m, 400 kHz beyond


def samples_for_range(range_m):
    rows = MEASURED_SAMPLES.get(range_m) or min(MAX_SAMPLES, round(range_m * SAMPLES_PER_M))
    return rows - rows % 2  # I420 needs an even height


def is_high_frequency(operation_mode, range_m):
    """700 kHz in HF mode, or in AUTO up to 30 m; 400 kHz otherwise (LF, LFLR)."""
    return operation_mode == 1 or (operation_mode == 0 and range_m <= HF_MAX_RANGE_M)


def sector_deg(operation_mode, range_m):
    return 120.0 if is_high_frequency(operation_mode, range_m) else 90.0


def frame_rate(operation_mode, range_m):
    """(numerator, denominator) frames/s: 187/12 (~15.6) as ffprobe reported, or 63/5 (12.6)."""
    return (187, 12) if is_high_frequency(operation_mode, range_m) else (63, 5)


class Target(BaseModel):
    range_m: float = Field(5.0, ge=0, description="distance from the transducer")
    bearing_deg: float = Field(0.0, description="0 = straight ahead, positive = starboard")
    intensity: float = Field(200.0, ge=0, le=255, description="peak echo value (0-255)")
    size_m: float = Field(0.15, gt=0, description="radial extent (Gaussian sigma)")
    width_deg: float = Field(1.5, gt=0, description="angular extent (Gaussian sigma)")
    drift_amplitude_m: float = Field(0.0, ge=0, description="sinusoidal range wobble, e.g. ROV surge")
    drift_period_s: float = Field(10.0, gt=0)


class Scene(BaseModel):
    targets: list[Target] = Field(default_factory=lambda: [
        Target(range_m=4.0, bearing_deg=-25.0, intensity=230.0),
        Target(range_m=9.0, bearing_deg=10.0, intensity=170.0, size_m=0.3, width_deg=3.0,
               drift_amplitude_m=0.5, drift_period_s=8.0),
    ])
    seabed_m: float | None = Field(12.0, gt=0, description="range where the seafloor starts "
                                   "straight ahead (further out at the sides); null = none")
    seabed_intensity: float = Field(45.0, ge=0, le=255)
    noise_level: float = Field(2.0, ge=0, description="mean background value")
    noise_std: float = Field(1.5, ge=0)
    ringdown_intensity: float = Field(12.0, ge=0, le=255, description="value at the transducer")
    ringdown_m: float = Field(0.05, gt=0, description="ring-down extent")
    clutter_per_frame: float = Field(2.0, ge=0, description="mean number of random specks (fish, particles)")
    seed: int | None = Field(None, description="set for reproducible noise")


class Simulator:
    def __init__(self, scene=None):
        self.scene = scene or Scene()
        self._boot = time.monotonic()
        self._rng = np.random.default_rng(self.scene.seed)

    def set_scene(self, scene):
        self.scene = scene
        self._rng = np.random.default_rng(scene.seed)

    def frame(self, range_m, operation_mode):
        """One frame as a (samples, 256) uint8 array."""
        s, rng = self.scene, self._rng
        now = time.monotonic() - self._boot
        rows = samples_for_range(range_m)
        fov = sector_deg(operation_mode, range_m)

        d = ((np.arange(rows) + 0.5) * range_m / rows)[:, None]                 # (rows, 1) metres
        theta = (-fov / 2 + (np.arange(BEAMS) + 0.5) * fov / BEAMS)[None, :]    # (1, beams) degrees

        img = s.noise_level + rng.normal(0.0, s.noise_std, (rows, BEAMS))
        img += s.ringdown_intensity * np.exp(-d / s.ringdown_m)

        if s.seabed_m is not None:
            # A flat bottom seen by a forward-looking sonar: it starts further out at the sides
            # and fades with range. Speckle makes it grainy, like real seafloor returns.
            onset = s.seabed_m / np.cos(np.radians(theta))
            rise = 1.0 / (1.0 + np.exp(-(d - onset) / 0.15))
            fade = np.exp(-np.clip(d - onset, 0, None) / max(0.5 * range_m, 1.0))
            img += s.seabed_intensity * rise * fade * rng.gamma(4.0, 0.25, (rows, BEAMS))

        for t in s.targets:
            r = t.range_m + t.drift_amplitude_m * math.sin(2 * math.pi * now / t.drift_period_s)
            img += t.intensity * np.exp(-0.5 * (((d - r) / t.size_m) ** 2
                                               + ((theta - t.bearing_deg) / t.width_deg) ** 2))

        for _ in range(rng.poisson(s.clutter_per_frame)):
            r, b = rng.uniform(0.3, range_m), rng.uniform(-fov / 2, fov / 2)
            img += rng.uniform(40, 120) * np.exp(-0.5 * (((d - r) / 0.05) ** 2 + ((theta - b) / 0.8) ** 2))

        return np.clip(img, 0, 255).astype(np.uint8)
