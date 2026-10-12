#!/usr/bin/env python3
"""Query and control a Sonoptix ECHO multibeam imaging sonar over its REST API.

The sonar sits on the BlueROV2's ethernet switch with a static IP
(default 192.168.2.42) and serves a FastAPI web API on port 8000, under
/api/v1. The full, browsable API is at http://<ip>:8000/docs. This script
only uses the control API; sonar data (rtsp://<ip>:8554/raw) is not read here.

Examples:
    python sonoptix_echo_api.py                          # print the sonar's current settings
    python sonoptix_echo_api.py --openapi echo_api.json  # save the firmware's full API schema
    python sonoptix_echo_api.py --range 6 --enable       # start pinging with a 6 m range
    python sonoptix_echo_api.py --mode 0                 # operation mode AUTO
    python sonoptix_echo_api.py --disable                # stop pinging
    python sonoptix_echo_api.py --get /config            # GET any endpoint
    python sonoptix_echo_api.py --patch /config '{"key": 1}'
"""

import argparse
import json
import urllib.error
import urllib.request

# Read-only endpoints printed by the default status query (ECHO API 1.5.0, see /openapi.json).
STATUS_ENDPOINTS = ("/version", "/serial", "/hostname", "/network/static", "/status", "/temperature",
                    "/transponder", "/streamtypes", "/streamtype", "/config",
                    "/colormap/names", "/colormap/index")

# Meaning of the integer codes, from the schema descriptions in /openapi.json.
STREAM_TYPES = {0: "Video (WebRTC)", 1: "WBMS (TCP)", 2: "RTSP"}
TRIGGER_MODES = {0: "internal", 1: "external, falling edge", 2: "external, rising edge"}
OPERATION_MODES = {0: "AUTO", 1: "HF (high frequency)", 2: "LF (low frequency)",
                   3: "LFLR (low frequency, long range)"}
COMPONENT_STATES = {0: "unknown", 1: "stopped", 2: "started", 3: "error"}

# sonar_range values the sonar keeps, the same in every operation mode (measured on firmware
# 1.5.0, 1-500 m). Other values up to 200 are silently changed to 3 m. Below 3 or above 200 get
# HTTP 400, yet the rejected value still reads back from GET /transponder, so never send them.
VALID_RANGES_M = (3, 6, 9, 12, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 125, 150, 175, 200)


class EchoApi:
    def __init__(self, host, port, timeout):
        self.root = f"http://{host}:{port}"
        self.timeout = timeout

    def request(self, method, path, body=None, prefix="/api/v1"):
        """Send one request and return the decoded JSON reply (or raw text, or None if empty)."""
        url = self.root + prefix + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode()
        except urllib.error.HTTPError as e:
            # FastAPI puts validation details in the body, so show it.
            raise SystemExit(f"{method} {url} -> HTTP {e.code}: {e.read().decode(errors='replace')}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            reason = getattr(e, "reason", e)
            raise SystemExit(f"No reply from Sonoptix ECHO at {url}: {reason}")
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


def describe(path, value):
    """Label the integer codes in a reply, e.g. operation_mode 1 -> "1 (HF (high frequency))"."""
    if not isinstance(value, dict):
        return value
    labels = {"/transponder": {"trigger_mode": TRIGGER_MODES, "operation_mode": OPERATION_MODES},
              "/streamtype": {"value": STREAM_TYPES},
              "/status": dict.fromkeys(("converter", "video_stream", "transponder"), COMPONENT_STATES)}
    out = dict(value)
    for key, names in labels.get(path, {}).items():
        if out.get(key) in names:
            out[key] = f"{out[key]} ({names[out[key]]})"
    if path == "/streamtypes" and isinstance(out.get("value"), list):
        out["value"] = [f"{v} ({STREAM_TYPES[v]})" if v in STREAM_TYPES else v for v in out["value"]]
    return out


def show(title, value, path=None):
    value = describe(path or title.split()[0], value)
    print(f"{title}:")
    print(json.dumps(value, indent=2) if not isinstance(value, str) else value)


def main():
    parser = argparse.ArgumentParser(description="Sonoptix ECHO REST API client")
    parser.add_argument("--host", default="192.168.2.42", help="sonar IP address")
    parser.add_argument("--port", type=int, default=8000, help="sonar API port")
    parser.add_argument("--timeout", type=float, default=3.0, help="request timeout (s)")
    parser.add_argument("--openapi", metavar="FILE", help="save the sonar's OpenAPI schema to FILE")
    on_off = parser.add_mutually_exclusive_group()
    on_off.add_argument("--enable", action="store_true", help="start the transponder (pinging)")
    on_off.add_argument("--disable", action="store_true", help="stop the transponder")
    parser.add_argument("--range", type=float, choices=VALID_RANGES_M, metavar="M",
                        help="set sonar_range (m): " + ", ".join(map(str, VALID_RANGES_M)))
    parser.add_argument("--mode", type=int, choices=OPERATION_MODES,
                        help="set operation_mode: " + ", ".join(f"{k} = {v}" for k, v in OPERATION_MODES.items()))
    parser.add_argument("--stream-type", type=int, choices=STREAM_TYPES,
                        help="set the data stream type: " + ", ".join(f"{k} = {v}" for k, v in STREAM_TYPES.items()))
    parser.add_argument("--get", metavar="PATH", help="GET any /api/v1 endpoint, e.g. /config")
    parser.add_argument("--patch", nargs=2, metavar=("PATH", "JSON"),
                        help="PATCH any /api/v1 endpoint with a JSON body")
    args = parser.parse_args()

    api = EchoApi(args.host, args.port, args.timeout)

    if args.openapi:
        schema = api.request("GET", "/openapi.json", prefix="")
        with open(args.openapi, "w") as f:
            json.dump(schema, f, indent=2)
        print(f"Saved OpenAPI schema ({len(schema.get('paths', {}))} endpoints) to {args.openapi}")

    if args.get:
        show(f"GET {args.get}", api.request("GET", args.get), args.get)
    if args.patch:
        path, body = args.patch
        show(f"PATCH {path}", api.request("PATCH", path, json.loads(body)), path)

    # Only send the transponder fields that were asked for.
    transponder = {}
    if args.enable or args.disable:
        transponder["enable"] = args.enable
    if args.range is not None:
        transponder["sonar_range"] = args.range
    if args.mode is not None:
        transponder["operation_mode"] = args.mode
    if transponder:
        api.request("PATCH", "/transponder", transponder)
        show("/transponder (updated)", api.request("GET", "/transponder"))
    if args.stream_type is not None:
        api.request("PUT", "/streamtype", {"value": args.stream_type})
        show("/streamtype (updated)", api.request("GET", "/streamtype"))
    if args.enable:
        print("\nThe sonar is left pinging. Stop it with --disable.")

    # No action requested: print the sonar's current settings.
    if not (args.openapi or args.get or args.patch or transponder or args.stream_type is not None):
        print(f"Sonoptix ECHO at {api.root} (API docs: {api.root}/docs)\n")
        for path in STATUS_ENDPOINTS:
            try:
                show(path, api.request("GET", path))
            except SystemExit as e:
                if str(e).startswith("No reply"):
                    raise
                # Endpoint missing or different on this firmware: report it and keep going.
                print(f"{path}: {e}")


if __name__ == "__main__":
    main()
