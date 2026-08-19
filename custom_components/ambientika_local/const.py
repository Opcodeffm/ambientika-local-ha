"""Constants for the Ambientika Local integration."""

import logging
from enum import IntEnum

DOMAIN = "ambientika_local"
LOGGER = logging.getLogger(__package__)

DEFAULT_PORT = 11000
DEFAULT_BIND_HOST = "0.0.0.0"
DEFAULT_ENABLE_LEGACY_PORT = False
DEFAULT_STATUS_FRAME_LENGTH = "auto"
DEFAULT_ENROLLMENT_DURATION = 300
DEFAULT_ENROLLMENT_EXPIRES_AT = 0.0
DEFAULT_REQUIRE_FIRMWARE = True
CONF_PORT = "port"
CONF_BIND_HOST = "bind_host"
CONF_ENABLE_LEGACY_PORT = "enable_legacy_port"
CONF_ALLOWED_DEVICES = "allowed_devices"
CONF_DEVICE_IP_BINDINGS = "device_ip_bindings"
CONF_STATUS_FRAME_LENGTH = "status_frame_length"
CONF_ENROLLMENT_EXPIRES_AT = "enrollment_expires_at"
CONF_OPEN_ENROLLMENT = "open_enrollment"
CONF_APPROVE_DEVICES = "approve_devices"
CONF_BIND_APPROVED_IPS = "bind_approved_ips"
CONF_COMMAND_ENABLED_DEVICES = "command_enabled_devices"
CONF_APPROVED_FIRMWARE = "approved_firmware"
CONF_REQUIRE_FIRMWARE = "require_firmware"

LEGACY_PORT = 4521
STATUS_FRAME_LENGTHS = (19, 21)
STATUS_FRAME_LENGTH_OPTIONS = ("auto", "19", "21")

# Defensive server defaults. These are deliberately conservative for a device
# that normally sends one small status frame roughly every 30 seconds.
DEFAULT_FIRST_FRAME_TIMEOUT = 15.0
DEFAULT_IDLE_TIMEOUT = 120.0
DEFAULT_FRAME_ASSEMBLY_TIMEOUT = 0.5
DEFAULT_WRITE_TIMEOUT = 5.0
DEFAULT_MAX_CONNECTIONS = 32
DEFAULT_MAX_CONNECTIONS_PER_IP = 2
DEFAULT_MAX_UNIDENTIFIED_CONNECTIONS = 4
DEFAULT_MAX_CONNECTION_ATTEMPTS_PER_MINUTE = 30
DEFAULT_MAX_ENROLLMENT_CANDIDATES = 32
DEFAULT_MAX_BUFFER_SIZE = 4096
DEFAULT_MAX_INVALID_FRAMES = 5
DEFAULT_MAX_DISCARDED_BYTES = 64
DEFAULT_MAX_FRAMES_PER_SECOND = 20
DEFAULT_MAX_COMMANDS_PER_MINUTE = 12
DEFAULT_COMMAND_DEDUP_WINDOW = 1.0
DEFAULT_REJECTION_LOG_INTERVAL = 30.0

# Protocol constants
MSG_TYPE_STATUS = 0x01
MSG_TYPE_COMMAND = 0x02
MSG_TYPE_FIRMWARE = 0x03

CMD_OPERATING_MODE = 0x01
CMD_FILTER_RESET = 0x03
CMD_WEATHER_UPDATE = 0x04

STATUS_MSG_LENGTH = 21
LEGACY_STATUS_MSG_LENGTH = 19
FIRMWARE_MSG_LENGTH = 18
COMMAND_MSG_LENGTH = 13
FILTER_RESET_MSG_LENGTH = 9

MANUFACTURER = "Ambientika"


class OperatingMode(IntEnum):
    """Operating modes of the Ambientika device."""

    SMART = 0
    AUTO = 1
    MANUAL_HEAT_RECOVERY = 2
    NIGHT = 3
    AWAY_HOME = 4
    SURVEILLANCE = 5
    TIMED_EXPULSION = 6
    EXPULSION = 7
    INTAKE = 8
    MASTER_SLAVE_FLOW = 9
    SLAVE_MASTER_FLOW = 10
    OFF = 11


class FanSpeed(IntEnum):
    """Fan speed settings."""

    LOW = 0
    MEDIUM = 1
    HIGH = 2


class HumidityLevel(IntEnum):
    """Target humidity level settings."""

    DRY = 0
    NORMAL = 1
    MOIST = 2


class AirQuality(IntEnum):
    """Air quality readings."""

    VERY_GOOD = 0
    GOOD = 1
    FAIR = 2
    POOR = 3
    VERY_POOR = 4


class FilterStatus(IntEnum):
    """Filter condition status."""

    GOOD = 0
    MEDIUM = 1
    BAD = 2


class DeviceRole(IntEnum):
    """Device role in master-slave configuration."""

    MASTER = 0
    SLAVE_EQUAL_MASTER = 1
    SLAVE_OPPOSITE_MASTER = 2


class LightSensitivity(IntEnum):
    """Light sensitivity settings."""

    NOT_AVAILABLE = 0
    OFF = 1
    LOW = 2
    MEDIUM = 3


# Mapping: OperatingMode -> HA preset name
OPERATING_MODE_TO_PRESET: dict[OperatingMode, str] = {
    OperatingMode.SMART: "Smart",
    OperatingMode.AUTO: "Auto",
    OperatingMode.MANUAL_HEAT_RECOVERY: "Heat Recovery",
    OperatingMode.NIGHT: "Night",
    OperatingMode.AWAY_HOME: "Away",
    OperatingMode.SURVEILLANCE: "Surveillance",
    OperatingMode.TIMED_EXPULSION: "Timed Expulsion",
    OperatingMode.EXPULSION: "Expulsion",
    OperatingMode.INTAKE: "Intake",
}

PRESET_TO_OPERATING_MODE: dict[str, OperatingMode] = {
    v: k for k, v in OPERATING_MODE_TO_PRESET.items()
}

FAN_SPEED_NAMES: dict[FanSpeed, str] = {
    FanSpeed.LOW: "low",
    FanSpeed.MEDIUM: "medium",
    FanSpeed.HIGH: "high",
}

FAN_SPEED_FROM_NAME: dict[str, FanSpeed] = {v: k for k, v in FAN_SPEED_NAMES.items()}

HUMIDITY_LEVEL_NAMES: dict[HumidityLevel, str] = {
    HumidityLevel.DRY: "Dry",
    HumidityLevel.NORMAL: "Normal",
    HumidityLevel.MOIST: "Moist",
}

HUMIDITY_LEVEL_FROM_NAME: dict[str, HumidityLevel] = {
    v: k for k, v in HUMIDITY_LEVEL_NAMES.items()
}

AIR_QUALITY_NAMES: dict[AirQuality, str] = {
    AirQuality.VERY_GOOD: "Very Good",
    AirQuality.GOOD: "Good",
    AirQuality.FAIR: "Fair",
    AirQuality.POOR: "Poor",
    AirQuality.VERY_POOR: "Very Poor",
}

FILTER_STATUS_NAMES: dict[FilterStatus, str] = {
    FilterStatus.GOOD: "Good",
    FilterStatus.MEDIUM: "Medium",
    FilterStatus.BAD: "Bad",
}
