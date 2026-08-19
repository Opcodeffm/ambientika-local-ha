"""Config flow for the Ambientika Local integration."""

from __future__ import annotations

import ipaddress
import time
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_ALLOWED_DEVICES,
    CONF_APPROVE_DEVICES,
    CONF_APPROVED_FIRMWARE,
    CONF_BIND_APPROVED_IPS,
    CONF_BIND_HOST,
    CONF_COMMAND_ENABLED_DEVICES,
    CONF_DEVICE_IP_BINDINGS,
    CONF_ENABLE_LEGACY_PORT,
    CONF_ENROLLMENT_EXPIRES_AT,
    CONF_OPEN_ENROLLMENT,
    CONF_PORT,
    CONF_REQUIRE_FIRMWARE,
    CONF_STATUS_FRAME_LENGTH,
    DEFAULT_BIND_HOST,
    DEFAULT_ENABLE_LEGACY_PORT,
    DEFAULT_ENROLLMENT_DURATION,
    DEFAULT_PORT,
    DEFAULT_REQUIRE_FIRMWARE,
    DEFAULT_STATUS_FRAME_LENGTH,
    DOMAIN,
    STATUS_FRAME_LENGTH_OPTIONS,
)
from .protocol import redact_serial
from .server import (
    EnrollmentCandidate,
    parse_allowed_devices,
    parse_approved_firmware,
    parse_device_ip_bindings,
)


def _safe_allowed(value: Any) -> frozenset[str]:
    """Return valid configured identifiers for form choices."""
    try:
        return parse_allowed_devices(value)
    except (TypeError, ValueError):
        return frozenset()


def _settings_schema(
    defaults: dict[str, Any],
    candidates: Mapping[str, EnrollmentCandidate] | None = None,
) -> vol.Schema:
    """Build the setup/options schema with quarantined candidate selectors."""
    candidates = candidates or {}
    allowed = _safe_allowed(defaults.get(CONF_ALLOWED_DEVICES, ""))
    command_defaults = sorted(
        parse_approved_firmware(defaults.get(CONF_APPROVED_FIRMWARE, ""))
    )
    command_choices = {
        serial: (
            candidates[serial].label
            if serial in candidates
            else f"Approved device {redact_serial(serial)}"
        )
        for serial in sorted(set(allowed) | set(candidates))
    }

    fields: dict[vol.Marker, Any] = {
        vol.Optional(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
            int, vol.Range(min=1, max=65535)
        ),
        vol.Optional(
            CONF_BIND_HOST,
            default=defaults.get(CONF_BIND_HOST, DEFAULT_BIND_HOST),
        ): str,
        vol.Optional(
            CONF_ENABLE_LEGACY_PORT,
            default=defaults.get(CONF_ENABLE_LEGACY_PORT, DEFAULT_ENABLE_LEGACY_PORT),
        ): bool,
        vol.Optional(
            CONF_STATUS_FRAME_LENGTH,
            default=defaults.get(CONF_STATUS_FRAME_LENGTH, DEFAULT_STATUS_FRAME_LENGTH),
        ): vol.In(STATUS_FRAME_LENGTH_OPTIONS),
        vol.Optional(
            CONF_ALLOWED_DEVICES,
            default=defaults.get(CONF_ALLOWED_DEVICES, ""),
        ): str,
        vol.Optional(
            CONF_DEVICE_IP_BINDINGS,
            default=defaults.get(CONF_DEVICE_IP_BINDINGS, ""),
        ): str,
        vol.Optional(
            CONF_REQUIRE_FIRMWARE,
            default=defaults.get(CONF_REQUIRE_FIRMWARE, DEFAULT_REQUIRE_FIRMWARE),
        ): bool,
        vol.Optional(CONF_OPEN_ENROLLMENT, default=False): bool,
    }
    if candidates:
        fields[vol.Optional(CONF_APPROVE_DEVICES, default=[])] = cv.multi_select(
            {serial: candidate.label for serial, candidate in candidates.items()}
        )
        fields[vol.Optional(CONF_BIND_APPROVED_IPS, default=True)] = bool
    if command_choices:
        fields[
            vol.Optional(
                CONF_COMMAND_ENABLED_DEVICES,
                default=[
                    serial for serial in command_defaults if serial in command_choices
                ],
            )
        ] = cv.multi_select(command_choices)
    return vol.Schema(fields)


def _validated_settings(
    user_input: dict[str, Any],
    *,
    candidates: Mapping[str, EnrollmentCandidate] | None = None,
    observed_firmware: Mapping[str, str] | None = None,
    existing_approved_firmware: str | Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate, approve explicitly selected candidates, and canonicalise."""
    candidates = candidates or {}
    observed_firmware = observed_firmware or {}
    result = dict(user_input)
    result[CONF_BIND_HOST] = ipaddress.ip_address(
        str(user_input[CONF_BIND_HOST]).strip()
    ).compressed

    try:
        allowed = set(parse_allowed_devices(user_input.get(CONF_ALLOWED_DEVICES, "")))
    except ValueError as err:
        raise ValueError("invalid allowed device identifier") from err
    try:
        bindings = parse_device_ip_bindings(user_input.get(CONF_DEVICE_IP_BINDINGS, ""))
    except ValueError as err:
        raise ValueError("invalid IP bindings") from err
    allowed.update(bindings)

    approvals = set(user_input.get(CONF_APPROVE_DEVICES, []))
    if not approvals.issubset(candidates):
        raise ValueError("invalid candidate approval")
    for serial in approvals:
        allowed.add(serial)
        if user_input.get(CONF_BIND_APPROVED_IPS, True):
            bindings[serial] = candidates[serial].peer_ip

    enrollment_requested = bool(user_input.get(CONF_OPEN_ENROLLMENT, False))
    if not allowed and not enrollment_requested:
        raise ValueError("approved devices or enrollment are required")

    allowed.update(bindings)
    selected_commands = set(user_input.get(CONF_COMMAND_ENABLED_DEVICES, []))
    if not selected_commands.issubset(allowed):
        raise ValueError("invalid command-enabled devices")

    existing_profiles = parse_approved_firmware(existing_approved_firmware)
    approved_profiles: dict[str, str] = {}
    for serial in sorted(selected_commands):
        candidate = candidates.get(serial)
        profile = (
            observed_firmware.get(serial)
            or (candidate.firmware if candidate is not None else None)
            or existing_profiles.get(serial)
        )
        if profile is None:
            raise ValueError("firmware profile has not been observed")
        approved_profiles[serial] = profile

    result[CONF_ALLOWED_DEVICES] = ",".join(sorted(allowed))
    result[CONF_DEVICE_IP_BINDINGS] = ",".join(
        f"{serial}={address}" for serial, address in sorted(bindings.items())
    )
    result[CONF_APPROVED_FIRMWARE] = ",".join(
        f"{serial}={profile}" for serial, profile in sorted(approved_profiles.items())
    )
    result[CONF_ENROLLMENT_EXPIRES_AT] = (
        time.time() + DEFAULT_ENROLLMENT_DURATION if enrollment_requested else 0.0
    )
    result[CONF_REQUIRE_FIRMWARE] = bool(
        user_input.get(CONF_REQUIRE_FIRMWARE, DEFAULT_REQUIRE_FIRMWARE)
    )
    for transient in (
        CONF_OPEN_ENROLLMENT,
        CONF_APPROVE_DEVICES,
        CONF_BIND_APPROVED_IPS,
        CONF_COMMAND_ENABLED_DEVICES,
    ):
        result.pop(transient, None)
    return result


def _settings_error(err: ValueError) -> str:
    """Map validation failures to non-sensitive translation keys."""
    message = str(err).lower()
    if "approved devices or enrollment" in message:
        return "enrollment_required"
    if "candidate approval" in message:
        return "invalid_approved_devices"
    if "command-enabled" in message:
        return "invalid_command_devices"
    if "firmware profile" in message:
        return "firmware_not_observed"
    if "ip address" in message or "ip binding" in message:
        return "invalid_ip_bindings"
    if "device identifier" in message or "hex" in message:
        return "invalid_allowed_devices"
    return "invalid_bind_host"


class AmbientikaLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle an Ambientika Local config flow."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle initial setup."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                settings = _validated_settings(user_input)
            except ValueError as err:
                errors["base"] = _settings_error(err)
            else:
                return self.async_create_entry(
                    title="Ambientika Local",
                    data=settings,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_settings_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow for existing installations."""
        return AmbientikaLocalOptionsFlow(config_entry)


class AmbientikaLocalOptionsFlow(OptionsFlow):
    """Allow security settings to be changed without removing the integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    def _runtime_policy(
        self,
    ) -> tuple[dict[str, EnrollmentCandidate], dict[str, str]]:
        """Read the current quarantine without assuming setup succeeded."""
        coordinator = getattr(self._config_entry, "runtime_data", None)
        server = getattr(coordinator, "server", None)
        if server is None:
            return {}, {}
        return server.enrollment_candidates, server.observed_firmware

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage integration options."""
        candidates, observed_firmware = self._runtime_policy()
        defaults = {**self._config_entry.data, **self._config_entry.options}
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                settings = _validated_settings(
                    user_input,
                    candidates=candidates,
                    observed_firmware=observed_firmware,
                    existing_approved_firmware=defaults.get(CONF_APPROVED_FIRMWARE, ""),
                )
            except ValueError as err:
                errors["base"] = _settings_error(err)
            else:
                return self.async_create_entry(title="", data=settings)

        if user_input is not None:
            defaults.update(user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_settings_schema(defaults, candidates),
            errors=errors,
        )
