"""Low-rate, SSH-verified status for the optional adbd repair."""

from __future__ import annotations

from datetime import timedelta
import logging
import threading
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from ..const import DOMAIN
from .repair import Device, inspect_status

_LOGGER = logging.getLogger(__name__)
REPAIR_STATUS_INTERVAL = timedelta(minutes=5)


class LP10RepairCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Share one SSH repair snapshot between status sensor, button and panel."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        identity = entry.data["identity"]
        super().__init__(
            hass,
            _LOGGER,
            name=f"Arylic LP10 repair {identity}",
            update_interval=REPAIR_STATUS_INTERVAL,
        )
        self.entry = entry
        self.identity = identity
        self.client = entry.runtime_data.client
        self._applying = False

    @property
    def is_applying(self) -> bool:
        """Whether a repair action currently owns this player's coordinator."""
        return self._applying

    def _inspect(self) -> dict[str, Any]:
        host = self.client.host
        try:
            device = Device(
                host,
                prompt_password=False,
            )
        except Exception as exc:  # SSH/library errors become diagnostic state, not HA entity failure.
            return {
                "status": "ssh_unavailable",
                "binary_sha256": None,
                "supported_binary": None,
                "patched_process_verified": None,
                "tcp5555_status": "not_checked",
                "detail": str(exc)[:180],
            }

        try:
            result = inspect_status(device)
            result["detail"] = ""
            return result
        except Exception as exc:
            _LOGGER.debug("LP10 repair status check failed for %s: %s", self.identity, exc)
            return {
                "status": "ssh_unavailable",
                "binary_sha256": None,
                "supported_binary": None,
                "patched_process_verified": None,
                "tcp5555_status": "not_checked",
                "detail": str(exc)[:180],
            }
        finally:
            device.close()

    async def _async_update_data(self) -> dict[str, Any]:
        return await self.hass.async_add_executor_job(self._inspect)

    async def async_apply_fix(self) -> dict[str, Any]:
        """Revalidate and apply only when the exact stock binary is present."""
        if self._applying:
            raise RuntimeError("An LP10 ADB repair is already running for this player.")
        self._applying = True
        self.async_set_updated_data({
            **(self.data or {}), "status": "checking", "progress_phase": "checking",
            "progress_message": "Checking the player before starting the repair.",
            "in_progress": True,
        })
        try:
            await self.async_request_refresh()
            if not self.data or self.data.get("status") != "fix_available":
                raise RuntimeError(
                    "The repair is only available when SSH verifies the supported stock adbd binary."
                )
        except Exception as exc:
            self._applying = False
            self.async_set_updated_data({
                **(self.data or {}), "status": "repair_failed", "progress_phase": "failed",
                "progress_message": f"Repair did not start: {str(exc)[:180]}",
                "detail": str(exc)[:180], "in_progress": False,
            })
            raise
        backup_dir = self.hass.config.path(
            "arylic_lp10", "repair_backups", self.identity
        )
        progress_snapshot = dict(self.data or {})

        def report_progress(phase: str, message: str) -> None:
            delivered = threading.Event()
            data = {
                **progress_snapshot, "status": phase, "progress_phase": phase,
                "progress_message": message, "in_progress": True,
            }

            def publish() -> None:
                self.async_set_updated_data(data)
                delivered.set()

            self.hass.loop.call_soon_threadsafe(publish)
            delivered.wait(timeout=10)

        def apply() -> dict[str, Any]:
            device = Device(
                self.client.host,
                prompt_password=False,
            )
            try:
                from pathlib import Path

                from .repair import apply as apply_repair

                return apply_repair(
                    device, confirmed=True, backup_dir=Path(backup_dir),
                    progress=report_progress,
                )
            finally:
                device.close()

        try:
            result = await self.hass.async_add_executor_job(apply)
        except Exception as exc:
            self._applying = False
            failed = {
                **(self.data or {}), "status": "repair_failed", "progress_phase": "failed",
                "progress_message": f"Repair stopped: {str(exc)[:180]}",
                "detail": str(exc)[:180], "in_progress": False,
            }
            self.async_set_updated_data(failed)
            raise
        self._applying = False
        result.update({
            "progress_phase": "complete",
            "progress_message": "Fix applied. Patched adbd is running and TCP/5555 responded.",
            "in_progress": False,
        })
        self.async_set_updated_data(result)
        return result


def coordinator_for_entry(hass: HomeAssistant, entry: ConfigEntry) -> LP10RepairCoordinator:
    """Return the repair coordinator created during integration setup."""
    return hass.data[DOMAIN]["repair_coordinators"][entry.entry_id]
