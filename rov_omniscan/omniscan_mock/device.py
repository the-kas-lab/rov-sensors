"""The emulated sonar: a Ping-protocol TCP server plus the ping loop that feeds it.

Behaves like the real Omniscan 450 FS on its TCP port:
  * answers general_request for device_information, protocol_version,
    sync_channel_number and os_mono_profile (a single ping)
  * accepts os_ping_params, set_speed_of_sound and set_sync_channel_number
  * while pinging is enabled, streams os_mono_profile to every connected client
Ping state is shared by all clients, as on the real device: one client
disabling pinging stops it for everyone.
"""

import asyncio
import logging
import os
from dataclasses import dataclass

from . import protocol
from .simulator import PingParams, Simulator

log = logging.getLogger("omniscan_mock")

# Drop a TCP client whose unsent backlog grows past this (it stopped reading).
MAX_CLIENT_BACKLOG = 4 * 1024 * 1024


@dataclass
class Config:
    tcp_host: str = "0.0.0.0"
    tcp_port: int = 51200
    autostart: bool = True          # ping from boot with default params, so --listen-only works
    device_type: int = 0
    device_revision: int = 1
    firmware: tuple = (1, 0, 0)

    @classmethod
    def from_env(cls):
        fw = os.environ.get("OMNISCAN_MOCK_FIRMWARE", "1.0.0")
        return cls(
            tcp_host=os.environ.get("OMNISCAN_MOCK_TCP_HOST", cls.tcp_host),
            tcp_port=int(os.environ.get("OMNISCAN_MOCK_TCP_PORT", cls.tcp_port)),
            autostart=os.environ.get("OMNISCAN_MOCK_AUTOSTART", "1") not in ("0", "false", "no"),
            device_type=int(os.environ.get("OMNISCAN_MOCK_DEVICE_TYPE", cls.device_type)),
            device_revision=int(os.environ.get("OMNISCAN_MOCK_DEVICE_REVISION", cls.device_revision)),
            firmware=tuple(int(x) for x in fw.split(".")),
        )


class MockSonar:
    def __init__(self, config):
        self.config = config
        self.sim = Simulator()
        self.sim.params = PingParams(length_mm=10000, enable=config.autostart)
        self.sync_channel = (0, 1)
        self.latest = None              # (fields, raw) of the most recent ping
        self._clients = set()           # StreamWriters of connected TCP clients
        self._subscribers = set()       # asyncio.Queues of WebSocket listeners
        self._enabled = asyncio.Event()
        self._wake = asyncio.Event()    # interrupts the ping loop's sleep on reconfigure
        self._server = None
        self._loop_task = None
        if config.autostart:
            self._enabled.set()

    # -- lifecycle ---------------------------------------------------------

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle_client, self.config.tcp_host, self.config.tcp_port)
        self._loop_task = asyncio.create_task(self._ping_loop())
        log.info("Ping protocol server listening on %s:%d", self.config.tcp_host, self.config.tcp_port)

    async def stop(self):
        self._loop_task.cancel()
        self._server.close()
        for writer in list(self._clients):
            writer.close()
        await self._server.wait_closed()

    @property
    def tcp_client_count(self):
        return len(self._clients)

    # -- control (shared by TCP clients and the HTTP API) -------------------

    def set_ping_params(self, params):
        self.sim.set_params(params)
        if params.enable:
            self._enabled.set()
        else:
            self._enabled.clear()
        self._wake.set()

    def set_speed_of_sound(self, mps):
        self.sim.speed_of_sound_mps = mps

    def single_ping(self):
        fields, raw = self.sim.ping()
        self.latest = fields, raw
        return fields, raw

    def subscribe(self, maxsize=8):
        queue = asyncio.Queue(maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue):
        self._subscribers.discard(queue)

    # -- ping loop ---------------------------------------------------------

    async def _ping_loop(self):
        loop = asyncio.get_running_loop()
        next_t = loop.time()
        while True:
            if not self._enabled.is_set():
                await self._enabled.wait()
                next_t = loop.time()

            fields, raw = self.single_ping()
            self._broadcast(encode_profile(fields, raw), (fields, raw))

            interval = max(self.sim.params.msec_per_ping / 1000.0, self.sim.min_ping_interval_s())
            next_t = max(next_t + interval, loop.time())  # don't burst to catch up after a stall
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), next_t - loop.time())
                next_t = loop.time()  # reconfigured: ping with the new params right away
            except asyncio.TimeoutError:
                pass

    def _broadcast(self, frame, ping):
        for writer in list(self._clients):
            if writer.transport.get_write_buffer_size() > MAX_CLIENT_BACKLOG:
                log.warning("Dropping TCP client %s: not reading", writer.get_extra_info("peername"))
                writer.close()
                self._clients.discard(writer)
                continue
            writer.write(frame)
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()  # slow WebSocket: drop its oldest ping
            queue.put_nowait(ping)

    # -- TCP / Ping protocol ------------------------------------------------

    async def _handle_client(self, reader, writer):
        peer = writer.get_extra_info("peername")
        log.info("TCP client connected: %s", peer)
        self._clients.add(writer)
        parser = protocol.Parser()
        try:
            while data := await reader.read(4096):
                for message_id, payload in parser.feed(data):
                    reply = self._handle_message(message_id, payload)
                    if reply:
                        writer.write(reply)
        except ConnectionError:
            pass
        finally:
            self._clients.discard(writer)
            writer.close()
            log.info("TCP client disconnected: %s", peer)

    def _handle_message(self, message_id, payload):
        fmt = protocol.FORMATS.get(message_id)
        if fmt is None or len(payload) < fmt.size:
            return nack(message_id, "unknown or malformed message")
        values = fmt.unpack_from(payload)

        if message_id == protocol.GENERAL_REQUEST:
            return self._handle_request(values[0])

        if message_id == protocol.OS_PING_PARAMS:
            raw = dict(zip(protocol.OS_PING_PARAMS_FIELDS, values))
            try:
                params = PingParams(**{k: raw[k] for k in PingParams.model_fields})
            except ValueError as e:
                return nack(message_id, str(e).splitlines()[0])
            log.info("os_ping_params from client: %s", params.model_dump())
            self.set_ping_params(params)
        elif message_id == protocol.SET_SPEED_OF_SOUND:
            self.set_speed_of_sound(values[0] / 1000.0)  # sent in mm/s
        elif message_id == protocol.SET_SYNC_CHANNEL_NUMBER:
            self.sync_channel = values
        else:
            return nack(message_id, "not a command")
        return protocol.pack_fields(protocol.ACK, message_id)

    def _handle_request(self, requested_id):
        if requested_id == protocol.DEVICE_INFORMATION:
            c = self.config
            return protocol.pack_fields(protocol.DEVICE_INFORMATION,
                                        c.device_type, c.device_revision, *c.firmware, 0)
        if requested_id == protocol.PROTOCOL_VERSION:
            return protocol.pack_fields(protocol.PROTOCOL_VERSION, 1, 0, 0, 0)
        if requested_id == protocol.SYNC_CHANNEL_NUMBER:
            return protocol.pack_fields(protocol.SYNC_CHANNEL_NUMBER, *self.sync_channel)
        if requested_id == protocol.OS_MONO_PROFILE:
            return encode_profile(*self.single_ping())
        return nack(requested_id, "unsupported request")


def encode_profile(fields, raw):
    header = protocol.FORMATS[protocol.OS_MONO_PROFILE].pack(
        *(fields[k] for k in protocol.OS_MONO_PROFILE_FIELDS))
    return protocol.pack(protocol.OS_MONO_PROFILE, header + raw.tobytes())


def nack(message_id, text):
    return protocol.pack(protocol.NACK, protocol.FORMATS[protocol.ACK].pack(message_id) + text.encode())
