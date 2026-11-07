"""Coordinator for the Ecowitt HP2550 backlight."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, UPDATE_INTERVAL
from .protocol import Hp2550Client, Hp2550Error, Hp2550Status

_LOGGER = logging.getLogger(__name__)


class Hp2550Coordinator(DataUpdateCoordinator[Hp2550Status | None]):
    """Polls the console's backlight status slot and sends commands."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
            config_entry=entry,
        )
        self.client = Hp2550Client(entry.data[CONF_HOST])

    async def _async_update_data(self) -> Hp2550Status | None:
        try:
            return await self.client.read_status()
        except Hp2550Error as err:
            raise UpdateFailed(str(err)) from err

    async def async_send(self, value: int) -> int:
        """Send a backlight command; return the sequence number used."""
        try:
            return await self.client.send_command(value)
        except Hp2550Error as err:
            raise HomeAssistantError(
                f"Failed to send backlight command: {err}"
            ) from err
