"""Coordinated LP10 polling, with bounded optional diagnostics reads."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ADB_RETRY_INTERVAL, CUSTOM_EQ_INTERVAL, DETAILS_INTERVAL, METADATA_INTERVAL,
    POLL_INTERVAL, STATUS_INTERVAL,
)
from .luci import (
    StatusUnavailable, read_details, read_now_playing,
)
from .protocol import LP10Client, LP10Error, discovery_metadata

_LOGGER = logging.getLogger(__name__)
class LP10Coordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Share one device snapshot among all Home Assistant entities."""

    def __init__(
        self, hass: HomeAssistant, client: LP10Client, identity: str,
        *, metadata_enabled: bool = True,
    ) -> None:
        super().__init__(
            hass, _LOGGER, name=f"Arylic LP10 {identity}", update_interval=POLL_INTERVAL
        )
        self.client = client
        self.identity = identity
        self.metadata_enabled = metadata_enabled
        self._status: dict[str, Any] = {}
        self._status_at = 0.0
        self._status_retry_at = 0.0
        self._custom_eq: dict[str, Any] = {}
        self._custom_eq_at = 0.0
        self._details: dict[str, Any] = {}
        self._now_playing: dict[str, Any] = {
            "available": False,
            "metadata_status": "unknown" if metadata_enabled else "disabled",
            "metadata_last_success": None,
            "metadata_failures": 0,
            "playback_state_stale": True,
        }
        self._idle_reads = 0
        self._media_failures = 0
        self._metadata_at = 0.0
        self._details_at = 0.0
        self._details_retry_at = 0.0
        self._media_retry_at = 0.0
        self._uptime_at = 0.0
        self._position_track: tuple[Any, ...] | None = None
        self._position_offset_ms = 0
        self._position_hold_ms: int | None = None
        self._position_last_raw_ms: int | None = None

    def _reset_position(self) -> None:
        self._position_track = None
        self._position_offset_ms = 0
        self._position_hold_ms = None
        self._position_last_raw_ms = None

    def _normalize_position(self, fresh: dict[str, Any]) -> None:
        """Keep AirPlay progress fixed while MID 49 advances during a pause."""
        if not fresh.get("available") or fresh.get("idle") or fresh.get("current_source") != 1:
            self._reset_position()
            return
        raw = fresh.get("position_ms")
        playing = fresh.get("playing")
        if not isinstance(playing, bool):
            # MID 51 did not establish playback state. A moving MID 49 value
            # cannot be presented as an audible position in that case.
            fresh["device_position_ms"] = raw
            fresh["position_ms"] = None
            fresh["position_updated_at"] = None
            return
        if not isinstance(raw, int) or raw < 0:
            return
        track = tuple(fresh.get(key) for key in (
            "current_source", "title", "artist", "album", "duration_ms",
        ))
        if track != self._position_track or (
            self._position_last_raw_ms is not None
            and raw + 5000 < self._position_last_raw_ms
        ):
            self._reset_position()
            self._position_track = track
        if not playing:
            if self._position_hold_ms is None:
                self._position_hold_ms = max(0, raw - self._position_offset_ms)
            corrected = self._position_hold_ms
        elif self._position_hold_ms is not None:
            corrected = self._position_hold_ms
            self._position_offset_ms = max(0, raw - corrected)
            self._position_hold_ms = None
        else:
            corrected = max(0, raw - self._position_offset_ms)
        duration = fresh.get("duration_ms")
        if isinstance(duration, int) and duration > 0:
            corrected = min(corrected, duration)
        fresh["device_position_ms"] = raw
        fresh["position_correction_ms"] = self._position_offset_ms
        fresh["position_ms"] = corrected
        self._position_last_raw_ms = raw

    def request_status_refresh(self, *, refresh_custom_eq: bool = False) -> None:
        """Force status refresh; optionally refresh EQ after preset selection."""
        self._status_at = 0.0
        self._metadata_at = 0.0
        if refresh_custom_eq:
            self._custom_eq_at = 0.0

    def note_custom_eq_write(self, index: int, gain_db: int) -> None:
        """Publish one successful band write without an immediate device read."""
        bands = self._custom_eq.get("bands", [])
        if not 0 <= index < len(bands) or bands[index] is None:
            return

        updated_bands = list(bands)
        updated_bands[index] = {**updated_bands[index], "gain_db": gain_db}
        self._custom_eq = {**self._custom_eq, "bands": updated_bands}
        now = time.monotonic()
        # Let the LP10 settle before the next full status/equalizer query.
        self._status_at = now
        self._custom_eq_at = now

        if self.data is not None:
            updated_data = dict(self.data)
            updated_data["custom_eq"] = self._custom_eq
            self.async_set_updated_data(updated_data)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.hass.async_add_executor_job(self._poll)
        except LP10Error as exc:
            raise UpdateFailed(str(exc)) from exc

    def _poll(self) -> dict[str, Any]:
        now = time.monotonic()
        if now < self._status_retry_at:
            raise LP10Error("Waiting for the LP10 control port to recover")
        try:
            if not self._status or now >= self._status_at + STATUS_INTERVAL:
                self._status = self.client.query_status()
                self._status_at = now
                self._status_retry_at = 0.0
                if (
                    self._status.get("eq_preset") == 10
                    and now >= self._custom_eq_at + CUSTOM_EQ_INTERVAL
                ):
                    self._custom_eq_at = now
                    try:
                        self._custom_eq = self.client.query_custom_eq()
                    except LP10Error as exc:
                        # EQ is optional telemetry; preserve core player availability.
                        _LOGGER.debug("Could not refresh custom EQ: %s", exc)
        except LP10Error:
            # After a reboot or power loss, re-read identity/uptime immediately
            # on recovery rather than extrapolating the old boot session.
            self._details_at = 0.0
            self._metadata_at = 0.0
            self._details_retry_at = 0.0
            self._media_retry_at = 0.0
            self._media_failures = 0
            self._idle_reads = 0
            self._reset_position()
            self._now_playing = {
                "available": False,
                "metadata_status": "unknown" if self.metadata_enabled else "disabled",
                "metadata_last_success": None,
                "metadata_failures": 0,
                "playback_state_stale": True,
            }
            self._uptime_at = 0.0
            self._details.pop("uptime_seconds", None)
            self._status_retry_at = time.monotonic() + 5.0
            raise
        status = self._status
        if now >= self._details_at + DETAILS_INTERVAL or not self._details:
            self._details_at = now
            try:
                discovery = discovery_metadata(self.client.host)
            except LP10Error:
                discovery = {}
            self._details.update({
                "name": discovery.get("devicename") or self._details.get("name"),
                "software_version": discovery.get("fwversion") or self._details.get("software_version"),
                "connectivity": (
                    "Ethernet" if discovery.get("netmode", "").upper() == "ETH0"
                    else "Wi-Fi" if discovery.get("netmode") else self._details.get("connectivity")
                ),
            })
            if self.metadata_enabled and now >= self._details_retry_at:
                live = read_details(self.client.host)
                if live:
                    self._details.update(live)
                    if "uptime_seconds" in live:
                        self._uptime_at = now
                if not live.get("mac_address"):
                    self._details_retry_at = now + ADB_RETRY_INTERVAL
                    self._details_at = now - DETAILS_INTERVAL + ADB_RETRY_INTERVAL

        if self.metadata_enabled and now >= self._metadata_at + METADATA_INTERVAL:
            self._metadata_at = now
            if now >= self._media_retry_at:
                try:
                    fresh = read_now_playing(self.client.host)
                    if not fresh.get("available"):
                        raise StatusUnavailable("The LP10 returned incomplete now-playing data")
                    held_idle = False
                    self._media_failures = 0
                    self._media_retry_at = 0.0
                    if fresh.get("idle") and self._now_playing.get("available") and not self._now_playing.get("idle"):
                        self._idle_reads += 1
                        if self._idle_reads < 2:
                            fresh = self._now_playing.copy()
                            held_idle = True
                    else:
                        self._idle_reads = 0
                    if not held_idle:
                        self._normalize_position(fresh)
                    if fresh.get("position_ms") is not None and not held_idle:
                        fresh["position_updated_at"] = datetime.now(timezone.utc)
                    fresh["metadata_last_success"] = datetime.now(timezone.utc).isoformat()
                    fresh["metadata_failures"] = 0
                    fresh["playback_state_stale"] = fresh.get("playing") is None
                    has_track_data = any(
                        fresh.get(key) for key in ("title", "artist", "album", "cover_art_url")
                    ) or (fresh.get("duration_ms") or 0) > 0
                    if has_track_data:
                        fresh["metadata_status"] = "track_data"
                    elif fresh.get("current_source") == 0 and fresh.get("idle"):
                        fresh["metadata_status"] = "idle"
                    elif fresh.get("current_source") is None:
                        fresh["metadata_status"] = "unknown"
                    else:
                        fresh["metadata_status"] = "no_track_data"
                    self._now_playing = fresh
                except StatusUnavailable:
                    self._media_failures += 1
                    self._now_playing = {
                        **self._now_playing,
                        "title": "",
                        "artist": "",
                        "album": "",
                        "cover_art_url": "",
                        "mime": "",
                        "sample_rate": None,
                        "bit_depth": None,
                        "duration_ms": None,
                        "position_ms": None,
                        "position_updated_at": None,
                        "device_position_ms": None,
                        "position_correction_ms": None,
                        "metadata_status": "unreachable",
                        "metadata_failures": self._media_failures,
                        "playback_state_stale": True,
                    }
                    self._media_retry_at = now + (
                        ADB_RETRY_INTERVAL if self._media_failures >= 2 else METADATA_INTERVAL
                    )
        details = {key: value for key, value in self._details.items() if value is not None}
        if "uptime_seconds" in details and self._uptime_at:
            details["uptime_seconds"] += int(now - self._uptime_at)
        details["ip_address"] = self.client.host
        details["firmware_version"] = status.get("firmware_version")
        if not details.get("mac_address") and len(self.identity) == 12:
            details["mac_address"] = ":".join(
                self.identity[index:index + 2] for index in range(0, 12, 2)
            )
        return {
            "status": status,
            "now_playing": self._now_playing.copy(),
            "details": details,
            "custom_eq": self._custom_eq.copy(),
        }
