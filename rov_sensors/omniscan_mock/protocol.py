"""Minimal Blue Robotics Ping protocol framing, as spoken by the Omniscan 450.

Wire format (little endian):
    "B" "R" | payload_length u16 | message_id u16 | src_id u8 | dst_id u8 | payload | checksum u16
The checksum is the sum of every preceding byte, truncated to 16 bits.

Message ids and payload layouts mirror brping/definitions.py so the real
brping client can't tell the mock from the sonar.
"""

import struct

# Common messages
ACK = 1
NACK = 2
DEVICE_INFORMATION = 4
PROTOCOL_VERSION = 5
GENERAL_REQUEST = 6

# Omniscan 450 messages
SET_SPEED_OF_SOUND = 116
SYNC_CHANNEL_NUMBER = 169
SET_SYNC_CHANNEL_NUMBER = 170
OS_PING_PARAMS = 2197
OS_MONO_PROFILE = 2198

# Fixed-size payload layouts (the mono profile is followed by a u16 array).
FORMATS = {
    ACK: struct.Struct("<H"),
    GENERAL_REQUEST: struct.Struct("<H"),
    DEVICE_INFORMATION: struct.Struct("<BBBBBB"),
    PROTOCOL_VERSION: struct.Struct("<BBBB"),
    SET_SPEED_OF_SOUND: struct.Struct("<I"),
    SYNC_CHANNEL_NUMBER: struct.Struct("<BB"),
    SET_SYNC_CHANNEL_NUMBER: struct.Struct("<BB"),
    OS_PING_PARAMS: struct.Struct("<IIIffffhHBBBB"),
    OS_MONO_PROFILE: struct.Struct("<IIIIIHHHBBffffff"),
}

OS_PING_PARAMS_FIELDS = (
    "start_mm", "length_mm", "msec_per_ping", "reserved_1", "reserved_2",
    "pulse_len_percent", "filter_duration_percent", "gain_index", "num_results",
    "enable", "reserved_3", "reserved_4", "reserved_5",
)
OS_MONO_PROFILE_FIELDS = (
    "ping_number", "start_mm", "length_mm", "timestamp_ms", "ping_hz", "gain_index",
    "num_results", "sos_dmps", "channel_number", "reserved", "pulse_duration_sec",
    "analog_gain", "max_pwr_db", "min_pwr_db", "transducer_heading_deg",
    "vehicle_heading_deg",
)

_HEADER = struct.Struct("<2sHHBB")
_CHECKSUM = struct.Struct("<H")
# Anything claiming a bigger payload is a corrupt header, not a real message.
MAX_PAYLOAD = 0xFFFF


def pack(message_id, payload=b"", src_id=1, dst_id=0):
    """Frame a payload as a complete Ping message."""
    body = _HEADER.pack(b"BR", len(payload), message_id, src_id, dst_id) + payload
    return body + _CHECKSUM.pack(sum(body) & 0xFFFF)


def pack_fields(message_id, *values, **kwargs):
    return pack(message_id, FORMATS[message_id].pack(*values), **kwargs)


class Parser:
    """Incremental decoder: feed() raw bytes, get back (message_id, payload) tuples."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data):
        self._buf += data
        messages = []
        while True:
            start = self._buf.find(b"BR")
            if start < 0:
                # Keep a trailing "B" in case the "R" arrives in the next chunk.
                del self._buf[:-1]
                return messages
            del self._buf[:start]
            if len(self._buf) < _HEADER.size:
                return messages
            _, length, message_id, _, _ = _HEADER.unpack_from(self._buf)
            total = _HEADER.size + length + _CHECKSUM.size
            if len(self._buf) < total:
                return messages
            body = bytes(self._buf[:total - _CHECKSUM.size])
            (checksum,) = _CHECKSUM.unpack_from(self._buf, total - _CHECKSUM.size)
            if checksum != sum(body) & 0xFFFF:
                # Bad frame: skip this sync marker and resynchronise.
                del self._buf[:2]
                continue
            del self._buf[:total]
            messages.append((message_id, body[_HEADER.size:]))
