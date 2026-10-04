"""The handwritten FIT encoder's building blocks, checked without fit_tool.

Every expected value here is worked out from the FIT protocol itself (or a
published CRC check value), so these tests stand on their own. The parity
tests in test_fit_encoder_parity.py then hold the encoder to fit_tool's bytes.
"""

import random
import struct
from typing import cast

import pytest

from gpx2fit.core import fit_encoder
from gpx2fit.core.fit_encoder import (
    ACTIVITY,
    EVENT,
    FILE_ID,
    LAP,
    RECORD,
    SESSION,
    SPORT,
    FitEncoder,
    crc16,
    encode_string,
    encode_value,
    file_header,
)


def _field(spec, name):
    return next(field for field in spec.fields if field.name == name)


class TestCrc16:
    def test_matches_the_published_check_value(self):
        # FIT's CRC is CRC-16/ARC, whose check value for "123456789" is 0xBB3D.
        assert crc16(b"123456789") == 0xBB3D

    def test_empty_input_returns_the_seed(self):
        assert crc16(b"") == 0
        assert crc16(b"", 0x1234) == 0x1234

    def test_can_be_computed_in_pieces(self):
        data = bytes(random.Random(3).randrange(256) for _ in range(1000))
        assert crc16(data[400:], crc16(data[:400])) == crc16(data)

    def test_a_buffer_followed_by_its_own_crc_checks_to_zero(self):
        data = b"any FIT payload"
        assert crc16(data + struct.pack("<H", crc16(data))) == 0

    def test_every_single_bit_flip_changes_it(self):
        data = bytearray(b"123456789")
        reference = crc16(bytes(data))
        for index in range(len(data)):
            for bit in range(8):
                data[index] ^= 1 << bit
                assert crc16(bytes(data)) != reference
                data[index] ^= 1 << bit


class TestFileHeader:
    def test_is_the_12_byte_header_without_a_header_crc(self):
        # size 12, protocol 2.3, profile 21.212 (21212 = 0x52DC), data size, ".FIT"
        assert file_header(100) == b"\x0c\x23\xdc\x52\x64\x00\x00\x00.FIT"

    def test_data_size_is_little_endian_uint32(self):
        assert file_header(0x01020304)[4:8] == b"\x04\x03\x02\x01"


class TestEncodeValue:
    @pytest.mark.parametrize("degrees, semicircles", [
        (0.0, 0),
        (45.0, 536870912),  # 2**29
        (90.0, 1073741824),
        (-90.0, -1073741824),
        (-180.0, -2147483648),
        (-179.99999, -2147483529),
        (-33.8688, -404070523),
        (151.2093, 1803997218),
    ])
    def test_positions_are_semicircles(self, degrees, semicircles):
        assert encode_value(_field(RECORD, "position_lat"), degrees) == semicircles
        assert encode_value(_field(RECORD, "position_long"), degrees) == semicircles

    def test_longitude_180_overflows_sint32(self):
        with pytest.raises(ValueError):
            encode_value(_field(RECORD, "position_long"), 180.0)

    @pytest.mark.parametrize("metres, encoded", [
        (-500.0, 0),
        (0.0, 2500),
        (8848.8, 46744),
        (12607.0, 65535),
    ])
    def test_altitude_is_offset_by_500_and_scaled_by_5(self, metres, encoded):
        assert encode_value(_field(RECORD, "altitude"), metres) == encoded

    @pytest.mark.parametrize("metres", [-501.0, 12608.0])
    def test_altitude_outside_uint16_raises(self, metres):
        with pytest.raises(ValueError):
            encode_value(_field(RECORD, "altitude"), metres)

    def test_scaled_values_round_half_to_even(self):
        # 0.125 m and 0.375 m are exact in binary: 12.5 and 37.5 centimetres.
        distance = _field(RECORD, "distance")
        assert encode_value(distance, 0.125) == 12
        assert encode_value(distance, 0.375) == 38

    def test_speed_is_mm_per_second_and_its_top_value_is_allowed(self):
        speed = _field(RECORD, "speed")
        assert encode_value(speed, 3.3333333) == 3333
        assert encode_value(speed, 65.535) == 65535
        with pytest.raises(ValueError):
            encode_value(speed, 65.536)

    def test_enhanced_speed_is_uint32_so_it_takes_what_speed_cannot(self):
        assert encode_value(_field(RECORD, "enhanced_speed"), 70.0) == 70000

    def test_negative_unsigned_values_raise(self):
        with pytest.raises(ValueError):
            encode_value(_field(RECORD, "distance"), -1.0)

    @pytest.mark.parametrize("unix_ms, fit_seconds", [
        (631065600000, 0),  # 1989-12-31T00:00:00Z, the FIT epoch
        (1704096000000, 1073030400),  # 2024-01-01T08:00:00Z
        (631065601500, 2),  # 1.5 s rounds to even
        (631065602500, 2),  # 2.5 s rounds to even
    ])
    def test_timestamps_are_seconds_since_the_fit_epoch(self, unix_ms, fit_seconds):
        assert encode_value(_field(RECORD, "timestamp"), unix_ms) == fit_seconds

    def test_timestamps_before_the_fit_epoch_raise(self):
        with pytest.raises(ValueError):
            encode_value(_field(RECORD, "timestamp"), 0)

    def test_unscaled_values_are_truncated_not_rounded(self):
        assert encode_value(_field(SESSION, "total_ascent"), 12.7) == 12

    def test_enum_values_pass_through(self):
        assert encode_value(_field(SPORT, "sport"), 11) == 11

    def test_enum_value_above_255_raises(self):
        with pytest.raises(ValueError):
            encode_value(_field(SPORT, "sport"), 256)


class TestEncodeString:
    def test_ascii_is_nul_terminated_and_padded(self):
        assert encode_string("My Watch", 50) == b"My Watch" + b"\x00" * 42

    def test_multibyte_utf8_counts_bytes_not_characters(self):
        encoded = encode_string("Fēnix ✓", 50)
        assert encoded == "Fēnix ✓".encode() + b"\x00" * 40
        assert len(encoded) == 50

    @pytest.mark.parametrize("length, size", [(49, 50), (50, 51), (51, 52)])
    def test_a_long_string_grows_past_the_minimum_instead_of_being_cut(self, length, size):
        encoded = encode_string("x" * length, 50)
        assert len(encoded) == size
        assert encoded == b"x" * length + b"\x00" * (size - length)


class TestProfileConstants:
    """The global message numbers and field numbers, per the FIT profile."""

    def test_global_message_numbers(self):
        assert [spec.global_id for spec in (FILE_ID, SPORT, EVENT, RECORD, LAP, SESSION, ACTIVITY)] == [
            0, 12, 21, 20, 19, 18, 34,
        ]

    def test_record_field_numbers(self):
        assert {field.name: field.field_id for field in RECORD.fields} == {
            "timestamp": 253, "position_lat": 0, "position_long": 1, "altitude": 2,
            "distance": 5, "speed": 6, "enhanced_speed": 73,
        }

    def test_lap_and_session_put_sport_and_speeds_at_different_numbers(self):
        lap = {field.name: field.field_id for field in LAP.fields}
        session = {field.name: field.field_id for field in SESSION.fields}
        assert (lap["sport"], lap["avg_speed"], lap["enhanced_max_speed"]) == (25, 13, 111)
        assert (session["sport"], session["avg_speed"], session["enhanced_max_speed"]) == (5, 14, 125)


class TestFitEncoder:
    def test_one_message_is_a_definition_then_its_data(self):
        encoder = FitEncoder()
        encoder.add(SPORT, {"sport": 11})
        body = encoder.to_bytes()[12:-2]

        # Definition: header 0x40 (local 0), reserved, little-endian, global 12, 1 field: (0, 1 byte, enum).
        # Data: header 0x00 (local 0), then the value.
        assert body == b"\x40\x00\x00\x0c\x00\x01\x00\x01\x00" + b"\x00\x0b"

    def test_the_header_counts_the_body_and_the_file_crc_checks(self):
        encoder = FitEncoder()
        encoder.add(SPORT, {"sport": 1})
        data = encoder.to_bytes()

        assert data[:12] == file_header(len(data) - 14)
        assert data[-2:] == struct.pack("<H", crc16(data[:-2]))
        assert crc16(data) == 0

    def test_fields_are_written_in_profile_order_whatever_order_they_are_given(self):
        forwards, backwards = FitEncoder(), FitEncoder()
        values = {"event": 0, "event_type": 4, "timestamp": 1704096000000}
        forwards.add(EVENT, values)
        backwards.add(EVENT, dict(reversed(list(values.items()))))

        assert forwards.to_bytes() == backwards.to_bytes()
        # timestamp (253, uint32) is declared before event (0) and event_type (1).
        assert forwards.to_bytes()[12 + 6:12 + 15] == b"\xfd\x04\x86\x00\x01\x00\x01\x01\x00"

    def test_a_field_given_as_none_is_left_out_of_the_definition(self):
        encoder = FitEncoder()
        encoder.add(RECORD, {"timestamp": 1704096000000, "position_lat": 45.0, "position_long": 7.0,
                             "altitude": None, "distance": 0.0, "speed": 1.0, "enhanced_speed": 1.0})
        body = encoder.to_bytes()[12:-2]
        field_count = body[5]
        field_numbers = [body[6 + 3 * index] for index in range(field_count)]

        assert field_numbers == [253, 0, 1, 5, 6, 73]

    def test_unknown_field_names_raise(self):
        with pytest.raises(KeyError):
            FitEncoder().add(SPORT, {"sprot": 1})

    def test_a_string_of_254_bytes_fills_the_largest_field(self):
        encoder = FitEncoder()
        encoder.add(FILE_ID, {"product_name": "x" * 254})
        body = encoder.to_bytes()[12:-2]
        # Definition: one field (8, 255 bytes, string); then the data record.
        assert body[6:9] == b"\x08\xff\x07"
        assert body[9:] == b"\x00" + b"x" * 254 + b"\x00"

    def test_a_string_longer_than_a_fit_field_raises_a_value_error(self):
        with pytest.raises(ValueError, match="product_name"):
            FitEncoder().add(FILE_ID, {"product_name": "x" * 255})

    def test_a_number_for_a_string_field_raises(self):
        with pytest.raises(TypeError, match="product_name"):
            FitEncoder().add(FILE_ID, {"product_name": cast(str, 12)})

    def test_a_string_for_a_number_field_raises(self):
        with pytest.raises(TypeError, match="manufacturer"):
            FitEncoder().add(FILE_ID, {"manufacturer": cast(int, "255")})

    def test_a_definition_is_repeated_only_when_the_message_changes(self):
        encoder = FitEncoder()
        event = {"event": 0, "event_type": 0, "timestamp": 1704096000000}
        record = {"timestamp": 1704096000000, "position_lat": 45.0, "position_long": 7.0,
                  "distance": 0.0, "speed": 1.0, "enhanced_speed": 1.0}
        encoder.add(EVENT, event)
        encoder.add(RECORD, record)
        encoder.add(RECORD, record)
        encoder.add(EVENT, event)
        encoder.add(EVENT, event)
        encoder.add(RECORD, {**record, "altitude": 100.0})

        headers = _record_headers(encoder.to_bytes())
        assert headers == [0x40, 0x00, 0x40, 0x00, 0x00, 0x40, 0x00, 0x00, 0x40, 0x00]

    def test_add_many_matches_adding_one_at_a_time(self):
        names = ("timestamp", "position_lat", "position_long", "altitude", "distance", "speed", "enhanced_speed")
        rows = [(1704096000000 + 1000 * i, 45.0 + i * 1e-5, 7.0, 100.0 + i, 3.0 * i, 3.0, 3.0) for i in range(5)]
        one_by_one, many = FitEncoder(), FitEncoder()
        for row in rows:
            one_by_one.add(RECORD, dict(zip(names, row)))
        many.add_many(RECORD, names, rows)

        assert many.to_bytes() == one_by_one.to_bytes()

    def test_add_many_writes_fields_in_profile_order_whatever_order_they_are_named(self):
        names = ("timestamp", "position_lat", "position_long", "distance", "speed", "enhanced_speed")
        row = (1704096000000, 45.0, 7.0, 12.5, 3.0, 3.0)
        shuffled = (3, 5, 0, 2, 4, 1)
        in_order, out_of_order = FitEncoder(), FitEncoder()
        in_order.add_many(RECORD, names, [row])
        out_of_order.add_many(RECORD, [names[i] for i in shuffled], [tuple(row[i] for i in shuffled)])

        assert out_of_order.to_bytes() == in_order.to_bytes()

    def test_add_many_checks_ranges_too(self):
        with pytest.raises(ValueError):
            FitEncoder().add_many(RECORD, ("timestamp", "position_long"), [(1704096000000, 180.0)])

    def test_add_many_refuses_a_field_named_twice(self):
        with pytest.raises(ValueError, match="speed"):
            FitEncoder().add_many(RECORD, ("timestamp", "speed", "speed"), [(1704096000000, 1.0, 2.0)])

    def test_add_many_with_no_rows_writes_nothing(self):
        encoder = FitEncoder()
        encoder.add_many(RECORD, ("timestamp",), [])
        assert encoder.to_bytes() == file_header(0) + struct.pack("<H", crc16(file_header(0)))


def _record_headers(data: bytes) -> list[int]:
    """Each record's header byte, walking definitions to know each data record's size."""
    body = data[12:-2]
    sizes: dict[int, int] = {}
    headers = []
    offset = 0
    while offset < len(body):
        header = body[offset]
        headers.append(header)
        local = header & 0x0F
        if header & 0x40:
            count = body[offset + 5]
            sizes[local] = sum(body[offset + 6 + 3 * index + 1] for index in range(count))
            offset += 6 + 3 * count
        else:
            offset += 1 + sizes[local]
    return headers


def test_module_has_no_dependencies_outside_the_standard_library():
    # It runs in Pyodide, where fit_tool is no longer installed.
    import ast
    import sys

    tree = ast.parse(open(fit_encoder.__file__, encoding="utf-8").read())
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {
        node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}
