"""Sensor platform for Ambientika Local integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import AIR_QUALITY_NAMES, DOMAIN, MANUFACTURER
from .coordinator import AmbientikaCoordinator
from .protocol import DeviceStatus


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ambientika sensor entities."""
    coordinator: AmbientikaCoordinator = entry.runtime_data
    known_devices: set[str] = set()

    @callback
    def _check_new_devices() -> None:
        new_entities = []
        for serial in coordinator.data:
            if serial not in known_devices:
                known_devices.add(serial)
                new_entities.extend([
                    AmbientikaTemperatureSensor(coordinator, serial),
                    AmbientikaHumiditySensor(coordinator, serial),
                    AmbientikaAirQualitySensor(coordinator, serial),
                    AmbientikaSignalSensor(coordinator, serial),
                ])
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_check_new_devices))
    _check_new_devices()


class AmbientikaBaseSensor(
    CoordinatorEntity[AmbientikaCoordinator], SensorEntity
):
    """Base class for Ambientika sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AmbientikaCoordinator,
        serial_number: str,
        key: str,
        name: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._serial = serial_number
        self._attr_unique_id = f"{serial_number}_{key}"
        self._attr_name = name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, serial_number)},
        }

    @property
    def _status(self) -> DeviceStatus | None:
        return self.coordinator.get_status(self._serial)

    @property
    def available(self) -> bool:
        return self.coordinator.is_connected(self._serial) and self._status is not None


class AmbientikaTemperatureSensor(AmbientikaBaseSensor):
    """Temperature sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: AmbientikaCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "temperature", "Temperature")

    @property
    def native_value(self) -> float | None:
        status = self._status
        return status.temperature if status else None


class AmbientikaHumiditySensor(AmbientikaBaseSensor):
    """Humidity sensor."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: AmbientikaCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "humidity", "Humidity")

    @property
    def native_value(self) -> int | None:
        status = self._status
        return status.humidity if status else None


class AmbientikaAirQualitySensor(AmbientikaBaseSensor):
    """Air quality sensor."""

    _attr_icon = "mdi:blur"

    def __init__(self, coordinator: AmbientikaCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "air_quality", "Air Quality")

    @property
    def native_value(self) -> str | None:
        status = self._status
        if status is None:
            return None
        return AIR_QUALITY_NAMES.get(status.air_quality, "Unknown")


class AmbientikaSignalSensor(AmbientikaBaseSensor):
    """WiFi signal strength sensor."""

    _attr_icon = "mdi:wifi"
    _attr_entity_registry_enabled_default = False  # Disabled by default

    def __init__(self, coordinator: AmbientikaCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "signal_strength", "Signal Strength")

    @property
    def native_value(self) -> int | None:
        status = self._status
        return status.signal_strength if status else None
