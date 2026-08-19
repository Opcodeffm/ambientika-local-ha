"""Privacy-preserving diagnostics for Ambientika Local."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import AmbientikaConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AmbientikaConfigEntry
) -> dict[str, Any]:
    """Return aggregate policy and runtime counters without household secrets."""
    del hass
    return entry.runtime_data.server.security_diagnostics
