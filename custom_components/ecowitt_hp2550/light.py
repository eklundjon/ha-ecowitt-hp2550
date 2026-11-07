"""Backlight light for the Ecowitt HP2550 console."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, format_mac
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Hp2550Coordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import Hp2550ConfigEntry


def _level_to_brightness(level: int) -> int:
    """Console level 0-100 -> HA brightness 0-255."""
    return round(level * 255 / 100)


def _brightness_to_level(brightness: int) -> int:
    """HA brightness 0-255 -> console level, clamped to 2-100.

    Levels 0 and 1 are 'off' and 'on at the saved level' in the command
    language, so a brightness request maps to 2-100, never below.
    """
    return max(2, min(100, round(brightness * 100 / 255)))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Hp2550ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the backlight light."""
    async_add_entities([Hp2550Backlight(entry.runtime_data, entry)])


class Hp2550Backlight(CoordinatorEntity[Hp2550Coordinator], LightEntity):
    """The console backlight, controlled through the IP command mailbox.

    State is assumed: the console's own backlight schedule and auto-brightness
    can change the backlight with no command, and the status slot does not
    report those. So the entity tracks what it last commanded or last read, and
    reconciles from the status slot only for the command it is waiting on.
    """

    _attr_has_entity_name = True
    _attr_name = "Backlight"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_assumed_state = True

    def __init__(
        self, coordinator: Hp2550Coordinator, entry: Hp2550ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        mac = format_mac(entry.unique_id)
        self._attr_unique_id = f"{mac}_backlight"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            connections={(CONNECTION_NETWORK_MAC, mac)},
            name="Ecowitt HP2550",
            manufacturer="Ecowitt / Ambient Weather",
            model="HP2550",
        )
        self._is_on: bool | None = None
        self._brightness: int | None = None
        self._pending_seq: int | None = None

    @property
    def is_on(self) -> bool | None:
        return self._is_on

    @property
    def brightness(self) -> int | None:
        return self._brightness

    def _reconcile(self) -> None:
        """Adopt the status slot, but only for the command we are awaiting.

        A poll in the few seconds before the console acts still shows the
        previous command's report; ignoring a seq mismatch while a command is
        pending keeps that stale report from reverting the optimistic state.
        """
        status = self.coordinator.data
        if status is None:
            return
        if self._pending_seq is None or status.seq == self._pending_seq:
            self._is_on = status.on
            self._brightness = (
                _level_to_brightness(status.level) if status.on else None
            )
            self._pending_seq = None

    def _handle_coordinator_update(self) -> None:
        self._reconcile()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._reconcile()

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            level = _brightness_to_level(kwargs[ATTR_BRIGHTNESS])
            self._pending_seq = await self.coordinator.async_send(level)
            self._is_on = True
            self._brightness = _level_to_brightness(level)
        else:
            self._pending_seq = await self.coordinator.async_send(1)
            self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._pending_seq = await self.coordinator.async_send(0)
        self._is_on = False
        self._brightness = None
        self.async_write_ha_state()
