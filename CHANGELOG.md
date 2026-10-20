# Changelog

## Unreleased

- Now-playing metadata (title, artist, album, play state, position/duration) is
  now read over the control port (TCP 2018), the same port used for controls.
  ADB (TCP 5555) is no longer required for any playback feature, so firmware
  that ships without `adbd` (e.g. `AR241CP_8747.*`) is fully supported.
- The coordinator no longer makes ADB reads on its default path; the SSH
  "Apply ADB fix" repair is retained only as an optional legacy tool.
- Added control-port metadata parsers and tests (`parse_now_playing`,
  `parse_elapsed`, `LP10Client.query_now_playing`).
- Added best-effort album art via the player's UPnP renderer (`upnp.py`). It is
  fetched once per track and populates only for UPnP/DLNA-pushed playback (e.g.
  Music Assistant); the player's own NET/streaming sources expose no renderer
  metadata, so no art is shown for them. Never fatal to a poll.

## 0.3.0rc15

- Added a Lite or Detailed-with-Repair choice during manual and discovered
  player setup, with the same choice available in player Options.
- Existing config entries without a stored profile continue using Detailed
  playback so upgrades preserve their previous feature set.
- Lite mode skips TCP/5555 metadata reads, hides metadata sensors, and does not
  create SSH repair entities.
- Removed temporary device-specific INFO logs that could include track names.
- Updated HACS metadata and documented observed AirPlay behavior and repair
  reboot limitations.

## 0.3.0rc14

- Removed redundant Add LP10 and Find players links from the HA sidebar.
