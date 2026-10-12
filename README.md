# rov-sensors

ROS 2 (Humble) code for the BlueROV2 Heavy's sensors, viewed in **Foxglove**:

- **Sonar:** Cerulean Omniscan 450 FS at `192.168.2.92`. It uses the Blue Robotics Ping protocol on TCP `51200`.
- **Multibeam sonar:** Sonoptix ECHO at `192.168.2.42`. REST API on port `8000` (`/api/v1`, browsable at `/docs`); data as an H.264 RTSP stream at `rtsp://192.168.2.42:8554/raw`. The ROS driver is tested against the mock, not yet on the robot.
- **Camera:** the BlueROV USB camera, streamed by BlueOS as H.264 over RTSP or RTP/UDP.

Each sensor can be mocked, so everything can be developed and tested without the robot.

---

## How to run

**Run `source ~/ros2_ws/src/rov-sensors/env.sh` first, in every new terminal.** It sets up ROS, the project's Python environment and the workspace.

| I want to… | Command | Needs the robot? |
|---|---|---|
| **See sonar + multibeam + camera in Foxglove, no robot** | `ros2 launch rov_bringup rov.launch.py use_omniscan_mock:=true use_multibeam_mock:=true use_camera_mock:=true use_foxglove_app:=true` (camera = laptop webcam; another one: `camera_mock_device:=/dev/videoN`) | No |
| Real sonar, laptop webcam as the camera | `ros2 launch rov_bringup rov.launch.py use_camera_mock:=true use_foxglove_app:=true` | Sonar only |
| **See sonar + multibeam + camera in Foxglove, real robot** | `ros2 launch rov_bringup rov.launch.py use_foxglove_app:=true` (laptop must be `192.168.2.1`, see [Camera stream setup](#camera-stream-setup)) | Yes |
| Real robot, camera over RTSP instead | `ros2 launch rov_bringup rov.launch.py camera_url:=rtsp://192.168.2.2:8554/<name> use_foxglove_app:=true` | Yes |
| Sonar only | `ros2 launch rov_omniscan omniscan450.launch.py` (add `use_mock:=true` for no robot) | Optional |
| Multibeam only | `ros2 launch rov_sonoptix sonoptix_echo.launch.py` (add `use_mock:=true` for no robot, or `range_m:=15` etc.) | Optional |
| Camera only | `ros2 launch rov_camera camera.launch.py url:=rtsp://…` (or `use_mock:=true`) | Optional |
| Check that the sonar is reachable | `ping 192.168.2.92` | Yes |
| Print 10 sonar pings (standalone, no ROS) | `python standalone/omniscan450_reader.py --count 10` | Yes |
| Record sonar pings to CSV (standalone, no ROS) | `python standalone/omniscan450_reader.py --count 100 --csv out.csv` | Yes |
| Check that the multibeam is reachable | `ping 192.168.2.42` | Yes |
| Print the multibeam's settings (standalone, no ROS) | `python standalone/sonoptix_echo_api.py` | Yes |
| Print 10 multibeam frames (standalone, no ROS) | `python standalone/sonoptix_echo_reader.py --count 10` (in the `rov-sensors` conda env) | Yes |
| Save the multibeam's API schema | `python standalone/sonoptix_echo_api.py --openapi echo_api.json` | Yes |
| Show the camera with OpenCV (standalone, no ROS) | `python standalone/bluerov_camera_reader.py --udp-port 5600` | Yes |
| Run only the mock sonar | `ros2 run rov_omniscan omniscan_mock` | No |
| Run the mock multibeam (REST + RTSP) | `cd rov_sonoptix && python -m sonoptix_mock` (in the `rov-sensors` conda env), then use `--host 127.0.0.1 --port 8001` (reader: `--api-port 8001`) with the multibeam scripts | No |

**Stop anything with Ctrl-C.** The sonar drivers and readers send their sonar a stop command when they exit, so it isn't left transmitting. The multibeam driver also switches the ECHO back to its WebRTC video stream, for the web viewer. On shutdown the launch log shows `ffmpeg … exit code 255` when the camera mock was running. That's normal: ffmpeg always exits that way when interrupted.

### `rov_bringup rov.launch.py` arguments

| Argument | Default | Meaning |
|---|---|---|
| `use_omniscan_mock` | `false` | run the mock sonar on this machine instead of the real one |
| `use_multibeam_mock` | `false` | run the mock Sonoptix ECHO on this machine instead of the real one |
| `use_camera_mock` | `false` | stream a local webcam instead of the robot camera |
| `camera_mock_device` | `/dev/video0` | webcam used when `use_camera_mock:=true` |
| `use_sonar`, `use_multibeam`, `use_camera` | `true` | turn a sensor off |
| `sonar_host` | `192.168.2.92` | sonar IP |
| `multibeam_host` | `192.168.2.42` | multibeam IP |
| `camera_url` | *(empty)* | RTSP URL from BlueOS → Video Streams. Empty means UDP on `camera_udp_port`. |
| `camera_udp_port` | `5600` | port receiving the BlueOS RTP/H.264 stream |
| `use_foxglove_app` | `false` | open the Foxglove desktop app, already connected |
| `bridge_port` | `8765` | Foxglove websocket port |

The per-sensor launch files take more options, such as `range_m:=20 gain_index:=5` for the sonar, `range_m:=15 operation_mode:=2` for the multibeam and `max_width:=1280 publish_raw:=true` for the camera. See [Reference](#reference).

---

## First-time setup

```bash
# 1. Python environment with the packages ROS doesn't provide (brping, matplotlib, fastapi...)
cd ~/ros2_ws/src/rov-sensors
python3 -m venv --system-site-packages .venv && touch .venv/COLCON_IGNORE
.venv/bin/pip install -r requirements.txt

# 2. System packages for the camera (already installed on the dev laptop)
sudo apt install python3-gi gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav ffmpeg

# 3. Build the ROS packages
cd ~/ros2_ws
source src/rov-sensors/env.sh
colcon build --symlink-install --packages-select rov_interfaces rov_omniscan rov_sonoptix rov_camera rov_bringup

# 4. In Foxglove: Layouts → Import from file → rov_bringup/config/rov_foxglove_layout.json
#    (sonar-only layout: rov_omniscan/config/omniscan450_foxglove_layout.json,
#     multibeam-only: rov_sonoptix/config/sonoptix_echo_foxglove_layout.json)
```

**Standalone scripts only:** instead of `.venv`/`env.sh`, you can use a conda env: `conda create -n rov-sensors python=3.10 && conda run -n rov-sensors pip install -r requirements.txt`, then `conda activate rov-sensors`. The multibeam reader also needs GStreamer from conda-forge: see [standalone/README.md](standalone/README.md).

**When to rebuild:** only after changing a `.msg` file, `setup.py`, `package.xml` or adding files. Python changes take effect without a rebuild, because of `--symlink-install`.

**Why `env.sh` is needed:** ROS node scripts run with the system `/usr/bin/python3`, which can't see packages inside a venv. `env.sh` adds the venv to `PYTHONPATH`. If you forget it, the sonar driver stops with `brping not found`.

---

## Testing with the real robot

1. **Network:** your computer should be `192.168.2.1` (see [Camera stream setup](#camera-stream-setup)). `ping 192.168.2.2` (BlueOS) and `ping 192.168.2.92` (sonar) should both reply.
2. **Sonar, with the standalone script first:** `python standalone/omniscan450_reader.py --count 10`. It's known to work on this robot, so if it fails, the problem is hardware or network. Expect `Connected … device_type=104 rev=2 fw=1.5.6`.
3. **Multibeam, with the standalone script first:** `ping 192.168.2.42`, then `python standalone/sonoptix_echo_reader.py --count 10` (in the `rov-sensors` conda env). Expect `Frames: 256 beams x 404 samples for 6.0 m`.
4. **Everything:** `ros2 launch rov_bringup rov.launch.py use_foxglove_app:=true`. The multibeam node should log `Receiving frames: 256 beams x 404 samples, 6 m, HF, 120 deg`. The camera node should log `Receiving camera from udp port 5600: 1920x1080` within a few seconds.

### Camera stream setup

BlueOS (Mavlink Camera Manager) creates a 1080p30 H.264 **UDP** stream for each USB camera and **pushes** it to **`192.168.2.1:5600`**, the standard Blue Robotics topside address. Unlike the sonar, which answers whoever connects to it, this stream only reaches a computer with that exact address.

**Recommended (one-time):** give the tether adapter the standard address. It's stored in the adapter's network profile, so it applies every time you plug it in:

```bash
sudo nmcli con mod "Wired connection 2" ipv4.method manual ipv4.addresses 192.168.2.1/24
sudo nmcli con up "Wired connection 2"     # "Wired connection 2" = the USB tether adapter's profile
```

Then use the launch defaults (`camera_udp_port` 5600). QGC, BlueOS in the browser and the sonar keep working.

| Alternative | How | When |
|---|---|---|
| RTSP | BlueOS → Video Streams → switch the stream to RTSP and copy its URL (typically `rtsp://192.168.2.2:8554/<name>`). Pass `camera_url:=…`. | Any computer IP works, but QGC must then be pointed at the RTSP URL too |
| Extra UDP endpoint | BlueOS → Video Streams → edit `UDP Stream 0` → add `udp://<your IP>:5602`. Pass `camera_udp_port:=5602`. | A second topside computer, or QGC video at the same time (see below) |

**QGC video and the camera node can't share port 5600.** With QGC's video enabled at the same time, add the `udp://192.168.2.1:5602` endpoint above and launch with `camera_udp_port:=5602`.

---

## What's in Foxglove

The combined layout is `rov_bringup/config/rov_foxglove_layout.json`.

| Panel | Shows | How to read it |
|---|---|---|
| Camera | `/camera/image_raw/compressed` | the live camera |
| Sonar waterfall | `/omniscan450/waterfall`, one row per ping, newest at the top | Bright continuous line = surface (seabed, wall). Short specks = fish or particles. |
| Multibeam fan | `/sonoptix_echo/fan`, a top-down view of the latest frame | The transducer is at the bottom centre, straight ahead is up and starboard is right. The radius is `range_m` and the angle covers the sector (120° at 700 kHz, 90° at 400 kHz). Bright = strong echo. |
| Current ping | `power_db` against `range_m` for the latest ping | Spike = object, and its x position is the distance. The bump near 0 m is ring-down, so ignore it. The low jagged line is the noise floor. |
| Strongest echo | `/omniscan450/peak.range` over time | Distance to the strongest echo beyond `peak_blank_m` |
| Parameters | node parameters | Edit the sonar's `range_m`, `gain_index`, `peak_blank_m`… and the multibeam's `range_m`, `operation_mode` live. The sonars are reconfigured immediately. |

If a panel is empty:
- **Current ping:** set the X axis to **Path (current)** using `/omniscan450/profile.range_m[:]`.
- **Image panels:** check the topic in the panel settings.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `brping not found` | Run `source ~/ros2_ws/src/rov-sensors/env.sh` in that terminal. |
| Sonar: `No route to host` / `ping 192.168.2.92` gets no reply | The sonar is off, unplugged from the ROV's switch, or still booting. If BlueOS at `192.168.2.2` replies but the sonar doesn't, check the sonar's power and cable. |
| Camera: `no frames for 5 s (is the stream running and sent to this computer?)` | The stream goes to `192.168.2.1` and this computer has a different address (`ip -4 -br addr`), or QGC already holds port 5600. See [Camera stream setup](#camera-stream-setup). |
| Camera: `Could not open resource for reading and writing` | The RTSP URL is wrong or BlueOS isn't reachable. Copy the URL again from BlueOS → Video Streams. |
| `Connection refused … retrying in 2 s` once at startup with the mock | Normal: the driver started before the mock was ready. |
| `address already in use` (51200 / 8000 / 8765 / 5600) | A previous mock, bridge, launch or QGC is still running. `ss -lntup` shows which process owns the port. |
| Foxglove app doesn't open: `bad option: --no-sandbox` | VS Code terminals set `ELECTRON_RUN_AS_NODE=1`. The launch files clear it. To open Foxglove manually from a VS Code terminal, use `env -u ELECTRON_RUN_AS_NODE foxglove-studio`. |
| Multibeam: `no frames for 5 s (is the sonar enabled and its stream type RTSP?)` | Another client (the ECHO web viewer, `sonoptix_echo_api.py`) switched the stream type or disabled the sonar. The node re-enables it on its next retry, unless `listen_only` is set. Check with `python standalone/sonoptix_echo_api.py`. |
| Multibeam: `no reply from http://192.168.2.42:8000/api/v1` | The ECHO is off, unplugged or still booting: check `ping 192.168.2.42`. With the mock: it isn't running, or it uses another port. |
| Multibeam: `range_m must be one of 3, 6, 9, …` | The ECHO only keeps those ranges. Any other value would silently become 3 m, so the node refuses it. |
| Multibeam fan view updates only about once a second | The subscriber uses best-effort QoS. The fan images (about 830 kB) are published reliable, because most best-effort messages that size are lost in transport. Subscribe with reliable QoS; Foxglove does this automatically. |
| Sonar or multibeam shows only ring-down near 0 m plus noise | Nothing within range, or the ROV is **out of the water**: sound barely passes from the transducer into air. |
| `/omniscan450/peak` stays at about 0.2–0.25 m | That's the edge of the ring-down. On the real sonar it extends past the default `peak_blank_m` of 0.2, so raise it to about 0.4 in the Parameters panel. |
| The reader prints `peak … @ 0.02 m` but Foxglove shows a different distance | Expected. The original reader doesn't skip the ring-down, but the ROS node does. |
| `standalone/bluerov_camera_reader.py`: `numpy.core.multiarray failed to import` | Ubuntu's OpenCV 4.5.4 needs numpy 1.x, but numpy 2 is installed. The ROS camera node doesn't use OpenCV, so it isn't affected. |

---

## Repository layout

```
rov-sensors/
├── README.md, env.sh, requirements.txt
├── standalone/                scripts that work without ROS (see standalone/README.md)
│   ├── omniscan450_reader.py        sonar: print / CSV, proven on the robot
│   ├── bluerov_camera_reader.py     camera: OpenCV viewer
│   ├── sonoptix_echo_api.py         multibeam: REST API status / control
│   ├── sonoptix_echo_reader.py      multibeam: RTSP frames → print / .npz
│   └── omniscan_10pings.csv         10 real pings recorded from this sonar
│
├── rov_interfaces/    shared ROS messages for all ROV sensors
│   ├── msg/OmniscanProfile.msg      one sonar ping: metadata + range_m[] + power_db[]
│   └── msg/SonoptixFrame.msg        one multibeam frame: metadata + range_m[] + beam_angle_deg[] + intensity[]
├── rov_omniscan/               sonar package
│   ├── rov_omniscan/omniscan450_node.py      driver: sonar → ROS topics
│   ├── rov_omniscan/sonar_waterfall_node.py  profiles → waterfall image
│   ├── omniscan_mock/                       fake sonar: Ping protocol + FastAPI (own README)
│   ├── launch/omniscan450.launch.py
│   └── config/omniscan450_foxglove_layout.json
├── rov_sonoptix/              multibeam package
│   ├── rov_sonoptix/sonoptix_echo_node.py    driver: REST setup + RTSP frames → ROS topics
│   ├── rov_sonoptix/sonoptix_fan_node.py     frames → fan-shaped (top-down) image
│   ├── sonoptix_mock/                       fake ECHO: REST API mirror + RTSP frames (own README)
│   ├── launch/sonoptix_echo.launch.py
│   └── config/sonoptix_echo_foxglove_layout.json
├── rov_camera/                camera package
│   ├── rov_camera/bluerov_camera_node.py    BlueOS stream → JPEG frames (GStreamer)
│   └── launch/camera.launch.py              (use_mock:=true = laptop webcam via ffmpeg)
└── rov_bringup/               starts everything
    ├── launch/rov.launch.py                 sonar + multibeam + camera + one foxglove_bridge
    └── config/rov_foxglove_layout.json
```

```
sonar (or mock) ──TCP 51200──▶ omniscan450_node ──▶ /omniscan450/profile ──▶ sonar_waterfall_node ──▶ /omniscan450/waterfall
                                               └──▶ /omniscan450/peak
ECHO (or mock) ──REST :8000 + RTSP H.264 :8554──▶ sonoptix_echo_node ──▶ /sonoptix_echo/frame ──▶ sonoptix_fan_node ──▶ /sonoptix_echo/fan
                                                          └──▶ /sonoptix_echo/image
BlueOS camera (or mock) ──RTSP / RTP-UDP H.264──▶ bluerov_camera_node ──▶ /camera/image_raw/compressed
                                         all topics ──▶ foxglove_bridge :8765 ──▶ Foxglove
```

The camera only uses standard ROS messages. Any custom messages a sensor needs go in `rov_interfaces`.

**Why the multibeam uses its own message:** the community standard for imaging sonars is `marine_acoustic_msgs/ProjectedSonarImage`, which is installed with Jazzy. `SonoptixFrame` follows this repo's own pattern instead, like `OmniscanProfile`:
- It carries the range and beam axes as plain arrays (`range_m[]`, `beam_angle_deg[]`), so Foxglove can plot them directly.
- It keeps sonar-specific fields (`operation_mode`, `sector_deg`).

If outside tools need the multibeam data (e.g. `sonar_image_proc`), publishing a `ProjectedSonarImage` alongside it is a small addition to `sonoptix_echo_node`: one beam direction vector per `beam_angle_deg`, `ranges` = `range_m`, and `intensity` as `DTYPE_UINT8` image data.

The scripts in `standalone/` are intentionally **kept separate** from the ROS packages, as known-good references until the ROS nodes are validated on the robot.

---

## Reference

### Topics

| Topic | Type | Rate | Contents |
|---|---|---|---|
| `/omniscan450/profile` | `rov_interfaces/OmniscanProfile` | every ping (~58 Hz at 10 m) | ping metadata + `range_m[]`, `power_db[]` |
| `/omniscan450/peak` | `sensor_msgs/Range` | every ping | distance of the strongest echo beyond `peak_blank_m` |
| `/omniscan450/waterfall` | `sensor_msgs/Image` (rgb8) | `publish_rate` (10 Hz) | last `history` (300) pings, newest row on top |
| `/sonoptix_echo/frame` | `rov_interfaces/SonoptixFrame` | every frame (~15.6 Hz at 700 kHz, 12.6 Hz at 400 kHz) | frame metadata + `range_m[]`, `beam_angle_deg[]`, `intensity[]` (samples × 256 beams, uint8), about 260 kB |
| `/sonoptix_echo/image` | `sensor_msgs/Image` (mono8) | every frame | the same frame as an image: rows = range (nearest at the top), columns = beams (port on the left) |
| `/sonoptix_echo/fan` | `sensor_msgs/Image` (rgb8), **reliable** QoS | `publish_rate` (10 Hz) | top-down fan view of the latest frame, about 830 kB at the default 400 px |
| `/camera/image_raw/compressed` | `sensor_msgs/CompressedImage` (jpeg) | camera rate (30 Hz) | camera frames, about 60 kB each at 1080p |
| `/camera/image_raw` | `sensor_msgs/Image` (rgb8) | camera rate | only with `publish_raw:=true`. About 6 MB/frame at 1080p, so use it for local vision nodes only. |

### `omniscan450_node` parameters

| Parameter | Default | Live? | Meaning |
|---|---|---|---|
| `host`, `port` | `192.168.2.92`, `51200` | on reconnect | sonar address |
| `start_m`, `range_m` | `0.0`, `10.0` | ✅ | scan window (m) |
| `num_results` | `600` | ✅ | samples per ping |
| `gain_index` | `-1` | ✅ | 0–7, `-1` = auto |
| `ping_rate_hz` | `0` | ✅ | `0` = as fast as the range allows |
| `listen_only` | `false` | no | don't configure the sonar, just publish what it sends |
| `frame_id` | `omniscan450_link` | ✅ | TF frame of the transducer |
| `peak_blank_m` | `0.2` | ✅ | ignore this much ring-down when finding the peak |

The driver retries its connection every 2 s. On shutdown it sends the sonar a stop command, unless `listen_only` is set.

### `sonar_waterfall_node` parameters

| Parameter | Default | Meaning |
|---|---|---|
| `history` | `300` | pings (rows) kept in the image |
| `publish_rate` | `10.0` | images per second. Every ping still gets a row; this only limits bandwidth (about 540 kB per image). |
| `colormap` | `inferno` | any matplotlib colormap |
| `min_db`, `max_db` | NaN | colour scale. NaN = use the scale the sonar reports. |

### `sonoptix_echo_node` parameters

| Parameter | Default | Live? | Meaning |
|---|---|---|---|
| `host`, `api_port`, `rtsp_port` | `192.168.2.42`, `8000`, `8554` | on reconnect | sonar address: REST API and RTSP stream |
| `range_m` | `6.0` | ✅ | 3, 6, 9, 12, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 125, 150, 175 or 200 m. The sonar silently changes any other value to 3 m, so the node refuses it. |
| `operation_mode` | `1` | ✅ | `0` AUTO (700 kHz up to 30 m, 400 kHz beyond), `1` HF (700 kHz), `2` LF (400 kHz), `3` LFLR |
| `listen_only` | `false` | no | don't configure the sonar, just publish what it streams |
| `frame_id` | `sonoptix_echo_link` | ✅ | TF frame of the transducer |
| `rtsp_latency_ms` | `50` | on reconnect | RTSP jitter buffer |
| `stall_timeout_s` | `5.0` | on reconnect | reconnect if no frame arrives for this long |

**How it works:**
- **Startup:** the driver enables the sonar, sets the range and mode, and switches the stream type to RTSP. It then decodes the H.264 stream in GStreamer, keeping the luma plane as the intensity matrix. The connection is retried every 2 s.
- **Settings:** it re-reads range and mode every 2 s, so the axes stay right if another client changes them.
- **Shutdown:** it disables the sonar and switches it back to the WebRTC video stream, unless `listen_only` is set.
- **Assumptions:** beam angles assume evenly spaced beams across the sector (120° at 700 kHz, 90° at 400 kHz, from Blue Robotics' spec). `frequency_hz` is inferred from the mode and range, because the API doesn't report it.
- **Requirements:** it runs on the system Python with the same GStreamer packages as the camera node.

### `sonoptix_fan_node` parameters

| Parameter | Default | Meaning |
|---|---|---|
| `image_height` | `400` | pixels from the transducer to the end of the range. The width follows the sector: about 1.7 × the height at 120°, 1.4 × at 90°. |
| `publish_rate` | `10.0` | images per second. This only limits bandwidth. |
| `colormap` | `inferno` | any matplotlib colormap |
| `min_intensity`, `max_intensity` | `0`, `255` | colour scale. Lower `max_intensity` to brighten weak echoes. |

### `bluerov_camera_node` parameters

| Parameter | Default | Meaning |
|---|---|---|
| `url` | *(empty)* | RTSP URL. Empty means UDP on `udp_port`. |
| `udp_port` | `5600` | port receiving RTP/H.264 (payload 96) |
| `frame_id` | `camera_link` | TF frame of the camera |
| `jpeg_quality` | `80` | 1–100 |
| `max_width` | `0` | downscale to this width, keeping the aspect ratio. `0` = full resolution. |
| `publish_raw` | `false` | also publish uncompressed `/camera/image_raw` |
| `rtsp_latency_ms` | `50` | RTSP jitter buffer |
| `stall_timeout_s` | `5.0` | restart the stream if no frame arrives for this long |

Decoding and JPEG encoding run inside GStreamer, so the node doesn't use OpenCV or numpy. After an error or stall it restarts the stream every 2 s, so it can start before the robot is reachable. Timestamps are the time each frame arrived on this computer, not the camera's exposure time.

### Mocks

- **Sonar:** synthetic pings with the same binary protocol as the real sonar on port 51200. An HTTP API on port 8000 changes the simulated scene (seabed distance, targets, noise). See [rov_omniscan/omniscan_mock/README.md](rov_omniscan/omniscan_mock/README.md).
  - It reports `device_type=0`, `fw=1.0.0`; the real unit reports `104`, `1.5.6`.
  - Its dB scale is fixed (−45 to 75); the real unit's changes from ping to ping.
- **Multibeam:** mirrors the Sonoptix ECHO's REST API (firmware 1.5.0) on port 8001 and streams synthetic H.264 frames on `rtsp://…:8554/raw` in the same format, sizes and rates as the real unit. `/mock/scene` changes the simulated targets and seabed. See [rov_sonoptix/sonoptix_mock/README.md](rov_sonoptix/sonoptix_mock/README.md).
  - Its REST port is 8001, not the real sonar's 8000, because the Omniscan mock uses 8000. `sonoptix_echo.launch.py use_mock:=true` points the driver there.
  - Under `ros2 launch` it needs fastapi/uvicorn (from the `.venv`) and GStreamer's RTSP server and x264 encoder for the system Python (`sudo apt install gir1.2-gst-rtsp-server-1.0 gstreamer1.0-plugins-ugly`). Without them it serves only the REST API, and the driver keeps waiting for frames. It runs fully in the `rov-sensors` conda env: `cd rov_sonoptix && python -m sonoptix_mock`.
- **Camera:** `ffmpeg` encodes a local source to H.264 and sends it over RTP/UDP to `127.0.0.1:<udp_port>`, the same format BlueOS sends. The node therefore runs exactly the same receive and decode path it uses on the robot.
  - Source: the webcam given by `mock_device` (default `/dev/video0`; list them with `ls /dev/video*`), at 1280×720, 30 fps, so vision code sees real images.
  - If the webcam is already in use by another app (a video call, Cheese…), ffmpeg exits and the camera node reports `no frames`.
