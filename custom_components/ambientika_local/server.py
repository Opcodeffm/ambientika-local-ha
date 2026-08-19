"""Hardened asyncio TCP server for Ambientika device communication."""

from __future__ import annotations

import asyncio
import ipaddress
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from .const import (
    CMD_FILTER_RESET,
    CMD_OPERATING_MODE,
    CMD_WEATHER_UPDATE,
    COMMAND_MSG_LENGTH,
    DEFAULT_BIND_HOST,
    DEFAULT_COMMAND_DEDUP_WINDOW,
    DEFAULT_ENROLLMENT_EXPIRES_AT,
    DEFAULT_FIRST_FRAME_TIMEOUT,
    DEFAULT_FRAME_ASSEMBLY_TIMEOUT,
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_MAX_BUFFER_SIZE,
    DEFAULT_MAX_COMMANDS_PER_MINUTE,
    DEFAULT_MAX_CONNECTION_ATTEMPTS_PER_MINUTE,
    DEFAULT_MAX_CONNECTIONS,
    DEFAULT_MAX_CONNECTIONS_PER_IP,
    DEFAULT_MAX_DISCARDED_BYTES,
    DEFAULT_MAX_ENROLLMENT_CANDIDATES,
    DEFAULT_MAX_FRAMES_PER_SECOND,
    DEFAULT_MAX_INVALID_FRAMES,
    DEFAULT_MAX_UNIDENTIFIED_CONNECTIONS,
    DEFAULT_PORT,
    DEFAULT_REJECTION_LOG_INTERVAL,
    DEFAULT_REQUIRE_FIRMWARE,
    DEFAULT_STATUS_FRAME_LENGTH,
    DEFAULT_WRITE_TIMEOUT,
    FILTER_RESET_MSG_LENGTH,
    FIRMWARE_MSG_LENGTH,
    LEGACY_PORT,
    LEGACY_STATUS_MSG_LENGTH,
    LOGGER,
    MSG_TYPE_COMMAND,
    MSG_TYPE_FIRMWARE,
    MSG_TYPE_STATUS,
    STATUS_FRAME_LENGTH_OPTIONS,
    STATUS_MSG_LENGTH,
)
from .protocol import (
    DeviceStatus,
    FirmwareInfo,
    ProtocolError,
    firmware_fingerprint,
    normalize_serial,
    parse_message,
    redact_serial,
)

_DEVICE_FRAME_TYPES = frozenset((MSG_TYPE_STATUS, MSG_TYPE_FIRMWARE))
_COMMAND_LENGTHS = {
    CMD_OPERATING_MODE: COMMAND_MSG_LENGTH,
    CMD_FILTER_RESET: FILTER_RESET_MSG_LENGTH,
    CMD_WEATHER_UPDATE: COMMAND_MSG_LENGTH,
}


class ConnectionRejected(Exception):
    """Raised when an untrusted connection violates the access policy."""


class EnrollmentCandidateRecorded(ConnectionRejected):
    """Raised after an unapproved device is recorded without being admitted."""


def parse_allowed_devices(value: str | Iterable[str] | None) -> frozenset[str]:
    """Parse a comma/newline separated device allowlist."""
    if value is None:
        return frozenset()
    if isinstance(value, str):
        items = value.replace(";", ",").replace("\n", ",").split(",")
    else:
        items = list(value)
    return frozenset(normalize_serial(item) for item in items if item.strip())


def parse_device_ip_bindings(
    value: str | Mapping[str, str] | None,
) -> dict[str, str]:
    """Parse `device=ip` bindings and return canonical values."""
    if value is None:
        return {}
    if isinstance(value, Mapping):
        items = value.items()
    else:
        entries = value.replace(";", ",").replace("\n", ",").split(",")
        parsed: list[tuple[str, str]] = []
        for entry in entries:
            if not entry.strip():
                continue
            serial, separator, address = entry.partition("=")
            if not separator:
                raise ValueError("IP bindings must use DEVICE=IP syntax")
            parsed.append((serial, address))
        items = parsed

    result: dict[str, str] = {}
    for serial, address in items:
        canonical_serial = normalize_serial(serial)
        try:
            canonical_address = ipaddress.ip_address(address.strip()).compressed
        except ValueError as err:
            raise ValueError(f"invalid IP address for {redact_serial(serial)}") from err
        result[canonical_serial] = canonical_address
    return result


def parse_approved_firmware(
    value: str | Mapping[str, str] | None,
) -> dict[str, str]:
    """Parse `device=radio|micro|radio-at` firmware approvals."""
    if value is None:
        return {}
    if isinstance(value, Mapping):
        items = value.items()
    else:
        entries = value.replace(";", ",").replace("\n", ",").split(",")
        parsed: list[tuple[str, str]] = []
        for entry in entries:
            if not entry.strip():
                continue
            serial, separator, fingerprint = entry.partition("=")
            if not separator:
                raise ValueError("firmware approvals must use DEVICE=PROFILE syntax")
            parsed.append((serial, fingerprint))
        items = parsed

    result: dict[str, str] = {}
    for serial, fingerprint in items:
        canonical_serial = normalize_serial(serial)
        canonical_fingerprint = fingerprint.strip()
        parts = canonical_fingerprint.split("|")
        if len(parts) != 3 or any(
            not part or any(not item.isdigit() for item in part.split("."))
            for part in parts
        ):
            raise ValueError("invalid firmware approval profile")
        result[canonical_serial] = canonical_fingerprint
    return result


def redact_ip(address: str) -> str:
    """Return a useful but non-identifying IP representation for logs."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return "unknown-peer"
    if isinstance(parsed, ipaddress.IPv4Address):
        octets = str(parsed).split(".")
        return f"{octets[0]}.{octets[1]}.{octets[2]}.x"
    groups = parsed.exploded.split(":")
    return f"{groups[0]}:{groups[1]}:…"


class FrameDecoder:
    """Incrementally decode fixed-size frames from the TCP byte stream."""

    def __init__(
        self,
        status_frame_length: str | int = DEFAULT_STATUS_FRAME_LENGTH,
        max_buffer_size: int = DEFAULT_MAX_BUFFER_SIZE,
    ) -> None:
        selection = str(status_frame_length)
        if selection not in STATUS_FRAME_LENGTH_OPTIONS:
            raise ValueError(
                "status frame length must be one of "
                f"{', '.join(STATUS_FRAME_LENGTH_OPTIONS)}"
            )
        self.status_frame_length: int | None = (
            None if selection == "auto" else int(selection)
        )
        self.max_buffer_size = max_buffer_size
        self.buffer = bytearray()
        self.discarded_bytes = 0
        self.discarded_since_frame = 0

    def __len__(self) -> int:
        """Return the number of currently buffered bytes."""
        return len(self.buffer)

    def feed(self, data: bytes) -> None:
        """Append stream data while enforcing a strict memory bound."""
        if len(self.buffer) + len(data) > self.max_buffer_size:
            raise BufferError("connection buffer limit exceeded")
        self.buffer.extend(data)

    @property
    def needs_frame_grace(self) -> bool:
        """Return whether a short read may be a complete legacy status frame."""
        if not self.buffer or self.buffer[0] != MSG_TYPE_STATUS:
            return False
        expected = self.status_frame_length
        if expected is not None:
            return False
        if LEGACY_STATUS_MSG_LENGTH <= len(self.buffer) < STATUS_MSG_LENGTH:
            return True
        return (
            self._header_at(LEGACY_STATUS_MSG_LENGTH)
            and len(self.buffer) < LEGACY_STATUS_MSG_LENGTH + FIRMWARE_MSG_LENGTH
        )

    def pop_frame(self, *, flush_legacy: bool = False) -> bytes | None:
        """Pop one complete frame, resynchronising past untrusted junk bytes."""
        self._resynchronise()
        if len(self.buffer) < 2:
            return None

        msg_type = self.buffer[0]
        if msg_type == MSG_TYPE_FIRMWARE:
            if len(self.buffer) < FIRMWARE_MSG_LENGTH:
                return None
            return self._pop(FIRMWARE_MSG_LENGTH)

        if msg_type != MSG_TYPE_STATUS:
            return None

        frame_length = self.status_frame_length
        if frame_length is None:
            frame_length = self._detect_status_length(flush_legacy=flush_legacy)
            if frame_length is None:
                return None
            self.status_frame_length = frame_length

        if len(self.buffer) < frame_length:
            return None
        return self._pop(frame_length)

    def _detect_status_length(self, *, flush_legacy: bool) -> int | None:
        """Detect the firmware-specific 19/21-byte status size once per socket."""
        size = len(self.buffer)
        if size < LEGACY_STATUS_MSG_LENGTH:
            return None
        if size == LEGACY_STATUS_MSG_LENGTH:
            return LEGACY_STATUS_MSG_LENGTH if flush_legacy else None
        if size == LEGACY_STATUS_MSG_LENGTH + 1:
            return LEGACY_STATUS_MSG_LENGTH if flush_legacy else None

        # Two or more complete legacy frames make the 19-byte format explicit.
        if size >= LEGACY_STATUS_MSG_LENGTH * 2 and self._plausible_frame_at(
            LEGACY_STATUS_MSG_LENGTH
        ):
            return LEGACY_STATUS_MSG_LENGTH

        # A header directly after byte 19 is ambiguous until its next frame has
        # arrived. Wait briefly; on flush, preserve the apparent legacy boundary.
        if self._header_at(LEGACY_STATUS_MSG_LENGTH):
            if size < LEGACY_STATUS_MSG_LENGTH + FIRMWARE_MSG_LENGTH:
                return LEGACY_STATUS_MSG_LENGTH if flush_legacy else None
            if self._plausible_frame_at(LEGACY_STATUS_MSG_LENGTH):
                return LEGACY_STATUS_MSG_LENGTH

        if size >= STATUS_MSG_LENGTH:
            return STATUS_MSG_LENGTH
        return None

    def _header_at(self, offset: int) -> bool:
        return (
            len(self.buffer) >= offset + 2
            and self.buffer[offset] in _DEVICE_FRAME_TYPES
            and self.buffer[offset + 1] == 0x00
        )

    def _plausible_frame_at(self, offset: int) -> bool:
        if not self._header_at(offset):
            return False
        msg_type = self.buffer[offset]
        if msg_type == MSG_TYPE_FIRMWARE:
            return len(self.buffer) >= offset + FIRMWARE_MSG_LENGTH
        if len(self.buffer) < offset + LEGACY_STATUS_MSG_LENGTH:
            return False
        try:
            parse_message(
                bytes(self.buffer[offset : offset + LEGACY_STATUS_MSG_LENGTH])
            )
        except ProtocolError:
            return False
        return True

    def _resynchronise(self) -> None:
        while self.buffer:
            if self.buffer[0] not in _DEVICE_FRAME_TYPES:
                del self.buffer[0]
                self.discarded_bytes += 1
                self.discarded_since_frame += 1
                continue
            if len(self.buffer) == 1:
                return
            if self.buffer[1] != 0x00:
                del self.buffer[0]
                self.discarded_bytes += 1
                self.discarded_since_frame += 1
                continue
            return

    def _pop(self, size: int) -> bytes:
        frame = bytes(self.buffer[:size])
        del self.buffer[:size]
        return frame


@dataclass
class _ConnectionState:
    peer_ip: str
    peer_label: str
    writer: asyncio.StreamWriter
    serial: str | None = None
    firmware: FirmwareInfo | None = None
    invalid_frames: int = 0
    frame_times: deque[float] = field(default_factory=deque)


@dataclass(frozen=True)
class EnrollmentCandidate:
    """Metadata retained for explicit owner approval, never entity creation."""

    serial: str
    peer_ip: str
    first_seen: float
    last_seen: float
    frame_length: int
    firmware: str | None = None

    @property
    def label(self) -> str:
        """Return a redacted human-readable selector label."""
        firmware = self.firmware or "firmware not observed"
        return (
            f"{redact_serial(self.serial)} from {redact_ip(self.peer_ip)} "
            f"({firmware}, {self.frame_length}-byte first frame)"
        )


class AmbientikaServer:
    """TCP server that accepts policy-constrained Ambientika devices."""

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        *,
        host: str = DEFAULT_BIND_HOST,
        enable_legacy_port: bool = False,
        allowed_devices: str | Iterable[str] | None = None,
        device_ip_bindings: str | Mapping[str, str] | None = None,
        enrollment_expires_at: float = DEFAULT_ENROLLMENT_EXPIRES_AT,
        approved_firmware: str | Mapping[str, str] | None = None,
        require_firmware: bool = DEFAULT_REQUIRE_FIRMWARE,
        status_frame_length: str | int = DEFAULT_STATUS_FRAME_LENGTH,
        first_frame_timeout: float = DEFAULT_FIRST_FRAME_TIMEOUT,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
        frame_assembly_timeout: float = DEFAULT_FRAME_ASSEMBLY_TIMEOUT,
        write_timeout: float = DEFAULT_WRITE_TIMEOUT,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        max_connections_per_ip: int = DEFAULT_MAX_CONNECTIONS_PER_IP,
        max_unidentified_connections: int = DEFAULT_MAX_UNIDENTIFIED_CONNECTIONS,
        max_connection_attempts_per_minute: int = (
            DEFAULT_MAX_CONNECTION_ATTEMPTS_PER_MINUTE
        ),
        max_enrollment_candidates: int = DEFAULT_MAX_ENROLLMENT_CANDIDATES,
        max_buffer_size: int = DEFAULT_MAX_BUFFER_SIZE,
        max_invalid_frames: int = DEFAULT_MAX_INVALID_FRAMES,
        max_discarded_bytes: int = DEFAULT_MAX_DISCARDED_BYTES,
        max_frames_per_second: int = DEFAULT_MAX_FRAMES_PER_SECOND,
        max_commands_per_minute: int = DEFAULT_MAX_COMMANDS_PER_MINUTE,
        command_dedup_window: float = DEFAULT_COMMAND_DEDUP_WINDOW,
        rejection_log_interval: float = DEFAULT_REJECTION_LOG_INTERVAL,
    ) -> None:
        """Initialize the server and validate its access policy."""
        if not 1 <= port <= 65535 and port != 0:
            raise ValueError("port must be between 1 and 65535, or zero for tests")
        if max_connections < 1:
            raise ValueError("max_connections must be positive")
        if not 1 <= max_connections_per_ip <= max_connections:
            raise ValueError("per-IP connection limit must fit the global limit")
        if not 1 <= max_unidentified_connections <= max_connections:
            raise ValueError("unidentified connection limit must fit the global limit")
        if max_connection_attempts_per_minute < 1:
            raise ValueError("connection attempt limit must be positive")
        if max_enrollment_candidates < 1:
            raise ValueError("enrollment candidate limit must be positive")
        if max_buffer_size < STATUS_MSG_LENGTH:
            raise ValueError("max_buffer_size is too small for a status frame")
        if max_commands_per_minute < 1:
            raise ValueError("command rate limit must be positive")
        if command_dedup_window < 0 or rejection_log_interval < 0:
            raise ValueError("time windows must not be negative")

        self._port = port
        self._host = host
        self._enable_legacy_port = enable_legacy_port
        self._allowed_devices = parse_allowed_devices(allowed_devices)
        self._device_ip_bindings = parse_device_ip_bindings(device_ip_bindings)
        self._allowed_devices = frozenset(
            set(self._allowed_devices) | set(self._device_ip_bindings)
        )
        self._enrollment_expires_at = max(0.0, float(enrollment_expires_at))
        self._approved_firmware = parse_approved_firmware(approved_firmware)
        if not set(self._approved_firmware).issubset(self._allowed_devices):
            raise ValueError("firmware-approved devices must also be allowlisted")
        self._require_firmware = bool(require_firmware)
        selection = str(status_frame_length)
        if selection not in STATUS_FRAME_LENGTH_OPTIONS:
            raise ValueError("invalid status frame length selection")
        self._status_frame_length = selection

        self._first_frame_timeout = first_frame_timeout
        self._idle_timeout = idle_timeout
        self._frame_assembly_timeout = frame_assembly_timeout
        self._write_timeout = write_timeout
        self._max_connections = max_connections
        self._max_connections_per_ip = max_connections_per_ip
        self._max_unidentified_connections = max_unidentified_connections
        self._max_connection_attempts_per_minute = max_connection_attempts_per_minute
        self._max_enrollment_candidates = max_enrollment_candidates
        self._max_buffer_size = max_buffer_size
        self._max_invalid_frames = max_invalid_frames
        self._max_discarded_bytes = max_discarded_bytes
        self._max_frames_per_second = max_frames_per_second
        self._max_commands_per_minute = max_commands_per_minute
        self._command_dedup_window = command_dedup_window
        self._rejection_log_interval = rejection_log_interval

        self._servers: list[asyncio.Server] = []
        self._active_writers: set[asyncio.StreamWriter] = set()
        self._states_by_writer: dict[asyncio.StreamWriter, _ConnectionState] = {}
        self._writers_by_ip: dict[str, set[asyncio.StreamWriter]] = defaultdict(set)
        self._connection_attempts: dict[str, deque[float]] = {}
        self._client_tasks: set[asyncio.Task] = set()
        self._connections: dict[str, _ConnectionState] = {}
        self._write_locks: dict[str, asyncio.Lock] = {}
        self._firmware_info: dict[str, FirmwareInfo] = {}
        self._enrollment_candidates: dict[str, EnrollmentCandidate] = {}
        self._command_times: dict[str, deque[float]] = {}
        self._last_commands: dict[str, tuple[bytes, float]] = {}
        self._last_warning_times: dict[str, float] = {}
        self._stats: dict[str, int] = defaultdict(int)
        self._enrollment_task: asyncio.Task | None = None
        self._on_status_callbacks: list[Callable[[DeviceStatus], None]] = []
        self._on_connect_callbacks: list[Callable[[str], None]] = []
        self._on_disconnect_callbacks: list[Callable[[str], None]] = []

    def on_status(self, callback: Callable[[DeviceStatus], None]) -> None:
        """Register a callback for device status updates."""
        self._on_status_callbacks.append(callback)

    def on_connect(self, callback: Callable[[str], None]) -> None:
        """Register a callback for accepted device connections."""
        self._on_connect_callbacks.append(callback)

    def on_disconnect(self, callback: Callable[[str], None]) -> None:
        """Register a callback for device disconnections."""
        self._on_disconnect_callbacks.append(callback)

    @property
    def connected_devices(self) -> list[str]:
        """Return a stable list of connected device identifiers."""
        return sorted(self._connections)

    @property
    def allowed_devices(self) -> frozenset[str]:
        """Return the immutable admission allowlist."""
        return self._allowed_devices

    @property
    def command_enabled_devices(self) -> frozenset[str]:
        """Return devices with an explicitly approved firmware write profile."""
        return frozenset(self._approved_firmware)

    def can_send_commands(self, serial_number: str) -> bool:
        """Return whether writes are approved for a canonical device identifier."""
        try:
            serial = normalize_serial(serial_number)
        except ValueError:
            return False
        return serial in self._approved_firmware

    @property
    def enrollment_active(self) -> bool:
        """Return whether unknown-device recording is currently allowed."""
        return time.time() < self._enrollment_expires_at

    @property
    def enrollment_candidates(self) -> dict[str, EnrollmentCandidate]:
        """Return a copy of candidates awaiting explicit approval."""
        return dict(self._enrollment_candidates)

    @property
    def observed_firmware(self) -> dict[str, str]:
        """Return observed firmware profiles for allowlisted devices."""
        return {
            serial: firmware_fingerprint(info)
            for serial, info in self._firmware_info.items()
            if serial in self._allowed_devices
        }

    @property
    def security_diagnostics(self) -> dict[str, object]:
        """Return privacy-safe aggregate diagnostics for Home Assistant."""
        now = time.time()
        devices = []
        for serial in sorted(self._allowed_devices):
            observed = self._firmware_info.get(serial)
            devices.append(
                {
                    "device": redact_serial(serial),
                    "connected": serial in self._connections,
                    "command_enabled": serial in self._approved_firmware,
                    "firmware_observed": (
                        firmware_fingerprint(observed) if observed else None
                    ),
                    "firmware_approved": self._approved_firmware.get(serial),
                    "ip_binding_configured": serial in self._device_ip_bindings,
                }
            )
        return {
            "listener": {
                "bind_host": self._host,
                "bound_ports": self.bound_ports,
                "legacy_port_enabled": self._enable_legacy_port,
                "fail_closed": not self._allowed_devices and not self.enrollment_active,
            },
            "enrollment": {
                "active": self.enrollment_active,
                "seconds_remaining": max(0, int(self._enrollment_expires_at - now)),
                "candidate_count": len(self._enrollment_candidates),
                "candidates": [
                    candidate.label
                    for candidate in self._enrollment_candidates.values()
                ],
            },
            "policy": {
                "allowed_device_count": len(self._allowed_devices),
                "command_enabled_device_count": len(self._approved_firmware),
                "require_firmware": self._require_firmware,
                "max_connections": self._max_connections,
                "max_connections_per_ip": self._max_connections_per_ip,
                "max_unidentified_connections": self._max_unidentified_connections,
                "max_frames_per_second": self._max_frames_per_second,
                "max_commands_per_minute": self._max_commands_per_minute,
            },
            "devices": devices,
            "counters": dict(sorted(self._stats.items())),
        }

    @property
    def bound_ports(self) -> list[int]:
        """Return bound TCP ports, primarily for diagnostics and tests."""
        return sorted(
            {
                socket.getsockname()[1]
                for server in self._servers
                for socket in (server.sockets or [])
            }
        )

    def get_firmware_info(self, serial_number: str) -> FirmwareInfo | None:
        """Get firmware info for a device."""
        try:
            serial = normalize_serial(serial_number)
        except ValueError:
            return None
        return self._firmware_info.get(serial)

    async def start(self) -> None:
        """Start the configured TCP listeners."""
        if not self._allowed_devices and not self.enrollment_active:
            LOGGER.error(
                "Ambientika listener remains closed: configure approved devices "
                "or open a time-limited enrollment window"
            )
            self._stats["fail_closed_starts"] += 1
            return

        ports = [self._port]
        if self._enable_legacy_port and LEGACY_PORT not in ports:
            ports.append(LEGACY_PORT)

        try:
            for port in ports:
                server = await asyncio.start_server(
                    self._handle_connection,
                    self._host,
                    port,
                    limit=self._max_buffer_size,
                )
                self._servers.append(server)
        except Exception:
            await self.stop()
            raise

        LOGGER.info(
            "Ambientika TCP server listening on %s, ports %s",
            self._host,
            ", ".join(str(port) for port in self.bound_ports),
        )
        if self._host in ("0.0.0.0", "::"):
            LOGGER.warning(
                "Ambientika listener is reachable on all host interfaces; "
                "restrict it with the device allowlist and network firewall"
            )
        if self.enrollment_active:
            LOGGER.warning(
                "Ambientika enrollment window is open for %d more seconds; "
                "unapproved devices are recorded but never admitted",
                max(0, int(self._enrollment_expires_at - time.time())),
            )
            self._enrollment_task = asyncio.create_task(
                self._expire_enrollment(), name="ambientika-enrollment-expiry"
            )

    async def stop(self) -> None:
        """Stop listeners and close every accepted or unidentified socket."""
        enrollment_task = self._enrollment_task
        self._enrollment_task = None
        if (
            enrollment_task is not None
            and enrollment_task is not asyncio.current_task()
        ):
            enrollment_task.cancel()
            await asyncio.gather(enrollment_task, return_exceptions=True)

        # Python 3.13 waits for active client handlers in Server.wait_closed().
        # Close the listeners first, but reap them only after their sockets and
        # tracked handler tasks have been shut down to avoid a circular wait.
        servers = self._close_listeners()
        writers = list(self._active_writers)
        for writer in writers:
            writer.close()
        current_task = asyncio.current_task()
        client_tasks = [task for task in self._client_tasks if task is not current_task]
        if client_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*client_tasks, return_exceptions=True),
                    timeout=self._write_timeout,
                )
            except asyncio.TimeoutError:
                for task in client_tasks:
                    task.cancel()
                await asyncio.gather(*client_tasks, return_exceptions=True)
        await asyncio.gather(
            *(server.wait_closed() for server in servers),
            return_exceptions=True,
        )
        self._active_writers.clear()
        self._states_by_writer.clear()
        self._writers_by_ip.clear()
        self._client_tasks.clear()
        self._connections.clear()
        self._write_locks.clear()
        self._connection_attempts.clear()
        self._command_times.clear()
        self._last_commands.clear()
        LOGGER.info("Ambientika TCP server stopped")

    async def _expire_enrollment(self) -> None:
        """Close enrollment-only listeners at the configured absolute deadline."""
        delay = max(0.0, self._enrollment_expires_at - time.time())
        await asyncio.sleep(delay)

        self._stats["enrollment_windows_expired"] += 1
        if self._allowed_devices:
            LOGGER.info(
                "Ambientika enrollment window closed; approved-device listener remains active"
            )
            return

        servers = self._close_listeners()
        for writer in tuple(self._active_writers):
            writer.close()
        await asyncio.gather(
            *(server.wait_closed() for server in servers), return_exceptions=True
        )
        LOGGER.warning(
            "Ambientika enrollment window closed with no approved devices; "
            "all listeners are now closed"
        )

    async def send_command(self, serial_number: str, command: bytes) -> bool:
        """Send a serial-matched command to a connected device."""
        try:
            serial = normalize_serial(serial_number)
        except ValueError:
            LOGGER.warning("Rejected command for an invalid device identifier")
            return False
        command_type = command[8] if len(command) > 8 else None
        if (
            len(command) < FILTER_RESET_MSG_LENGTH
            or command[0] != MSG_TYPE_COMMAND
            or command[1] != 0x00
            or _COMMAND_LENGTHS.get(command_type) != len(command)
        ):
            LOGGER.error("Rejected malformed command for %s", redact_serial(serial))
            self._stats["commands_rejected_malformed"] += 1
            return False
        if command[2:8].hex() != serial:
            LOGGER.error(
                "Rejected command whose embedded device identifier does not match %s",
                redact_serial(serial),
            )
            self._stats["commands_rejected"] += 1
            return False

        approved_firmware = self._approved_firmware.get(serial)
        if approved_firmware is None:
            self._log_warning(
                "command_not_approved",
                "Rejected command for read-only device %s; approve its observed "
                "firmware in integration options first",
                redact_serial(serial),
            )
            self._stats["commands_rejected_read_only"] += 1
            return False

        state = self._connections.get(serial)
        if state is None or state.writer.is_closing():
            LOGGER.warning(
                "Cannot send command: device %s is not connected",
                redact_serial(serial),
            )
            self._stats["commands_rejected_disconnected"] += 1
            return False
        writer = state.writer

        if state.firmware is None:
            self._log_warning(
                "command_without_firmware",
                "Rejected command for %s because this connection has no verified "
                "firmware handshake",
                redact_serial(serial),
            )
            self._stats["commands_rejected_no_firmware"] += 1
            return False

        observed_firmware = firmware_fingerprint(state.firmware)
        if observed_firmware != approved_firmware:
            self._log_warning(
                "command_firmware_mismatch",
                "Rejected command for %s because observed firmware no longer "
                "matches the explicitly approved profile",
                redact_serial(serial),
            )
            self._stats["commands_rejected_firmware_mismatch"] += 1
            return False

        now = asyncio.get_running_loop().time()
        previous = self._last_commands.get(serial)
        if (
            command_type == CMD_OPERATING_MODE
            and previous is not None
            and previous[0] == command
            and now - previous[1] <= self._command_dedup_window
        ):
            self._stats["commands_deduplicated"] += 1
            return True

        command_times = self._command_times.setdefault(serial, deque())
        while command_times and command_times[0] <= now - 60.0:
            command_times.popleft()
        if len(command_times) >= self._max_commands_per_minute:
            self._log_warning(
                "command_rate_limit",
                "Rejected command burst for %s: per-device rate limit exceeded",
                redact_serial(serial),
            )
            self._stats["commands_rate_limited"] += 1
            return False
        command_times.append(now)

        lock = self._write_locks.setdefault(serial, asyncio.Lock())
        try:
            async with lock:
                if self._connections.get(serial) is not state or writer.is_closing():
                    return False
                writer.write(command)
                await asyncio.wait_for(writer.drain(), timeout=self._write_timeout)
            self._last_commands[serial] = (bytes(command), now)
            self._stats["commands_sent"] += 1
            LOGGER.debug(
                "Sent %d-byte command type 0x%02x to %s",
                len(command),
                command[8] if len(command) > 8 else 0,
                redact_serial(serial),
            )
            return True
        except (ConnectionError, OSError, asyncio.TimeoutError) as err:
            LOGGER.error(
                "Command write to %s failed: %s",
                redact_serial(serial),
                type(err).__name__,
            )
            self._stats["command_write_failures"] += 1
            writer.close()
            return False

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        try:
            peer_ip = (
                ipaddress.ip_address(str(peer[0])).compressed if peer else "unknown"
            )
        except ValueError:
            peer_ip = "unknown"
        state = _ConnectionState(peer_ip, redact_ip(peer_ip), writer)
        client_task = asyncio.current_task()
        if client_task is not None:
            self._client_tasks.add(client_task)

        try:
            self._admit_connection_attempt(state)
        except ConnectionRejected as err:
            self._stats["connections_rejected"] += 1
            self._log_warning(
                "connection_gate",
                "Rejected connection from %s: %s",
                state.peer_label,
                err,
            )
            writer.close()
            try:
                await self._wait_closed(writer)
            finally:
                if client_task is not None:
                    self._client_tasks.discard(client_task)
            return

        self._active_writers.add(writer)
        self._states_by_writer[writer] = state
        self._writers_by_ip[peer_ip].add(writer)
        self._stats["connections_accepted_for_identification"] += 1
        decoder = FrameDecoder(self._status_frame_length, self._max_buffer_size)
        LOGGER.info("Device socket connected from %s", state.peer_label)

        try:
            while True:
                timeout = (
                    self._frame_assembly_timeout
                    if len(decoder)
                    else self._first_frame_timeout
                    if state.serial is None
                    else self._idle_timeout
                )
                try:
                    chunk = await asyncio.wait_for(reader.read(1024), timeout=timeout)
                except asyncio.TimeoutError:
                    if decoder.needs_frame_grace:
                        self._process_available_frames(
                            decoder, state, flush_legacy=True
                        )
                        continue
                    reason = (
                        "frame-assembly"
                        if len(decoder)
                        else "first-frame"
                        if state.serial is None
                        else "idle"
                    )
                    LOGGER.info("Closed %s after %s timeout", state.peer_label, reason)
                    break

                if not chunk:
                    self._process_available_frames(decoder, state, flush_legacy=True)
                    break

                try:
                    decoder.feed(chunk)
                except BufferError as err:
                    raise ConnectionRejected(str(err)) from err
                self._process_available_frames(decoder, state)

                if decoder.discarded_since_frame > self._max_discarded_bytes:
                    raise ConnectionRejected("too many unrecognised stream bytes")

        except EnrollmentCandidateRecorded:
            self._stats["enrollment_candidates_recorded"] += 1
            self._log_info(
                "enrollment_candidate",
                "Recorded an unapproved Ambientika candidate from %s; "
                "the socket was not admitted",
                state.peer_label,
            )
        except ConnectionRejected as err:
            self._stats["connections_rejected"] += 1
            self._log_warning(
                "device_socket_rejected",
                "Rejected device socket from %s: %s",
                state.peer_label,
                err,
            )
        except (ConnectionError, asyncio.IncompleteReadError) as err:
            LOGGER.info(
                "Device socket %s disconnected: %s",
                state.peer_label,
                type(err).__name__,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unexpected device socket error from %s", state.peer_label)
        finally:
            self._unregister(state)
            self._active_writers.discard(writer)
            self._states_by_writer.pop(writer, None)
            writers_for_ip = self._writers_by_ip.get(peer_ip)
            if writers_for_ip is not None:
                writers_for_ip.discard(writer)
                if not writers_for_ip:
                    self._writers_by_ip.pop(peer_ip, None)
            if client_task is not None:
                self._client_tasks.discard(client_task)
            writer.close()
            await self._wait_closed(writer)

    def _admit_connection_attempt(self, state: _ConnectionState) -> None:
        """Apply source and resource policy before reading attacker-controlled bytes."""
        if state.peer_ip == "unknown":
            raise ConnectionRejected("peer IP is unavailable or invalid")
        if len(self._active_writers) >= self._max_connections:
            raise ConnectionRejected("global connection limit exceeded")
        if (
            len(self._writers_by_ip.get(state.peer_ip, ()))
            >= self._max_connections_per_ip
        ):
            raise ConnectionRejected("per-IP connection limit exceeded")

        unidentified = sum(
            connection.serial is None for connection in self._states_by_writer.values()
        )
        if unidentified >= self._max_unidentified_connections:
            raise ConnectionRejected("unidentified connection limit exceeded")

        fully_bound = self._allowed_devices and set(self._device_ip_bindings) == set(
            self._allowed_devices
        )
        if (
            fully_bound
            and state.peer_ip not in set(self._device_ip_bindings.values())
            and not self.enrollment_active
        ):
            raise ConnectionRejected("source IP is outside the configured device set")

        now = asyncio.get_running_loop().time()
        self._prune_connection_attempts(now)
        attempts = self._connection_attempts.setdefault(state.peer_ip, deque())
        while attempts and attempts[0] <= now - 60.0:
            attempts.popleft()
        if len(attempts) >= self._max_connection_attempts_per_minute:
            raise ConnectionRejected("connection attempt rate limit exceeded")
        attempts.append(now)

    def _prune_connection_attempts(self, now: float) -> None:
        """Drop expired source buckets so spoofed labels cannot grow memory forever."""
        stale = [
            address
            for address, attempts in self._connection_attempts.items()
            if not attempts or attempts[-1] <= now - 60.0
        ]
        for address in stale:
            self._connection_attempts.pop(address, None)

    def _process_available_frames(
        self,
        decoder: FrameDecoder,
        state: _ConnectionState,
        *,
        flush_legacy: bool = False,
    ) -> None:
        while frame := decoder.pop_frame(flush_legacy=flush_legacy):
            if decoder.discarded_since_frame > self._max_discarded_bytes:
                raise ConnectionRejected("too many unrecognised stream bytes")
            self._check_frame_rate(state)
            self._process_frame(frame, state)
            decoder.discarded_since_frame = 0
            flush_legacy = False

    def _process_frame(self, frame: bytes, state: _ConnectionState) -> None:
        try:
            parsed = parse_message(frame)
        except ProtocolError as err:
            state.invalid_frames += 1
            self._stats["malformed_frames"] += 1
            self._log_warning(
                "malformed_frame",
                "Discarded malformed frame from %s: %s",
                state.peer_label,
                err,
            )
            if state.invalid_frames >= self._max_invalid_frames:
                raise ConnectionRejected("too many malformed frames") from err
            return

        if not isinstance(parsed, (DeviceStatus, FirmwareInfo)):
            state.invalid_frames += 1
            self._stats["unsupported_frames"] += 1
            if state.invalid_frames >= self._max_invalid_frames:
                raise ConnectionRejected("too many unsupported frames")
            return

        state.invalid_frames = 0

        serial = parsed.serial_number
        if (
            isinstance(parsed, DeviceStatus)
            and self._require_firmware
            and state.firmware is None
        ):
            if state.serial is None and serial not in self._allowed_devices:
                self._admit_device(parsed, len(frame), state)
            raise ConnectionRejected("firmware frame is required before status data")

        if state.serial is None:
            self._admit_device(parsed, len(frame), state)
        elif state.serial != serial:
            raise ConnectionRejected(
                "one socket attempted to claim multiple device identifiers"
            )
        elif self._connections.get(serial) is not state:
            raise ConnectionRejected("device connection ownership changed")

        if isinstance(parsed, FirmwareInfo):
            if state.firmware is not None and firmware_fingerprint(
                state.firmware
            ) != firmware_fingerprint(parsed):
                raise ConnectionRejected(
                    "firmware identity changed within one connection"
                )
            state.firmware = parsed
            self._firmware_info[serial] = parsed
            self._stats["firmware_frames_accepted"] += 1
            LOGGER.info(
                "Device %s identified: radio=%s micro=%s",
                redact_serial(serial),
                parsed.radio_fw,
                parsed.micro_fw,
            )
            return

        self._stats["status_frames_accepted"] += 1
        LOGGER.debug(
            "Accepted %d-byte status update from %s",
            len(frame),
            redact_serial(serial),
        )
        self._run_callbacks(self._on_status_callbacks, parsed, "status")

    def _admit_device(
        self,
        parsed: DeviceStatus | FirmwareInfo,
        frame_length: int,
        state: _ConnectionState,
    ) -> None:
        """Admit an approved identity or record it as a quarantined candidate."""
        serial = parsed.serial_number
        if serial not in self._allowed_devices:
            if self.enrollment_active:
                self._record_enrollment_candidate(
                    serial, state.peer_ip, parsed, frame_length
                )
                raise EnrollmentCandidateRecorded("candidate awaiting owner approval")
            raise ConnectionRejected("device identifier is not allowlisted")
        self._register(serial, state)

    def _register(self, serial: str, state: _ConnectionState) -> None:
        if serial not in self._allowed_devices:
            raise ConnectionRejected("device identifier is not allowlisted")

        expected_ip = self._device_ip_bindings.get(serial)
        if expected_ip is not None:
            try:
                actual_ip = ipaddress.ip_address(state.peer_ip).compressed
            except ValueError as err:
                raise ConnectionRejected("bound device has no valid peer IP") from err
            if actual_ip != expected_ip:
                raise ConnectionRejected(
                    "device identifier arrived from an unexpected IP"
                )

        existing = self._connections.get(serial)
        if existing is not None and existing is not state:
            if existing.writer.is_closing():
                self._connections.pop(serial, None)
            else:
                raise ConnectionRejected(
                    "duplicate device identifier is already connected"
                )

        state.serial = serial
        self._connections[serial] = state
        self._write_locks.setdefault(serial, asyncio.Lock())
        self._stats["devices_admitted"] += 1
        LOGGER.info(
            "Accepted device %s from %s",
            redact_serial(serial),
            state.peer_label,
        )
        self._run_callbacks(self._on_connect_callbacks, serial, "connect")

    def _unregister(self, state: _ConnectionState) -> None:
        serial = state.serial
        if serial is None or self._connections.get(serial) is not state:
            return
        self._connections.pop(serial, None)
        self._write_locks.pop(serial, None)
        self._command_times.pop(serial, None)
        self._last_commands.pop(serial, None)
        self._run_callbacks(self._on_disconnect_callbacks, serial, "disconnect")
        LOGGER.info(
            "Device %s disconnected from %s",
            redact_serial(serial),
            state.peer_label,
        )

    def _record_enrollment_candidate(
        self,
        serial: str,
        peer_ip: str,
        parsed: DeviceStatus | FirmwareInfo,
        frame_length: int,
    ) -> None:
        """Record bounded redacted metadata without registering a HA device."""
        now = time.time()
        previous = self._enrollment_candidates.get(serial)
        firmware = (
            firmware_fingerprint(parsed)
            if isinstance(parsed, FirmwareInfo)
            else previous.firmware
            if previous is not None
            else None
        )
        if (
            previous is None
            and len(self._enrollment_candidates) >= self._max_enrollment_candidates
        ):
            self._stats["enrollment_candidates_dropped"] += 1
            raise ConnectionRejected("enrollment candidate limit exceeded")
        self._enrollment_candidates[serial] = EnrollmentCandidate(
            serial=serial,
            peer_ip=peer_ip,
            first_seen=previous.first_seen if previous is not None else now,
            last_seen=now,
            frame_length=frame_length,
            firmware=firmware,
        )

    def _check_frame_rate(self, state: _ConnectionState) -> None:
        now = asyncio.get_running_loop().time()
        while state.frame_times and state.frame_times[0] <= now - 1.0:
            state.frame_times.popleft()
        state.frame_times.append(now)
        if len(state.frame_times) > self._max_frames_per_second:
            raise ConnectionRejected("frame rate limit exceeded")

    def _should_log(self, key: str) -> bool:
        """Bound repeated remote-triggered logs without retaining peer identities."""
        now = time.monotonic()
        previous = self._last_warning_times.get(key)
        if previous is not None and now - previous < self._rejection_log_interval:
            self._stats["logs_suppressed"] += 1
            return False
        self._last_warning_times[key] = now
        return True

    def _log_warning(self, key: str, message: str, *args: object) -> None:
        """Emit a warning at a bounded rate per non-identifying reason."""
        if self._should_log(key):
            LOGGER.warning(message, *args)

    def _log_info(self, key: str, message: str, *args: object) -> None:
        """Emit informational remote-triggered logs at a bounded rate."""
        if self._should_log(key):
            LOGGER.info(message, *args)

    @staticmethod
    def _run_callbacks(callbacks: Iterable[Callable], value: object, name: str) -> None:
        for callback in callbacks:
            try:
                callback(value)
            except Exception:  # noqa: BLE001
                LOGGER.exception("Error in %s callback", name)

    def _close_listeners(self) -> list[asyncio.Server]:
        """Close listeners immediately and return them for later reaping."""
        servers = self._servers.copy()
        for server in servers:
            server.close()
        self._servers.clear()
        return servers

    @staticmethod
    async def _wait_closed(writer: asyncio.StreamWriter) -> None:
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001,S110
            pass
