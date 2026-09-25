"""Offline discovery contracts with a small HA flow-API stand-in.

These exercise actual flow methods without installing/running HA. They do not
replace a Home Assistant integration test of the discovery UI/lifecycle.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


COMPONENT = Path(__file__).parents[1] / "custom_components" / "arylic_lp10"
PACKAGE = "_lp10_discovery_tests"


class AbortFlow(Exception):
    pass


class FlowStandIn:
    def __init_subclass__(cls, **kwargs):
        pass

    async def async_set_unique_id(self, unique_id, **kwargs):
        if unique_id in self.other_flow_ids:
            raise AbortFlow("already_in_progress")
        self.unique_id = unique_id

    def _async_current_entries(self):
        return self.entries

    def _abort_if_unique_id_configured(self, updates=None, reload_on_update=False):
        for entry in self.entries:
            if entry.unique_id == self.unique_id:
                if updates:
                    entry.data.update(updates)
                    entry.reloaded = reload_on_update
                raise AbortFlow("already_configured")

    def _get_reconfigure_entry(self):
        return self.entries[0]

    def _abort_if_unique_id_mismatch(self, reason):
        if self.unique_id != self.entries[0].unique_id:
            raise AbortFlow(reason)

    def async_update_reload_and_abort(self, entry, data_updates):
        entry.data.update(data_updates)
        return self.async_abort(reason="reconfigure_successful")

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}


def load_modules():
    package = ModuleType(PACKAGE)
    package.__path__ = [str(COMPONENT)]
    modules = {PACKAGE: package}
    for name in (
        "voluptuous", "homeassistant", "homeassistant.config_entries",
        "homeassistant.const", "homeassistant.helpers", "homeassistant.helpers.selector",
        "homeassistant.helpers.service_info", "homeassistant.helpers.service_info.zeroconf",
        f"{PACKAGE}.const",
    ):
        modules[name] = ModuleType(name)
    class Selector:
        def __init__(self, config=None, **kwargs):
            self.config = config
            self.kwargs = kwargs

        def __call__(self, value):
            return value

    class SelectOptionDict(dict):
        def __init__(self, *, value, label):
            super().__init__(value=value, label=label)

    class OptionsFlowStandIn:
        pass

    modules["voluptuous"].Schema = lambda value: value
    modules["voluptuous"].Required = lambda value, **kwargs: value
    modules["voluptuous"].Optional = lambda value, **kwargs: value
    modules["homeassistant"].config_entries = modules["homeassistant.config_entries"]
    modules["homeassistant.config_entries"].ConfigFlow = FlowStandIn
    modules["homeassistant.config_entries"].OptionsFlow = OptionsFlowStandIn
    modules["homeassistant.config_entries"].ConfigFlowResult = dict
    modules["homeassistant.const"].CONF_HOST = "host"
    modules["homeassistant.helpers.selector"].SelectOptionDict = SelectOptionDict
    modules["homeassistant.helpers.selector"].SelectSelector = Selector
    modules["homeassistant.helpers.selector"].SelectSelectorConfig = Selector
    modules["homeassistant.helpers.selector"].SelectSelectorMode = SimpleNamespace(DROPDOWN="dropdown")
    modules["homeassistant.helpers.service_info.zeroconf"].ZeroconfServiceInfo = SimpleNamespace
    for key, value in {
        "CONF_IDENTITY": "identity", "CONF_NAME": "name", "DOMAIN": "arylic_lp10",
        "CONF_ARTWORK_PROXY_HOST": "artwork_proxy_host", "CONF_PROFILE": "profile",
        "DEFAULT_PROFILE": "detailed_with_repair", "PROFILE_DETAILED": "detailed_with_repair",
        "PROFILE_LITE": "lite", "configured_profile": lambda entry: entry.data.get("profile", "detailed_with_repair"),
    }.items():
        setattr(modules[f"{PACKAGE}.const"], key, value)
    with patch.dict(sys.modules, modules):
        loaded = []
        for name in ("protocol", "config_flow"):
            spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", COMPONENT / f"{name}.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            loaded.append(module)
        return loaded


protocol, config_flow = load_modules()
IDENTITY = "a1b2c3d4e5f6"
INFO = {"host": "192.168.50.10", "name": "Test Office", "identity": IDENTITY}


def make_flow():
    flow = config_flow.LP10ConfigFlow()
    flow.context = {}
    flow.unique_id = None
    flow.entries = []
    flow.other_flow_ids = set()

    async def executor(func, *args):
        return func(*args)

    flow.hass = SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=executor))
    return flow


def advertisement(**overrides):
    return SimpleNamespace(**{
        "host": "192.168.50.10", "name": "Test Office._airplay._tcp.local.",
        "properties": {"model": "LP10", "deviceid": "00:E0:3C:10:04:5A"}, **overrides,
    })


class DiscoveryFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsolicited_discovery_and_form_refresh_are_entirely_passive(self):
        flow = make_flow()
        with patch.object(config_flow, "identify", side_effect=AssertionError("identity probe")), \
             patch.object(protocol.socket, "socket", side_effect=AssertionError("network probe")), \
             patch.object(protocol.socket, "create_connection", side_effect=AssertionError("TCP probe")):
            result = await flow.async_step_zeroconf(advertisement())
            self.assertEqual(result["step_id"], "discovery_confirm")
            await flow.async_step_discovery_confirm()
        flow.hass.async_add_executor_job.assert_not_called()
        self.assertNotIn("identity", flow._discovered)
        self.assertNotEqual(flow.unique_id, "00e03c10045a")

    async def test_same_host_configured_aborts_without_probe(self):
        flow = make_flow()
        flow.entries = [SimpleNamespace(unique_id=IDENTITY, data={"host": INFO["host"]})]
        result = await flow.async_step_zeroconf(advertisement())
        self.assertEqual(result["reason"], "already_configured")
        flow.hass.async_add_executor_job.assert_not_called()

    async def test_untrusted_or_missing_advertisement_fields_are_rejected(self):
        for changes in (
            {"properties": {}}, {"properties": {"model": None, "am": 123}},
            {"properties": {"model": "OTHER"}}, {"host": "8.8.8.8"},
            {"name": ""}, {"name": None},
        ):
            flow = make_flow()
            with self.subTest(changes=changes):
                result = await flow.async_step_zeroconf(advertisement(**changes))
                self.assertEqual(result["type"], "abort")
                flow.hass.async_add_executor_job.assert_not_called()

    async def test_bytes_model_and_missing_deviceid_are_supported(self):
        flow = make_flow()
        result = await flow.async_step_zeroconf(advertisement(properties={"am": b"lp10"}))
        self.assertEqual(result["type"], "form")
        flow.hass.async_add_executor_job.assert_not_called()

    async def test_repeated_service_is_deduplicated_before_confirmation(self):
        flow = make_flow()
        flow.other_flow_ids.add("zeroconf:test office._airplay._tcp.local.")
        with self.assertRaisesRegex(AbortFlow, "already_in_progress"):
            await flow.async_step_zeroconf(advertisement())
        flow.hass.async_add_executor_job.assert_not_called()

    async def test_ignored_advertisement_is_not_probed(self):
        flow = make_flow()
        flow.entries = [SimpleNamespace(
            unique_id="zeroconf:test office._airplay._tcp.local.", data={})]
        with self.assertRaisesRegex(AbortFlow, "already_configured"):
            await flow.async_step_zeroconf(advertisement())
        flow.hass.async_add_executor_job.assert_not_called()

    async def test_explicit_confirmation_preserves_legacy_identity(self):
        flow = make_flow()
        await flow.async_step_zeroconf(advertisement())
        with patch.object(config_flow, "identify", return_value=INFO) as identify:
            result = await flow.async_step_discovery_confirm({})
        identify.assert_called_once_with(INFO["host"])
        self.assertEqual(result["data"]["identity"], IDENTITY)
        self.assertEqual(flow.unique_id, IDENTITY)

    async def test_changed_host_updates_only_after_verified_confirmation(self):
        flow = make_flow()
        entry = SimpleNamespace(unique_id=IDENTITY, data={"host": "192.168.50.11"})
        flow.entries = [entry]
        await flow.async_step_zeroconf(advertisement())
        self.assertEqual(entry.data["host"], "192.168.50.11")
        with patch.object(config_flow, "identify", return_value=INFO), \
             self.assertRaisesRegex(AbortFlow, "already_configured"):
            await flow.async_step_discovery_confirm({})
        self.assertEqual(entry.data["host"], INFO["host"])
        self.assertTrue(entry.reloaded)

    async def test_confirmation_failure_can_be_retried(self):
        flow = make_flow()
        await flow.async_step_zeroconf(advertisement())
        with patch.object(config_flow, "identify", side_effect=protocol.LP10Error("no UDP reply")):
            result = await flow.async_step_discovery_confirm({})
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        with patch.object(config_flow, "identify", return_value=INFO):
            result = await flow.async_step_discovery_confirm({})
        self.assertEqual(result["type"], "create_entry")

    async def test_manual_setup_uses_verified_identity(self):
        flow = make_flow()
        with patch.object(config_flow, "identify", return_value=INFO):
            result = await flow.async_step_user({"host": INFO["host"], "profile": "lite"})
        self.assertEqual(result["data"]["identity"], IDENTITY)
        self.assertEqual(result["data"]["profile"], "lite")

    async def test_discovered_setup_persists_the_selected_profile(self):
        flow = make_flow()
        await flow.async_step_zeroconf(advertisement())
        with patch.object(config_flow, "identify", return_value=INFO):
            result = await flow.async_step_discovery_confirm({"profile": "lite"})
        self.assertEqual(result["data"]["profile"], "lite")

    async def test_reconfigure_still_rejects_different_device(self):
        flow = make_flow()
        flow.entries = [SimpleNamespace(unique_id="112233445566", data={"host": "192.168.50.10"})]
        with patch.object(config_flow, "identify", return_value=INFO), \
             self.assertRaisesRegex(AbortFlow, "wrong_device"):
            await flow.async_step_reconfigure({"host": INFO["host"]})


class IdentityTests(unittest.TestCase):
    def test_explicit_identity_uses_only_bounded_unicast_udp(self):
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.getsockname.return_value = ("0.0.0.0", 45678)
        sock.recvfrom.return_value = (
            b"CAST_MODEL: LP10\r\nUSN: a1b2c3d4e5f6\r\nDEVICENAME: Office\r\n",
            (INFO["host"], 1800),
        )
        with patch.object(protocol.socket, "socket", return_value=sock) as create_socket, \
             patch.object(protocol.socket, "create_connection", side_effect=AssertionError("TCP probe")):
            info = protocol.identify(INFO["host"])
        create_socket.assert_called_once_with(protocol.socket.AF_INET, protocol.socket.SOCK_DGRAM)
        sock.settimeout.assert_called_once_with(0.5)
        self.assertEqual(sock.sendto.call_args.args[1], (INFO["host"], 1800))
        self.assertEqual(info["identity"], IDENTITY)

    def test_identity_rejects_wrong_peer_and_wrong_model(self):
        for payload, peer in (
            (b"CAST_MODEL: LP10\r\nUSN: a1b2c3d4e5f6", ("192.168.50.11", 1800)),
            (b"CAST_MODEL: OTHER\r\nUSN: a1b2c3d4e5f6", (INFO["host"], 1800)),
        ):
            sock = MagicMock()
            sock.__enter__.return_value = sock
            sock.getsockname.return_value = ("0.0.0.0", 45678)
            sock.recvfrom.return_value = (payload, peer)
            with self.subTest(peer=peer, payload=payload), \
                 patch.object(protocol.socket, "socket", return_value=sock), \
                 self.assertRaises(protocol.LP10Error):
                protocol.identify(INFO["host"])

    def test_identity_verification_does_not_create_control_client(self):
        with patch.object(protocol, "discovery_metadata", return_value={
            "cast_model": "LP10", "usn": IDENTITY, "devicename": INFO["name"],
        }), patch.object(protocol, "LP10Client", side_effect=AssertionError("control client")):
            info = protocol.identify(INFO["host"])
        self.assertEqual(info["identity"], IDENTITY)

    def test_malformed_or_missing_identity_never_creates_entry(self):
        for usn in ("", "xyz", "12345678901z", "1234567890123"):
            with self.subTest(usn=usn), patch.object(protocol, "discovery_metadata", return_value={
                "cast_model": "LP10", "usn": usn,
            }), self.assertRaises(protocol.LP10Error):
                protocol.identify(INFO["host"])


if __name__ == "__main__":
    unittest.main()
