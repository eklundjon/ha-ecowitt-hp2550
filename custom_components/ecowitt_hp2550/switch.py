"""Switches for the Ecowitt HP2550 console (v2 firmware)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory

from .entity import Hp2550Entity
from .protocol import GEN_V2, Hp2550Status

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import Hp2550ConfigEntry
    from .coordinator import Hp2550Coordinator


@dataclass(frozen=True, kw_only=True)
class Hp2550SwitchDescription(SwitchEntityDescription):
    """A switch backed by one status pair and one command verb."""

    value_fn: Callable[[Hp2550Status], bool | None]
    verb: str
    # Display mode only exists if the firmware reports a DM pair.
    available_fn: Callable[[Hp2550Status], bool] = lambda status: True


SWITCHES: tuple[Hp2550SwitchDescription, ...] = (
    Hp2550SwitchDescription(
        key="auto_brightness",
        translation_key="auto_brightness",
        value_fn=lambda s: s.auto,
        verb="BL",
    ),
    Hp2550SwitchDescription(
        key="backlight_schedule",
        translation_key="backlight_schedule",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.schedule,
        verb="BS",
        available_fn=lambda s: s.schedule is not None,
    ),
    Hp2550SwitchDescription(
        key="display_mode",
        translation_key="display_mode",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.display_mode,
        verb="DM",
        available_fn=lambda s: s.display_mode is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Hp2550ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add switches once the console is seen to be v2.

    The generation is only known from a published status, so this also runs on
    every coordinator update: a console that hasn't published yet at setup
    gets its switches when it does, and DM only if the status carries it.
    """
    coordinator = entry.runtime_data
    added: set[str] = set()

    def _add_new() -> None:
        status = coordinator.data
        if status is None or status.generation != GEN_V2:
            return
        new = [
            Hp2550Switch(coordinator, entry.unique_id, desc)
            for desc in SWITCHES
            if desc.key not in added and desc.available_fn(status)
        ]
        added.update(e.entity_description.key for e in new)
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class Hp2550Switch(Hp2550Entity, SwitchEntity):
    """A v2 backlight setting."""

    entity_description: Hp2550SwitchDescription

    def __init__(
        self,
        coordinator: Hp2550Coordinator,
        unique_id: str,
        description: Hp2550SwitchDescription,
    ) -> None:
        super().__init__(coordinator, unique_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return None if data is None else self.entity_description.value_fn(data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.is_on is True:
            return  # already there; every command costs a record push
        await self.coordinator.async_send(
            self.entity_description.verb,
            "A" if self.entity_description.key == "auto_brightness" else 1,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.is_on is False:
            return
        # Auto-brightness has no "off" verb: setting a numeric level turns it
        # off, so restore the last level the integration saw.
        value = (
            self.coordinator.restore_level
            if self.entity_description.key == "auto_brightness"
            else 0
        )
        await self.coordinator.async_send(self.entity_description.verb, value)
