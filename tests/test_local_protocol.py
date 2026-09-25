"""Dependency-free tests for the LP10 wire protocol and status parsers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch


COMPONENT = Path(__file__).parents[1] / "custom_components" / "arylic_lp10"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, COMPONENT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


transport = load("transport")
protocol = load("protocol")
luci = load("luci")


class ProtocolTests(unittest.TestCase):
    def test_manifest_and_translation(self):
        manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["domain"], "arylic_lp10")
        self.assertEqual(manifest["zeroconf"][0]["properties"], {"model": "lp10"})
        json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
        json.loads((COMPONENT / "translations" / "en.json").read_text(encoding="utf-8"))

    def test_private_host_guard(self):
        self.assertEqual(protocol.private_ipv4(" 192.168.50.10 "), "192.168.50.10")
        for value in ("8.8.8.8", "127.0.0.1", "localhost", "::1"):
            with self.subTest(value=value), self.assertRaises(protocol.LP10Error):
                protocol.private_ipv4(value)

    def test_status_parsing_and_unknown_fields(self):
        replies = {
            "source": "SRC:NET;", "volume": "VOL:35;", "muted": "MUT:0;",
            "display_on": "LED:1;", "firmware_version": "VER:23-4ef47210-9;",
            "deep_bass": "VBS:1;", "balance": "BAL:-100;",
        }
        result = protocol.parse_status(replies)
        self.assertEqual(result["volume"], 35)
        self.assertEqual(result["balance"], -100)
        self.assertFalse(result["muted"])
        self.assertTrue(result["display_on"])
        self.assertEqual(result["firmware_version"], "23-4ef47210-9")
        with self.assertRaises(protocol.LP10Error):
            protocol.parse_status({"volume": "VOL:35;"})

    def test_identification_rejects_non_lp10_and_uses_stable_id(self):
        with patch.object(protocol, "discovery_metadata", return_value={
            "cast_model": "LP10", "usn": "a1b2c3d4e5f6", "devicename": "Test Office",
            "fwversion": "AR241CE_8530.23.2", "netmode": "ETH0",
        }), patch.object(protocol.LP10Client, "query_status") as query:
            info = protocol.identify("192.168.50.10")
        query.assert_not_called()
        self.assertEqual(info["identity"], "a1b2c3d4e5f6")
        self.assertEqual(info["connectivity"], "Ethernet")
        self.assertEqual(info["name"], "Test Office")

    def test_write_allowlist_and_bounds(self):
        client = protocol.LP10Client("192.168.50.10")
        with patch.object(client, "_write") as write:
            client.set_volume(30)
            client.set_mute(True)
            client.set_display(False)
            client.playback("previous")
            self.assertEqual([call.args[0] for call in write.call_args_list], [
                "VOL:30;", "MUT:1;", "LED:0;", "PRE;",
            ])
            with self.assertRaises(protocol.LP10Error):
                client.set_volume(101)
            with self.assertRaises(protocol.LP10Error):
                client.playback("seek")

    def test_custom_eq_profile_and_signed_gain_parsing(self):
        profiles = protocol.parse_custom_eq_profiles("CEQ:LST:0@HA-Band-Test;")
        self.assertEqual(profiles, {0: "HA-Band-Test"})
        self.assertEqual(protocol.parse_custom_eq_profiles("CEQ:LST:;"), {})
        parsed = protocol.parse_custom_eq_band("CEQ:FLT:0:0064,02D4,0A00;", 0)
        self.assertEqual(parsed, {
            "frequency_hz": 100, "frequency_code": "0064", "q_code": "02D4",
            "q_factor": 2.828, "gain_db": 10,
        })
        self.assertEqual(protocol.parse_custom_eq_band(
            "CEQ:FLT:1:03E8,0599,FE00;", 1
        )["gain_db"], -2)
        self.assertEqual(protocol.parse_custom_eq_band(
            "CEQ:FLT:2:01F4,0599,0080;", 2
        )["gain_db"], 0.5)
        with self.assertRaises(protocol.LP10Error):
            protocol.parse_custom_eq_band("CEQ:FLT:0:0000,0599,0000;", 0)

    def test_eq_write_commands_and_bounds(self):
        client = protocol.LP10Client("192.168.50.10")
        with patch.object(client, "_write") as write:
            client.set_custom_eq_band(0, 10, "0064", "02D4")
            self.assertEqual(write.call_args.args[0], "CEQ:FLT:0:0064,02d4,0a00;")
            client.set_eq_preset(10)
            self.assertEqual(write.call_args.args[0], "EQS:10;")
            with self.assertRaises(protocol.LP10Error):
                client.set_custom_eq_band(8, 0, "0064", "02D4")
            with self.assertRaises(protocol.LP10Error):
                client.set_custom_eq_band(0, 11, "0064", "02D4")

    def test_custom_eq_read_is_read_only_and_keeps_partial_bands(self):
        client = protocol.LP10Client("192.168.50.10")
        existing = {
            "profiles": {},
            "bands": [
                {"frequency_code": f"{frequency:04X}", "q_code": "02D4", "gain_db": -2}
                for frequency in (100, 250, 500, 1000)
            ] + [None] * 4,
        }
        with patch.object(client, "_read_custom_eq", return_value=existing), \
                patch.object(client, "_write") as write, \
                patch.object(client, "_write_sequence", create=True) as write_sequence:
            result = client.query_custom_eq()

        self.assertEqual(result, existing)
        self.assertEqual(sum(band is None for band in result["bands"]), 4)
        write.assert_not_called()
        write_sequence.assert_not_called()

    def test_exchange_ignores_stale_responses(self):
        class ReplySocket:
            def __init__(self):
                self.replies = [b"MID:0;", b"CEQ:LST:0@HA-Band-Test;"]

            def sendall(self, payload):
                self.sent = payload

            def settimeout(self, timeout):
                pass

            def recv(self, size):
                if self.replies:
                    return self.replies.pop(0)
                raise socket.timeout

        self.assertEqual(
            protocol._exchange(ReplySocket(), "CEQ:LST;"),
            "CEQ:LST:0@HA-Band-Test;",
        )

    def test_now_playing_and_position(self):
        payload = '{"Window CONTENTS":{"TrackName":"Song","Artist":"Artist",' \
            '"Album":"Album","Current Source":1,"PlayState":0,"TotalTime":240000}}'
        parsed = luci.parse_now_playing(f"MID-Read:42 Data:{payload} Length:132")
        self.assertEqual(parsed["title"], "Song")
        self.assertEqual(parsed["duration_ms"], 240000)
        self.assertTrue(parsed["playing"])
        with patch.object(luci, "read_register", return_value="MID-Read:49 Data:90000 Length:5"):
            self.assertEqual(luci.read_position("192.168.50.10"), 90000)

    def test_active_dac_format_fallback(self):
        self.assertEqual(
            luci.parse_output_format("access: MMAP_INTERLEAVED\nformat: S16_LE\nrate: 44100\n"),
            {"sample_rate": 44100, "bit_depth": 16},
        )

    def test_status_channel_is_read_only(self):
        with self.assertRaises(luci.StatusUnavailable):
            luci.read_register("192.168.50.10", "reboot")


if __name__ == "__main__":
    unittest.main()
