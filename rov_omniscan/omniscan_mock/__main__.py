"""Run the mock sonar: python -m omniscan_mock [--tcp-port 51200] [--http-port 8000]"""

import argparse
import logging
import signal

import uvicorn

from .api import create_app
from .device import Config


class _Server(uvicorn.Server):
    def handle_exit(self, sig, frame):
        # Ctrl-C reaches us twice under ros2 launch (terminal + launch forwarding); uvicorn
        # would treat the second as "force quit" and skip a clean shutdown.
        if self.should_exit and sig == signal.SIGINT:
            return
        super().handle_exit(sig, frame)


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
    server = _Server(uvicorn.Config(create_app(config), host=args.host, port=args.http_port))
    try:
        server.run()
    except KeyboardInterrupt:  # uvicorn re-raises the Ctrl-C it handled; it already shut down
        pass


if __name__ == "__main__":
    main()
