"""Binary sensor platform for Ambientika Local integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, FILTER_STATUS_NAMES, FilterStatus
from .coordinator import AmbientikaCoordinator
from .protocol import DeviceStatus


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ambientika binary sensor entities."""
    coordinator: AmbientikaCoordinator = entry.runtime_data
    known_devices: set[str] = set()

    @callback
    def _check_new_devices() -> None:
        new_entities = []
        for serial in coordinator.data:
            if serial not in known_devices:
                known_devices.add(serial)
                new_entities.extend(
                    [
                        AmbientikaFilterSensor(coordinator, serial),
                        AmbientikaHumidityAlarmSensor(coordinator, serial),
                        AmbientikaNightAlarmSensor(coordinator, serial),
                    ]
                )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_check_new_devices))
    _check_new_devices()


class AmbientikaBaseBinarySensor(
    CoordinatorEntity[AmbientikaCoordinator], BinarySensorEntity
):
    """Base class for Ambientika binary sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AmbientikaCoordinator,
        serial_number: str,
        key: str,
        name: str,
    ) -> None:
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


class AmbientikaFilterSensor(AmbientikaBaseBinarySensor):
    """Filter status sensor - ON means filter needs attention."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator: AmbientikaCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "filter_status", "Filter")

    @property
    def is_on(self) -> bool | None:
        """Return True if filter needs attention (MEDIUM or BAD)."""
        status = self._status
        if status is None:
            return None
        return status.filter_status != FilterStatus.GOOD

    @property
    def extra_state_attributes(self) -> dict:
        status = self._status
        if status is None:
            return {}
        return {
            "filter_condition": FILTER_STATUS_NAMES.get(status.filter_status, "Unknown")
        }


class AmbientikaHumidityAlarmSensor(AmbientikaBaseBinarySensor):
    """Humidity alarm binary sensor."""

    _attr_device_class = BinarySensorDeviceClass.MOISTURE
    _attr_icon = "mdi:water-percent-alert"

    def __init__(self, coordinator: AmbientikaCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "humidity_alarm", "Humidity Alarm")

    @property
    def is_on(self) -> bool | None:
        status = self._status
        return status.humidity_alarm if status else None


class AmbientikaNightAlarmSensor(AmbientikaBaseBinarySensor):
    """Night alarm binary sensor."""

    _attr_icon = "mdi:weather-night"

    def __init__(self, coordinator: AmbientikaCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "night_alarm", "Night Alarm")

    @property
    def is_on(self) -> bool | None:
        status = self._status
        return status.night_alarm if status else None
