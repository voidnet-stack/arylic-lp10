"""One-shot LP10 restart control."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import LP10Entity
from .const import PROFILE_DETAILED, configured_profile
from .protocol import LP10Error
from .release_profile import ALLOW_SSH_REPAIR


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([
        LP10RestartButton(entry.runtime_data, entry),
        LP10PlaybackToggleButton(entry.runtime_data, entry),
    ])
    if ALLOW_SSH_REPAIR and configured_profile(entry) == PROFILE_DETAILED:
        from .repair_support.entity import async_setup_repair_button

        await async_setup_repair_button(hass, entry, async_add_entities)


class LP10PlaybackToggleButton(LP10Entity, ButtonEntity):
    """Toggle playback without claiming to know the current state."""

    _attr_name = "Play/Pause toggle"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "playback_toggle")

    async def async_press(self) -> None:
        try:
            await self.hass.async_add_executor_job(self.coordinator.client.playback, "toggle")
        except LP10Error as exc:
            raise HomeAssistantError(str(exc)) from exc
        self.coordinator.request_status_refresh()
        await self.coordinator.async_request_refresh()


class LP10RestartButton(LP10Entity, ButtonEntity):
    """Ask the LP10 web-management interface to restart the player."""

    _attr_name = "Restart"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "restart")

    @property
    def available(self) -> bool:
        """Allow recovery when TCP 2018 is down but the web UI still responds."""
        return True

    async def async_press(self) -> None:
        try:
            await self.hass.async_add_executor_job(self.coordinator.client.reboot)
        except LP10Error as exc:
            raise HomeAssistantError(str(exc)) from exc
