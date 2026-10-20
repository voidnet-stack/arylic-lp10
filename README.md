# Arylic LP10 for Home Assistant

A local Home Assistant integration for Arylic LP10 network audio players. It
provides an HA media player, device controls, an LP10 Control sidebar panel,
and an optional metadata and repair profile. It does not use a cloud service or
require a separate web server.

## Choose a profile

Choose a profile when adding each player. Existing entries from older versions
default to Detailed playback so an update preserves their current behavior. You
can change the profile later in that player's integration Options; Home
Assistant reloads that player entry to apply the change.

### Lite - controls only

- Controls use the LP10's TCP port 2018.
- No media title, artist, album, or reported playback state (controls only, by
  choice — not a technical limitation).
- Provides volume, mute, source, next/previous, and a separate Play/Pause
  toggle.
- Does not create the SSH repair status or repair button entities.

### Detailed playback

- Adds track details (title, artist, album), current playback state, playback
  position/duration, and a metadata health sensor.
- **All of this is read over the same control port (TCP 2018) as the controls —
  no ADB / TCP 5555 access is required.** Firmware that ships without `adbd`
  (e.g. `AR241CP_8747.*`) is fully supported.
- Album art is not carried on the control port. The integration fetches it
  best-effort from the player's UPnP renderer, which only reports art for
  UPnP/DLNA-pushed playback (for example Music Assistant's DLNA provider). For
  the player's own NET/streaming sources the renderer is empty, so no art is
  shown — all other now-playing fields still work.

### Optional legacy ADB repair

Older builds of this integration read metadata over ADB (TCP 5555) and shipped
an SSH **Apply ADB fix** button to re-enable `adbd` on firmware that had removed
it. Metadata no longer depends on ADB, so this repair is **no longer required**
and is retained only as an optional tool. If enabled it uses the known factory
root credential and accepts the player's current SSH host key; use it only on a
trusted private LAN.

The Lite and Detailed choices are profiles of one HACS integration and share
one maintained codebase. HACS manages one integration per repository, so the
profile selector is the supported way to choose between them without asking
users to add competing repositories.

The firmware-specific AOSP `remote_close` repair, its verified binary limits,
rollback behavior, and evidence boundary are documented in
[`docs/ADB-REPAIR.md`](docs/ADB-REPAIR.md).

## Installation

In HACS, open **Custom repositories** and add
`voidnet-stack/arylic-lp10` with the type **Integration**. Open **Arylic LP10**
in HACS and download it, then restart Home Assistant. Add each player through
**Settings → Devices & services** and choose its profile during setup.

## Network requirements

Home Assistant must have a route to each LP10. TCP port 2018 supports both
controls and now-playing metadata; UDP port 1800 verifies device identity. No
TCP/5555 (ADB) access is needed for any playback feature. The optional legacy
ADB repair, if used, is the only thing that touches TCP 5555 / SSH.

The LP10 services are unauthenticated on the local network. Keep the players
and Home Assistant on a trusted network; do not forward their control or status
ports to the internet.

## Known AirPlay behavior

On some Music Assistant AirPlay 2 starts, audio can be audible while the LP10
reports Idle without title or artwork. HA, LP10 Control, and GoControl showed
blank/Idle in the observed case; skipping to the next track restored metadata.

After a short pause, the LP10 may report Idle and clear track details. In the
observed pause check, audio stopped and resumed from the same audible point,
while HA and GoControl showed blank/Idle. Music Assistant releasing its
AirPlay session is a likely explanation; that teardown was not directly
captured. The integration follows the LP10's current report and does not retain
stale track details. Volume and basic controls remain available.

Playback position is read-only. Seeking is not enabled because a command to
move the LP10 playback position has not been verified.

These behaviors were observed on firmware `AR241CE_8530.23.2`; other firmware
variants have not been validated.

## Version

The repository currently contains `0.3.0rc15`, based on the owner-tested
`0.3.0rc14`. RC15 adds the profile selector for new and existing player
entries and removes temporary device-specific playback logs. The Detailed
profile is the default for entries created before the selector existed.

## Developer checks

The integration includes offline contract tests for protocol parsing, config
flow behavior, persistent transport, and lifecycle cleanup. From the repository
root, run:

```sh
python -m unittest discover -s tests -v
python -m compileall -q custom_components
```

Automated checks do not replace validation on the target Home Assistant
release and LP10 firmware. Before each release, review the public files for
private diagnostics and credentials, and validate the version on Home
Assistant. Publish a GitHub Release for a versioned HACS download; a Git tag
alone does not appear as a release in HACS.
