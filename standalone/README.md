# Standalone scripts (no ROS)

Plain Python scripts that talk to the sensors directly, without ROS. They're useful for quick hardware checks, and as known-good references that the ROS packages are compared against. They're intentionally independent of the ROS packages, so some small helpers are duplicated.

Run them from the repo root after `source ~/ros2_ws/src/rov-sensors/env.sh`, or in the `rov-sensors` conda environment:

```bash
conda create -n rov-sensors python=3.10          # one-time
conda run -n rov-sensors pip install -r requirements.txt
conda install -n rov-sensors -c conda-forge pygobject gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-libav   # for sonoptix_echo_reader.py
conda activate rov-sensors
```

| Script | What it does | Example |
|---|---|---|
| `omniscan450_reader.py` | Connects to the Omniscan 450 FS, configures it, prints one summary line per ping, and can log every ping to CSV or a SonarView `.svlog`. **Proven on the robot.** | `python standalone/omniscan450_reader.py --count 10` |
| `bluerov_camera_reader.py` | Opens the BlueOS camera stream (RTSP or RTP/UDP H.264) with OpenCV and shows it in a window | `python standalone/bluerov_camera_reader.py --udp-port 5600` |
| `sonoptix_echo_api.py` | Talks to the Sonoptix ECHO multibeam's REST API (`http://192.168.2.42:8000/api/v1`): prints its version, status, temperature and settings, saves its OpenAPI schema, enables/disables it, sets range and stream type. Control only, no sonar data yet. Uses only the standard library. | `python standalone/sonoptix_echo_api.py` |
| `sonoptix_echo_reader.py` | Switches the ECHO to its raw RTSP stream, decodes it with GStreamer and prints one line per frame (beams × samples, strongest return as distance and beam angle). Can save every frame to `.npz`. Restores the sonar's stream type, range, mode and power on exit. **Tested on the robot (in air).** | `python standalone/sonoptix_echo_reader.py --count 10` |
| `omniscan_10pings.csv` | 10 real pings recorded with `omniscan450_reader.py --csv` (sonar in air: ring-down plus noise, no echo) | reference data |

Useful options:
- `omniscan450_reader.py`: `--host 127.0.0.1` (talk to the mock: `ros2 run rov_omniscan omniscan_mock`), `--range 20`, `--gain 5`, `--listen-only` (don't change the sonar's settings), `--csv out.csv`, `--svlog logs/`.
- `sonoptix_echo_api.py`: `--openapi echo_api.json` (save the full API schema), `--range 5 --enable`, `--disable`, `--mode 0` (operation mode: 0 AUTO, 1 HF, 2 LF, 3 LFLR), `--stream-type 2` (0 WebRTC video, 1 WBMS over TCP, 2 RTSP), `--get /config`, `--patch /config '{"…": …}'`. `--enable` leaves the sonar pinging until `--disable`. The browsable API is at `http://192.168.2.42:8000/docs`.
- `sonoptix_echo_reader.py`: `--range M`, `--mode 2` (400 kHz), `--count N`, `--save frames.npz` (arrays `frames` [N, samples, beams] uint8, `host_time`, `range_m`, `fov_deg`), `--blank 0.3` (skip near range when finding the peak), `--listen-only`.

Sonoptix ECHO facts measured on this unit (firmware 1.5.0, serial 2448827):
- Allowed ranges, the same in every mode: 3, 6, 9, 12, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 125, 150, 175 and 200 m. Other values are silently changed to 3 m, and values below 3 or above 200 are rejected. The scripts only accept the allowed values.
- Frames are 256 beams wide, one column per beam. They have about 67 samples per metre (about 1.5 cm each), up to 1024 rows: 200 at 3 m, 404 at 6 m, 1012 at 15 m, 1024 at 30 m and beyond.
- The sector is 120° at 700 kHz (HF) and 90° at 400 kHz (LF/LFLR; AUTO beyond 30 m), per Blue Robotics' spec. Beam angles assume evenly spaced beams, which hasn't been verified.
- About 15.5 frames/s in HF and 12.6 in LF.
- This unit offers only the WebRTC and RTSP stream types; WBMS isn't available.

- `bluerov_camera_reader.py`: `--url rtsp://…` or `--udp-port N`, `--count N`, `--no-display`.

On exit, the sonar reader tells the sonar to stop pinging, unless `--listen-only` is set.

Known issue: `bluerov_camera_reader.py` fails with `numpy.core.multiarray failed to import`. Ubuntu's OpenCV (4.5.4) needs numpy 1.x, but numpy 2 is installed. The ROS camera node (`rov_camera`) doesn't use OpenCV, so it isn't affected.
