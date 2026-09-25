"""Device and playback diagnostics for Arylic LP10."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import LP10Entity
from .const import PROFILE_DETAILED, configured_profile
from .release_profile import ALLOW_SSH_REPAIR


@dataclass(frozen=True)
class SensorDefinition:
    key: str
    name: str
    group: str = "details"
    unit: str | None = None
    diagnostic: bool = True


SENSORS = (
    SensorDefinition("name", "Device name"),
    SensorDefinition("ip_address", "IP address"),
    SensorDefinition("software_version", "Software version"),
    SensorDefinition("firmware_version", "Firmware version"),
    SensorDefinition("mac_address", "MAC address"),
    SensorDefinition("connectivity", "Connectivity type"),
    SensorDefinition("mcu_version", "MCU version"),
    SensorDefinition("serial_number", "Serial number"),
    SensorDefinition("uptime_seconds", "Uptime", unit="s"),
    SensorDefinition("metadata_status", "Playback metadata status", group="now_playing"),
    SensorDefinition("sample_rate", "Sample rate", group="now_playing", unit="Hz", diagnostic=False),
    SensorDefinition("bit_depth", "Bit depth", group="now_playing", unit="bit", diagnostic=False),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        LP10Sensor(coordinator, entry, definition)
        for definition in SENSORS
        if coordinator.metadata_enabled or definition.group != "now_playing"
    )
    if ALLOW_SSH_REPAIR and configured_profile(entry) == PROFILE_DETAILED:
        from .repair_support.entity import async_setup_status_sensor

        await async_setup_status_sensor(hass, entry, async_add_entities)


class LP10Sensor(LP10Entity, SensorEntity):
    """One value from the shared LP10 snapshot."""

    def __init__(self, coordinator, entry: ConfigEntry, definition: SensorDefinition) -> None:
        super().__init__(coordinator, entry, definition.key)
        self.definition = definition
        self._attr_name = definition.name
        self._attr_native_unit_of_measurement = definition.unit
        if definition.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> Any:
        source = self.coordinator.data.get(self.definition.group, {})
        if self.definition.group == "now_playing" and self.definition.key != "metadata_status" and (
            not source.get("available") or source.get("idle")
        ):
            return None
        return source.get(self.definition.key)
