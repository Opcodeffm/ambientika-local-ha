"""The Ambientika Local integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_PORT, DEFAULT_PORT, DOMAIN, LOGGER
from .coordinator import AmbientikaCoordinator
from .server import AmbientikaServer

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]

type AmbientikaConfigEntry = ConfigEntry[AmbientikaCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: AmbientikaConfigEntry) -> bool:
    """Set up Ambientika Local from a config entry."""
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)

    # Create and start the TCP server
    server = AmbientikaServer(port=port)
    await server.start()

    # Create the coordinator
    coordinator = AmbientikaCoordinator(hass, server)
    entry.runtime_data = coordinator

    # Register callback to add entities when new devices connect
    async def _handle_new_device(serial_number: str) -> None:
        """Handle discovery of a new device."""
        LOGGER.info("Setting up entities for new device: %s", serial_number)
        # Trigger a coordinator update so platforms pick up the new device
        coordinator.async_set_updated_data(coordinator.data)

    coordinator.on_new_device(_handle_new_device)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AmbientikaConfigEntry
) -> bool:
    """Unload an Ambientika Local config entry."""
    coordinator: AmbientikaCoordinator = entry.runtime_data

    # Stop the TCP server
    await coordinator.server.stop()

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
