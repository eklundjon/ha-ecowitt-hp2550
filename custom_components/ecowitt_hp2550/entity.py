"""Shared base entity for the Ecowitt HP2550 console."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, format_mac
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Hp2550Coordinator


class Hp2550Entity(CoordinatorEntity[Hp2550Coordinator]):
    """An entity on the console's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: Hp2550Coordinator, unique_id: str, key: str) -> None:
        super().__init__(coordinator)
        mac = format_mac(unique_id)
        self._attr_unique_id = f"{mac}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            connections={(CONNECTION_NETWORK_MAC, mac)},
            name="Ecowitt HP2550",
            manufacturer="Ecowitt / Ambient Weather",
            model="HP2550",
        )
