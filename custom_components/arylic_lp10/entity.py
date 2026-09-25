"""Base entity shared by the LP10 platforms."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo, format_mac
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_NAME, DOMAIN
from .coordinator import LP10Coordinator


class LP10Entity(CoordinatorEntity[LP10Coordinator]):
    """Attach an entity to the LP10 device registry entry."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LP10Coordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._lp10_key = key
        self._attr_unique_id = f"{coordinator.identity}_{key}"

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Stable markers let the bundled sidebar group renamed entities."""
        return {"lp10_identity": self.coordinator.identity, "lp10_role": self._lp10_key}

    @property
    def device_info(self) -> DeviceInfo:
        details = self.coordinator.data.get("details", {}) if self.coordinator.data else {}
        info = DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.identity)},
            manufacturer="Arylic",
            model="LP10",
            name=details.get("name") or self._entry.data.get(CONF_NAME) or "Arylic LP10",
            sw_version=details.get("software_version"),
            configuration_url=f"http://{self.coordinator.client.host}/",
        )
        if mac := details.get("mac_address"):
            info["connections"] = {("mac", format_mac(mac))}
        return info
