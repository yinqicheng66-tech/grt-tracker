"""A tiny Protocol Buffers encoder for building GTFS-rt test feeds."""
import struct


def varint(n: int) -> bytes:
    if n < 0:
        n += 1 << 64
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def int_field(number: int, value: int) -> bytes:
    return varint(number << 3) + varint(value)


def bytes_field(number: int, value) -> bytes:
    if isinstance(value, str):
        value = value.encode()
    return varint(number << 3 | 2) + varint(len(value)) + value


def float_field(number: int, value: float) -> bytes:
    return varint(number << 3 | 5) + struct.pack("<f", value)


def trip(trip_id, route_id="7", start_date="20261105", start_time="12:00:00", relationship=0) -> bytes:
    return (
        bytes_field(1, trip_id) + bytes_field(2, start_time) + bytes_field(3, start_date)
        + int_field(4, relationship) + bytes_field(5, route_id)
    )


def stop_update(seq, stop_id, time=None, delay=None, departure=True, relationship=0) -> bytes:
    event = b""
    if delay is not None:
        event += int_field(1, delay)
    if time is not None:
        event += int_field(2, time)
    body = int_field(1, seq) + bytes_field(4, stop_id) + int_field(5, relationship)
    body += bytes_field(3 if departure else 2, event)
    return body


def trip_update(trip_bytes, stops, timestamp=None, vehicle_id=None) -> bytes:
    body = bytes_field(1, trip_bytes) + b"".join(bytes_field(2, s) for s in stops)
    if vehicle_id is not None:
        body += bytes_field(3, bytes_field(1, vehicle_id))
    if timestamp is not None:
        body += int_field(4, timestamp)
    return body


def vehicle(trip_bytes, vehicle_id, lat, lon, seq, status, timestamp) -> bytes:
    position = float_field(1, lat) + float_field(2, lon)
    return (
        bytes_field(1, trip_bytes) + bytes_field(2, position) + int_field(3, seq)
        + int_field(4, status) + int_field(5, timestamp) + bytes_field(8, bytes_field(1, vehicle_id))
    )


def feed(timestamp, trip_updates=(), vehicles=()) -> bytes:
    header = bytes_field(1, "2.0") + int_field(3, timestamp)
    out = bytes_field(1, header)
    for i, tu in enumerate(trip_updates):
        out += bytes_field(2, bytes_field(1, f"tu{i}") + bytes_field(3, tu))
    for i, v in enumerate(vehicles):
        out += bytes_field(2, bytes_field(1, f"v{i}") + bytes_field(4, v))
    return out
