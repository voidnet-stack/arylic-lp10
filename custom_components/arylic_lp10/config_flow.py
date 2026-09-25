"""UI setup for Arylic LP10, by mDNS discovery or manual IP address."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import (
    CONF_ARTWORK_PROXY_HOST, CONF_IDENTITY, CONF_NAME, CONF_PROFILE,
    DEFAULT_PROFILE, DOMAIN, PROFILE_DETAILED, PROFILE_LITE, configured_profile,
)
from .protocol import LP10Error, identify, private_ipv4
from .release_profile import ALLOW_DETAILED_PLAYBACK, ALLOW_SSH_REPAIR


_PROFILE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
    options=[
            SelectOptionDict(value=PROFILE_LITE, label="Lite - controls only"),
            SelectOptionDict(
                value=PROFILE_DETAILED, label="Detailed playback with SSH repair"
            ),
        ],
        mode=SelectSelectorMode.DROPDOWN,
    )
)
_PROFILE_CHOICE_ENABLED = ALLOW_DETAILED_PLAYBACK and ALLOW_SSH_REPAIR


def _profile_fields(default: str = DEFAULT_PROFILE) -> dict:
    if not _PROFILE_CHOICE_ENABLED:
        return {}
    return {vol.Required(CONF_PROFILE, default=default): _PROFILE_SELECTOR}


def _selected_profile(user_input: dict[str, Any]) -> str:
    if not ALLOW_DETAILED_PLAYBACK:
        return PROFILE_LITE
    if not ALLOW_SSH_REPAIR:
        return PROFILE_DETAILED
    return user_input.get(CONF_PROFILE, DEFAULT_PROFILE)


class LP10ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure one verified LP10 per entry."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, str] | None = None

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return LP10OptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow manual setup by an IP address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                host = private_ipv4(user_input[CONF_HOST])
                info = await self.hass.async_add_executor_job(identify, host)
            except LP10Error:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(info["identity"], raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=info["name"],
                    data={
                        CONF_HOST: host,
                        CONF_IDENTITY: info["identity"],
                        CONF_NAME: info["name"],
                        CONF_PROFILE: _selected_profile(user_input),
                    },
                )
        fields = {vol.Required(CONF_HOST): str}
        fields.update(_profile_fields())
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(fields),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address only after verifying it is the same LP10."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                host = private_ipv4(user_input[CONF_HOST])
                info = await self.hass.async_add_executor_job(identify, host)
            except LP10Error:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(info["identity"])
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: host, CONF_NAME: info["name"]}
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}),
            errors=errors,
        )

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Offer an advertised candidate without contacting the player.

        AirPlay device IDs need not match the LSSDP USN used by existing entries.
        A discovery-only key deduplicates/permits ignoring advertisements; it is
        replaced by the verified legacy identity after explicit confirmation.
        """
        model = discovery_info.properties.get("model", "")
        airplay_model = discovery_info.properties.get("am", "")
        if isinstance(model, bytes):
            model = model.decode("utf-8", errors="replace")
        if isinstance(airplay_model, bytes):
            airplay_model = airplay_model.decode("utf-8", errors="replace")
        if not any(isinstance(value, str) and value.lower() == "lp10"
                   for value in (model, airplay_model)):
            return self.async_abort(reason="not_lp10")
        try:
            host = private_ipv4(discovery_info.host)
        except LP10Error:
            return self.async_abort(reason="cannot_connect")
        if any(entry.data.get(CONF_HOST) == host for entry in self._async_current_entries()):
            return self.async_abort(reason="already_configured")
        service_name = discovery_info.name
        if not isinstance(service_name, str) or not service_name or len(service_name) > 255:
            return self.async_abort(reason="cannot_connect")
        await self.async_set_unique_id(f"zeroconf:{service_name.lower()}")
        self._abort_if_unique_id_configured()
        name = service_name.removesuffix("._airplay._tcp.local.").strip()
        name = name if name and name.isprintable() else "Arylic LP10"
        self._discovered = {"host": host, "name": name}
        self.context["title_placeholders"] = {"name": name}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Never add a discovered player without user confirmation."""
        if self._discovered is None:
            return self.async_abort(reason="cannot_connect")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await self.hass.async_add_executor_job(identify, self._discovered["host"])
            except LP10Error:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(info["identity"])
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: info["host"]}, reload_on_update=True
                )
                return self.async_create_entry(
                    title=info["name"],
                    data={
                        CONF_HOST: info["host"],
                        CONF_IDENTITY: info["identity"],
                        CONF_NAME: info["name"],
                        CONF_PROFILE: _selected_profile(user_input),
                    },
                )
        fields = _profile_fields()
        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=vol.Schema(fields),
            description_placeholders={
                "name": self._discovered["name"],
                "host": self._discovered["host"],
            },
            errors=errors,
        )


class LP10OptionsFlow(config_entries.OptionsFlow):
    """Configure the player feature profile and optional artwork host."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            artwork_host = user_input.get(CONF_ARTWORK_PROXY_HOST, "").strip()
            if artwork_host:
                try:
                    artwork_host = private_ipv4(artwork_host)
                except LP10Error:
                    errors[CONF_ARTWORK_PROXY_HOST] = "invalid_artwork_host"
            if not errors:
                options = {CONF_ARTWORK_PROXY_HOST: artwork_host}
                if _PROFILE_CHOICE_ENABLED:
                    options[CONF_PROFILE] = _selected_profile(user_input)
                return self.async_create_entry(data=options)
        fields = {
            vol.Optional(
                CONF_ARTWORK_PROXY_HOST,
                default=self.config_entry.options.get(CONF_ARTWORK_PROXY_HOST, ""),
            ): str,
        }
        fields.update(_profile_fields(configured_profile(self.config_entry)))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(fields),
            errors=errors,
        )
