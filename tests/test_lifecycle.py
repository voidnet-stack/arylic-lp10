"""Offline contracts for persistent connection ownership during HA lifecycle.

Actual integration entry functions run against minimal HA API doubles. An HA
test-bed check is still required for real platform/coordinator scheduling.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


COMPONENT = Path(__file__).parents[1] / "custom_components" / "arylic_lp10"
PACKAGE = "_lp10_lifecycle_tests"


def load_integration():
    modules = {}
    for name in (
        "homeassistant", "homeassistant.components", "homeassistant.components.frontend",
        "homeassistant.components.panel_custom", "homeassistant.components.http",
        "homeassistant.config_entries", "homeassistant.const", "homeassistant.core",
        "homeassistant.exceptions", "homeassistant.helpers", "homeassistant.helpers.config_validation",
        "voluptuous",
        f"{PACKAGE}.const", f"{PACKAGE}.coordinator", f"{PACKAGE}.protocol",
        f"{PACKAGE}.release_profile",
    ):
        modules[name] = ModuleType(name)
    modules["homeassistant.components"].frontend = modules["homeassistant.components.frontend"]
    modules["homeassistant.components"].panel_custom = modules["homeassistant.components.panel_custom"]
    modules["homeassistant.components.frontend"].async_remove_panel = MagicMock()
    modules["homeassistant.components.panel_custom"].async_register_panel = AsyncMock()
    modules["homeassistant.components.http"].StaticPathConfig = lambda *args, **kwargs: (args, kwargs)
    modules["homeassistant.config_entries"].ConfigEntry = SimpleNamespace
    modules["homeassistant.core"].HomeAssistant = SimpleNamespace
    modules["homeassistant.core"].ServiceCall = SimpleNamespace
    modules["homeassistant.exceptions"].HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    modules["homeassistant.helpers"].config_validation = modules["homeassistant.helpers.config_validation"]
    modules["homeassistant.helpers.config_validation"].string = str
    modules["homeassistant.helpers.config_validation"].entity_id = str
    modules["voluptuous"].Schema = lambda value: value
    modules["voluptuous"].Required = lambda value, **kwargs: value
    modules["voluptuous"].Optional = lambda value, **kwargs: value
    modules["voluptuous"].All = lambda *args: args[0] if args else None
    modules["voluptuous"].Coerce = lambda value: value
    modules["voluptuous"].Range = lambda **kwargs: lambda value: value
    modules["voluptuous"].Length = lambda **kwargs: lambda value: value
    modules["voluptuous"].Invalid = ValueError
    modules["homeassistant.const"].CONF_HOST = "host"
    modules["homeassistant.const"].EVENT_HOMEASSISTANT_STOP = "homeassistant_stop"
    modules["homeassistant.const"].Platform = SimpleNamespace(**{
        key: key.lower() for key in ("MEDIA_PLAYER", "SENSOR", "SWITCH", "BUTTON", "NUMBER", "SELECT")
    })
    modules[f"{PACKAGE}.const"].CONF_IDENTITY = "identity"
    modules[f"{PACKAGE}.const"].DOMAIN = "arylic_lp10"
    modules[f"{PACKAGE}.const"].PROFILE_DETAILED = "detailed_with_repair"
    modules[f"{PACKAGE}.const"].configured_profile = lambda entry: entry.options.get(
        "profile", entry.data.get("profile", "detailed_with_repair")
    )
    modules[f"{PACKAGE}.release_profile"].ALLOW_DETAILED_PLAYBACK = True
    modules[f"{PACKAGE}.release_profile"].ALLOW_SSH_REPAIR = False
    modules[f"{PACKAGE}.coordinator"].LP10Coordinator = MagicMock()
    modules[f"{PACKAGE}.protocol"].LP10Client = MagicMock()
    modules[f"{PACKAGE}.protocol"].LP10Error = type("LP10Error", (Exception,), {})
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location(
            PACKAGE, COMPONENT / "__init__.py", submodule_search_locations=[str(COMPONENT)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE] = module
        spec.loader.exec_module(module)
        return module


integration = load_integration()


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = MagicMock()
        self.coordinator = SimpleNamespace(
            client=self.client, async_config_entry_first_refresh=AsyncMock()
        )
        self.entry = SimpleNamespace(
            data={"host": "192.168.50.10", "identity": "a1b2c3d4e5f6"},
            options={},
            entry_id="office", async_on_unload=MagicMock(),
            add_update_listener=MagicMock(return_value=MagicMock()),
        )

        async def executor(func, *args):
            return func(*args)

        self.hass = SimpleNamespace(
            data={}, async_add_executor_job=AsyncMock(side_effect=executor),
            config_entries=SimpleNamespace(
                async_forward_entry_setups=AsyncMock(), async_unload_platforms=AsyncMock(return_value=True)
            ),
            http=SimpleNamespace(async_register_static_paths=AsyncMock()),
            services=SimpleNamespace(
                has_service=MagicMock(return_value=False), async_register=MagicMock()
            ),
            bus=SimpleNamespace(async_listen_once=MagicMock(return_value=MagicMock())),
        )
        self.patches = [
            patch.object(integration, "LP10Client", return_value=self.client),
            patch.object(integration, "LP10Coordinator", return_value=self.coordinator),
            patch.object(integration.panel_custom, "async_register_panel", new_callable=AsyncMock),
            patch.object(integration.frontend, "async_remove_panel", new_callable=MagicMock),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def test_first_refresh_failure_closes_client_and_propagates(self):
        self.coordinator.async_config_entry_first_refresh.side_effect = RuntimeError("initial read failed")
        with self.assertRaisesRegex(RuntimeError, "initial read failed"):
            await integration.async_setup_entry(self.hass, self.entry)
        self.client.close.assert_called_once_with()
        self.hass.config_entries.async_forward_entry_setups.assert_not_called()
        self.hass.bus.async_listen_once.assert_not_called()

    async def test_cancelled_first_refresh_closes_client_and_propagates_cancellation(self):
        self.coordinator.async_config_entry_first_refresh.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await integration.async_setup_entry(self.hass, self.entry)
        self.client.close.assert_called_once_with()
        self.hass.bus.async_listen_once.assert_not_called()

    async def test_later_setup_failure_also_closes_client(self):
        integration.panel_custom.async_register_panel.side_effect = RuntimeError("panel failed")
        with self.assertRaisesRegex(RuntimeError, "panel failed"):
            await integration.async_setup_entry(self.hass, self.entry)
        self.client.close.assert_called_once_with()
        self.hass.config_entries.async_forward_entry_setups.assert_awaited_once()

    async def test_successful_setup_retains_connection_and_registers_stop_cleanup(self):
        self.assertTrue(await integration.async_setup_entry(self.hass, self.entry))
        self.assertTrue(integration.LP10Coordinator.call_args.kwargs["metadata_enabled"])
        self.client.close.assert_not_called()
        self.assertIs(self.entry.runtime_data, self.coordinator)
        event, callback = self.hass.bus.async_listen_once.call_args.args
        self.assertEqual(event, "homeassistant_stop")
        self.assertEqual(self.entry.async_on_unload.call_count, 2)
        self.entry.async_on_unload.assert_any_call(self.hass.bus.async_listen_once.return_value)
        await callback(SimpleNamespace())
        self.client.close.assert_called_once_with()

    async def test_lite_profile_disables_metadata_polling(self):
        self.entry.data["profile"] = "lite"
        self.assertTrue(await integration.async_setup_entry(self.hass, self.entry))
        self.assertFalse(integration.LP10Coordinator.call_args.kwargs["metadata_enabled"])

    async def test_successful_unload_closes_after_platforms_stop(self):
        await integration.async_setup_entry(self.hass, self.entry)

        async def unload(*args):
            self.client.close.assert_not_called()
            return True

        self.hass.config_entries.async_unload_platforms.side_effect = unload
        self.assertTrue(await integration.async_unload_entry(self.hass, self.entry))
        self.client.close.assert_called_once_with()
        integration.frontend.async_remove_panel.assert_called_once_with(self.hass, integration.DOMAIN)

    async def test_failed_unload_preserves_live_connection_and_panel(self):
        await integration.async_setup_entry(self.hass, self.entry)
        self.hass.config_entries.async_unload_platforms.return_value = False
        self.assertFalse(await integration.async_unload_entry(self.hass, self.entry))
        self.client.close.assert_not_called()
        integration.frontend.async_remove_panel.assert_not_called()
        self.assertIn(self.entry.entry_id, self.hass.data[integration.DOMAIN]["entries"])

    async def test_one_unload_does_not_remove_panel_for_other_entries(self):
        await integration.async_setup_entry(self.hass, self.entry)
        self.hass.data[integration.DOMAIN]["entries"].add("bedroom")
        self.assertTrue(await integration.async_unload_entry(self.hass, self.entry))
        self.client.close.assert_called_once_with()
        integration.frontend.async_remove_panel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
