"""Asyncio TCP server for Ambientika device communication."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from .const import LOGGER, FIRMWARE_MSG_LENGTH
from .protocol import (
    DeviceStatus,
    FirmwareInfo,
    parse_message,
)


class AmbientikaServer:
    """TCP server that accepts connections from Ambientika devices."""

    ADDITIONAL_PORTS = [4521]  # Cloud API port - some devices use this

    def __init__(self, port: int = 11000) -> None:
        """Initialize the server."""
        self._port = port
        self._server: asyncio.Server | None = None
        self._extra_servers: list[asyncio.Server] = []
        self._connections: dict[str, asyncio.StreamWriter] = {}  # serial -> writer
        self._ip_to_serial: dict[str, str] = {}  # ip -> serial
        self._firmware_info: dict[str, FirmwareInfo] = {}  # serial -> fw info
        self._on_status_callbacks: list[Callable[[DeviceStatus], None]] = []
        self._on_connect_callbacks: list[Callable[[str], None]] = []
        self._on_disconnect_callbacks: list[Callable[[str], None]] = []

    def on_status(self, callback: Callable[[DeviceStatus], None]) -> None:
        """Register a callback for device status updates."""
        self._on_status_callbacks.append(callback)

    def on_connect(self, callback: Callable[[str], None]) -> None:
        """Register a callback for device connections (serial_number)."""
        self._on_connect_callbacks.append(callback)

    def on_disconnect(self, callback: Callable[[str], None]) -> None:
        """Register a callback for device disconnections (serial_number)."""
        self._on_disconnect_callbacks.append(callback)

    @property
    def connected_devices(self) -> list[str]:
        """Return list of connected device serial numbers."""
        return list(self._connections.keys())

    def get_firmware_info(self, serial_number: str) -> FirmwareInfo | None:
        """Get firmware info for a device."""
        return self._firmware_info.get(serial_number)

    async def start(self) -> None:
        """Start the TCP server on primary and additional ports."""
        self._server = await asyncio.start_server(
            self._handle_connection,
            "0.0.0.0",
            self._port,
        )
        LOGGER.info("Ambientika TCP server listening on port %d", self._port)

        for extra_port in self.ADDITIONAL_PORTS:
            try:
                srv = await asyncio.start_server(
                    self._handle_connection,
                    "0.0.0.0",
                    extra_port,
                )
                self._extra_servers.append(srv)
                LOGGER.info(
                    "Ambientika TCP server also listening on port %d", extra_port
                )
            except OSError as err:
                LOGGER.warning(
                    "Could not bind to additional port %d: %s", extra_port, err
                )

    async def stop(self) -> None:
        """Stop the TCP server and close all connections."""
        for srv in self._extra_servers:
            srv.close()
            await srv.wait_closed()
        self._extra_servers.clear()

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            LOGGER.info("Ambientika TCP server stopped")

        # Close all device connections
        for serial, writer in list(self._connections.items()):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        self._connections.clear()
        self._ip_to_serial.clear()

    async def send_command(self, serial_number: str, command: bytes) -> bool:
        """Send a command to a connected device."""
        writer = self._connections.get(serial_number)
        if writer is None:
            LOGGER.warning(
                "Cannot send command: device %s not connected", serial_number
            )
            return False

        try:
            writer.write(command)
            await writer.drain()
            LOGGER.debug(
                "Sent command to %s: %s", serial_number, command.hex()
            )
            return True
        except (ConnectionError, OSError) as err:
            LOGGER.error("Error sending command to %s: %s", serial_number, err)
            return False

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle an incoming device connection."""
        peer = writer.get_extra_info("peername")
        peer_ip = peer[0] if peer else "unknown"
        LOGGER.info("Device connected from %s", peer_ip)

        serial_number: str | None = None

        try:
            while True:
                # Read data - devices send fixed-length messages
                data = await reader.read(1024)
                if not data:
                    LOGGER.info("Device %s disconnected (EOF)", peer_ip)
                    break

                # Process all complete messages in the buffer
                await self._process_data(data, peer_ip, writer)

                # Track serial from first message
                if serial_number is None and peer_ip in self._ip_to_serial:
                    serial_number = self._ip_to_serial[peer_ip]

        except (ConnectionError, asyncio.IncompleteReadError) as err:
            LOGGER.info("Device %s disconnected: %s", peer_ip, err)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Error handling connection from %s", peer_ip)
        finally:
            # Clean up connection
            if serial_number:
                self._connections.pop(serial_number, None)
                self._ip_to_serial.pop(peer_ip, None)
                for cb in self._on_disconnect_callbacks:
                    try:
                        cb(serial_number)
                    except Exception:  # noqa: BLE001
                        LOGGER.exception("Error in disconnect callback")
                LOGGER.info("Device %s (%s) cleaned up", serial_number, peer_ip)

            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def _process_data(
        self, data: bytes, peer_ip: str, writer: asyncio.StreamWriter
    ) -> None:
        """Process received data buffer, potentially containing multiple messages."""
        offset = 0
        while offset < len(data):
            remaining = data[offset:]

            if len(remaining) < 8:
                # Not enough data for any message
                break

            # Determine message length based on type byte
            msg_type = remaining[0]
            msg_len = len(remaining)

            if msg_type == 0x01 and msg_len >= 19:
                # Status message: 19 bytes (older fw) or 21 bytes (newer fw)
                actual_len = min(msg_len, 21) if msg_len >= 21 else 19
                msg = remaining[:actual_len]
                offset += actual_len
            elif msg_type == 0x03 and msg_len >= FIRMWARE_MSG_LENGTH:
                msg = remaining[:FIRMWARE_MSG_LENGTH]
                offset += FIRMWARE_MSG_LENGTH
            elif msg_type == 0x04:
                # Unknown message type 0x04 from device - log hex and skip
                LOGGER.debug(
                    "Message type 0x04 from %s: %s",
                    peer_ip,
                    remaining.hex(),
                )
                break
            else:
                # Unknown or incomplete, log hex dump and skip rest
                LOGGER.debug(
                    "Unknown message from %s: type=0x%02x, len=%d, hex=%s",
                    peer_ip,
                    msg_type,
                    msg_len,
                    remaining.hex(),
                )
                break

            parsed = parse_message(msg)
            if parsed is None:
                continue

            if isinstance(parsed, FirmwareInfo):
                serial = parsed.serial_number
                self._firmware_info[serial] = parsed
                self._connections[serial] = writer
                self._ip_to_serial[peer_ip] = serial
                LOGGER.info(
                    "Device %s identified: radio=%s micro=%s",
                    serial,
                    parsed.radio_fw,
                    parsed.micro_fw,
                )
                for cb in self._on_connect_callbacks:
                    try:
                        cb(serial)
                    except Exception:  # noqa: BLE001
                        LOGGER.exception("Error in connect callback")

            elif isinstance(parsed, DeviceStatus):
                serial = parsed.serial_number
                # Ensure connection is tracked
                if serial not in self._connections:
                    self._connections[serial] = writer
                    self._ip_to_serial[peer_ip] = serial

                LOGGER.debug(
                    "Status from %s: mode=%s speed=%s temp=%d°C humidity=%d%%",
                    serial,
                    parsed.operating_mode.name,
                    parsed.fan_speed.name,
                    parsed.temperature,
                    parsed.humidity,
                )
                for cb in self._on_status_callbacks:
                    try:
                        cb(parsed)
                    except Exception:  # noqa: BLE001
                        LOGGER.exception("Error in status callback")
