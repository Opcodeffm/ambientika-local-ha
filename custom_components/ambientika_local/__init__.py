"""The Ambientika Local integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_ALLOWED_DEVICES,
    CONF_APPROVED_FIRMWARE,
    CONF_BIND_HOST,
    CONF_DEVICE_IP_BINDINGS,
    CONF_ENABLE_LEGACY_PORT,
    CONF_ENROLLMENT_EXPIRES_AT,
    CONF_PORT,
    CONF_REQUIRE_FIRMWARE,
    CONF_STATUS_FRAME_LENGTH,
    DEFAULT_BIND_HOST,
    DEFAULT_ENABLE_LEGACY_PORT,
    DEFAULT_ENROLLMENT_EXPIRES_AT,
    DEFAULT_PORT,
    DEFAULT_REQUIRE_FIRMWARE,
    DEFAULT_STATUS_FRAME_LENGTH,
    DOMAIN,
    LOGGER,
)
from .coordinator import AmbientikaCoordinator
from .protocol import normalize_serial, redact_serial
from .server import AmbientikaServer, parse_allowed_devices

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]

type AmbientikaConfigEntry = ConfigEntry[AmbientikaCoordinator]


async def async_migrate_entry(
    hass: HomeAssistant, entry: AmbientikaConfigEntry
) -> bool:
    """Import already-created owned devices before enabling fail-closed setup."""
    if entry.version >= 2:
        return True

    data = dict(entry.data)
    options = dict(entry.options)
    effective = {**data, **options}
    allowed = set(parse_allowed_devices(effective.get(CONF_ALLOWED_DEVICES, "")))

    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        for identifier_domain, identifier in device.identifiers:
            if identifier_domain != DOMAIN:
                continue
            try:
                allowed.add(normalize_serial(identifier))
            except ValueError:
                LOGGER.warning(
                    "Ignored an invalid legacy Ambientika device identifier during migration"
                )

    target = options if options else data
    target[CONF_ALLOWED_DEVICES] = ",".join(sorted(allowed))
    target.setdefault(CONF_ENROLLMENT_EXPIRES_AT, DEFAULT_ENROLLMENT_EXPIRES_AT)
    target.setdefault(CONF_APPROVED_FIRMWARE, "")
    target.setdefault(CONF_REQUIRE_FIRMWARE, DEFAULT_REQUIRE_FIRMWARE)
    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        version=2,
    )
    LOGGER.info(
        "Migrated Ambientika Local security policy with %d approved devices; "
        "commands remain read-only until firmware approval",
        len(allowed),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AmbientikaConfigEntry) -> bool:
    """Set up Ambientika Local from a config entry."""
    settings = {**entry.data, **entry.options}
    server = AmbientikaServer(
        port=settings.get(CONF_PORT, DEFAULT_PORT),
        host=settings.get(CONF_BIND_HOST, DEFAULT_BIND_HOST),
        enable_legacy_port=settings.get(
            CONF_ENABLE_LEGACY_PORT, DEFAULT_ENABLE_LEGACY_PORT
        ),
        allowed_devices=settings.get(CONF_ALLOWED_DEVICES, ""),
        device_ip_bindings=settings.get(CONF_DEVICE_IP_BINDINGS, ""),
        enrollment_expires_at=settings.get(
            CONF_ENROLLMENT_EXPIRES_AT, DEFAULT_ENROLLMENT_EXPIRES_AT
        ),
        approved_firmware=settings.get(CONF_APPROVED_FIRMWARE, ""),
        require_firmware=settings.get(CONF_REQUIRE_FIRMWARE, DEFAULT_REQUIRE_FIRMWARE),
        status_frame_length=settings.get(
            CONF_STATUS_FRAME_LENGTH, DEFAULT_STATUS_FRAME_LENGTH
        ),
    )

    try:
        await server.start()
        coordinator = AmbientikaCoordinator(hass, server)
        entry.runtime_data = coordinator

        async def _handle_new_device(serial_number: str) -> None:
            """Notify platforms only after an approved device sends valid status."""
            LOGGER.info(
                "Setting up entities for approved device: %s",
                redact_serial(serial_number),
            )
            coordinator.async_set_updated_data(coordinator.data)

        coordinator.on_new_device(_handle_new_device)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await server.stop()
        raise

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _update_security_issues(hass, server)
    return True


def _update_security_issues(hass: HomeAssistant, server: AmbientikaServer) -> None:
    """Surface unsafe or inactive listener policy in Home Assistant Repairs."""
    if not server.allowed_devices and not server.enrollment_active:
        ir.async_create_issue(
            hass,
            DOMAIN,
            "no_approved_devices",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="no_approved_devices",
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, "no_approved_devices")

    diagnostics = server.security_diagnostics
    listener = diagnostics["listener"]
    if isinstance(listener, dict) and listener.get("bind_host") in ("0.0.0.0", "::"):
        ir.async_create_issue(
            hass,
            DOMAIN,
            "wildcard_listener",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="wildcard_listener",
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, "wildcard_listener")


async def _async_update_listener(
    hass: HomeAssistant, entry: AmbientikaConfigEntry
) -> None:
    """Reload the listener after a security option changes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AmbientikaConfigEntry) -> bool:
    """Unload an Ambientika Local config entry without leaving sockets behind."""
    coordinator: AmbientikaCoordinator = entry.runtime_data
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await coordinator.server.stop()
    return unloaded
