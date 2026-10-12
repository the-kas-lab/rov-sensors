"""State of the emulated Sonoptix ECHO and the rules its REST API follows.

The behaviour mirrors what the real unit (firmware 1.5.0) did when probed:
  * sonar_range keeps only VALID_RANGES_M, in every operation mode. Any other
    value from 3 to 200 m reads back as sent at first and then changes to 3 m
    about a second later. Values below 3 or above 200 are answered with
    HTTP 400 "Parameter value out of range", yet are stored and read back anyway.
  * /streamtypes offers only 0 (WebRTC video) and 2 (RTSP): no WBMS.
  * /status: transponder and converter follow `enable`; video_stream is always started.
Where the real unit's behaviour is unknown (e.g. an invalid operation_mode), the mock
refuses the value with the same 400 error and does not store it.
"""

import asyncio
import random
import time

VERSION = "1.5.0"
VALID_RANGES_M = (3, 6, 9, 12, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 125, 150, 175, 200)
MIN_RANGE_M, MAX_RANGE_M = 3, 200
SNAP_RANGE_M = 3.0
SNAP_DELAY_S = 1.0
STREAM_TYPES = (0, 2)                    # what this unit offers (1 = WBMS is not available)
STREAM_TYPE_RTSP = 2
COLORMAPS = ["gray", "ironbow", "deep", "rainbow", "amber"]
STOPPED, STARTED = 1, 2
OUT_OF_RANGE = "Parameter value out of range"


class OutOfRange(ValueError):
    pass


def _defaults(serial):
    """Factory state: what the real unit reported before anything was changed."""
    return {
        "transponder": {"enable": False, "sonar_range": 30.0, "trigger_mode": 0, "operation_mode": 1},
        "stream_type": 0,
        "config": {"contrast": 0.4000000059604645, "gain": 0.0, "mirror_image": False,
                   "autodetect_orientation": 0},
        "colormap_index": 4,
        "hostname": f"echo-{serial}",
        "network": {"address": "192.168.2.42", "netmask": "255.255.255.0", "router": None},
        "video_text": None,
        "logo": None,
    }


class EchoDevice:
    def __init__(self, serial="0000000"):
        self.serial = serial
        self.state = _defaults(serial)
        self.clock_offset_s = 0.0
        self._enabled_since = None

    # -- transponder -------------------------------------------------------

    @property
    def transponder(self):
        return self.state["transponder"]

    @property
    def streaming(self):
        """True while frames should flow on the RTSP stream."""
        return self.transponder["enable"] and self.state["stream_type"] == STREAM_TYPE_RTSP

    def patch_transponder(self, changes):
        t = self.transponder
        if "operation_mode" in changes and changes["operation_mode"] not in (0, 1, 2, 3):
            raise OutOfRange(OUT_OF_RANGE)
        if "trigger_mode" in changes and changes["trigger_mode"] not in (0, 1, 2):
            raise OutOfRange(OUT_OF_RANGE)
        for key in ("enable", "trigger_mode", "operation_mode"):
            if key in changes:
                t[key] = changes[key]
        if t["enable"] and self._enabled_since is None:
            self._enabled_since = time.monotonic()
        elif not t["enable"]:
            self._enabled_since = None

        if "sonar_range" in changes:
            value = float(changes["sonar_range"])
            t["sonar_range"] = value  # the real unit stores it even when it then complains
            if not MIN_RANGE_M <= value <= MAX_RANGE_M:
                raise OutOfRange(OUT_OF_RANGE)
            if value not in VALID_RANGES_M:
                asyncio.get_running_loop().call_later(SNAP_DELAY_S, self._snap_range, value)
        return t

    def _snap_range(self, value):
        if self.transponder["sonar_range"] == value:
            self.transponder["sonar_range"] = SNAP_RANGE_M

    # -- everything else ---------------------------------------------------

    def set_stream_type(self, value):
        if value not in STREAM_TYPES:
            raise OutOfRange(OUT_OF_RANGE)
        self.state["stream_type"] = value

    def set_colormap_index(self, value):
        if not 0 <= value < len(COLORMAPS):
            raise OutOfRange(OUT_OF_RANGE)
        self.state["colormap_index"] = value

    def status(self):
        on = STARTED if self.transponder["enable"] else STOPPED
        return {"converter": on, "video_stream": STARTED, "transponder": on}

    def temperature(self):
        """About 46 C idle, as measured in air; warms by up to 4 C over a few minutes of pinging."""
        warm = 0.0
        if self._enabled_since is not None:
            warm = 4.0 * (1 - 2.718 ** (-(time.monotonic() - self._enabled_since) / 120.0))
        return round(46.3 + warm + random.uniform(-0.1, 0.1), 3)

    def factory_reset(self):
        self.state = _defaults(self.serial)
        self._enabled_since = None
