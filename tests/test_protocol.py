"""Tests for strict protocol parsing and log redaction."""

from __future__ import annotations

import unittest

from tests._loader import const, protocol
from tests.helpers import firmware_frame, status_frame


class ProtocolTests(unittest.TestCase):
    def test_parses_legacy_and_current_status_frames(self) -> None:
        legacy = protocol.parse_message(status_frame(length=19))
        current = protocol.parse_message(status_frame(length=21))

        self.assertIsInstance(legacy, protocol.DeviceStatus)
        self.assertIsInstance(current, protocol.DeviceStatus)
        self.assertEqual(legacy.light_sensitivity, const.LightSensitivity.NOT_AVAILABLE)
        self.assertEqual(current.light_sensitivity, const.LightSensitivity.MEDIUM)
        self.assertEqual(current.serial_number, "aabbccddeeff")

    def test_parses_firmware_frame(self) -> None:
        parsed = protocol.parse_message(firmware_frame())
        self.assertIsInstance(parsed, protocol.FirmwareInfo)
        self.assertEqual(parsed.micro_fw, "0.1.22")

    def test_rejects_invalid_enum_without_leaking_device_id(self) -> None:
        frame = bytearray(status_frame())
        frame[8] = 255

        with self.assertRaises(protocol.ProtocolError) as caught:
            protocol.parse_message(bytes(frame))

        self.assertNotIn("aabbccddeeff", str(caught.exception))
        self.assertIn("operating mode", str(caught.exception))

    def test_rejects_invalid_humidity_and_boolean(self) -> None:
        humidity = bytearray(status_frame())
        humidity[12] = 101
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_message(bytes(humidity))

        boolean = bytearray(status_frame())
        boolean[14] = 2
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_message(bytes(boolean))

    def test_rejects_invalid_frame_marker(self) -> None:
        frame = bytearray(status_frame())
        frame[1] = 1

        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_message(bytes(frame))

    def test_setup_offsets_match_protocol(self) -> None:
        setup = (
            bytes.fromhex("0200aabbccddeeff")
            + bytes([0, 2, 7])
            + (123456).to_bytes(4, "little")
        )
        parsed = protocol.parse_message(setup)

        self.assertIsInstance(parsed, protocol.DeviceSetup)
        self.assertEqual(parsed.device_role, const.DeviceRole.SLAVE_OPPOSITE_MASTER)
        self.assertEqual(parsed.zone, 7)
        self.assertEqual(parsed.house_id, 123456)

    def test_normalises_and_redacts_device_identifiers(self) -> None:
        self.assertEqual(protocol.normalize_serial("AA:BB:CC:DD:EE:FF"), "aabbccddeeff")
        self.assertEqual(protocol.redact_serial("aabbccddeeff"), "********eeff")
        redacted = protocol.redact_frame(status_frame())
        self.assertNotIn("aabbccddeeff", redacted)
        self.assertIn("device-id-redacted", redacted)

    def test_command_builder_requires_a_real_device_identifier(self) -> None:
        with self.assertRaises(ValueError):
            protocol.build_filter_reset("not-a-device")

    def test_weather_builder_rejects_out_of_range_sensor_values(self) -> None:
        with self.assertRaises(ValueError):
            protocol.build_weather_update(
                "aabbccddeeff", 400.0, 50, const.AirQuality.GOOD
            )
        with self.assertRaises(ValueError):
            protocol.build_weather_update(
                "aabbccddeeff", 20.0, 101, const.AirQuality.GOOD
            )


if __name__ == "__main__":
    unittest.main()
