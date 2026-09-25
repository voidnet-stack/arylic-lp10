"""Small, bounded client for the LP10's LAN control and discovery protocols.

Only verified day-to-day commands are available. The device has no
authentication on these LAN ports, so this integration is for trusted LANs.
"""

from __future__ import annotations

from contextlib import contextmanager
import http.client
import ipaddress
import re
import socket
import time
from typing import Any

if __package__:
    from .transport import PersistentConnection
else:  # Dependency-free source-loader tests/tools.
    from transport import PersistentConnection


EQ_FREQUENCIES = (125, 250, 500, 1000, 2000, 4000, 8000, 16000)


class LP10Error(Exception):
    """The device rejected a request or could not be reached."""


def private_ipv4(value: str) -> str:
    """Accept a literal private IPv4 address, not a hostname or public IP."""
    try:
        address = ipaddress.IPv4Address(value.strip())
    except (ipaddress.AddressValueError, AttributeError) as exc:
        raise LP10Error("Enter a private IPv4 address") from exc
    if not any(address in network for network in (
        ipaddress.IPv4Network("10.0.0.0/8"),
        ipaddress.IPv4Network("172.16.0.0/12"),
        ipaddress.IPv4Network("192.168.0.0/16"),
    )):
        raise LP10Error("The player must have a private LAN IPv4 address")
    return str(address)


def _receive(sock: socket.socket, timeout: float = 0.35) -> str:
    sock.settimeout(timeout)
    payload = bytearray()
    try:
        while len(payload) < 4096:
            chunk = sock.recv(1024)
            if not chunk:
                break
            payload.extend(chunk)
            if b";" in payload:
                break
    except socket.timeout:
        pass
    return payload.decode("ascii", errors="replace").strip("\x00\r\n ")


def _exchange(sock: socket.socket, command: str, timeout: float = 0.35) -> str:
    """Send a query and skip stale/unsolicited replies until its response arrives."""
    sock.sendall(command.encode("ascii"))
    request = command.rstrip(";")
    expected = f"{request}:" if request else None
    if expected is None:
        return _receive(sock, timeout)
    deadline = time.monotonic() + timeout
    last_reply = ""
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        reply = _receive(sock, remaining)
        if expected in reply:
            return reply
        if not reply:
            break
        last_reply = reply
    return last_reply


def _field(reply: str, code: str) -> str | None:
    marker = f"{code}:"
    if marker not in reply:
        return None
    return reply.split(marker, 1)[1].split(";", 1)[0]


def _integer(value: str | None, minimum: int, maximum: int) -> int | None:
    try:
        number = int(value) if value is not None else None
    except ValueError:
        return None
    return number if number is not None and minimum <= number <= maximum else None


def parse_status(replies: dict[str, str]) -> dict[str, Any]:
    """Normalize known responses; ignore malformed and unknown values."""
    codes = {
        "source": "SRC", "volume": "VOL", "muted": "MUT",
        "treble": "TRE", "mid": "MID", "bass": "BAS",
        "balance": "BAL", "max_volume": "MXV",
        "deep_bass": "VBS", "deep_bass_intensity": "VBI",
        "eq_preset": "EQS", "display_on": "LED", "firmware_version": "VER",
    }
    result: dict[str, Any] = {}
    for key, code in codes.items():
        raw = _field(replies.get(key, ""), code)
        if raw is None:
            continue
        if key in ("source", "firmware_version"):
            if raw and len(raw) <= 80 and raw.isprintable():
                result[key] = raw
        elif key in ("muted", "deep_bass", "display_on"):
            if raw in ("0", "1"):
                result[key] = raw == "1"
        else:
            limits = {
                "volume": (0, 100), "treble": (-10, 10),
                "mid": (-10, 10), "bass": (-10, 10),
                "balance": (-100, 100), "max_volume": (30, 100),
                "deep_bass_intensity": (0, 100), "eq_preset": (0, 10),
            }
            number = _integer(raw, *limits[key])
            if number is not None:
                result[key] = number
    if "source" not in result:
        raise LP10Error("The device did not return LP10 control status")
    return result


def parse_custom_eq_profiles(reply: str) -> dict[int, str]:
    """Parse the named custom-EQ slots reported by ``CEQ:LST;``."""
    payload = _field(reply, "CEQ:LST")
    if payload is None:
        raise LP10Error("The LP10 did not return its custom EQ profile list")
    profiles: dict[int, str] = {}
    for item in payload.split(","):
        if not item:
            continue
        match = re.fullmatch(r"(\d+)@([^;]{1,64})", item)
        if not match:
            continue
        profiles[int(match.group(1))] = match.group(2)
    return profiles


def parse_custom_eq_band(reply: str, index: int) -> dict[str, Any]:
    """Parse one ``CEQ:FLT`` reply, preserving its frequency and Q values."""
    if isinstance(index, bool) or not 0 <= index < len(EQ_FREQUENCIES):
        raise LP10Error("Unknown custom EQ band")
    payload = _field(reply, f"CEQ:FLT:{index}")
    if payload is None:
        raise LP10Error(f"The LP10 did not return custom EQ band {index + 1}")
    parts = payload.split(",")
    try:
        frequency = int(parts[0], 16)
        frequency_code = parts[0].upper().zfill(4)
        q_code = parts[1].upper().zfill(4)
        q_value = int(q_code, 16)
        raw_gain = int(parts[2], 16)
    except (IndexError, ValueError) as exc:
        raise LP10Error(f"Invalid custom EQ band {index + 1} response") from exc
    if not 20 <= frequency <= 20000 or q_value <= 0:
        raise LP10Error(f"Invalid custom EQ filter {index + 1}")
    signed_gain = raw_gain - 0x10000 if raw_gain & 0x8000 else raw_gain
    gain = signed_gain / 256
    if not -10 <= gain <= 10:
        raise LP10Error(f"Custom EQ band {index + 1} is outside the supported range")
    return {
        "frequency_hz": frequency,
        "frequency_code": frequency_code,
        "q_code": q_code,
        "q_factor": round(q_value / 256, 3),
        "gain_db": int(gain) if gain.is_integer() else round(gain, 2),
    }


def discovery_metadata(host: str, timeout: float = 0.5) -> dict[str, str]:
    """Read the LP10's unicast LSSDP identity packet on UDP 1800."""
    host = private_ipv4(host)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.bind(("0.0.0.0", 0))
        port = sock.getsockname()[1]
        request = (
            "M-SEARCH * HTTP/1.1\r\n"
            f"HOST: 0.0.0.0:{port}\r\n\r\n"
            "PROTOCOL: Version 1.0"
        ).encode("ascii")
        try:
            sock.sendto(request, (host, 1800))
            payload, peer = sock.recvfrom(8192)
        except OSError as exc:
            raise LP10Error("LP10 discovery did not answer") from exc
    if peer[0] != host:
        raise LP10Error("LP10 discovery answered from an unexpected address")
    result: dict[str, str] = {}
    for line in payload.decode("utf-8", errors="replace").replace("\r", "").split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip().lower()] = value.strip()[:128]
    if result.get("cast_model", "").upper() != "LP10":
        raise LP10Error("This address did not identify as an Arylic LP10")
    return result


def identify(host: str) -> dict[str, str]:
    """Verify identity over UDP only; setup owns the control connection.

    Call only for explicit setup/reconfiguration, not unsolicited discovery.
    Control availability is checked by the entry's initial coordinator refresh.
    """
    host = private_ipv4(host)
    metadata = discovery_metadata(host)
    usn = metadata.get("usn", "").lower().replace(":", "")
    if len(usn) != 12 or any(c not in "0123456789abcdef" for c in usn):
        raise LP10Error("LP10 discovery did not report a stable device ID")
    return {
        "host": host,
        "identity": usn,
        "name": metadata.get("devicename") or "Arylic LP10",
        "model": metadata.get("cast_model", "LP10"),
        "software_version": metadata.get("fwversion", ""),
        "connectivity": "Ethernet" if metadata.get("netmode", "").upper() == "ETH0" else "Wi-Fi",
    }


class LP10Client:
    """Synchronous transport; call from Home Assistant's executor."""

    CODES = {
        "source": "SRC", "volume": "VOL", "muted": "MUT",
        "treble": "TRE", "mid": "MID", "bass": "BAS",
        "balance": "BAL", "max_volume": "MXV", "deep_bass": "VBS",
        "deep_bass_intensity": "VBI", "eq_preset": "EQS",
        "display_on": "LED", "firmware_version": "VER",
    }

    def __init__(self, host: str) -> None:
        self.host = private_ipv4(host)
        self._connection = PersistentConnection(self.host)
        self._last_write = 0.0

    @contextmanager
    def _transaction(self):
        """Bound lock waits and translate transport errors for the coordinator."""
        try:
            with self._connection.transaction():
                yield
        except OSError as exc:
            raise LP10Error("The LP10 control connection is unavailable or busy") from exc

    def close(self) -> None:
        """Release the persistent control session on unload/shutdown."""
        self._connection.close()

    def query_status(self) -> dict[str, Any]:
        replies: dict[str, str] = {}
        with self._transaction():
            try:
                with self._connection.transaction():
                    for key, code in self.CODES.items():
                        replies[key] = self._connection.query(f"{code};")
            except OSError as exc:
                raise LP10Error("Could not reach the LP10 control port") from exc
        return parse_status(replies)

    def _write(self, command: str, cooldown: float) -> None:
        with self._transaction():
            delay = cooldown - (time.monotonic() - self._last_write)
            if delay > 0:
                time.sleep(delay)
            try:
                self._connection.write(command)
            except OSError as exc:
                raise LP10Error("Could not send the LP10 control") from exc
            self._last_write = time.monotonic()

    def set_volume(self, value: int) -> None:
        if isinstance(value, bool) or not 0 <= value <= 100:
            raise LP10Error("Volume must be between 0 and 100")
        self._write(f"VOL:{value};", 0.35)

    def set_mute(self, value: bool) -> None:
        self._write(f"MUT:{int(value)};", 0.35)

    def set_source(self, value: str) -> None:
        if value not in ("NET", "BT", "LINE-IN", "USBPLAY"):
            raise LP10Error("Unknown LP10 source")
        self._write(f"SRC:{value};", 0.75)

    def set_display(self, value: bool) -> None:
        self._write(f"LED:{int(value)};", 1.25)

    def set_deep_bass(self, value: bool) -> None:
        self._write(f"VBS:{int(value)};", 0.35)

    def set_sound_setting(self, setting: str, value: int) -> None:
        """Set one of the verified tone, balance, max-volume or bass controls."""
        bounds = {
            "treble": ("TRE", -10, 10),
            "mid": ("MID", -10, 10),
            "bass": ("BAS", -10, 10),
            "balance": ("BAL", -100, 100),
            "max_volume": ("MXV", 30, 100),
            "deep_bass_intensity": ("VBI", 0, 100),
        }
        if setting not in bounds or isinstance(value, bool):
            raise LP10Error("Unknown LP10 sound setting")
        code, minimum, maximum = bounds[setting]
        if not isinstance(value, int) or not minimum <= value <= maximum:
            raise LP10Error(f"{setting.replace('_', ' ').title()} must be from {minimum} to {maximum}")
        self._write(f"{code}:{value};", 0.35)

    def set_eq_preset(self, preset: int) -> None:
        if isinstance(preset, bool) or preset not in (0, 1, 2, 3, 4, 5, 10):
            raise LP10Error("Unknown LP10 EQ preset")
        self._write(f"EQS:{preset};", 0.45)

    def create_custom_eq(self, name: str, gains: list[int]) -> None:
        """Create the LP10's named custom slot, then apply its eight gains."""
        try:
            encoded_name = name.strip().encode("ascii").decode("ascii")
        except (AttributeError, UnicodeEncodeError) as exc:
            raise LP10Error("The custom EQ name must use plain ASCII characters") from exc
        if (
            not encoded_name or len(encoded_name) > 32
            or any(character in encoded_name for character in ":;\r\n")
            or not encoded_name.isprintable()
        ):
            raise LP10Error("Use a 1–32 character custom EQ name without ':' or ';'")
        if not isinstance(gains, list) or len(gains) != len(EQ_FREQUENCIES):
            raise LP10Error("Custom EQ needs exactly eight band values")
        if any(
            isinstance(gain, bool) or not isinstance(gain, int) or not -10 <= gain <= 10
            for gain in gains
        ):
            raise LP10Error("Each custom EQ band must be from -10 to 10 dB")

        commands = ["EQS:10;"]
        commands.extend(
            f"CEQ:FLT:{index}:{frequency:04x},0599,0000;"
            for index, frequency in enumerate(EQ_FREQUENCIES)
        )
        commands.append(f"CEQ:SAV:0:{encoded_name};")
        commands.extend(
            f"CEQ:FLT:{index}:{frequency:04x},0599,{(gain * 256) & 0xFFFF:04x};"
            for index, (frequency, gain) in enumerate(zip(EQ_FREQUENCIES, gains, strict=True))
        )
        # Keep this verified multi-command sequence atomic with respect to the
        # other integration entities sharing this player's persistent socket.
        with self._transaction():
            for command in commands:
                self._write(command, 0.45)

    def set_custom_eq_band(
        self, index: int, gain: int, frequency_code: str, q_code: str
    ) -> None:
        if isinstance(index, bool) or not 0 <= index < len(EQ_FREQUENCIES):
            raise LP10Error("Unknown custom EQ band")
        if isinstance(gain, bool) or not isinstance(gain, int) or not -10 <= gain <= 10:
            raise LP10Error("Custom EQ gain must be from -10 to 10 dB")
        if (
            not isinstance(frequency_code, str)
            or not re.fullmatch(r"[0-9A-Fa-f]{1,4}", frequency_code)
            or not isinstance(q_code, str)
            or not re.fullmatch(r"[0-9A-Fa-f]{1,4}", q_code)
        ):
            raise LP10Error("The LP10 did not report this band's filter settings")
        code = int(frequency_code, 16)
        q = int(q_code, 16)
        if not 20 <= code <= 20000 or q <= 0:
            raise LP10Error("Invalid custom EQ filter settings")
        encoded_gain = (gain * 256) & 0xFFFF
        self._write(f"CEQ:FLT:{index}:{code:04x},{q:04x},{encoded_gain:04x};", 0.45)

    def query_custom_eq(self) -> dict[str, Any]:
        """Read the active custom filters without changing device state."""
        return self._read_custom_eq()

    def _read_custom_eq(self) -> dict[str, Any]:
        """Read named profiles and each band, preserving individually valid bands."""
        with self._transaction():
            try:
                with self._connection.transaction():
                    profiles = parse_custom_eq_profiles(self._connection.query("CEQ:LST;"))
                    bands = []
                    for index in range(len(EQ_FREQUENCIES)):
                        try:
                            bands.append(
                                parse_custom_eq_band(self._connection.query(f"CEQ:FLT:{index};"), index)
                            )
                        except LP10Error:
                            bands.append(None)
            except OSError as exc:
                raise LP10Error("Could not read the LP10 custom EQ") from exc
        return {"profiles": profiles, "bands": bands}

    def playback(self, action: str) -> None:
        code = {"toggle": "POP", "next": "NXT", "previous": "PRE"}.get(action)
        if code is None:
            raise LP10Error("Unknown playback action")
        self._write(f"{code};", 0.45)

    def reboot(self) -> None:
        """Use only the confirmed web-management reboot endpoint."""
        with self._transaction():
            connection = http.client.HTTPConnection(self.host, 80, timeout=2)
            try:
                connection.request(
                    "POST", "/goform/SaveAllHandler", body="Settings=Reboot",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response = connection.getresponse()
                accepted = response.status in (301, 302, 303, 307, 308) and \
                    "/success.asp?page=reboot" in response.getheader("Location", "")
                response.close()
                if not accepted:
                    raise LP10Error("The LP10 did not confirm a restart")
            except (OSError, http.client.HTTPException) as exc:
                raise LP10Error("Could not reach the LP10 restart page") from exc
            finally:
                connection.close()
