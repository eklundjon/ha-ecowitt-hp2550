"""Coordinator for the Ecowitt HP2550 backlight."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_LEVEL, DOMAIN, UPDATE_INTERVAL
from .protocol import (
    GEN_V1,
    Hp2550Client,
    Hp2550Error,
    Hp2550Rejected,
    Hp2550Status,
    accept_status,
)

_LOGGER = logging.getLogger(__name__)


class Hp2550Coordinator(DataUpdateCoordinator[Hp2550Status | None]):
    """Polls the console's backlight status and sends commands."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
            config_entry=entry,
        )
        self.client = Hp2550Client(entry.data[CONF_HOST])
        self._last_level: int | None = None

    @property
    def restore_level(self) -> int:
        """The level to send when auto-brightness is switched off."""
        return self._last_level if self._last_level is not None else DEFAULT_LEVEL

    def _remember(self, status: Hp2550Status | None) -> None:
        if status is not None and status.level is not None:
            self._last_level = status.level

    async def _async_update_data(self) -> Hp2550Status | None:
        try:
            status = await self.client.read_status()
        except Hp2550Error as err:
            raise UpdateFailed(str(err)) from err
        if status is not None and not accept_status(status, self.client.pending_seq):
            # A command is in flight and this report predates it; keep what we have.
            return self.data
        self._remember(status)
        return status

    async def async_send(self, verb: str, value: int | str) -> None:
        """Send a command, wait for the console to echo it, and adopt the result."""
        # A v1 console publishes nothing until it has acted on a command, so no
        # status anywhere means v1-or-unknown: speak v1 (BL 0 / 1 / 2-100), which
        # is also what a v2 console that hasn't published yet would reject
        # harmlessly as BL n.
        generation = self.data.generation if self.data else GEN_V1
        try:
            status = await self.client.send_command(verb, value, generation)
        except Hp2550Rejected as err:
            _LOGGER.error("%s (%s %s); nothing was changed", err, verb, value)
            self._remember(err.status)
            self.async_set_updated_data(err.status)
            raise HomeAssistantError(f"The console rejected {verb} {value}") from err
        except Hp2550Error as err:
            raise HomeAssistantError(
                f"Failed to send backlight command: {err}"
            ) from err
        self._remember(status)
        self.async_set_updated_data(status)
