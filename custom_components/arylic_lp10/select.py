"""Go Control equalizer preset selector."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import LP10Entity
from .protocol import LP10Error


PRESETS = {
    0: "Flat",
    1: "Classical",
    2: "Pop",
    3: "Jazz",
    4: "Rock",
    5: "Vocal",
    10: "Custom",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([LP10EQPresetSelect(entry.runtime_data, entry)])


class LP10EQPresetSelect(LP10Entity, SelectEntity):
    """Select an LP10 built-in preset or its custom EQ curve."""

    _attr_name = "EQ preset"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:equalizer"
    _attr_options = list(PRESETS.values())

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "eq_preset")

    @property
    def current_option(self) -> str | None:
        preset = self.coordinator.data.get("status", {}).get("eq_preset")
        return PRESETS.get(preset)

    async def async_select_option(self, option: str) -> None:
        preset = next((key for key, name in PRESETS.items() if name == option), None)
        if preset is None:
            raise HomeAssistantError("Unknown LP10 EQ preset")
        try:
            await self.hass.async_add_executor_job(self.coordinator.client.set_eq_preset, preset)
        except LP10Error as exc:
            raise HomeAssistantError(str(exc)) from exc
        self.coordinator.request_status_refresh(refresh_custom_eq=preset == 10)
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self):
        attributes = super().extra_state_attributes
        profiles = self.coordinator.data.get("custom_eq", {}).get("profiles", {})
        attributes["custom_eq_profiles"] = list(profiles.values())
        return attributes
