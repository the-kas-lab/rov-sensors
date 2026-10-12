"""RTSP server that streams synthetic frames the way the real ECHO does.

rtsp://<host>:8554/raw carries H.264 (full-range 4:2:0, i.e. ffprobe's yuvj420p)
with the sonar intensities in the luma plane and neutral chroma. Frames only flow
while the transponder is enabled and the stream type is RTSP; otherwise clients
stay connected and wait, as they would on the real unit. A keyframe is sent every
30 frames (about 2 s), so a new client waits up to that long for its first image.

Needs GStreamer's RTSP server and an H.264 encoder (x264enc) with Python bindings:
in the rov-sensors conda env, `conda install -c conda-forge gst-rtsp-server gst-plugins-ugly`.
"""

import logging
import threading
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstRtspServer", "1.0")
from gi.repository import GLib, Gst, GstRtspServer  # noqa: E402

from .simulator import BEAMS, frame_rate  # noqa: E402

log = logging.getLogger("sonoptix_mock")

# colorimetry 1:4:0:0 = full range (0-255), BT.601 matrix: the encoder flags the stream
# as full range, so decoders hand back the luma values exactly as generated.
LAUNCH = (
    "( appsrc name=src is-live=true format=time do-timestamp=true ! "
    "x264enc tune=zerolatency speed-preset=ultrafast key-int-max=30 ! "
    "rtph264pay name=pay0 pt=96 config-interval=1 )"
)


class RtspStreamer:
    def __init__(self, device, sim, host="0.0.0.0", port=8554, path="/raw"):
        self.device, self.sim = device, sim
        self.host, self.port, self.path = host, port, path
        self.frames_sent = 0
        self.shape = None
        self._sources = []                 # appsrc of each prepared media (shared: usually one)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._loop = None

    @property
    def url(self):
        return f"rtsp://{self.host}:{self.port}{self.path}"

    @property
    def client_count(self):
        with self._lock:
            return len(self._sources)

    def start(self):
        Gst.init(None)
        context = GLib.MainContext.new()
        server = GstRtspServer.RTSPServer()
        server.set_address(self.host)
        server.set_service(str(self.port))
        factory = GstRtspServer.RTSPMediaFactory()
        factory.set_launch(LAUNCH)
        factory.set_shared(True)
        factory.connect("media-configure", self._on_media_configure)
        server.get_mount_points().add_factory(self.path, factory)
        if not server.attach(context):
            raise RuntimeError(f"could not open RTSP port {self.port}")
        self._loop = GLib.MainLoop.new(context, False)
        threading.Thread(target=self._loop.run, daemon=True).start()
        threading.Thread(target=self._produce, daemon=True).start()
        log.info("RTSP server streaming on %s", self.url)

    def stop(self):
        self._stop.set()
        if self._loop:
            self._loop.quit()

    def _on_media_configure(self, factory, media):
        src = media.get_element().get_by_name("src")
        with self._lock:
            self._sources.append(src)
        media.connect("unprepared", self._on_media_unprepared, src)

    def _on_media_unprepared(self, media, src):
        with self._lock:
            if src in self._sources:
                self._sources.remove(src)

    def _produce(self):
        """Generate frames at the sonar's rate and push them to every connected stream."""
        caps_key = None
        next_t = time.monotonic()
        while not self._stop.is_set():
            t = self.device.transponder
            range_m, mode = t["sonar_range"], t["operation_mode"]
            num, den = frame_rate(mode, range_m)
            with self._lock:
                sources = list(self._sources)
            if not (self.device.streaming and sources):
                time.sleep(0.1)
                next_t = time.monotonic()
                continue

            frame = self.sim.frame(range_m, mode)
            rows = frame.shape[0]
            key = (rows, num, den, tuple(map(id, sources)))
            if key != caps_key:
                # The real sonar also changes the frame height when the range changes.
                caps = Gst.Caps.from_string(
                    f"video/x-raw,format=I420,width={BEAMS},height={rows},"
                    f"framerate={num}/{den},colorimetry=1:4:0:0")
                for src in sources:
                    src.set_property("caps", caps)
                caps_key = key
            chroma = bytes([128]) * (BEAMS // 2 * rows // 2 * 2)
            data = frame.tobytes() + chroma
            for src in sources:
                src.emit("push-buffer", Gst.Buffer.new_wrapped(data))
            self.frames_sent += 1
            self.shape = frame.shape

            next_t += den / num
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.monotonic()  # fell behind: don't try to catch up in a burst
