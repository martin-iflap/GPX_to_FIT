"""A minimal FIT encoder: just the messages and fields write_fit uses, packed by hand.

fit_tool built every RecordMessage as an object holding all 80+ fields the
profile allows a record, which made a 1 Hz file of a long hike take several
seconds in the browser. This module writes the same bytes with `struct`.

The output is byte-for-byte what fit_tool's `FitFileBuilder(auto_define=True,
min_string_size=50).build_bytes()` writes for the same messages, because
Strava has accepted that layout. tests/core/fit_tool_reference.py keeps the
old fit_tool encoding as the oracle. Matching it means keeping its quirks:
- a 12-byte file header with no header CRC
- every message on local message type 0, with a new definition each time
  the message (or its set of fields) changes
- scaled values rounded with round() (half to even), unscaled ones truncated
  with int(), and anything outside the base type's range refused
- strings NUL-terminated and padded to at least `min_string_size` bytes

Standard library only: this runs in Pyodide.
"""

import struct
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache

PROTOCOL_VERSION = 0x23  # 2.3
PROFILE_VERSION = 21212  # FIT SDK 21.212, as major * 1000 + minor

# Enum values from the FIT profile (pinned against fit_tool's in the parity tests).
FILE_TYPE_ACTIVITY = 4
MANUFACTURER_DEVELOPMENT = 255
SPORT_RUNNING = 1
SPORT_WALKING = 11
EVENT_TIMER = 0
EVENT_TYPE_START = 0
EVENT_TYPE_STOP_ALL = 4
ACTIVITY_TYPE_MANUAL = 0

_DEFINITION_HEADER = 0x40  # a definition message on local message type 0
_DATA_HEADER = 0x00  # a data message on local message type 0


@dataclass(frozen=True)
class BaseType:
    """A FIT base type: its number on the wire, size, `struct` code and the range it can hold."""
    name: str
    wire_id: int
    size: int
    code: str
    min: int
    max: int


ENUM = BaseType("enum", 0x00, 1, "B", 0, 0xFF)
UINT16 = BaseType("uint16", 0x84, 2, "H", 0, 0xFFFF)
SINT32 = BaseType("sint32", 0x85, 4, "i", -0x80000000, 0x7FFFFFFF)
UINT32 = BaseType("uint32", 0x86, 4, "I", 0, 0xFFFFFFFF)
UINT32Z = BaseType("uint32z", 0x8C, 4, "I", 0, 0xFFFFFFFF)
STRING = BaseType("string", 0x07, 1, "s", 0, 0)

MAX_FIELD_SIZE = 255  # a definition gives each field's size in one byte
MAX_STRING_BYTES = MAX_FIELD_SIZE - 1  # the longest UTF-8 text that fits with its NUL


@dataclass(frozen=True)
class FieldSpec:
    """One field of a message: encoded = round((value + offset) * scale)."""
    name: str
    field_id: int
    base_type: BaseType
    scale: float = 1
    offset: float = 0


@dataclass(frozen=True)
class MessageSpec:
    """A global message and the fields of it we write, in the profile's declaration order."""
    name: str
    global_id: int
    fields: tuple[FieldSpec, ...]

    def field(self, name: str) -> FieldSpec:
        for field in self.fields:
            if field.name == name:
                return field
        raise KeyError(f"{self.name} has no field {name!r}")


_SEMICIRCLES_PER_DEGREE = 2 ** 31 / 180


def _timestamp(name: str = "timestamp", field_id: int = 253) -> FieldSpec:
    """A date_time field. Values are Unix epoch ms; FIT counts seconds from 1989-12-31T00:00:00Z."""
    return FieldSpec(name, field_id, UINT32, scale=0.001, offset=-631065600000)


FILE_ID = MessageSpec("file_id", 0, (
    FieldSpec("type", 0, ENUM),
    FieldSpec("manufacturer", 1, UINT16),
    FieldSpec("product", 2, UINT16),
    FieldSpec("serial_number", 3, UINT32Z),
    _timestamp("time_created", 4),
    FieldSpec("product_name", 8, STRING),
))

SPORT = MessageSpec("sport", 12, (
    FieldSpec("sport", 0, ENUM),
))

EVENT = MessageSpec("event", 21, (
    _timestamp(),
    FieldSpec("event", 0, ENUM),
    FieldSpec("event_type", 1, ENUM),
))

RECORD = MessageSpec("record", 20, (
    _timestamp(),
    FieldSpec("position_lat", 0, SINT32, scale=_SEMICIRCLES_PER_DEGREE),
    FieldSpec("position_long", 1, SINT32, scale=_SEMICIRCLES_PER_DEGREE),
    FieldSpec("altitude", 2, UINT16, scale=5, offset=500),
    FieldSpec("distance", 5, UINT32, scale=100),
    FieldSpec("speed", 6, UINT16, scale=1000),
    FieldSpec("enhanced_speed", 73, UINT32, scale=1000),
))

LAP = MessageSpec("lap", 19, (
    _timestamp(),
    _timestamp("start_time", 2),
    FieldSpec("total_elapsed_time", 7, UINT32, scale=1000),
    FieldSpec("total_timer_time", 8, UINT32, scale=1000),
    FieldSpec("total_distance", 9, UINT32, scale=100),
    FieldSpec("avg_speed", 13, UINT16, scale=1000),
    FieldSpec("max_speed", 14, UINT16, scale=1000),
    FieldSpec("total_ascent", 21, UINT16),
    FieldSpec("sport", 25, ENUM),
    FieldSpec("enhanced_avg_speed", 110, UINT32, scale=1000),
    FieldSpec("enhanced_max_speed", 111, UINT32, scale=1000),
))

SESSION = MessageSpec("session", 18, (
    _timestamp(),
    _timestamp("start_time", 2),
    FieldSpec("sport", 5, ENUM),
    FieldSpec("total_elapsed_time", 7, UINT32, scale=1000),
    FieldSpec("total_timer_time", 8, UINT32, scale=1000),
    FieldSpec("total_distance", 9, UINT32, scale=100),
    FieldSpec("avg_speed", 14, UINT16, scale=1000),
    FieldSpec("max_speed", 15, UINT16, scale=1000),
    FieldSpec("total_ascent", 22, UINT16),
    FieldSpec("enhanced_avg_speed", 124, UINT32, scale=1000),
    FieldSpec("enhanced_max_speed", 125, UINT32, scale=1000),
))

ACTIVITY = MessageSpec("activity", 34, (
    _timestamp(),
    FieldSpec("total_timer_time", 0, UINT32, scale=1000),
    FieldSpec("num_sessions", 1, UINT16),
    FieldSpec("type", 2, ENUM),
))


def _crc_table() -> tuple[int, ...]:
    """The byte-at-a-time table for FIT's CRC-16 (CRC-16/ARC, reflected polynomial 0xA001)."""
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        table.append(crc)
    return tuple(table)


_CRC_TABLE = _crc_table()


def crc16(data: bytes, crc: int = 0) -> int:
    """FIT's CRC-16 of `data`, continuing from `crc` (so it can be computed in pieces)."""
    table = _CRC_TABLE
    for byte in data:
        crc = (crc >> 8) ^ table[(crc ^ byte) & 0xFF]
    return crc


def file_header(data_size: int) -> bytes:
    """The 12-byte FIT file header (no header CRC) for `data_size` bytes of records."""
    return struct.pack("<BBHI4s", 12, PROTOCOL_VERSION, PROFILE_VERSION, data_size, b".FIT")


@cache
def _value_encoder(field: FieldSpec) -> Callable[[float], int]:
    """A function turning one value of `field` into its stored integer, refusing what won't fit."""
    low, high = field.base_type.min, field.base_type.max
    scale, offset = field.scale, field.offset

    def check(value: float, encoded: int) -> int:
        if not low <= encoded <= high:
            raise ValueError(
                f"{field.name} = {value!r} can't be stored in a FIT {field.base_type.name} "
                f"(encodes to {encoded}, outside {low}..{high})"
            )
        return encoded

    if scale == 1 and offset == 0:
        return lambda value: check(value, int(value))
    return lambda value: check(value, round((value + offset) * scale))


def encode_value(field: FieldSpec, value: float) -> int:
    """The integer `field` stores for `value`: scaled and rounded half to even, or truncated if unscaled.

    Raises:
        ValueError: If the result is outside the field's base type.
    """
    return _value_encoder(field)(value)


def encode_string(value: str, min_size: int) -> bytes:
    """UTF-8, NUL-terminated, zero-padded to at least `min_size` bytes (a longer string isn't cut)."""
    return (value.encode("utf-8") + b"\x00").ljust(min_size, b"\x00")


class FitEncoder:
    """Collects messages and writes them as a FIT file, the way fit_tool's FitFileBuilder does."""

    def __init__(self, min_string_size: int = 50) -> None:
        self._min_string_size = min_string_size
        self._chunks: list[bytes] = []
        self._definition: tuple | None = None

    def _define(self, spec: MessageSpec, layout: Sequence[tuple[FieldSpec, int]]) -> None:
        """Write a definition for `layout` ((field, size) pairs) unless it's the one in force."""
        key = (spec.global_id, tuple((field.field_id, size, field.base_type.wire_id) for field, size in layout))
        if key == self._definition:
            return
        self._definition = key
        self._chunks.append(
            struct.pack("<BBBHB", _DEFINITION_HEADER, 0, 0, spec.global_id, len(layout))
            + b"".join(struct.pack("BBB", *field_def) for field_def in key[1])
        )

    def add(self, spec: MessageSpec, values: Mapping[str, str | float | None]) -> None:
        """Add one message. Fields missing from `values`, or given as None, are left out of it.

        Raises:
            KeyError: If `values` names a field `spec` doesn't have.
            TypeError: If a string field gets a non-string, or a number field a non-number.
            ValueError: If a value can't be stored in its field (a string
                over MAX_STRING_BYTES bytes of UTF-8, or a number out of range).
        """
        for name in values:
            spec.field(name)
        layout: list[tuple[FieldSpec, int]] = []
        data = bytearray([_DATA_HEADER])
        for field in spec.fields:
            value = values.get(field.name)
            if value is None:
                continue
            if field.base_type is STRING:
                if not isinstance(value, str):
                    raise TypeError(f"{field.name} must be a str, got {value!r}")
                encoded = encode_string(value, self._min_string_size)
                if len(encoded) > MAX_FIELD_SIZE:
                    raise ValueError(
                        f"{field.name} is {len(encoded) - 1} bytes of UTF-8, "
                        f"more than the {MAX_STRING_BYTES} a FIT string field holds"
                    )
            else:
                if not isinstance(value, int | float):
                    raise TypeError(f"{field.name} must be a number, got {value!r}")
                encoded = struct.pack("<" + field.base_type.code, encode_value(field, value))
            layout.append((field, len(encoded)))
            data += encoded
        self._define(spec, layout)
        self._chunks.append(bytes(data))

    def add_many(self, spec: MessageSpec, names: Sequence[str], rows: Iterable[Sequence[float]]) -> None:
        """Add one message per row, each with exactly the fields `names` (no None, no strings).

        The fast path for records: one definition check, then one `struct`
        pack per row. The bytes are the same as calling `add` for each row.

        Raises:
            KeyError: If `names` has a field `spec` doesn't have.
            ValueError: If `names` has a string field or names a field twice,
                or if a value can't be stored in its field.
        """
        fields = [spec.field(name) for name in names]
        if any(field.base_type is STRING for field in fields):
            raise ValueError("add_many doesn't take string fields")
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"add_many got {', '.join(repeated)} more than once")
        order = sorted(range(len(fields)), key=lambda index: spec.fields.index(fields[index]))
        ordered = [fields[index] for index in order]
        encoders = [_value_encoder(field) for field in ordered]
        packer = struct.Struct("<B" + "".join(field.base_type.code for field in ordered))
        pack = packer.pack
        chunks = self._chunks
        defined = False
        for row in rows:
            if not defined:
                self._define(spec, [(field, field.base_type.size) for field in ordered])
                defined = True
            chunks.append(pack(_DATA_HEADER, *[encode(row[index]) for encode, index in zip(encoders, order)]))

    def to_bytes(self) -> bytes:
        """The whole file: header, every message added so far, and the file CRC."""
        body = b"".join(self._chunks)
        head = file_header(len(body))
        return head + body + struct.pack("<H", crc16(body, crc16(head)))
