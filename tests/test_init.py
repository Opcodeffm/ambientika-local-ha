"""Native Home Assistant lifecycle and migration tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ambientika_local import (
    async_migrate_entry,
    async_setup_entry,
)
from custom_components.ambientika_local.const import (
    CONF_ALLOWED_DEVICES,
    CONF_APPROVED_FIRMWARE,
    CONF_BIND_HOST,
    CONF_PORT,
    CONF_REQUIRE_FIRMWARE,
    DOMAIN,
)
from custom_components.ambientika_local.diagnostics import (
    async_get_config_entry_diagnostics,
)

SERIAL = "aabbccddeeff"
OTHER_SERIAL = "112233445566"


async def test_migration_imports_existing_owned_registry_devices(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={CONF_ALLOWED_DEVICES: OTHER_SERIAL},
    )
    entry.add_to_hass(hass)
    legacy_device = SimpleNamespace(identifiers={(DOMAIN, SERIAL)})

    with (
        patch(
            "custom_components.ambientika_local.dr.async_get",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.ambientika_local.dr.async_entries_for_config_entry",
            return_value=[legacy_device],
        ),
    ):
        assert await async_migrate_entry(hass, entry)

    assert entry.version == 2
    assert entry.data[CONF_ALLOWED_DEVICES] == f"{OTHER_SERIAL},{SERIAL}"
    assert entry.data[CONF_APPROVED_FIRMWARE] == ""
    assert entry.data[CONF_REQUIRE_FIRMWARE] is True


async def test_setup_failure_always_closes_started_server(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={
            CONF_ALLOWED_DEVICES: SERIAL,
            CONF_BIND_HOST: "127.0.0.1",
            CONF_PORT: 11000,
        },
    )
    entry.add_to_hass(hass)
    fake_server = MagicMock()
    fake_server.start = AsyncMock()
    fake_server.stop = AsyncMock()
    fake_server.on_status = MagicMock()
    fake_server.on_connect = MagicMock()
    fake_server.on_disconnect = MagicMock()

    with (
        patch(
            "custom_components.ambientika_local.AmbientikaServer",
            return_value=fake_server,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            AsyncMock(side_effect=RuntimeError("platform failed")),
        ),
        pytest.raises(RuntimeError, match="platform failed"),
    ):
        await async_setup_entry(hass, entry)

    fake_server.start.assert_awaited_once()
    fake_server.stop.assert_awaited_once()


async def test_real_entry_setup_diagnostics_and_unload_close_listener(
    hass, socket_enabled
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={
            CONF_ALLOWED_DEVICES: SERIAL,
            CONF_BIND_HOST: "127.0.0.1",
            CONF_PORT: 0,
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    running_server = entry.runtime_data.server
    assert len(running_server.bound_ports) == 1

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert SERIAL not in repr(diagnostics)
    assert diagnostics["policy"]["allowed_device_count"] == 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert running_server.bound_ports == []
