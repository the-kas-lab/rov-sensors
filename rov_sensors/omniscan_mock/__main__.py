"""Run the mock sonar: python -m omniscan_mock [--tcp-port 51200] [--http-port 8000]"""

import argparse
import logging

import uvicorn

from .api import create_app
from .device import Config


def main():
    env = Config.from_env()
    parser = argparse.ArgumentParser(description="Mock Cerulean Omniscan 450 FS sonar")
    parser.add_argument("--host", default=env.tcp_host, help="interface for both servers")
    parser.add_argument("--tcp-port", type=int, default=env.tcp_port,
                        help="Ping protocol port (the real sonar uses 51200)")
    parser.add_argument("--http-port", type=int, default=8000, help="FastAPI port")
    parser.add_argument("--no-autostart", action="store_true",
                        help="stay idle until a client sends os_ping_params")
    args, _ = parser.parse_known_args()  # tolerate --ros-args when started by ros2 launch

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    config = Config(tcp_host=args.host, tcp_port=args.tcp_port,
                    autostart=env.autostart and not args.no_autostart,
                    device_type=env.device_type, device_revision=env.device_revision,
                    firmware=env.firmware)
    uvicorn.run(create_app(config), host=args.host, port=args.http_port)


if __name__ == "__main__":
    main()
