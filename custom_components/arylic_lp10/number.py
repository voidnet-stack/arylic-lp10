"""Tone, sound-shaping and custom equalizer controls."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import LP10Entity
from .protocol import EQ_FREQUENCIES, LP10Error


@dataclass(frozen=True)
class NumberDefinition:
    key: str
    name: str
    minimum: int
    maximum: int
    unit: str
    setting: str | None = None
    band: int | None = None


SOUND_NUMBERS = (
    NumberDefinition("treble", "Treble", -10, 10, "dB", setting="treble"),
    NumberDefinition("mid", "Mid", -10, 10, "dB", setting="mid"),
    NumberDefinition("bass", "Bass", -10, 10, "dB", setting="bass"),
    NumberDefinition("balance", "Balance", -100, 100, PERCENTAGE, setting="balance"),
    NumberDefinition("max_volume", "Maximum volume", 30, 100, PERCENTAGE, setting="max_volume"),
    NumberDefinition(
        "deep_bass_intensity", "Deep Bass intensity", 0, 100, PERCENTAGE,
        setting="deep_bass_intensity",
    ),
)
EQ_NUMBERS = tuple(
    NumberDefinition(f"eq_band_{index + 1}", f"Custom EQ band {index + 1}", -10, 10, "dB", band=index)
    for index, _frequency in enumerate(EQ_FREQUENCIES)
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        LP10SoundNumber(coordinator, entry, definition)
        for definition in (*SOUND_NUMBERS, *EQ_NUMBERS)
    )


class LP10SoundNumber(LP10Entity, NumberEntity):
    """One writable sound control backed by the LP10's own reported value."""

    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator, entry: ConfigEntry, definition: NumberDefinition) -> None:
        super().__init__(coordinator, entry, definition.key)
        self.definition = definition
        self._attr_name = definition.name
        self._attr_native_min_value = definition.minimum
        self._attr_native_max_value = definition.maximum
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = definition.unit
        if definition.band is not None:
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def native_value(self) -> float | None:
        if self.definition.band is None:
            return self.coordinator.data.get("status", {}).get(self.definition.key)
        if self.coordinator.data.get("status", {}).get("eq_preset") != 10:
            return None
        bands = self.coordinator.data.get("custom_eq", {}).get("bands", [])
        if len(bands) <= self.definition.band or bands[self.definition.band] is None:
            return None
        return bands[self.definition.band].get("gain_db")

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        if self.definition.band is not None:
            return (
                self.coordinator.data.get("status", {}).get("eq_preset") == 10
                and len(self.coordinator.data.get("custom_eq", {}).get("bands", [])) > self.definition.band
                and self.coordinator.data["custom_eq"]["bands"][self.definition.band] is not None
                and self.coordinator.data["custom_eq"]["bands"][self.definition.band].get("gain_db") is not None
            )
        return self.coordinator.data.get("status", {}).get(self.definition.key) is not None

    async def async_set_native_value(self, value: float) -> None:
        definition = self.definition
        try:
            if definition.band is not None:
                band = self.coordinator.data["custom_eq"]["bands"][definition.band]
                await self.hass.async_add_executor_job(
                    self.coordinator.client.set_custom_eq_band,
                    definition.band,
                    int(value),
                    band["frequency_code"],
                    band["q_code"],
                )
                # Reflect this one confirmed write locally. Do not immediately
                # request a status + eight-band readback from the LP10.
                self.coordinator.note_custom_eq_write(definition.band, int(value))
                return
            else:
                await self.hass.async_add_executor_job(
                    self.coordinator.client.set_sound_setting, definition.setting, int(value)
                )
        except LP10Error as exc:
            raise HomeAssistantError(str(exc)) from exc
        self.coordinator.request_status_refresh()
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self):
        attributes = super().extra_state_attributes
        if self.definition.band is not None:
            bands = self.coordinator.data.get("custom_eq", {}).get("bands", [])
            if len(bands) > self.definition.band and bands[self.definition.band] is not None:
                band = bands[self.definition.band]
                attributes["frequency_hz"] = band.get("frequency_hz")
                attributes["q_factor"] = band.get("q_factor")
        return attributes
