"""Decode GTFS Realtime feeds with the standard library only.

GTFS Realtime is a Protocol Buffers format
(https://gtfs.org/documentation/realtime/proto/). Instead of depending on the
protobuf package, this module reads the wire format directly and keeps only
the fields the tracker uses. Field numbers below come from gtfs-realtime.proto.
"""
import struct
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

# Protocol Buffers wire types
VARINT, I64, LEN, I32 = 0, 1, 2, 5


class DecodeError(ValueError):
    pass


def read_varint(data: bytes, pos: int) -> Tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(data):
            raise DecodeError("truncated varint")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise DecodeError("varint too long")


def iter_fields(data: bytes) -> Iterator[Tuple[int, int, object]]:
    """Yield (field number, wire type, raw value) for each field in a message."""
    pos = 0
    while pos < len(data):
        key, pos = read_varint(data, pos)
        number, wire = key >> 3, key & 7
        if wire == VARINT:
            value, pos = read_varint(data, pos)
        elif wire == LEN:
            length, pos = read_varint(data, pos)
            if pos + length > len(data):
                raise DecodeError("truncated field")
            value, pos = data[pos : pos + length], pos + length
        elif wire == I64:
            value, pos = data[pos : pos + 8], pos + 8
        elif wire == I32:
            value, pos = data[pos : pos + 4], pos + 4
        else:
            raise DecodeError(f"unsupported wire type {wire}")
        yield number, wire, value


def to_int32(value: int) -> int:
    """Protobuf int32/int64 store negatives as 64-bit two's complement."""
    return value - (1 << 64) if value >= 1 << 63 else value


def to_float(raw: bytes) -> float:
    return struct.unpack("<f", raw)[0]


def text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


# ----- messages -----


@dataclass
class TripDescriptor:
    trip_id: Optional[str] = None
    route_id: Optional[str] = None
    start_time: Optional[str] = None  # "HH:MM:SS", may exceed 24:00:00
    start_date: Optional[str] = None  # "YYYYMMDD"
    schedule_relationship: int = 0  # 0 scheduled, 1 added, 2 unscheduled, 3 canceled


def parse_trip(data: bytes) -> TripDescriptor:
    trip = TripDescriptor()
    for number, _, value in iter_fields(data):
        if number == 1:
            trip.trip_id = text(value)
        elif number == 2:
            trip.start_time = text(value)
        elif number == 3:
            trip.start_date = text(value)
        elif number == 4:
            trip.schedule_relationship = value
        elif number == 5:
            trip.route_id = text(value)
    return trip


@dataclass
class StopTimeEvent:
    delay: Optional[int] = None  # seconds late (negative = early)
    time: Optional[int] = None  # Unix time


def parse_event(data: bytes) -> StopTimeEvent:
    event = StopTimeEvent()
    for number, _, value in iter_fields(data):
        if number == 1:
            event.delay = to_int32(value)
        elif number == 2:
            event.time = to_int32(value)
    return event


@dataclass
class StopTimeUpdate:
    stop_sequence: Optional[int] = None
    stop_id: Optional[str] = None
    arrival: Optional[StopTimeEvent] = None
    departure: Optional[StopTimeEvent] = None
    schedule_relationship: int = 0  # 0 scheduled, 1 skipped, 2 no data


def parse_stop_time_update(data: bytes) -> StopTimeUpdate:
    update = StopTimeUpdate()
    for number, _, value in iter_fields(data):
        if number == 1:
            update.stop_sequence = value
        elif number == 2:
            update.arrival = parse_event(value)
        elif number == 3:
            update.departure = parse_event(value)
        elif number == 4:
            update.stop_id = text(value)
        elif number == 5:
            update.schedule_relationship = value
    return update


@dataclass
class TripUpdate:
    trip: TripDescriptor
    vehicle_id: Optional[str] = None
    timestamp: Optional[int] = None
    stop_time_updates: List[StopTimeUpdate] = field(default_factory=list)


def parse_vehicle_descriptor(data: bytes) -> Dict[str, str]:
    out = {}
    for number, _, value in iter_fields(data):
        if number == 1:
            out["id"] = text(value)
        elif number == 2:
            out["label"] = text(value)
    return out


def parse_trip_update(data: bytes) -> TripUpdate:
    update = TripUpdate(trip=TripDescriptor())
    for number, _, value in iter_fields(data):
        if number == 1:
            update.trip = parse_trip(value)
        elif number == 2:
            update.stop_time_updates.append(parse_stop_time_update(value))
        elif number == 3:
            update.vehicle_id = parse_vehicle_descriptor(value).get("id")
        elif number == 4:
            update.timestamp = value
    return update


@dataclass
class VehiclePosition:
    trip: Optional[TripDescriptor] = None
    vehicle_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    current_stop_sequence: Optional[int] = None
    stop_id: Optional[str] = None
    current_status: Optional[int] = None  # 0 incoming at, 1 stopped at, 2 in transit to
    timestamp: Optional[int] = None


def parse_vehicle_position(data: bytes) -> VehiclePosition:
    vp = VehiclePosition()
    for number, wire, value in iter_fields(data):
        if number == 1:
            vp.trip = parse_trip(value)
        elif number == 2:
            for n, w, v in iter_fields(value):
                if n == 1 and w == I32:
                    vp.latitude = to_float(v)
                elif n == 2 and w == I32:
                    vp.longitude = to_float(v)
        elif number == 3:
            vp.current_stop_sequence = value
        elif number == 4:
            vp.current_status = value
        elif number == 5:
            vp.timestamp = value
        elif number == 7:
            vp.stop_id = text(value)
        elif number == 8:
            vp.vehicle_id = parse_vehicle_descriptor(value).get("id")
    return vp


@dataclass
class Feed:
    timestamp: Optional[int] = None
    trip_updates: List[TripUpdate] = field(default_factory=list)
    vehicles: List[VehiclePosition] = field(default_factory=list)


def parse_feed(data: bytes) -> Feed:
    feed = Feed()
    for number, _, value in iter_fields(data):
        if number == 1:  # header
            for n, _, v in iter_fields(value):
                if n == 3:
                    feed.timestamp = v
        elif number == 2:  # entity
            for n, _, v in iter_fields(value):
                if n == 3:
                    feed.trip_updates.append(parse_trip_update(v))
                elif n == 4:
                    feed.vehicles.append(parse_vehicle_position(v))
    return feed
