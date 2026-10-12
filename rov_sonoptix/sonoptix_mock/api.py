"""FastAPI app mirroring the Sonoptix ECHO web API (firmware 1.5.0) under /api/v1.

Endpoints, request/response models and their names follow the sonar's own
/openapi.json, so clients written for the real unit (standalone/sonoptix_echo_api.py,
standalone/sonoptix_echo_reader.py) work unchanged. Mock-only controls live under
/mock. The RTSP data stream is started in the app's lifespan, so one process serves both.
"""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from .device import COLORMAPS, STREAM_TYPES, VERSION, EchoDevice, OutOfRange
from .simulator import Scene, Simulator, samples_for_range, sector_deg

log = logging.getLogger("sonoptix_mock")

DESCRIPTION = "\n# Introduction\n\nAPI for controlling, monitoring and configuring the ECHO FLS.\n\n"
TAGS = [
    {"name": "colormap", "description": "Define video colormap"},
    {"name": "config", "description": "Image processing configuration"},
    {"name": "misc", "description": ""},
    {"name": "network", "description": "Manage network settings"},
    {"name": "temperature", "description": "Monitor FLS temperature"},
    {"name": "transponder", "description": "FLS transponder settings. Ping rate is dependent on range."},
    {"name": "mock", "description": "Mock only: simulated scene and stream status (not on the real sonar)"},
]


# -- models: same names and fields as the sonar's /openapi.json ----------------

class TransponderChange(BaseModel):
    """Transponder properties

Trigger mode values:
* 0: internal
* 1: external, falling edge
* 2: external, rising edge

Operation mode values:
* 0: AUTO
* 1: HF (High Frequency)
* 2: LF (Low Frequency)
* 3: LFLR (Low Frequency, Long Range)
    """
    enable: bool | None = None
    sonar_range: float | None = None
    trigger_mode: int | None = None
    operation_mode: int | None = None


class StreamTypeChange(BaseModel):
    """Type of data stream:
* 0: Video (WebRTC)
* 1: WBMS (TCP)
* 2: RTSP"""
    value: int | None = None


class StreamTypes(BaseModel):
    """List of available stream types"""
    value: list


class ConfigChange(BaseModel):
    """Image processing configuration settings
* contrast: range from 0 to 1
* gain: positive or negative gain in dB
* mirror_image: boolean, true if video image shall be mirrored
* autodetect_orientation: -1: not supported, 0: disabled, 1: enabled"""
    contrast: float | None = None
    gain: float | None = None
    mirror_image: bool | None = None
    autodetect_orientation: int | None = None


class ColormapIndexChange(BaseModel):
    """Index of colormap in the list of available colormaps """
    value: int | None = None


class Colormaps(BaseModel):
    """List of available colormaps"""
    value: list


class SystemStatus(BaseModel):
    """System componets description:
* converter: converts raw sonar data to images
* transponder: collects raw sonar data
* video_stream: converts sonar images to video stream

System status values:
* 0: unknown
* 1: stopped
* 2: started
* 3: error"""
    converter: int
    video_stream: int
    transponder: int


class StaticIPChange(BaseModel):
    address: str | None = None
    netmask: str | None = None
    router: str | None = None


class Timestamp(BaseModel):
    """Timestamp in UNIX time, seconds since midnight January 1 1970"""
    now: int


class SoftwareVersion(BaseModel):
    """"Semantic version string"""
    value: str


class SerialNumber(BaseModel):
    """"Serial number as string"""
    value: str


class Hostname(BaseModel):
    """Hostname string"""
    value: str


class Temperature(BaseModel):
    """Sonar internal termperature in degrees Celsius"""
    value: float


class VideoDescriptionChange(BaseModel):
    """Video description string"""
    value: str


class CustomerLogo(BaseModel):
    """Customer Logo string"""
    value: str


def create_app(serial="0000000", rtsp_host="0.0.0.0", rtsp_port=8554, enable_rtsp=True):
    device = EchoDevice(serial)
    sim = Simulator()
    streamer = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal streamer
        if enable_rtsp:
            try:
                from .rtsp import RtspStreamer
                streamer = RtspStreamer(device, sim, rtsp_host, rtsp_port)
                streamer.start()
            except (ImportError, ValueError, RuntimeError) as e:
                # ValueError: gi.require_version() found no GstRtspServer bindings.
                streamer = None
                log.warning("RTSP stream disabled (%s); only the REST API is served. "
                            "See sonoptix_mock/README.md for the GStreamer packages.", e)
        try:
            yield
        finally:
            if streamer:
                streamer.stop()

    app = FastAPI(title="SONOPTIX  ECHO API", version=VERSION, description=DESCRIPTION,
                  contact={"name": "SONOPTIX", "url": "https://www.sonoptix.com"},
                  openapi_tags=TAGS, lifespan=lifespan)
    v1 = APIRouter(prefix="/api/v1")

    def apply(fn, *args):
        try:
            return fn(*args)
        except OutOfRange as e:
            raise HTTPException(400, str(e))

    # colormap
    @v1.get("/colormap/names", tags=["colormap"], response_model=Colormaps,
            summary="Get Colormap Names", description="Get a list of available colormaps names")
    def get_colormap_names():
        return {"value": COLORMAPS}

    @v1.get("/colormap/index", tags=["colormap"], response_model=ColormapIndexChange,
            summary="Get Colormap Index", description="Get index of active color map")
    def get_colormap_index():
        return {"value": device.state["colormap_index"]}

    @v1.put("/colormap/index", tags=["colormap"], response_model=ColormapIndexChange,
            summary="Put Colormap Index", description="Enable colormap at index")
    def put_colormap_index(body: ColormapIndexChange):
        apply(device.set_colormap_index, body.value)
        return {"value": device.state["colormap_index"]}

    # config
    @v1.get("/config", tags=["config"], response_model=ConfigChange, summary="Get Config",
            description="Get the current image processing configuration values")
    def get_config():
        return device.state["config"]

    @v1.patch("/config", tags=["config"], response_model=ConfigChange, summary="Patch Config",
              description="Patch one or more values in the image processing configuration")
    def patch_config(body: ConfigChange):
        changes = body.model_dump(exclude_unset=True)
        if not 0 <= changes.get("contrast", 0) <= 1:
            raise HTTPException(400, "Parameter value out of range")
        device.state["config"].update(changes)
        return device.state["config"]

    # misc
    @v1.get("/version", tags=["misc"], response_model=SoftwareVersion, summary="Get Version",
            description="Get the current software version")
    def get_version():
        return {"value": VERSION}

    @v1.get("/serial", tags=["misc"], response_model=SerialNumber, summary="Get Serial",
            description="Get the unit serial number version")
    def get_serial():
        return {"value": device.serial}

    @v1.get("/status", tags=["misc"], response_model=SystemStatus, summary="Get System Status",
            description="Get the current system status")
    def get_system_status():
        return device.status()

    @v1.put("/time", tags=["misc"], response_model=Timestamp, summary="Put Time",
            description="Set sonar timestamp in UNIX time, seconds since midnight January 1 1970")
    def put_time(body: Timestamp):
        device.clock_offset_s = body.now - time.time()
        return body

    @v1.put("/videotext", tags=["misc"], response_model=VideoDescriptionChange,
            summary="Put Video Description Text", description="Embed a description in the video")
    def put_video_description_text(body: VideoDescriptionChange):
        device.state["video_text"] = body.value
        return body

    @v1.delete("/videotext", tags=["misc"], summary="Delete Video Description Text",
               description="Delete the video description")
    def delete_video_description_text():
        device.state["video_text"] = None
        return None

    @v1.get("/hostname", tags=["misc"], response_model=Hostname, summary="Get Hostname",
            description="Get the current hostname")
    def get_hostname():
        return {"value": device.state["hostname"]}

    @v1.put("/hostname", tags=["misc"], response_model=Hostname, summary="Set Hostname")
    def set_hostname(body: Hostname):
        device.state["hostname"] = body.value
        return body

    @v1.delete("/factoryreset", tags=["misc"], summary="Factoryreset",
               description="Reset configuration settings to default")
    def factoryreset():
        device.factory_reset()
        return None

    @v1.get("/streamtypes", tags=["misc"], response_model=StreamTypes, summary="Get Stream Types",
            description="Get a list of available stream types")
    def get_stream_types():
        return {"value": list(STREAM_TYPES)}

    @v1.get("/streamtype", tags=["misc"], response_model=StreamTypeChange,
            summary="Get Current Stream Type", description="Get currently active stream type")
    def get_current_stream_type():
        return {"value": device.state["stream_type"]}

    @v1.put("/streamtype", tags=["misc"], response_model=StreamTypeChange, summary="Put Stream Type",
            description="Set type of data stream")
    def put_stream_type(body: StreamTypeChange):
        apply(device.set_stream_type, body.value)
        return {"value": device.state["stream_type"]}

    @v1.put("/logo", tags=["misc"], summary="Upload Logo", description="Embed a logo in the video")
    def upload_logo(body: CustomerLogo):
        device.state["logo"] = body.value
        return None

    @v1.delete("/logo", tags=["misc"], summary="Delete Logo", description="Delete the logo in the video")
    def delete_logo():
        device.state["logo"] = None
        return None

    # network: stored and reported, but the mock keeps listening where it was started
    @v1.get("/network/static", tags=["network"], response_model=StaticIPChange, summary="Get Network",
            description="Get the current network settings")
    def get_network():
        return device.state["network"]

    @v1.put("/network/static", tags=["network"], response_model=StaticIPChange, summary="Put Network")
    def put_network(body: StaticIPChange):
        device.state["network"].update(body.model_dump(exclude_unset=True))
        return device.state["network"]

    # temperature
    @v1.get("/temperature", tags=["temperature"], response_model=Temperature, summary="Get Temperature")
    def get_temperature():
        return {"value": device.temperature()}

    # transponder
    @v1.get("/transponder", tags=["transponder"], response_model=TransponderChange, summary="Get Status")
    def get_status():
        return device.transponder

    @v1.patch("/transponder", tags=["transponder"], response_model=TransponderChange,
              summary="Patch Status", description="Patch one or more of the transponder properties")
    async def patch_status(body: TransponderChange):
        # async: range snapping schedules a timer on the event loop.
        return apply(device.patch_transponder, body.model_dump(exclude_unset=True))

    app.include_router(v1)

    # -- mock-only ---------------------------------------------------------

    @app.get("/mock/status", tags=["mock"])
    def mock_status():
        t = device.transponder
        return {
            "rtsp": streamer.url if streamer else None,
            "rtsp_clients": streamer.client_count if streamer else 0,
            "streaming": device.streaming,
            "frames_sent": streamer.frames_sent if streamer else 0,
            "frame_shape": list(streamer.shape) if streamer and streamer.shape else
            [samples_for_range(t["sonar_range"]), 256],
            "sector_deg": sector_deg(t["operation_mode"], t["sonar_range"]),
        }

    @app.get("/mock/scene", tags=["mock"], response_model=Scene)
    def get_scene():
        return sim.scene

    @app.put("/mock/scene", tags=["mock"], response_model=Scene)
    def set_scene(scene: Scene):
        """Replace the simulated environment (targets, seabed, noise...)."""
        sim.set_scene(scene)
        return scene

    return app
