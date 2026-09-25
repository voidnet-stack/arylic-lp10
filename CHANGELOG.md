# Changelog

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
