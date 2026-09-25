"""Entities for the optional LP10 ADB repair workflow."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from ..entity import LP10Entity
from .coordinator import LP10RepairCoordinator, coordinator_for_entry


async def async_setup_status_sensor(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one per-player SSH repair status sensor."""
    async_add_entities([LP10RepairStatusSensor(coordinator_for_entry(hass, entry), entry)])


class LP10RepairStatusSensor(LP10Entity, SensorEntity):
    """Report what SSH verified about stock and patched adbd."""

    _attr_name = "Repair status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_visible_default = True

    def __init__(self, coordinator: LP10RepairCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "repair_status")

    @property
    def native_value(self) -> str:
        return (self.coordinator.data or {}).get("status", "checking")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            **super().extra_state_attributes,
            **(self.coordinator.data or {}),
        }


async def async_setup_repair_button(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the explicit, status-gated repair action."""
    async_add_entities([LP10ApplyRepairButton(coordinator_for_entry(hass, entry), entry)])


class LP10ApplyRepairButton(LP10Entity, ButtonEntity):
    """Apply the candidate only after SSH verifies the supported stock binary."""

    _attr_name = "Apply ADB fix"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_visible_default = True

    def __init__(self, coordinator: LP10RepairCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "repair")

    @property
    def available(self) -> bool:
        return (
            not self.coordinator.is_applying
            and (self.coordinator.data or {}).get("status") == "fix_available"
        )

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_apply_fix()
        except Exception as exc:
            raise HomeAssistantError(f"LP10 ADB repair stopped: {exc}") from exc
