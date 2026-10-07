"""Synthetic Omniscan 450 FS profiles.

Each ping is built in dB from the pieces visible in real captures
(see standalone/omniscan_10pings.csv):
  * transducer ring-down: ~60 dB in the first few centimetres, decaying fast
  * a noise floor that rises ~3 dB per gain step (about -16 dB at gain 0)
  * echoes from the scene: a seabed with a reverberation tail, extra targets,
    and random clutter (fish, particles) that comes and goes between pings
"""

import math
import time

import numpy as np
from pydantic import BaseModel, Field

PING_HZ = 451127               # carrier frequency reported by the real unit
MIN_PWR_DB = -45.0              # scale used to pack pwr_results into u16
MAX_PWR_DB = 75.0
GAIN_STEP_DB = 3.0
AUTO_GAIN_SEQUENCE = (0, 4, 5, 6, 7)  # how the real unit settled in auto gain


class Target(BaseModel):
    distance_m: float = Field(6.0, ge=0, description="range from the transducer")
    strength_db: float = Field(40.0, description="peak echo level at gain 0")
    width_m: float = Field(0.15, gt=0, description="echo thickness (Gaussian sigma)")
    tail_m: float = Field(0.0, ge=0, description="reverberation tail beyond the echo (seabed ~1-2 m)")
    drift_amplitude_m: float = Field(0.0, ge=0, description="sinusoidal range wobble, e.g. ROV heave")
    drift_period_s: float = Field(10.0, gt=0)


class Scene(BaseModel):
    targets: list[Target] = Field(default_factory=lambda: [
        Target(distance_m=6.0, strength_db=45.0, width_m=0.08, tail_m=1.5,
               drift_amplitude_m=0.4, drift_period_s=12.0),
    ])
    noise_floor_db: float = Field(-16.0, description="median noise level at gain 0")
    noise_std_db: float = Field(6.0, ge=0)
    ringdown_db: float = Field(61.0, description="peak level right at the transducer")
    ringdown_length_m: float = Field(0.12, gt=0)
    clutter_per_ping: float = Field(0.5, ge=0, description="mean number of random transient echoes")
    seed: int | None = Field(None, description="set for reproducible noise")


class PingParams(BaseModel):
    """Same fields and defaults as brping's Omniscan450.control_os_ping_params()."""
    start_mm: int = Field(0, ge=0)
    length_mm: int = Field(5000, gt=0)
    msec_per_ping: int = Field(0, ge=0, description="0 = as fast as the range allows")
    pulse_len_percent: float = 0.002
    filter_duration_percent: float = 0.0015
    gain_index: int = Field(-1, ge=-1, le=7, description="-1 = auto")
    num_results: int = Field(600, ge=1, le=32000)
    enable: bool = True


class Simulator:
    def __init__(self, scene=None):
        self.scene = scene or Scene()
        self.params = PingParams()
        self.speed_of_sound_mps = 1500.0
        self.ping_number = 0
        self._boot = time.monotonic()
        self._auto_gain_step = 0
        self._rng = np.random.default_rng(self.scene.seed)

    def set_scene(self, scene):
        self.scene = scene
        self._rng = np.random.default_rng(scene.seed)

    def set_params(self, params):
        self.params = params
        self._auto_gain_step = 0  # auto gain re-converges after a reconfigure

    def min_ping_interval_s(self):
        """Two-way travel time to the end of the range, plus processing overhead."""
        far_m = (self.params.start_mm + self.params.length_mm) / 1000.0
        return 2.0 * far_m / self.speed_of_sound_mps + 0.004

    def _next_gain(self):
        if self.params.gain_index >= 0:
            return self.params.gain_index
        gain = AUTO_GAIN_SEQUENCE[min(self._auto_gain_step, len(AUTO_GAIN_SEQUENCE) - 1)]
        self._auto_gain_step += 1
        return gain

    def ping(self):
        """Produce one os_mono_profile as (fields dict, raw u16 pwr_results array)."""
        p, s, rng = self.params, self.scene, self._rng
        now = time.monotonic() - self._boot
        gain = self._next_gain()
        gain_db = gain * GAIN_STEP_DB

        d = p.start_mm / 1000.0 + np.arange(p.num_results) * (p.length_mm / 1000.0 / p.num_results)

        noise = s.noise_floor_db + gain_db + rng.normal(0.0, s.noise_std_db, d.size)
        echo = np.full(d.size, -np.inf)

        for t in s.targets:
            r = t.distance_m + t.drift_amplitude_m * math.sin(2 * math.pi * now / t.drift_period_s)
            level = t.strength_db + gain_db
            echo = np.maximum(echo, level - 0.5 * ((d - r) / t.width_m) ** 2 * 4.343)
            if t.tail_m > 0:
                behind = d > r
                tail = level - 12.0 - 20.0 * (d - r) / t.tail_m
                echo = np.where(behind, np.maximum(echo, tail), echo)

        for _ in range(rng.poisson(s.clutter_per_ping)):
            r = rng.uniform(0.3, max(0.3, d[-1]))
            level = rng.uniform(5.0, 25.0) + gain_db
            echo = np.maximum(echo, level - 0.5 * ((d - r) / 0.04) ** 2 * 4.343)

        # Echoes add speckle; the strongest contributor dominates in dB.
        echo = echo + rng.normal(0.0, 2.0, d.size)
        ringdown = s.ringdown_db - 20.0 * (np.abs(d - 0.017) / s.ringdown_length_m)
        power_db = np.maximum.reduce([noise, echo, ringdown])
        power_db = np.clip(power_db, MIN_PWR_DB, MAX_PWR_DB)

        raw = np.round((power_db - MIN_PWR_DB) / (MAX_PWR_DB - MIN_PWR_DB) * 65535).astype("<u2")

        fields = {
            "ping_number": self.ping_number,
            "start_mm": p.start_mm,
            "length_mm": p.length_mm,
            "timestamp_ms": int(now * 1000),
            "ping_hz": PING_HZ,
            "gain_index": gain,
            "num_results": p.num_results,
            "sos_dmps": int(round(self.speed_of_sound_mps * 10)),
            "channel_number": 0,
            "reserved": 0,
            "pulse_duration_sec": p.pulse_len_percent * 2 * (p.length_mm / 1000.0) / self.speed_of_sound_mps,
            "analog_gain": gain_db,
            "max_pwr_db": MAX_PWR_DB,
            "min_pwr_db": MIN_PWR_DB,
            "transducer_heading_deg": 0.0,
            "vehicle_heading_deg": (20.0 * math.sin(2 * math.pi * now / 60.0)) % 360.0,
        }
        self.ping_number = (self.ping_number + 1) & 0xFFFFFFFF
        return fields, raw
