"""Ambientika binary TCP protocol parser and command builder."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .const import (
    CMD_FILTER_RESET,
    CMD_OPERATING_MODE,
    CMD_WEATHER_UPDATE,
    FIRMWARE_MSG_LENGTH,
    LOGGER,
    MSG_TYPE_COMMAND,
    MSG_TYPE_FIRMWARE,
    MSG_TYPE_STATUS,
    STATUS_MSG_LENGTH,
    AirQuality,
    DeviceRole,
    FanSpeed,
    FilterStatus,
    HumidityLevel,
    LightSensitivity,
    OperatingMode,
)


def _mac_from_buffer(data: bytes, offset: int = 2) -> str:
    """Extract MAC address as hex string from buffer at offset (6 bytes)."""
    return data[offset : offset + 6].hex()


def _mac_to_bytes(mac: str) -> bytes:
    """Convert MAC hex string to 6 bytes."""
    return bytes.fromhex(mac)


@dataclass
class DeviceStatus:
    """Parsed device status from a 21-byte status message."""

    serial_number: str
    operating_mode: OperatingMode
    fan_speed: FanSpeed
    humidity_level: HumidityLevel
    temperature: int  # Celsius
    humidity: int  # Percent
    air_quality: AirQuality
    humidity_alarm: bool
    filter_status: FilterStatus
    night_alarm: bool
    device_role: DeviceRole
    last_operating_mode: OperatingMode
    light_sensitivity: LightSensitivity
    signal_strength: int  # Raw value 0-255


@dataclass
class FirmwareInfo:
    """Parsed firmware info from an 18-byte firmware message."""

    serial_number: str
    radio_fw: str
    micro_fw: str
    radio_at_fw: str


@dataclass
class DeviceSetup:
    """Parsed device setup message."""

    serial_number: str
    device_role: DeviceRole
    zone: int
    house_id: int


def parse_message(data: bytes) -> DeviceStatus | FirmwareInfo | DeviceSetup | None:
    """Parse an incoming message from a device.

    Returns the appropriate dataclass or None if unrecognized.
    """
    if len(data) < 8:
        LOGGER.debug("Message too short: %d bytes", len(data))
        return None

    msg_type = data[0]

    if msg_type == MSG_TYPE_STATUS and len(data) >= 19:
        return _parse_status(data)
    if msg_type == MSG_TYPE_FIRMWARE and len(data) >= FIRMWARE_MSG_LENGTH:
        return _parse_firmware(data)
    if msg_type == MSG_TYPE_COMMAND and len(data) >= 15:
        # Device setup message from cloud (we act as cloud)
        return _parse_setup(data)

    LOGGER.debug("Unknown message type 0x%02x, length %d", msg_type, len(data))
    return None


def _parse_status(data: bytes) -> DeviceStatus:
    """Parse a 19 or 21-byte status message."""
    mac = _mac_from_buffer(data, 2)
    msg_len = len(data)

    LOGGER.debug("Parsing status message: %s (%d bytes)", data.hex(), msg_len)

    return DeviceStatus(
        serial_number=mac,
        operating_mode=OperatingMode(data[8]),
        fan_speed=FanSpeed(data[9]),
        humidity_level=HumidityLevel(data[10]),
        temperature=struct.unpack_from("b", data, 11)[0],  # signed int8
        humidity=data[12],
        air_quality=AirQuality(data[13]),
        humidity_alarm=data[14] == 1,
        filter_status=FilterStatus(data[15]),
        night_alarm=data[16] == 1,
        device_role=DeviceRole(data[17]),
        last_operating_mode=OperatingMode(data[18]),
        light_sensitivity=LightSensitivity(data[19]) if msg_len > 19 else LightSensitivity.NOT_AVAILABLE,
        signal_strength=data[20] if msg_len > 20 else 0,
    )


def _parse_firmware(data: bytes) -> FirmwareInfo:
    """Parse an 18-byte firmware info message."""
    mac = _mac_from_buffer(data, 2)

    return FirmwareInfo(
        serial_number=mac,
        radio_fw=f"{data[8]}.{data[9]}.{data[10]}",
        micro_fw=f"{data[11]}.{data[12]}.{data[13]}",
        radio_at_fw=f"{data[14]}.{data[15]}.{data[16]}.{data[17]}",
    )


def _parse_setup(data: bytes) -> DeviceSetup | None:
    """Parse a device setup message (15 bytes)."""
    if len(data) < 15:
        return None
    mac = _mac_from_buffer(data, 2)

    return DeviceSetup(
        serial_number=mac,
        device_role=DeviceRole(data[10]),
        zone=data[11],
        house_id=struct.unpack_from("<I", data, 11)[0],
    )


def build_mode_command(
    serial_number: str,
    operating_mode: OperatingMode,
    fan_speed: FanSpeed,
    humidity_level: HumidityLevel,
    light_sensitivity: LightSensitivity,
) -> bytes:
    """Build a 13-byte operating mode change command."""
    buf = bytearray(13)
    buf[0] = MSG_TYPE_COMMAND
    buf[1] = 0x00
    buf[2:8] = _mac_to_bytes(serial_number)
    buf[8] = CMD_OPERATING_MODE
    buf[9] = int(operating_mode)
    buf[10] = int(fan_speed)
    buf[11] = int(humidity_level)
    buf[12] = int(light_sensitivity)
    return bytes(buf)


def build_filter_reset(serial_number: str) -> bytes:
    """Build a 9-byte filter reset command."""
    buf = bytearray(9)
    buf[0] = MSG_TYPE_COMMAND
    buf[1] = 0x00
    buf[2:8] = _mac_to_bytes(serial_number)
    buf[8] = CMD_FILTER_RESET
    return bytes(buf)


def build_weather_update(
    serial_number: str,
    temperature: float,
    humidity: int,
    air_quality: AirQuality,
) -> bytes:
    """Build a 13-byte weather update command."""
    buf = bytearray(13)
    buf[0] = MSG_TYPE_COMMAND
    buf[1] = 0x00
    buf[2:8] = _mac_to_bytes(serial_number)
    buf[8] = CMD_WEATHER_UPDATE

    # Temperature: multiply by 100, encode as Int16LE
    temp_int = int(temperature * 100)
    struct.pack_into("<h", buf, 9, temp_int)

    buf[11] = humidity
    buf[12] = int(air_quality)
    return bytes(buf)
