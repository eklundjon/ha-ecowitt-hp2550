"""The Ecowitt HP2550 console integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import Hp2550Coordinator

PLATFORMS = [Platform.LIGHT]

type Hp2550ConfigEntry = ConfigEntry[Hp2550Coordinator]


async def async_setup_entry(hass: HomeAssistant, entry: Hp2550ConfigEntry) -> bool:
    """Set up from a config entry."""
    coordinator = Hp2550Coordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Hp2550ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
