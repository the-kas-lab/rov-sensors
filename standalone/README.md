# Standalone scripts (no ROS)

Plain Python scripts that talk to the sensors directly, without ROS. They're useful for quick hardware checks, and as known-good references that the ROS packages are compared against. They're intentionally independent of the ROS packages, so some small helpers are duplicated.

Run them from the repo root after `source ~/ros2_ws/src/rov-sensors/env.sh`.

| Script | What it does | Example |
|---|---|---|
| `omniscan450_reader.py` | Connects to the Omniscan 450 FS, configures it, prints one summary line per ping, and can log every ping to CSV or a SonarView `.svlog`. **Proven on the robot.** | `python standalone/omniscan450_reader.py --count 10` |
| `bluerov_camera_reader.py` | Opens the BlueOS camera stream (RTSP or RTP/UDP H.264) with OpenCV and shows it in a window | `python standalone/bluerov_camera_reader.py --udp-port 5600` |
| `omniscan_10pings.csv` | 10 real pings recorded with `omniscan450_reader.py --csv` (sonar in air: ring-down plus noise, no echo) | reference data |

Useful options:
- `omniscan450_reader.py`: `--host 127.0.0.1` (talk to the mock: `ros2 run rov_omniscan omniscan_mock`), `--range 20`, `--gain 5`, `--listen-only` (don't change the sonar's settings), `--csv out.csv`, `--svlog logs/`.
- `bluerov_camera_reader.py`: `--url rtsp://…` or `--udp-port N`, `--count N`, `--no-display`.

On exit, the sonar reader tells the sonar to stop pinging, unless `--listen-only` is set.

Known issue: `bluerov_camera_reader.py` fails with `numpy.core.multiarray failed to import`. Ubuntu's OpenCV (4.5.4) needs numpy 1.x, but numpy 2 is installed. The ROS camera node (`rov_camera`) doesn't use OpenCV, so it isn't affected.
