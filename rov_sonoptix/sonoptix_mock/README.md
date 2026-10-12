# Sonoptix ECHO mock

Emulates the Sonoptix ECHO multibeam imaging sonar so you can develop without the sonar or the ROV.

One process runs two servers:

| Port | What | Who uses it |
|------|------|-------------|
| 8001 | REST API, `/api/v1/...` (FastAPI, browsable at `/docs`). The real sonar uses 8000, but so does the Omniscan mock. | `standalone/sonoptix_echo_api.py`, the reader, the ROS driver, a browser |
| 8554 | RTSP (same as the real sonar), `rtsp://<host>:8554/raw`: H.264 sonar frames | `standalone/sonoptix_echo_reader.py`, ffprobe, VLC |

The REST API mirrors firmware 1.5.0: the same endpoints, field names, models and error messages as the real unit's `/openapi.json`. Existing clients only need the IP address changed.

## Setup

It runs in the `rov-sensors` conda env, which needs GStreamer's RTSP server and the x264 encoder on top of the reader's packages:

```bash
conda install -n rov-sensors -c conda-forge pygobject gstreamer gst-plugins-base gst-plugins-good \
    gst-plugins-bad gst-plugins-ugly gst-libav gst-rtsp-server
```

Without them the mock still starts, but serves only the REST API and logs a warning.

## Run

```bash
conda activate rov-sensors
cd rov-sensors/rov_sonoptix && python -m sonoptix_mock
```

Options: `--host` (default `0.0.0.0`), `--http-port` (8001), `--rtsp-port` (8554), `--serial`, `--no-rtsp`.

`ros2 launch rov_sonoptix sonoptix_echo.launch.py use_mock:=true` starts it for you, together with the ROS driver (already pointed at port 8001), the fan node and the Foxglove bridge. Under ROS it runs on the system Python, so it needs fastapi/uvicorn (from the `.venv`) and `sudo apt install gir1.2-gst-rtsp-server-1.0 gstreamer1.0-plugins-ugly` for the RTSP stream.

Then point the existing scripts at it (the REST port differs from the real sonar's):

```bash
python standalone/sonoptix_echo_api.py --host 127.0.0.1 --port 8001
python standalone/sonoptix_echo_reader.py --host 127.0.0.1 --api-port 8001 --range 15 --count 20
ros2 run rov_sonoptix sonoptix_echo_node --ros-args -p host:=127.0.0.1 -p api_port:=8001
```

## Behaviour (as measured on the real unit)

- **Starting state:** the real unit's factory state: disabled, 30 m, HF, internal trigger, stream type 0 (WebRTC), contrast 0.4, gain 0, colormap 4 (amber).
- **Allowed ranges:** `sonar_range` keeps only 3, 6, 9, 12, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 125, 150, 175 or 200 m, in every mode.
  - **Other values from 3 to 200 m:** read back as sent, then change to 3 m about a second later.
  - **Below 3 or above 200 m:** answered with HTTP 400 `Parameter value out of range`, but still stored and read back.
- **Stream types:** `/streamtypes` offers only `[0, 2]`; WBMS isn't available.
- **`/status`:** `transponder` and `converter` follow `enable`; `video_stream` is always started.
- **`/temperature`:** about 46.3 °C while idle, warming by up to 4 °C while pinging.
- **Frames flow only while enabled with the stream type set to RTSP (2).** Otherwise RTSP clients connect and wait, the same as on the real unit.
- **Frame format:** H.264, full-range 4:2:0 (`yuvj420p`), 256 beams wide, with the intensities in the luma plane.
  - **Rows:** about 67 per metre, capped at 1024: 200 at 3 m, 404 at 6 m, 1012 at 15 m. The height changes mid-stream when the range changes.
  - **Frame rate:** 187/12 (about 15.6) frames/s at 700 kHz, 12.6 at 400 kHz.
  - **Keyframes:** one every 30 frames, so a new client waits up to about 2 s for its first image.
- **Sector:** 120° at 700 kHz (HF, or AUTO up to 30 m) and 90° at 400 kHz (LF, LFLR, or AUTO beyond 30 m).

**Where the real unit's behaviour is unknown, the mock makes these assumptions:**
- An invalid `operation_mode`, `trigger_mode`, stream type or colormap index gets the same 400 error and isn't stored.
- `config` (gain, contrast) doesn't change the raw frames.
- `/network/static` and `/hostname` are stored, but the mock keeps listening where it was started.

## Mock-only endpoints

| Endpoint | What |
|---|---|
| `GET /mock/status` | RTSP URL, connected clients, whether frames are flowing, frames sent, frame shape, sector |
| `GET/PUT /mock/scene` | The simulated scene |

The default scene has:
- a strong target at 4 m, 25° to port
- a larger one at about 9 m, 10° to starboard, drifting ±0.5 m
- a grainy seabed from 12 m that starts further out at the sides
- a faint ring-down and noise, and a few random specks per frame

For example, to move the seabed in to 6 m:

```bash
curl -X PUT localhost:8001/mock/scene -H 'Content-Type: application/json' \
     -d '{"seabed_m": 6, "targets": [{"range_m": 3, "bearing_deg": 20}]}'
```

`PUT /mock/scene` replaces the whole scene. Any field you leave out returns to its default; the full list is in `/docs`.
