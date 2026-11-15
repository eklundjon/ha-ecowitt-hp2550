"""Entity tests: light and switches against a mocked client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecowitt_hp2550 import protocol as p
from custom_components.ecowitt_hp2550.const import DEFAULT_LEVEL, DOMAIN

MAC = "aa:bb:cc:dd:ee:ff"
V2 = "BP 1 BL 50 BS 0 #4"


class State:
    """A scripted console: send_command applies the command and echoes it."""

    def __init__(self, text, generation=p.GEN_V2):
        self.text = text
        self.gen = generation
        self.sent: list[tuple[str, str]] = []
        self.reject = False

    async def read_status(self):
        if self.gen == p.GEN_V2:
            return p.parse_status_v2(self.text)
        return p.parse_status_v1(self.text)

    async def send_command(self, verb, value, generation, **kw):
        if self.reject:
            raise p.Hp2550Rejected(f"{verb} {value}", await self.read_status())
        self.sent.append((verb, str(value)))
        s = await self.read_status() or p.Hp2550Status(
            generation=p.GEN_V1, on=False, level=0, seq=0)
        on, level, auto, sched = s.on, s.level, s.auto, s.schedule
        value = str(value)
        if self.gen == p.GEN_V2:
            if verb == "BP":
                on = value == "1"
            elif verb == "BL":
                auto, level = (True, None) if value == "A" else (False, int(value))
            elif verb == "BS":
                sched = value == "1"
            lv = "A" if auto else level
            if auto is False and level is None:
                lv = DEFAULT_LEVEL
            self.text = f"BP {int(on)} BL {lv} BS {int(bool(sched))} #{s.seq + 1}"
        else:
            v = int(value)
            self.text = f"BL {int(v > 0)} {s.level if v < 2 else v} #{s.seq + 1}"
        return await self.read_status()


async def _setup(hass, state):
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4"}, unique_id=MAC)
    entry.add_to_hass(hass)
    with patch("custom_components.ecowitt_hp2550.coordinator.Hp2550Client") as cls:
        cls.return_value.read_status = AsyncMock(side_effect=state.read_status)
        cls.return_value.send_command = AsyncMock(side_effect=state.send_command)
        cls.return_value.pending_seq = None
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_v2_light_and_switches(hass: HomeAssistant):
    state = State(V2)
    await _setup(hass, state)
    light = hass.states.get("light.ecowitt_hp2550_backlight")
    assert light.state == "on"
    assert light.attributes["brightness"] == p.level_to_brightness(50, p.GEN_V2)
    assert "assumed_state" not in light.attributes
    assert hass.states.get("switch.ecowitt_hp2550_auto_brightness").state == "off"
    assert hass.states.get("switch.ecowitt_hp2550_backlight_schedule").state == "off"
    assert hass.states.get("switch.ecowitt_hp2550_dark_mode") is None


async def test_v2_turn_on_lit_panel_sends_no_bp(hass: HomeAssistant):
    state = State(V2)
    await _setup(hass, state)
    await hass.services.async_call("light", "turn_on",
        {"entity_id": "light.ecowitt_hp2550_backlight"}, blocking=True)
    assert state.sent == []
    await hass.services.async_call("light", "turn_on",
        {"entity_id": "light.ecowitt_hp2550_backlight", "brightness": 1}, blocking=True)
    assert state.sent == [("BL", "0")]


async def test_v2_brightness_while_off_sends_bl_then_bp(hass: HomeAssistant):
    state = State("BP 0 BL 50 BS 0 #4")
    await _setup(hass, state)
    await hass.services.async_call("light", "turn_on",
        {"entity_id": "light.ecowitt_hp2550_backlight", "brightness": 255}, blocking=True)
    assert state.sent == [("BL", "100"), ("BP", "1")]
    await hass.services.async_call("light", "turn_off",
        {"entity_id": "light.ecowitt_hp2550_backlight"}, blocking=True)
    assert state.sent[-1] == ("BP", "0")


async def test_auto_on_brightness_unknown_then_off_restores_last_level(hass: HomeAssistant):
    state = State(V2)  # level 50 seen first
    await _setup(hass, state)
    await hass.services.async_call("switch", "turn_on",
        {"entity_id": "switch.ecowitt_hp2550_auto_brightness"}, blocking=True)
    assert state.sent == [("BL", "A")]
    light = hass.states.get("light.ecowitt_hp2550_backlight")
    assert light.state == "on" and "brightness" not in light.attributes or \
        light.attributes["brightness"] is None
    await hass.services.async_call("switch", "turn_off",
        {"entity_id": "switch.ecowitt_hp2550_auto_brightness"}, blocking=True)
    assert state.sent[-1] == ("BL", "50")


async def test_auto_off_without_known_level_uses_default(hass: HomeAssistant):
    state = State("BP 1 BL A BS 1 #9")
    await _setup(hass, state)
    await hass.services.async_call("switch", "turn_off",
        {"entity_id": "switch.ecowitt_hp2550_auto_brightness"}, blocking=True)
    assert state.sent == [("BL", str(DEFAULT_LEVEL))]


async def test_display_mode_switch_only_with_dm(hass: HomeAssistant):
    state = State("BP 1 BL 50 BS 0 DM 1 #2")
    await _setup(hass, state)
    assert hass.states.get("switch.ecowitt_hp2550_dark_mode").state == "on"


async def test_v1_is_light_only_and_assumed(hass: HomeAssistant):
    state = State("BL 1 40 #3", generation=p.GEN_V1)
    await _setup(hass, state)
    light = hass.states.get("light.ecowitt_hp2550_backlight")
    assert light.attributes["assumed_state"] is True
    assert hass.states.async_entity_ids("switch") == []
    await hass.services.async_call("light", "turn_on",
        {"entity_id": "light.ecowitt_hp2550_backlight", "brightness": 1}, blocking=True)
    assert state.sent == [("BL", "2")]
    await hass.services.async_call("light", "turn_off",
        {"entity_id": "light.ecowitt_hp2550_backlight"}, blocking=True)
    assert state.sent[-1] == ("BL", "0")


async def test_fresh_v1_console_with_no_status_accepts_v1_commands(hass: HomeAssistant):
    state = State("", generation=p.GEN_V1)
    await _setup(hass, state)
    light = hass.states.get("light.ecowitt_hp2550_backlight")
    assert light.state == "unknown"
    assert light.attributes["assumed_state"] is True
    assert hass.states.async_entity_ids("switch") == []
    await hass.services.async_call("light", "turn_on",
        {"entity_id": "light.ecowitt_hp2550_backlight"}, blocking=True)
    assert state.sent == [("BL", "1")]
    assert hass.states.get("light.ecowitt_hp2550_backlight").state == "on"
    await hass.services.async_call("light", "turn_on",
        {"entity_id": "light.ecowitt_hp2550_backlight", "brightness": 128}, blocking=True)
    assert state.sent[-1] == ("BL", "50")


async def test_no_status_turn_off_sends_v1_off(hass: HomeAssistant):
    state = State("", generation=p.GEN_V1)
    await _setup(hass, state)
    await hass.services.async_call("light", "turn_off",
        {"entity_id": "light.ecowitt_hp2550_backlight"}, blocking=True)
    assert state.sent == [("BL", "0")]


async def test_v2_skips_commands_already_in_target_state(hass: HomeAssistant):
    state = State("BP 1 BL 50 BS 1 #4")
    await _setup(hass, state)
    same = p.level_to_brightness(50, p.GEN_V2)
    await hass.services.async_call("light", "turn_on",
        {"entity_id": "light.ecowitt_hp2550_backlight", "brightness": same}, blocking=True)
    await hass.services.async_call("switch", "turn_on",
        {"entity_id": "switch.ecowitt_hp2550_backlight_schedule"}, blocking=True)
    await hass.services.async_call("switch", "turn_off",
        {"entity_id": "switch.ecowitt_hp2550_auto_brightness"}, blocking=True)
    assert state.sent == []
    await hass.services.async_call("switch", "turn_off",
        {"entity_id": "switch.ecowitt_hp2550_backlight_schedule"}, blocking=True)
    assert state.sent == [("BS", "0")]


async def test_rejected_command_raises_and_logs(hass: HomeAssistant, caplog):
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    state = State(V2)
    await _setup(hass, state)
    state.reject = True
    with pytest.raises(HomeAssistantError, match="rejected"):
        await hass.services.async_call("switch", "turn_on",
            {"entity_id": "switch.ecowitt_hp2550_auto_brightness"}, blocking=True)
    assert "rejected" in caplog.text
    assert hass.states.get("switch.ecowitt_hp2550_auto_brightness").state == "off"
