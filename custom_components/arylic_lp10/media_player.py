"""Home Assistant media player for the Arylic LP10."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any
from urllib.parse import urljoin, urlparse

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_ARTWORK_PROXY_HOST, SOURCE_NAMES
from .entity import LP10Entity
from .protocol import LP10Error

FEATURES = (
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.SELECT_SOURCE
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([LP10MediaPlayer(entry.runtime_data, entry)])


def _artwork_url(player_host: str, raw: str, artwork_host: str | None) -> str | None:
    """Accept player artwork or Music Assistant's image proxy on one allowed host."""
    raw = raw.strip()
    if not raw or any(ord(char) < 32 for char in raw) or "\\" in raw:
        return None
    parsed = urlparse(raw)
    if not parsed.scheme and not parsed.netloc:
        raw = urljoin(f"http://{player_host}/", raw)
        parsed = urlparse(raw)
    try:
        if parsed.username or parsed.password or parsed.fragment:
            return None
        if parsed.scheme == "http" and parsed.hostname == player_host and parsed.port in (None, 80):
            return raw
        if (
            artwork_host and parsed.scheme in {"http", "https"}
            and parsed.hostname == artwork_host and parsed.port == 8097
            and parsed.path.startswith("/imageproxy/")
        ):
            return raw
    except ValueError:
        return None
    return None


class LP10MediaPlayer(LP10Entity, MediaPlayerEntity):
    """A player with transport, volume, source and read-only track position."""

    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    # SEEK is intentionally not advertised.
    _attr_source_list = list(SOURCE_NAMES.values())
    _attr_media_content_type = MediaType.MUSIC
    _attr_media_image_remotely_accessible = False

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "media_player")
        self._attr_supported_features = FEATURES
        self._artwork_cache_identity: str | None = None
        self._artwork_cache: tuple[bytes | None, str | None] | None = None
        self._artwork_cache_lock = asyncio.Lock()
        if coordinator.metadata_enabled:
            self._attr_supported_features |= (
                MediaPlayerEntityFeature.PLAY | MediaPlayerEntityFeature.PAUSE
            )

    @property
    def _status(self) -> dict[str, Any]:
        return self.coordinator.data.get("status", {})

    @property
    def _track(self) -> dict[str, Any]:
        return self.coordinator.data.get("now_playing", {})

    @property
    def state(self) -> MediaPlayerState | None:
        if not self.coordinator.metadata_enabled:
            return None
        track = self._track
        if track.get("playback_state_stale"):
            return None
        if not track.get("available") or track.get("idle"):
            return MediaPlayerState.IDLE
        return MediaPlayerState.PLAYING if track.get("playing") else MediaPlayerState.PAUSED

    @property
    def volume_level(self) -> float | None:
        volume = self._status.get("volume")
        return volume / 100 if isinstance(volume, int) else None

    @property
    def is_volume_muted(self) -> bool | None:
        return self._status.get("muted")

    @property
    def source(self) -> str | None:
        return SOURCE_NAMES.get(self._status.get("source"))

    @property
    def media_title(self) -> str | None:
        title = self._track.get("title") if not self._track.get("idle") else None
        return title or None

    @property
    def media_artist(self) -> str | None:
        artist = self._track.get("artist") if not self._track.get("idle") else None
        return artist or None

    @property
    def media_album_name(self) -> str | None:
        album = self._track.get("album") if not self._track.get("idle") else None
        return album or None

    @property
    def media_duration(self) -> int | None:
        value = self._track.get("duration_ms")
        return round(value / 1000) if isinstance(value, int) and value > 0 else None

    @property
    def media_position(self) -> int | None:
        value = self._track.get("position_ms")
        return round(value / 1000) if isinstance(value, int) and value >= 0 else None

    @property
    def media_position_updated_at(self):
        return self._track.get("position_updated_at")

    @property
    def media_image_url(self) -> str | None:
        if not self._track.get("available") or self._track.get("idle"):
            return None
        artwork_host = self._entry.options.get(CONF_ARTWORK_PROXY_HOST) or getattr(
            getattr(self.hass.config, "api", None), "local_ip", None
        )
        return _artwork_url(
            self.coordinator.client.host,
            self._track.get("cover_art_url", ""),
            artwork_host,
        )

    @property
    def _current_artwork_identity(self) -> str | None:
        """Identify artwork by both its URL and the track it represents."""
        if (url := self.media_image_url) is None:
            return None
        track = self._track
        identity = json.dumps(
            [url, track.get("title"), track.get("artist"), track.get("album")],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @property
    def media_image_hash(self) -> str | None:
        """Change HA's image URL when a track reuses the same artwork URL."""
        identity = self._current_artwork_identity
        return identity[:16] if identity else None

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        """Fetch once per track, bypassing HA's URL-only image cache."""
        url = self.media_image_url
        identity = self._current_artwork_identity
        if not url or not identity:
            self._artwork_cache_identity = None
            self._artwork_cache = None
            return None, None

        if self._artwork_cache_identity == identity and self._artwork_cache:
            return self._artwork_cache

        async with self._artwork_cache_lock:
            # Metadata may have changed while this request waited for the lock.
            url = self.media_image_url
            identity = self._current_artwork_identity
            if not url or not identity:
                self._artwork_cache_identity = None
                self._artwork_cache = None
                return None, None
            if self._artwork_cache_identity == identity and self._artwork_cache:
                return self._artwork_cache

            # Drop the previous track before fetching so a failed request cannot
            # leave its cover displayed under the new track's image URL.
            self._artwork_cache_identity = None
            self._artwork_cache = None
            image = await self._async_fetch_image(url)
            if image[0] is not None:
                self._artwork_cache_identity = identity
                self._artwork_cache = image
            return image

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        track = self._track
        attributes = super().extra_state_attributes.copy()
        attributes.update({
            key: track[key] for key in ("sample_rate", "bit_depth", "mime")
            if track.get(key) not in (None, "")
        })
        attributes.update({
            key: track[key] for key in (
                "metadata_status", "metadata_last_success", "metadata_failures",
                "playback_state_stale", "playback_state_source", "metadata_play_state",
                "device_position_ms", "position_correction_ms",
            ) if key in track
        })
        if track.get("metadata_status") == "track_data" and not track.get("idle"):
            label = " | ".join(value for value in (self.media_artist, self.media_title) if value)
            if label:
                attributes["now_playing_label"] = label
        return attributes

    async def _send(self, method, *args) -> None:
        try:
            await self.hass.async_add_executor_job(method, *args)
        except LP10Error as exc:
            raise HomeAssistantError(str(exc)) from exc
        self.coordinator.request_status_refresh()
        await self.coordinator.async_request_refresh()

    async def async_set_volume_level(self, volume: float) -> None:
        await self._send(self.coordinator.client.set_volume, round(max(0, min(1, volume)) * 100))

    async def async_mute_volume(self, mute: bool) -> None:
        await self._send(self.coordinator.client.set_mute, mute)

    async def async_select_source(self, source: str) -> None:
        reverse = {name: code for code, name in SOURCE_NAMES.items()}
        if source not in reverse:
            raise HomeAssistantError("Unknown LP10 input")
        await self._send(self.coordinator.client.set_source, reverse[source])

    async def async_media_play(self) -> None:
        state = self.state
        if state is None:
            raise HomeAssistantError("Playback state is unknown while LP10 metadata is unavailable")
        if state != MediaPlayerState.PLAYING:
            await self._send(self.coordinator.client.playback, "toggle")

    async def async_media_pause(self) -> None:
        state = self.state
        if state is None:
            raise HomeAssistantError("Playback state is unknown while LP10 metadata is unavailable")
        if state == MediaPlayerState.PLAYING:
            await self._send(self.coordinator.client.playback, "toggle")

    async def async_media_play_pause(self) -> None:
        await self._send(self.coordinator.client.playback, "toggle")

    async def async_media_next_track(self) -> None:
        await self._send(self.coordinator.client.playback, "next")

    async def async_media_previous_track(self) -> None:
        await self._send(self.coordinator.client.playback, "previous")
