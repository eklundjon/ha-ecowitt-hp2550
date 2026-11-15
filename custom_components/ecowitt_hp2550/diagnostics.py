"""Diagnostics for the Ecowitt HP2550 console."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_UNIQUE_ID

from .protocol import Hp2550Error, parse_status_v2, redactable_id

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import Hp2550ConfigEntry

TO_REDACT = {CONF_HOST, CONF_UNIQUE_ID, "unique_id", "title", "host"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: Hp2550ConfigEntry
) -> dict[str, Any]:
    """Return the raw mailbox slots and the parsed status, without the MAC or host."""
    coordinator = entry.runtime_data
    try:
        raw = await coordinator.client.read_raw()
    except Hp2550Error as err:
        raw = {"error": str(err)}
    else:
        record = raw["custom_record"]
        # In Wunderground mode the Station ID and password are live credentials.
        # Keep them only if they look like the mailbox (command / status).
        if redactable_id(record["id"]):
            record["id"] = "**REDACTED**"
        if parse_status_v2(record["key"]) is None:
            record["key"] = "**REDACTED**"

    status = coordinator.data
    return {
        "entry": async_redact_data(
            {
                "unique_id": entry.unique_id,
                "title": entry.title,
                "data": dict(entry.data),
            },
            TO_REDACT,
        ),
        "status": dataclasses.asdict(status) if status else None,
        "raw": async_redact_data(raw, TO_REDACT),
    }
