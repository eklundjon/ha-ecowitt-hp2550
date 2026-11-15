"""Backlight light for the Ecowitt HP2550 console."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity

from .entity import Hp2550Entity
from .protocol import GEN_V1, GEN_V2, brightness_to_level, level_to_brightness

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import Hp2550ConfigEntry
    from .coordinator import Hp2550Coordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Hp2550ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the backlight light."""
    async_add_entities([Hp2550Backlight(entry.runtime_data, entry.unique_id)])


class Hp2550Backlight(Hp2550Entity, LightEntity):
    """The console backlight, controlled through the IP command mailbox.

    v2: state is read from the console's status and is authoritative. Power is
    BP only; brightness is BL n, which doesn't change power. While
    auto-brightness is on the level is unknown, so brightness is None.

    v1: the status only records the last command, and the console's schedule
    and auto-brightness can change the backlight without one, so the state is
    assumed.
    """

    _attr_name = "Backlight"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: Hp2550Coordinator, unique_id: str) -> None:
        super().__init__(coordinator, unique_id, "backlight")

    @property
    def assumed_state(self) -> bool:
        data = self.coordinator.data
        return data is None or data.generation != GEN_V2

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return None if data is None else data.on

    @property
    def brightness(self) -> int | None:
        data = self.coordinator.data
        if data is None or not data.on or data.level is None:
            return None
        return level_to_brightness(data.level, data.generation)

    async def async_turn_on(self, **kwargs: Any) -> None:
        data = self.coordinator.data
        gen = data.generation if data else GEN_V1
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if gen == GEN_V2:
            if brightness is not None:
                # BL sets the level (and turns auto off) without changing power.
                # Skip it if the console is already at that manual level.
                level = brightness_to_level(brightness, gen)
                if data.auto or data.level != level:
                    await self.coordinator.async_send("BL", level)
            if not data.on:
                await self.coordinator.async_send("BP", 1)
        elif brightness is not None:
            await self.coordinator.async_send("BL", brightness_to_level(brightness, gen))
        else:
            await self.coordinator.async_send("BL", 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        data = self.coordinator.data
        if data is not None and data.generation == GEN_V2:
            if data.on:
                await self.coordinator.async_send("BP", 0)
        else:
            await self.coordinator.async_send("BL", 0)
