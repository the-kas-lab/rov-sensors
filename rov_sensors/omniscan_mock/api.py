"""FastAPI app: hosts the mock sonar and exposes it over HTTP / WebSocket.

The Ping-protocol TCP server (what omniscan450_reader.py talks to) is started
in the app's lifespan, so one process serves both.
"""

from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .device import Config, MockSonar
from .simulator import PingParams, Scene


class SpeedOfSound(BaseModel):
    speed_of_sound_mps: float = Field(..., gt=1000, lt=2000)


def profile_json(fields, raw, include_raw=True):
    """Profile as JSON: the message fields plus pwr_results converted to dB, like the reader does."""
    lo, hi = fields["min_pwr_db"], fields["max_pwr_db"]
    n = fields["num_results"]
    start, length = fields["start_mm"] / 1000.0, fields["length_mm"] / 1000.0
    out = dict(fields)
    out["pwr_db"] = np.round(lo + raw / 65535.0 * (hi - lo), 2).tolist()
    out["distances_m"] = np.round(start + np.arange(n) * (length / n), 4).tolist()
    if include_raw:
        out["pwr_results"] = raw.tolist()
    return out


def create_app(config=None):
    config = config or Config.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.sonar = sonar = MockSonar(config)
        await sonar.start()
        try:
            yield
        finally:
            await sonar.stop()

    app = FastAPI(title="Omniscan 450 FS mock", lifespan=lifespan,
                  description="Emulated Cerulean Omniscan 450 FS producing synthetic profiles.")

    def sonar() -> MockSonar:
        return app.state.sonar

    @app.get("/status")
    def status():
        s = sonar()
        return {
            "tcp": f"{config.tcp_host}:{config.tcp_port}",
            "tcp_clients": s.tcp_client_count,
            "pinging": s.sim.params.enable,
            "ping_number": s.sim.ping_number,
            "speed_of_sound_mps": s.sim.speed_of_sound_mps,
            "device_information": {
                "device_type": config.device_type,
                "device_revision": config.device_revision,
                "firmware": ".".join(map(str, config.firmware)),
            },
        }

    @app.get("/ping_params", response_model=PingParams)
    def get_ping_params():
        return sonar().sim.params

    @app.put("/ping_params", response_model=PingParams)
    def set_ping_params(params: PingParams):
        """Equivalent of control_os_ping_params() over TCP."""
        sonar().set_ping_params(params)
        return params

    @app.put("/speed_of_sound", response_model=SpeedOfSound)
    def set_speed_of_sound(body: SpeedOfSound):
        sonar().set_speed_of_sound(body.speed_of_sound_mps)
        return body

    @app.get("/scene", response_model=Scene)
    def get_scene():
        return sonar().sim.scene

    @app.put("/scene", response_model=Scene)
    def set_scene(scene: Scene):
        """Replace the simulated environment (seabed distance, targets, noise...)."""
        sonar().sim.set_scene(scene)
        return scene

    @app.get("/profile/latest")
    def latest_profile(include_raw: bool = False):
        if sonar().latest is None:
            raise HTTPException(404, "no ping yet; enable pinging via PUT /ping_params")
        return profile_json(*sonar().latest, include_raw)

    @app.post("/profile/ping")
    def single_ping(include_raw: bool = False):
        """Trigger one ping, like a general_request for os_mono_profile."""
        return profile_json(*sonar().single_ping(), include_raw)

    @app.websocket("/ws/profiles")
    async def stream_profiles(ws: WebSocket, include_raw: bool = False):
        """Push every profile as JSON while pinging is enabled."""
        await ws.accept()
        queue = sonar().subscribe()
        try:
            while True:
                await ws.send_json(profile_json(*await queue.get(), include_raw))
        except WebSocketDisconnect:
            pass
        finally:
            sonar().unsubscribe(queue)

    return app


app = create_app()
