# Omniscan 450 FS mock

Emulates the Cerulean Omniscan 450 FS so you can develop without the sonar or the ROV.

One process runs two servers:

| Port  | What                         | Who uses it                                            |
|-------|------------------------------|--------------------------------------------------------|
| 51200 | Ping protocol over TCP       | `standalone/omniscan450_reader.py`, brping, SonarView… |
| 8000  | FastAPI (HTTP + WebSocket)   | You: JSON profiles, change ping params or the scene    |

The TCP side uses the same binary Blue Robotics Ping protocol as the real sonar
(the same message ids, payload layouts and checksums), so existing code only
needs the IP address changed.

## Run

```bash
source ~/ros2_ws/src/rov-sensors/env.sh   # venv with fastapi etc. (see ../../README.md)
ros2 run rov_omniscan omniscan_mock        # once the workspace is built
# or, without building:  cd rov-sensors/rov_omniscan && python -m omniscan_mock
```

`ros2 launch rov_omniscan omniscan450.launch.py use_mock:=true` starts it for you
together with the ROS driver and Foxglove bridge.

Options: `--tcp-port`, `--http-port`, `--host`, `--no-autostart`.
You can also run it with `uvicorn omniscan_mock.api:app --port 8000` (from `rov_omniscan/`). In that case,
set the TCP side with environment variables: `OMNISCAN_MOCK_TCP_PORT`,
`OMNISCAN_MOCK_TCP_HOST`, `OMNISCAN_MOCK_AUTOSTART=0`, `OMNISCAN_MOCK_DEVICE_TYPE`,
`OMNISCAN_MOCK_DEVICE_REVISION`, `OMNISCAN_MOCK_FIRMWARE=1.0.0`.

Then point the existing reader at it:

```bash
python standalone/omniscan450_reader.py --host 127.0.0.1
python standalone/omniscan450_reader.py --host 127.0.0.1 --range 20 --csv out.csv
```

## Behaviour

- Answers `general_request` for `device_information`, `protocol_version`,
  `sync_channel_number` and `os_mono_profile`.
- Accepts `os_ping_params`, `set_speed_of_sound` and `set_sync_channel_number`.
- While pinging is enabled, streams `os_mono_profile` to every TCP client. The
  ping rate is limited by the two-way travel time, about 58 Hz at 10 m. That
  matches the ~17 ms spacing in `standalone/omniscan_10pings.csv`.
- By default the mock starts pinging as soon as it boots (10 m range, 600
  samples), so `--listen-only` works straight away.
- Ping state is shared, the same as on the real device. When the reader exits
  without `--listen-only`, it sends `enable=False`, which stops pinging for
  everyone. Turn it back on with `PUT /ping_params` or run the reader again.
- Auto gain (`gain_index=-1`) steps 0 → 4 → 5 → 6 → 7, the same as the real unit in
  the capture.

## How the synthetic profiles are made

These are modelled on the real capture:
- Transducer ring-down of about 61 dB in the first few centimetres.
- A noise floor around -16 dB at gain 0 that rises 3 dB per gain step.
- A seabed echo at about 6 m that moves ±0.4 m (as if the ROV heaves), with a
  reverberation tail behind it.
- Random clutter echoes, such as fish or particles.

You can change all of this through `/scene`.

The `min_pwr_db`/`max_pwr_db` scaling (-45 to 75 dB), `device_type` (0) and the
firmware version are placeholders. If your code depends on them, set them to
the real unit's values.

## HTTP API

Interactive docs are at http://localhost:8000/docs.

| Method | Path                | Description                                                |
|--------|---------------------|------------------------------------------------------------|
| GET    | `/status`           | TCP clients, whether it is pinging, ping counter, device info |
| GET/PUT| `/ping_params`      | Same fields as `control_os_ping_params()`                  |
| PUT    | `/speed_of_sound`   | `{"speed_of_sound_mps": 1480}`                             |
| GET/PUT| `/scene`            | Simulated environment: targets, noise, ring-down, clutter  |
| GET    | `/profile/latest`   | Last profile as JSON (`?include_raw=true` adds the u16 `pwr_results`) |
| POST   | `/profile/ping`     | Fire one ping and return it                                |
| WS     | `/ws/profiles`      | Push every profile as JSON                                 |

Each profile JSON has every `os_mono_profile` field, plus `pwr_db` (already
scaled to dB) and `distances_m`. These are the same values `standalone/omniscan450_reader.py`
calculates.

Examples:

```bash
# 20 m range, 1200 samples, fixed gain 3
curl -X PUT localhost:8000/ping_params -H 'content-type: application/json' \
     -d '{"length_mm": 20000, "num_results": 1200, "gain_index": 3}'

# Seabed at 8 m plus a strong target at 3 m, no clutter, reproducible noise
curl -X PUT localhost:8000/scene -H 'content-type: application/json' -d '{
  "targets": [
    {"distance_m": 8, "strength_db": 45, "width_m": 0.08, "tail_m": 1.5},
    {"distance_m": 3, "strength_db": 40, "drift_amplitude_m": 0.5, "drift_period_s": 5}
  ],
  "clutter_per_ping": 0, "seed": 1}'
```

## Files

- `protocol.py`: Ping message framing, parsing and the message ids/layouts
- `simulator.py`: generates the profiles, plus the `Scene`/`PingParams` models
- `device.py`: the emulated sonar (TCP server, ping loop, command handling)
- `api.py`: the FastAPI app, which starts the TCP server in its lifespan
