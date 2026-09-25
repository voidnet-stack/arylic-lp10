"""LP10 front-display light switch."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import LP10Entity
from .protocol import LP10Error


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([
        LP10DisplaySwitch(entry.runtime_data, entry),
        LP10DeepBassSwitch(entry.runtime_data, entry),
    ])


class LP10DisplaySwitch(LP10Entity, SwitchEntity):
    """The LP10's front information display, called 'LED Light' in Go Control."""

    _attr_name = "Front display light"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:television-ambient-light"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "front_display")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get("status", {}).get("display_on")

    async def _set(self, on: bool) -> None:
        try:
            await self.hass.async_add_executor_job(self.coordinator.client.set_display, on)
        except LP10Error as exc:
            raise HomeAssistantError(str(exc)) from exc
        self.coordinator.request_status_refresh()
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)


class LP10DeepBassSwitch(LP10Entity, SwitchEntity):
    """Go Control's Deep Bass (VBS) effect toggle."""

    _attr_name = "Deep Bass"
    _attr_icon = "mdi:waveform"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "deep_bass")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get("status", {}).get("deep_bass")

    async def _set(self, on: bool) -> None:
        try:
            await self.hass.async_add_executor_job(self.coordinator.client.set_deep_bass, on)
        except LP10Error as exc:
            raise HomeAssistantError(str(exc)) from exc
        self.coordinator.request_status_refresh()
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)
