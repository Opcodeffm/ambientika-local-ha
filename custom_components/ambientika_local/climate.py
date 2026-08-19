"""Climate platform for Ambientika Local integration."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    FAN_SPEED_FROM_NAME,
    FAN_SPEED_NAMES,
    HUMIDITY_LEVEL_NAMES,
    LOGGER,
    MANUFACTURER,
    OPERATING_MODE_TO_PRESET,
    PRESET_TO_OPERATING_MODE,
    OperatingMode,
)
from .coordinator import AmbientikaCoordinator
from .protocol import DeviceStatus, build_mode_command


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ambientika climate entities."""
    coordinator: AmbientikaCoordinator = entry.runtime_data
    known_devices: set[str] = set()

    @callback
    def _check_new_devices() -> None:
        """Check for new devices and add entities."""
        new_entities = []
        for serial in coordinator.data:
            if serial not in known_devices:
                known_devices.add(serial)
                new_entities.append(AmbientikaClimateEntity(coordinator, serial, entry))
        if new_entities:
            async_add_entities(new_entities)

    # Check on every coordinator update
    entry.async_on_unload(coordinator.async_add_listener(_check_new_devices))
    _check_new_devices()


class AmbientikaClimateEntity(CoordinatorEntity[AmbientikaCoordinator], ClimateEntity):
    """Ambientika climate entity."""

    _attr_has_entity_name = True
    _attr_name = None  # Use device name
    _attr_temperature_unit = "°C"
    _attr_supported_features = (
        ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.PRESET_MODE
    )
    _attr_hvac_modes: ClassVar[list[HVACMode]] = [
        HVACMode.OFF,
        HVACMode.FAN_ONLY,
    ]
    _attr_fan_modes: ClassVar[list[str]] = list(FAN_SPEED_NAMES.values())
    _attr_preset_modes: ClassVar[list[str]] = list(PRESET_TO_OPERATING_MODE)
    _enable_turn_on_off_backwards_compat = False

    def __init__(
        self,
        coordinator: AmbientikaCoordinator,
        serial_number: str,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator)
        self._serial = serial_number
        self._attr_unique_id = f"{serial_number}_climate"

        # Device info
        mac_formatted = ":".join(
            serial_number[i : i + 2] for i in range(0, len(serial_number), 2)
        )
        fw_info = coordinator.get_firmware_info(serial_number)
        sw_version = fw_info.micro_fw if fw_info else None

        self._attr_device_info = {
            "identifiers": {(DOMAIN, serial_number)},
            "name": f"Ambientika {mac_formatted[-8:].upper()}",
            "manufacturer": MANUFACTURER,
            "model": "VMC",
            "sw_version": sw_version,
            "connections": {("mac", mac_formatted)},
        }

    @property
    def _status(self) -> DeviceStatus | None:
        """Get current device status."""
        return self.coordinator.get_status(self._serial)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.is_connected(self._serial) and self._status is not None

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Hide optional controls until writes are explicitly approved."""
        if not self.coordinator.can_send_commands(self._serial):
            return ClimateEntityFeature(0)
        return self._attr_supported_features

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        status = self._status
        if status is None or status.operating_mode == OperatingMode.OFF:
            return HVACMode.OFF
        return HVACMode.FAN_ONLY

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        status = self._status
        return status.temperature if status else None

    @property
    def current_humidity(self) -> int | None:
        """Return the current humidity."""
        status = self._status
        return status.humidity if status else None

    @property
    def fan_mode(self) -> str | None:
        """Return the current fan mode."""
        status = self._status
        if status is None:
            return None
        return FAN_SPEED_NAMES.get(status.fan_speed)

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        status = self._status
        if status is None:
            return None
        return OPERATING_MODE_TO_PRESET.get(status.operating_mode)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        status = self._status
        if status is None:
            return {}
        return {
            "control_enabled": self.coordinator.can_send_commands(self._serial),
            "humidity_level": HUMIDITY_LEVEL_NAMES.get(
                status.humidity_level, "Unknown"
            ),
            "light_sensitivity": status.light_sensitivity.name.replace(
                "_", " "
            ).title(),
            "device_role": status.device_role.name.replace("_", " ").title(),
            "signal_strength": status.signal_strength,
        }

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new HVAC mode."""
        status = self._status
        if status is None:
            return

        if hvac_mode == HVACMode.OFF:
            target_mode = OperatingMode.OFF
        else:
            # Restore last operating mode or default to AUTO
            target_mode = status.last_operating_mode
            if target_mode == OperatingMode.OFF:
                target_mode = OperatingMode.AUTO

        command = build_mode_command(
            self._serial,
            target_mode,
            status.fan_speed,
            status.humidity_level,
            status.light_sensitivity,
        )
        await self.coordinator.send_command(self._serial, command)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new fan mode."""
        status = self._status
        if status is None:
            return

        speed = FAN_SPEED_FROM_NAME.get(fan_mode)
        if speed is None:
            LOGGER.warning("Unknown fan mode: %s", fan_mode)
            return

        command = build_mode_command(
            self._serial,
            status.operating_mode,
            speed,
            status.humidity_level,
            status.light_sensitivity,
        )
        await self.coordinator.send_command(self._serial, command)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        status = self._status
        if status is None:
            return

        op_mode = PRESET_TO_OPERATING_MODE.get(preset_mode)
        if op_mode is None:
            LOGGER.warning("Unknown preset mode: %s", preset_mode)
            return

        command = build_mode_command(
            self._serial,
            op_mode,
            status.fan_speed,
            status.humidity_level,
            status.light_sensitivity,
        )
        await self.coordinator.send_command(self._serial, command)

    async def async_turn_on(self) -> None:
        """Turn on."""
        await self.async_set_hvac_mode(HVACMode.FAN_ONLY)

    async def async_turn_off(self) -> None:
        """Turn off."""
        await self.async_set_hvac_mode(HVACMode.OFF)
