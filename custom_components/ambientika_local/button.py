"""Button platform for Ambientika Local integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AmbientikaCoordinator
from .protocol import build_filter_reset


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ambientika button entities."""
    coordinator: AmbientikaCoordinator = entry.runtime_data
    known_devices: set[str] = set()

    @callback
    def _check_new_devices() -> None:
        new_entities = []
        for serial in coordinator.data:
            if serial not in known_devices:
                known_devices.add(serial)
                new_entities.append(AmbientikaFilterResetButton(coordinator, serial))
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_check_new_devices))
    _check_new_devices()


class AmbientikaFilterResetButton(
    CoordinatorEntity[AmbientikaCoordinator], ButtonEntity
):
    """Button to reset the filter status."""

    _attr_has_entity_name = True
    _attr_name = "Reset Filter"
    _attr_icon = "mdi:air-filter"

    def __init__(
        self,
        coordinator: AmbientikaCoordinator,
        serial_number: str,
    ) -> None:
        super().__init__(coordinator)
        self._serial = serial_number
        self._attr_unique_id = f"{serial_number}_filter_reset"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, serial_number)},
        }

    @property
    def available(self) -> bool:
        return self.coordinator.is_connected(
            self._serial
        ) and self.coordinator.can_send_commands(self._serial)

    async def async_press(self) -> None:
        """Handle button press."""
        command = build_filter_reset(self._serial)
        await self.coordinator.send_command(self._serial, command)
