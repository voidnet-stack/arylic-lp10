"""Constants for the Arylic LP10 integration."""

from datetime import timedelta

DOMAIN = "arylic_lp10"
CONF_IDENTITY = "identity"
CONF_NAME = "name"
CONF_ARTWORK_PROXY_HOST = "artwork_proxy_host"
CONF_PROFILE = "profile"
PROFILE_LITE = "lite"
PROFILE_DETAILED = "detailed_with_repair"
DEFAULT_PROFILE = PROFILE_DETAILED


def configured_profile(entry) -> str:
    """Read the per-player profile, defaulting old entries to full playback."""
    return entry.options.get(
        CONF_PROFILE, entry.data.get(CONF_PROFILE, DEFAULT_PROFILE)
    )


POLL_INTERVAL = timedelta(seconds=1)
STATUS_INTERVAL = 10.0
CUSTOM_EQ_INTERVAL = 60.0
METADATA_INTERVAL = 3.0
DETAILS_INTERVAL = 600.0
ADB_RETRY_INTERVAL = 60.0

SOURCE_NAMES = {
    "NET": "Network",
    "BT": "Bluetooth",
    "LINE-IN": "Line in",
    "USBPLAY": "USB",
}
