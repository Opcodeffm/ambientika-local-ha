"""Data coordinator for Ambientika Local integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, LOGGER
from .protocol import DeviceStatus, FirmwareInfo
from .server import AmbientikaServer


class AmbientikaCoordinator(DataUpdateCoordinator[dict[str, DeviceStatus]]):
    """Coordinator that receives push updates from the TCP server."""

    def __init__(self, hass: HomeAssistant, server: AmbientikaServer) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=None,  # Push-based, no polling
        )
        self.server = server
        self.data: dict[str, DeviceStatus] = {}
        self._new_device_callbacks: list[callable] = []

        # Register server callbacks
        server.on_status(self._handle_status)
        server.on_connect(self._handle_connect)
        server.on_disconnect(self._handle_disconnect)

    def on_new_device(self, callback_fn: callable) -> None:
        """Register callback for when a new device is discovered."""
        self._new_device_callbacks.append(callback_fn)

    @callback
    def _handle_status(self, status: DeviceStatus) -> None:
        """Handle a status update from a device."""
        is_new = status.serial_number not in self.data
        self.data[status.serial_number] = status
        self.async_set_updated_data(self.data)

        if is_new:
            LOGGER.info("New device discovered: %s", status.serial_number)
            for cb in self._new_device_callbacks:
                try:
                    self.hass.async_create_task(cb(status.serial_number))
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Error in new device callback")

    @callback
    def _handle_connect(self, serial_number: str) -> None:
        """Handle device connection."""
        LOGGER.info("Device connected: %s", serial_number)

    @callback
    def _handle_disconnect(self, serial_number: str) -> None:
        """Handle device disconnection."""
        LOGGER.info("Device disconnected: %s", serial_number)
        # Keep last known state but notify listeners
        self.async_set_updated_data(self.data)

    def get_status(self, serial_number: str) -> DeviceStatus | None:
        """Get the current status of a device."""
        return self.data.get(serial_number)

    def get_firmware_info(self, serial_number: str) -> FirmwareInfo | None:
        """Get firmware info for a device."""
        return self.server.get_firmware_info(serial_number)

    def is_connected(self, serial_number: str) -> bool:
        """Check if a device is currently connected."""
        return serial_number in self.server.connected_devices

    async def send_command(self, serial_number: str, command: bytes) -> bool:
        """Send a command to a device."""
        return await self.server.send_command(serial_number, command)

    async def _async_update_data(self) -> dict[str, DeviceStatus]:
        """No polling needed - data is pushed by the server."""
        return self.data
