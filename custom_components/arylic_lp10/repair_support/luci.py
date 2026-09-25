"""Read-only LUCI status over the LP10's LAN ADB daemon.

The LP10 firmware used for development exposes ADB without authentication.
Only fixed read commands are accepted here; no general shell API is exposed.
"""

from __future__ import annotations

import json
import re
import socket
import struct
import time
from typing import Any

class StatusUnavailable(Exception):
    """The optional diagnostic/now-playing channel is unavailable."""


def _code(value: bytes) -> int:
    return int.from_bytes(value, "little")


CNXN, AUTH, OPEN, OKAY, CLSE, WRTE = (
    _code(value) for value in (b"CNXN", b"AUTH", b"OPEN", b"OKAY", b"CLSE", b"WRTE")
)
MAX_RESPONSE = 256 * 1024
READS = {
    "LUCI_local -r 42", "LUCI_local -r 49", "LUCI_local -r 51",
    "LUCI_local -r 90", "LUCI_local -r 92", "cat /proc/uptime",
    "cat /proc/asound/card0/pcm1p/sub0/hw_params",
}


def _packet(code: int, arg0: int = 0, arg1: int = 0, payload: bytes = b"") -> bytes:
    return struct.pack(
        "<6I", code, arg0, arg1, len(payload), sum(payload) & 0xFFFFFFFF,
        code ^ 0xFFFFFFFF,
    ) + payload


def _exact(sock: socket.socket, count: int) -> bytes:
    output = bytearray()
    while len(output) < count:
        part = sock.recv(count - len(output))
        if not part:
            raise StatusUnavailable("ADB closed before returning the LP10 status")
        output.extend(part)
    return bytes(output)


def _read_packet(sock: socket.socket) -> tuple[int, int, bytes]:
    code, arg0, _, length, checksum, magic = struct.unpack("<6I", _exact(sock, 24))
    if magic != code ^ 0xFFFFFFFF or length > MAX_RESPONSE:
        raise StatusUnavailable("Invalid LP10 status packet")
    payload = _exact(sock, length) if length else b""
    if sum(payload) & 0xFFFFFFFF != checksum:
        raise StatusUnavailable("Damaged LP10 status packet")
    return code, arg0, payload


def read_register(host: str, command: str, timeout: float = 1.5) -> str:
    """Run one allowlisted read through a single short ADB connection."""
    if command not in READS:
        raise StatusUnavailable("Unsupported status read")
    deadline = time.monotonic() + timeout
    try:
        with socket.create_connection((host, 5555), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(_packet(CNXN, 0x01000001, 4096, b"host::features=cmd,shell_v2\0"))
            code, _, _ = _read_packet(sock)
            if code == AUTH:
                raise StatusUnavailable("LP10 status access requires authentication")
            if code != CNXN:
                raise StatusUnavailable("LP10 did not accept status access")
            sock.sendall(_packet(OPEN, 1, 0, f"shell:{command}\0".encode("ascii")))
            output = bytearray()
            remote_id = 0
            while time.monotonic() < deadline:
                sock.settimeout(max(0.05, deadline - time.monotonic()))
                code, arg0, payload = _read_packet(sock)
                if code == OKAY:
                    remote_id = arg0
                elif code == WRTE:
                    remote_id = arg0
                    output.extend(payload)
                    if len(output) > MAX_RESPONSE:
                        raise StatusUnavailable("LP10 status was too large")
                    sock.sendall(_packet(OKAY, 1, remote_id))
                elif code == CLSE:
                    sock.sendall(_packet(CLSE, 1, remote_id or arg0))
                    return output.decode("utf-8", errors="replace").strip("\x00\r\n ")
    except (OSError, struct.error) as exc:
        raise StatusUnavailable("Could not read LP10 status over ADB") from exc
    raise StatusUnavailable("LP10 status timed out")


def _json_record(text: str) -> dict[str, Any]:
    offset = text.find("Data:")
    if offset < 0:
        return {}
    offset = text.find("{", offset)
    if offset < 0:
        return {}
    try:
        record, _ = json.JSONDecoder().raw_decode(text[offset:])
    except json.JSONDecodeError:
        return {}
    return record if isinstance(record, dict) else {}


def _text(value: Any) -> str:
    return "".join(c for c in str(value or "") if c.isprintable())[:2048]


def _number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return None


def parse_now_playing(text: str) -> dict[str, Any]:
    raw = _json_record(text).get("Window CONTENTS")
    if not isinstance(raw, dict):
        return {"available": False}
    result = {
        "available": True,
        "title": _text(raw.get("TrackName")),
        "artist": _text(raw.get("Artist")),
        "album": _text(raw.get("Album")),
        "cover_art_url": _text(raw.get("CoverArtUrl")),
        "mime": _text(raw.get("Mime")),
        "sample_rate": _number(raw.get("SampleRate")),
        "bit_depth": _number(raw.get("BitDepth")),
        "duration_ms": _number(raw.get("TotalTime")),
        "current_source": _number(raw.get("Current Source")),
        "play_state": _number(raw.get("PlayState")),
        "can_previous": bool(raw.get("Prev", True)),
        "can_next": bool(raw.get("Next", True)),
    }
    result["playing"] = result["play_state"] == 0
    result["idle"] = not result["title"] and (result["duration_ms"] or 0) <= 0 \
        and (result["current_source"] or 0) == 0
    return result


def parse_playback_register(text: str) -> bool | None:
    """MID 51 reports 0 for playing and 2 for paused on the AirPlay source."""
    match = re.search(r"MID-Read:51\s+Data:\s*([02])(?:\s|$)", text)
    return match.group(1) == "0" if match else None


def read_now_playing(host: str) -> dict[str, Any]:
    response = read_register(host, "LUCI_local -r 42")
    result = parse_now_playing(response)
    # MID 42 can retain PlayState=0 after an AirPlay pause. The standalone
    # controller uses MID 51 for this source; other sources keep MID 42.
    if result.get("available") and not result.get("idle") and result.get("current_source") == 1:
        try:
            playing = parse_playback_register(read_register(host, "LUCI_local -r 51"))
        except StatusUnavailable:
            playing = None
        result["metadata_play_state"] = result.get("play_state")
        result["playing"] = playing
        result["play_state"] = 0 if playing is True else 2 if playing is False else None
        result["playback_state_source"] = "register_51" if playing is not None else "unknown"
    else:
        result["playback_state_source"] = "register_42"
    if not result.get("available") or result.get("idle"):
        return result
    if not result.get("sample_rate") or not result.get("bit_depth"):
        try:
            output = parse_output_format(read_register(
                host, "cat /proc/asound/card0/pcm1p/sub0/hw_params"
            ))
        except StatusUnavailable:
            output = {}
        for key in ("sample_rate", "bit_depth"):
            if not result.get(key) and output.get(key):
                result[key] = output[key]
                result[f"{key}_origin"] = "device output"
    if (result.get("duration_ms") or 0) > 0:
        try:
            result["position_ms"] = read_position(host)
        except StatusUnavailable:
            pass
    return result


def parse_output_format(text: str) -> dict[str, int]:
    """Read active DAC format only when the track does not report it."""
    result: dict[str, int] = {}
    rate = re.search(r"(?m)^rate:\s*(\d+)", text)
    if rate and 8000 <= int(rate.group(1)) <= 384000:
        result["sample_rate"] = int(rate.group(1))
    sample = re.search(r"(?m)^format:\s*[SU](\d+)(?:_|$)", text)
    if sample and 8 <= int(sample.group(1)) <= 64:
        result["bit_depth"] = int(sample.group(1))
    return result


def read_position(host: str) -> int | None:
    text = read_register(host, "LUCI_local -r 49")
    match = re.search(r"MID-Read:49\s+Data:\s*(\d+)(?:\s|$)", text)
    value = int(match.group(1)) if match else None
    return value if value is not None and 0 <= value <= 7 * 86400000 else None


def read_details(host: str) -> dict[str, Any]:
    """Read identity fields; missing fields remain unknown, never guessed."""
    details: dict[str, Any] = {}
    try:
        name = read_register(host, "LUCI_local -r 90")
        match = re.search(r"MID-Read:90\s+Data:(.*?)\s+Length:", name, re.S)
        if match and len(match.group(1).strip()) <= 128:
            details["name"] = _text(match.group(1).strip())
    except StatusUnavailable:
        pass
    try:
        raw = _json_record(read_register(host, "LUCI_local -r 92"))
    except StatusUnavailable:
        raw = {}
    versions = raw.get("versioninfo", {})
    if isinstance(versions, dict):
        details["software_version"] = _text(versions.get("devicefwversion"))[:80]
        details["mcu_version"] = _text(versions.get("mcuversion"))[:80]
    addresses = raw.get("macaddress", {})
    if isinstance(addresses, dict):
        for interface, key in (("eth0", "mac_address"), ("wlan0", "wifi_mac")):
            mac = _text(addresses.get(interface))
            if re.fullmatch(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}", mac):
                details[key] = mac.lower()
    serials = raw.get("serialnumber", {})
    if isinstance(serials, dict):
        serial = _text(serials.get("device_serialnumber"))[:80]
        if serial:
            details["serial_number"] = serial
    try:
        raw_uptime = read_register(host, "cat /proc/uptime")
        details["uptime_seconds"] = int(float(raw_uptime.split()[0]))
    except (StatusUnavailable, ValueError, IndexError):
        pass
    return {key: value for key, value in details.items() if value not in (None, "")}
