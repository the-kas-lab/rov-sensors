# rov-sensors

Code for the **Cerulean Omniscan 450 FS** sonar on the BlueROV2 Heavy. It includes:
- a ROS 2 (Humble) driver
- a Foxglove display
- standalone (non-ROS) tools
- a mock sonar, so you can work without the hardware

The sonar is at `192.168.2.92` on the ROV's network. It uses the Blue Robotics Ping protocol on TCP port `51200`.

---

## How to run

**Run `source ~/ros2_ws/src/rov-sensors/env.sh` first, in every new terminal.** It sets up ROS, the project's Python environment and the workspace. Run the commands below from the `rov-sensors/` folder.

| I want to… | Command | Needs the sonar? |
|---|---|---|
| **See everything in Foxglove, no hardware** | `ros2 launch rov_sensors omniscan450.launch.py use_mock:=true use_foxglove_app:=true` | No |
| **See the real sonar in Foxglove** | `ros2 launch rov_sensors omniscan450.launch.py use_foxglove_app:=true` | Yes |
| Run only the ROS nodes (no Foxglove app) | `ros2 launch rov_sensors omniscan450.launch.py` (add `use_mock:=true` for no hardware) | Optional |
| Check that the sonar is reachable | `ping 192.168.2.92` | Yes |
| Print 10 real pings (original script, no ROS) | `python omniscan450_reader.py --count 10` | Yes |
| Record pings to CSV (no ROS) | `python omniscan450_reader.py --count 100 --csv out.csv` | Yes |
| Live matplotlib plot (no ROS) | `python omniscan450_plot.py` | Yes |
| Run only the mock sonar | `ros2 run rov_sensors omniscan_mock` | No |
| Use the non-ROS tools with the mock | start the mock, then add `--host 127.0.0.1` to the reader or plot command | No |
| Listen without changing the sonar's settings | add `--listen-only` (scripts) or `listen_only:=true` (node parameter) | Yes |

**Stop anything with Ctrl-C.** The driver and the reader send the sonar a stop command when they exit, so it isn't left transmitting. `--listen-only` is the exception: it never sends settings or stop commands.

Common launch options, for example `ros2 launch rov_sensors omniscan450.launch.py range_m:=20 gain_index:=5`:

| Argument | Default | Meaning |
|---|---|---|
| `use_mock` | `false` | start the mock sonar on this machine and connect to it |
| `host` | `192.168.2.92` | sonar IP (ignored when `use_mock:=true`) |
| `range_m` | `10.0` | how far the sonar listens (m) |
| `num_results` | `600` | samples per ping |
| `gain_index` | `-1` | receiver gain 0–7, `-1` = auto |
| `use_foxglove_app` | `false` | open the Foxglove desktop app, already connected |
| `use_bridge` | `true` | start `foxglove_bridge` |
| `bridge_port` | `8765` | Foxglove websocket port |

---

## First-time setup

```bash
# 1. Python environment with the packages ROS doesn't provide (brping, matplotlib, fastapi...)
cd ~/ros2_ws/src/rov-sensors
python3 -m venv --system-site-packages .venv && touch .venv/COLCON_IGNORE
.venv/bin/pip install -r requirements.txt

# 2. Build the two ROS packages
cd ~/ros2_ws
source src/rov-sensors/env.sh
colcon build --symlink-install --packages-select rov_sensors_interfaces rov_sensors

# 3. In Foxglove: Layouts → Import from file →
#    rov_sensors/config/omniscan450_foxglove_layout.json   (Foxglove remembers it)
```

**When to rebuild:** only after changing `OmniscanProfile.msg`, `setup.py` or `package.xml`. Python changes take effect without a rebuild, because of `--symlink-install`.

**Why `env.sh` is needed:** ROS node scripts run with the system `/usr/bin/python3`, which can't see packages inside a venv. `env.sh` adds the venv to `PYTHONPATH`. If you forget it, the driver stops with `brping not found`.

---

## Testing with the real sonar

1. **Check the network:** `ping 192.168.2.92` should get replies. Your computer needs an address on `192.168.2.x`.
2. **Use the original script first:** `python omniscan450_reader.py --count 10`. It's known to work on this robot, so if it fails, the problem is the hardware or network, not the ROS code.
3. **Then start ROS + Foxglove:** `ros2 launch rov_sensors omniscan450.launch.py use_foxglove_app:=true`

A healthy connection prints `Connected … device_type=104 rev=2 fw=1.5.6` and delivers about 58 pings/s at a 10 m range.

---

## What's in Foxglove

| Panel | Shows | How to read it |
|---|---|---|
| Current ping | `power_db` against `range_m` for the latest ping | Spike = object, and its x position is the distance. The bump near 0 m is ring-down, so ignore it. The low jagged line is the noise floor. |
| Waterfall | `/omniscan450/waterfall`, one row per ping, newest at the top | Bright continuous line = surface (seabed, wall). Short specks = fish or particles. |
| Strongest echo | `/omniscan450/peak.range` over time | Distance to the strongest echo beyond `peak_blank_m` |
| Parameters | `omniscan450` node parameters | Edit `range_m`, `gain_index`, `peak_blank_m`… live. The sonar is reconfigured immediately. |

If a panel is empty:
- **Current ping:** set the X axis to **Path (current)** using `/omniscan450/profile.range_m[:]`.
- **Waterfall:** set the topic to `/omniscan450/waterfall`.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `brping not found` | Run `source ~/ros2_ws/src/rov-sensors/env.sh` in that terminal. |
| `No route to host` / `ping 192.168.2.92` gets no reply | The sonar is off, unplugged from the ROV's switch, or still booting. If BlueOS at `192.168.2.2` replies but the sonar doesn't, check the sonar's power and cable. |
| `Connection refused … retrying in 2 s` once at startup with the mock | Normal: the driver started before the mock was ready. It connects on the next retry. |
| `address already in use` (51200 / 8000 / 8765) | A previous mock, bridge or launch is still running. Stop it first (`ss -ltnp` shows which process owns the port). |
| Foxglove app doesn't open: `bad option: --no-sandbox` | VS Code terminals set `ELECTRON_RUN_AS_NODE=1`. The launch file already clears it. To open Foxglove manually from a VS Code terminal, use `env -u ELECTRON_RUN_AS_NODE foxglove-studio`. |
| Only ring-down near 0 m plus noise, no echo anywhere | Nothing within range, or the ROV is **out of the water**: sound barely passes from the transducer into air. |
| `/omniscan450/peak` stays at about 0.2–0.25 m | That's the edge of the ring-down. On the real sonar it extends past the default `peak_blank_m` of 0.2, so raise it to about 0.4 in the Parameters panel. |
| The reader prints `peak … @ 0.02 m` but Foxglove shows a different distance | Expected. The original reader doesn't skip the ring-down, but the ROS node does. |

---

## Repository layout

```
rov-sensors/
├── README.md, env.sh, requirements.txt
├── omniscan450_reader.py      original reader: print / CSV (no ROS), proven on the robot
├── omniscan450_plot.py        live matplotlib profile + waterfall (no ROS)
├── omniscan_10pings.csv       10 real pings recorded from this sonar
├── check.ipynb
├── rov_sensors_interfaces/    ROS message package
│   └── msg/OmniscanProfile.msg      one ping: metadata + range_m[] + power_db[]
└── rov_sensors/               ROS package
    ├── rov_sensors/omniscan450_node.py      driver: sonar → ROS topics
    ├── rov_sensors/sonar_waterfall_node.py  profiles → waterfall image
    ├── omniscan_mock/                       fake sonar: Ping protocol + FastAPI (own README)
    ├── launch/omniscan450.launch.py
    └── config/omniscan450_foxglove_layout.json
```

```
sonar (or mock) ──TCP 51200──▶ omniscan450_node ──▶ /omniscan450/profile ──▶ sonar_waterfall_node ──▶ /omniscan450/waterfall
                                               └──▶ /omniscan450/peak
                                         all topics ──▶ foxglove_bridge :8765 ──▶ Foxglove
```

The original scripts at the root are intentionally **kept separate** from the ROS package, as a known-good reference until the ROS driver is validated on the robot. Because of that, a few helpers exist in both places.

---

## Reference

### Topics

| Topic | Type | Rate | Contents |
|---|---|---|---|
| `/omniscan450/profile` | `rov_sensors_interfaces/OmniscanProfile` | every ping (~58 Hz at 10 m) | ping metadata + `range_m[]`, `power_db[]` |
| `/omniscan450/peak` | `sensor_msgs/Range` | every ping | distance of the strongest echo beyond `peak_blank_m` |
| `/omniscan450/waterfall` | `sensor_msgs/Image` (rgb8) | `publish_rate` (10 Hz) | last `history` (300) pings, newest row on top |

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

This node is separate from the driver so it can run on the topside computer, keeping the images off the tether.

### Mock sonar

It produces synthetic pings with the same binary protocol as the real sonar on port 51200, and has an HTTP API on port 8000 for changing the simulated scene (seabed distance, targets, noise). See [rov_sensors/omniscan_mock/README.md](rov_sensors/omniscan_mock/README.md).

How it differs from the real unit:
- It reports `device_type=0`, `fw=1.0.0`. The real unit reports `104`, `1.5.6`.
- Its dB scale is fixed (−45 to 75). The real unit's scale changes from ping to ping.
