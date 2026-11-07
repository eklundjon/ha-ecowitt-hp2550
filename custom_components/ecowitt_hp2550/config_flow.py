"""Config flow for the Ecowitt HP2550 console integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import format_mac

from .const import DOMAIN
from .protocol import Hp2550Client, Hp2550Error


class Hp2550ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the console."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the host and confirm the console answers."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                mac = await Hp2550Client(host).read_mac()
            except Hp2550Error:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(format_mac(mac))
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(
                    title="Ecowitt HP2550", data={CONF_HOST: host}
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )
