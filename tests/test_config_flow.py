"""Native Home Assistant tests for secure setup and approval flows."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ambientika_local import config_flow
from custom_components.ambientika_local.const import (
    CONF_ALLOWED_DEVICES,
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
    DOMAIN,
)
from custom_components.ambientika_local.server import EnrollmentCandidate

SERIAL = "aabbccddeeff"
FIRMWARE_PROFILE = "0.0.28|0.1.22|2.1.0.0"


def base_input(**changes):
    """Return a complete form submission independent of schema defaults."""
    result = {
        CONF_PORT: 11000,
        CONF_BIND_HOST: "127.0.0.1",
        CONF_ENABLE_LEGACY_PORT: False,
        CONF_STATUS_FRAME_LENGTH: "auto",
        CONF_ALLOWED_DEVICES: "",
        CONF_DEVICE_IP_BINDINGS: "",
        CONF_REQUIRE_FIRMWARE: True,
        CONF_OPEN_ENROLLMENT: False,
    }
    result.update(changes)
    return result


def test_empty_policy_requires_explicit_enrollment() -> None:
    with pytest.raises(ValueError, match="approved devices or enrollment"):
        config_flow._validated_settings(base_input())


def test_enrollment_is_absolute_and_time_limited() -> None:
    before = time.time()
    settings = config_flow._validated_settings(
        base_input(**{CONF_OPEN_ENROLLMENT: True})
    )
    assert before + 299 <= settings[CONF_ENROLLMENT_EXPIRES_AT] <= before + 301
    assert CONF_OPEN_ENROLLMENT not in settings
    assert settings[CONF_ALLOWED_DEVICES] == ""


def test_candidate_approval_can_bind_ip_and_enable_profiled_commands() -> None:
    candidate = EnrollmentCandidate(
        serial=SERIAL,
        peer_ip="192.0.2.10",
        first_seen=1.0,
        last_seen=2.0,
        frame_length=18,
        firmware=FIRMWARE_PROFILE,
    )
    settings = config_flow._validated_settings(
        base_input(
            approve_devices=[SERIAL],
            **{
                CONF_BIND_APPROVED_IPS: True,
                CONF_COMMAND_ENABLED_DEVICES: [SERIAL],
            },
        ),
        candidates={SERIAL: candidate},
    )

    assert settings[CONF_ALLOWED_DEVICES] == SERIAL
    assert settings[CONF_DEVICE_IP_BINDINGS] == f"{SERIAL}=192.0.2.10"
    assert settings[CONF_APPROVED_FIRMWARE] == f"{SERIAL}={FIRMWARE_PROFILE}"
    assert "approve_devices" not in settings
    assert CONF_COMMAND_ENABLED_DEVICES not in settings


def test_command_approval_requires_an_observed_firmware_profile() -> None:
    with pytest.raises(ValueError, match="firmware profile"):
        config_flow._validated_settings(
            base_input(
                **{
                    CONF_ALLOWED_DEVICES: SERIAL,
                    CONF_COMMAND_ENABLED_DEVICES: [SERIAL],
                }
            )
        )


async def test_user_flow_creates_fail_closed_policy(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    with patch(
        "custom_components.ambientika_local.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            base_input(**{CONF_ALLOWED_DEVICES: "AA:BB:CC:DD:EE:FF"}),
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ALLOWED_DEVICES] == SERIAL
    assert result["data"][CONF_APPROVED_FIRMWARE] == ""
    assert result["data"][CONF_ENROLLMENT_EXPIRES_AT] == 0.0


async def test_user_flow_refuses_implicit_discovery(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], base_input()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "enrollment_required"}
