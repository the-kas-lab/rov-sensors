"""Run the mock sonar: python -m sonoptix_mock [--http-port 8001] [--rtsp-port 8554]"""

import argparse
import logging
import signal

import uvicorn

from .api import create_app


class _Server(uvicorn.Server):
    def handle_exit(self, sig, frame):
        # Ctrl-C reaches us twice under ros2 launch (terminal + launch forwarding); uvicorn
        # would treat the second as "force quit" and skip a clean shutdown.
        if self.should_exit and sig == signal.SIGINT:
            return
        super().handle_exit(sig, frame)


def main():
    parser = argparse.ArgumentParser(description="Mock Sonoptix ECHO multibeam imaging sonar")
    parser.add_argument("--host", default="0.0.0.0", help="interface for both servers")
    parser.add_argument("--http-port", type=int, default=8001,
                        help="REST API port (the real sonar uses 8000, but so does the Omniscan mock)")
    parser.add_argument("--rtsp-port", type=int, default=8554, help="RTSP port (as on the real sonar)")
    parser.add_argument("--serial", default="0000000", help="serial number to report")
    parser.add_argument("--no-rtsp", action="store_true", help="serve only the REST API")
    args, _ = parser.parse_known_args()  # tolerate --ros-args when started by ros2 launch

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    app = create_app(serial=args.serial, rtsp_host=args.host, rtsp_port=args.rtsp_port,
                     enable_rtsp=not args.no_rtsp)
    server = _Server(uvicorn.Config(app, host=args.host, port=args.http_port))
    try:
        server.run()
    except KeyboardInterrupt:  # uvicorn re-raises the Ctrl-C it handled; it already shut down
        pass


if __name__ == "__main__":
    main()
