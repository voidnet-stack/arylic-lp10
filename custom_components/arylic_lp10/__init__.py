"""Arylic LP10 integration for Home Assistant."""

from __future__ import annotations

import asyncio
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import (
    CONF_IDENTITY, DOMAIN, PROFILE_DETAILED, configured_profile,
)
from .coordinator import LP10Coordinator
from .protocol import LP10Client, LP10Error
from .release_profile import ALLOW_DETAILED_PLAYBACK, ALLOW_SSH_REPAIR

if ALLOW_SSH_REPAIR:
    from .repair_support import LP10RepairCoordinator

PLATFORMS = [
    Platform.MEDIA_PLAYER, Platform.SENSOR, Platform.SWITCH, Platform.BUTTON,
    Platform.NUMBER, Platform.SELECT,
]


def _register_panel_services(hass: HomeAssistant) -> None:
    """Expose the multi-command named custom-EQ write to the bundled panel."""
    if hass.services.has_service(DOMAIN, "create_custom_eq"):
        return

    async def async_create_custom_eq(call: ServiceCall) -> None:
        target = hass.states.get(call.data["entity_id"])
        identity = target.attributes.get("lp10_identity") if target else None
        role = target.attributes.get("lp10_role") if target else None
        if not identity or role != "eq_preset":
            raise HomeAssistantError("Choose the EQ preset entity for one LP10")
        entry = next((
            item for item in hass.config_entries.async_entries(DOMAIN)
            if item.data.get(CONF_IDENTITY) == identity and item.runtime_data is not None
        ), None)
        if entry is None:
            raise HomeAssistantError("That LP10 integration entry is not loaded")
        coordinator = entry.runtime_data
        try:
            await hass.async_add_executor_job(
                coordinator.client.create_custom_eq,
                call.data["name"], call.data["bands"],
            )
        except LP10Error as exc:
            raise HomeAssistantError(str(exc)) from exc
        coordinator.request_status_refresh(refresh_custom_eq=True)
        await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        "create_custom_eq",
        async_create_custom_eq,
        schema=vol.Schema({
            vol.Required("entity_id"): cv.entity_id,
            vol.Required("name"): vol.All(str, vol.Length(min=1, max=32)),
            vol.Required("bands"): vol.All(
                [vol.Coerce(int)], vol.Length(min=8, max=8)
            ),
        }),
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one player and its entities."""
    client = LP10Client(entry.data[CONF_HOST])
    try:
        result = await _async_setup_player(hass, entry, client)
    except BaseException:
        # Includes cancelled setup and failures after first refresh.
        await hass.async_add_executor_job(client.close)
        raise

    _register_panel_services(hass)

    async def stop_connection(_event) -> None:
        await hass.async_add_executor_job(client.close)

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_connection))
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return result


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the player when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_setup_player(hass: HomeAssistant, entry: ConfigEntry, client: LP10Client) -> bool:
    """Set up entities and panel with connection cleanup owned by the caller."""
    coordinator = LP10Coordinator(
        hass, client, entry.data[CONF_IDENTITY],
        metadata_enabled=(
            ALLOW_DETAILED_PLAYBACK
            and configured_profile(entry) == PROFILE_DETAILED
        ),
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    repair_enabled = (
        ALLOW_SSH_REPAIR and configured_profile(entry) == PROFILE_DETAILED
    )
    if repair_enabled:
        repair_coordinator = LP10RepairCoordinator(hass, entry)
        coordinator.repair_coordinator = repair_coordinator
        domain_data = hass.data.setdefault(
            DOMAIN,
            {"entries": set(), "registered": False, "static_registered": False,
             "repair_coordinators": {}, "lock": asyncio.Lock()},
        )
        domain_data.setdefault("repair_coordinators", {})[entry.entry_id] = repair_coordinator
        def remove_repair_coordinator() -> None:
            domain_data["repair_coordinators"].pop(entry.entry_id, None)

        entry.async_on_unload(remove_repair_coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    panel_state = hass.data.setdefault(
        DOMAIN,
        {"entries": set(), "registered": False, "static_registered": False,
         "repair_coordinators": {}, "lock": asyncio.Lock()},
    )
    async with panel_state["lock"]:
        if not panel_state["registered"]:
            if not panel_state["static_registered"]:
                await hass.http.async_register_static_paths([
                    StaticPathConfig(
                        "/arylic_lp10_static",
                        str(Path(__file__).parent / "frontend"),
                        cache_headers=False,
                    )
                ])
                panel_state["static_registered"] = True
            await panel_custom.async_register_panel(
                hass,
                frontend_url_path=DOMAIN,
                webcomponent_name="arylic-lp10-panel",
                sidebar_title="LP10 Control",
                sidebar_icon="mdi:speaker-multiple",
                module_url="/arylic_lp10_static/panel.js",
                config={"domain": DOMAIN},
            )
            panel_state["registered"] = True
        panel_state["entries"].add(entry.entry_id)
    if repair_enabled:
        # SSH diagnostics must not delay playback/control setup if a player
        # has SSH disabled or is temporarily unreachable.
        async def refresh_repair_status() -> None:
            await repair_coordinator.async_request_refresh()

        hass.async_create_task(refresh_repair_status())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove all entities and stop polling."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await hass.async_add_executor_job(entry.runtime_data.client.close)
    panel_state = hass.data.get(DOMAIN)
    if panel_state:
        async with panel_state["lock"]:
            panel_state["entries"].discard(entry.entry_id)
            if not panel_state["entries"] and panel_state["registered"]:
                frontend.async_remove_panel(hass, DOMAIN)
                panel_state["registered"] = False
            if not panel_state["entries"] and hass.services.has_service(DOMAIN, "create_custom_eq"):
                hass.services.async_remove(DOMAIN, "create_custom_eq")
    return True
