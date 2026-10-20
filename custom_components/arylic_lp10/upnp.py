"""Best-effort album-art lookup via the LP10's UPnP MediaRenderer.

The control port (TCP 2018) gives title/artist/album but no cover art. The
player also runs a UPnP AVTransport renderer that *does* carry an
``upnp:albumArtURI`` — but only for playback that was pushed over UPnP/DLNA
(for example Music Assistant's DLNA provider). For the player's own NET/
streaming sources the renderer reports an empty transport, so this returns no
art and the integration simply shows none.

Standard library only, bounded, and read-only. Intended for a trusted LAN.
"""

from __future__ import annotations

from html import unescape
import http.client
import re
import socket
import urllib.parse
import urllib.request

# The renderer's AVTransport control path is stable across the firmware seen;
# discovery still confirms it, but this is the fallback if the description
# cannot be parsed.
_FALLBACK_CONTROL_PATH = "/upnp/control/rendertransport1"
_AV_TRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"
_MEDIA_RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"
_MAX_BODY = 64 * 1024


def _ssdp_location(host: str, timeout: float) -> str | None:
    """Unicast SSDP M-SEARCH to the player; return its description LOCATION."""
    message = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST:239.255.255.250:1900\r\n"
        'MAN:"ssdp:discover"\r\n'
        "MX:1\r\n"
        f"ST:{_MEDIA_RENDERER}\r\n"
        "\r\n"
    ).encode("ascii")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(message, (host, 1900))
            deadline_tries = 4
            while deadline_tries > 0:
                deadline_tries -= 1
                data, peer = sock.recvfrom(4096)
                if peer[0] != host:
                    continue
                match = re.search(rb"(?im)^LOCATION:\s*(\S+)\s*$", data)
                if match:
                    return match.group(1).decode("ascii", "replace")
        except OSError:
            return None
    return None


def _http_get(url: str, timeout: float) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 (LAN, http)
        return response.read(_MAX_BODY).decode("utf-8", "replace")


def _control_url(location: str, timeout: float) -> str:
    """Resolve the AVTransport controlURL from the device description."""
    base = re.match(r"(https?://[^/]+)", location)
    origin = base.group(1) if base else ""
    try:
        description = _http_get(location, timeout)
    except OSError:
        return origin + _FALLBACK_CONTROL_PATH
    # Find the AVTransport service block and its controlURL.
    for service in re.findall(r"<service>(.*?)</service>", description, re.S):
        if _AV_TRANSPORT in service:
            path = re.search(r"<controlURL>\s*(.*?)\s*</controlURL>", service, re.S)
            if path and path.group(1):
                return urllib.parse.urljoin(location, path.group(1).strip())
    return origin + _FALLBACK_CONTROL_PATH


def _soap(control_url: str, action: str, timeout: float) -> str:
    body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{_AV_TRANSPORT}"><InstanceID>0</InstanceID></u:{action}>'
        "</s:Body></s:Envelope>"
    ).encode("ascii")
    request = urllib.request.Request(
        control_url, data=body,
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{_AV_TRANSPORT}#{action}"',
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read(_MAX_BODY).decode("utf-8", "replace")


def extract_album_art(soap_response: str) -> str:
    """Pull ``upnp:albumArtURI`` from a (doubly XML-escaped) DIDL metadata blob."""
    # The DIDL-Lite document is XML-escaped inside the SOAP response; one
    # unescape exposes the inner tags.
    didl = unescape(soap_response)
    match = re.search(r"<upnp:albumArtURI[^>]*>(.*?)</upnp:albumArtURI>", didl, re.S)
    if not match:
        return ""
    url = unescape(match.group(1).strip())
    # Keep it a plausible, bounded http(s) URL; the caller's allowlist decides
    # whether it is actually shown.
    if len(url) > 2048 or any(ord(c) < 32 for c in url):
        return ""
    return url if url.lower().startswith(("http://", "https://")) else ""


class UPnPArtwork:
    """Cache the renderer control URL and the art for the current track."""

    def __init__(self, host: str, *, timeout: float = 2.0) -> None:
        self.host = host
        self.timeout = timeout
        self._control_url: str | None = None
        self._track_key: tuple | None = None
        self._art = ""

    def reset(self) -> None:
        """Forget the renderer location and cached art (e.g. after a reboot)."""
        self._control_url = None
        self._track_key = None
        self._art = ""

    def _ensure_control_url(self) -> str | None:
        if self._control_url:
            return self._control_url
        location = _ssdp_location(self.host, self.timeout)
        if not location:
            return None
        self._control_url = _control_url(location, self.timeout)
        return self._control_url

    def artwork_for(self, track_key: tuple) -> str:
        """Return album art for ``track_key``, fetching only when it changes.

        Never raises: on any discovery/SOAP failure it forgets the renderer
        location (so the next call re-discovers) and returns the last known art,
        which the caller clears when the track itself changes.
        """
        if track_key == self._track_key:
            return self._art
        self._track_key = track_key
        try:
            control_url = self._ensure_control_url()
            if not control_url:
                self._art = ""
                return ""
            art = ""
            for action in ("GetPositionInfo", "GetMediaInfo"):
                art = extract_album_art(_soap(control_url, action, self.timeout))
                if art:
                    break
            self._art = art
        except (OSError, http.client.HTTPException):
            self._control_url = None  # force rediscovery next time
            self._art = ""
        return self._art
