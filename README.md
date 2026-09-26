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
- No TCP/5555 metadata reads, media title, artist, album, artwork, or reported
  playback state.
- Provides volume, mute, source, next/previous, and a separate Play/Pause
  toggle.
- Does not create the SSH repair status or repair button entities.

### Detailed playback with SSH repair

- Adds track details, artwork when supplied, current playback state, and a
  metadata health sensor through TCP port 5555.
- Adds an SSH repair status sensor and an explicit **Apply ADB fix** button.
  The repair checks the binary against a supported version, creates backups,
  stages and verifies a candidate, and attempts rollback on failure. It does
  not run automatically.
- The device patch is stored under `/tmp`; it is lost on player reboot. A
  manufacturer firmware update can replace the binary, and a changed binary
  remains unsupported until it is checked.
- The bundled repair helper uses the known factory root credential by default
  and accepts the player's current SSH host key. Use the repair profile only on
  a trusted private LAN. The optional helper can execute commands as root on
  the player; the app verifies the executable before changing the running
  service.

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

Home Assistant must have a route to each LP10. TCP port 2018 supports controls;
UDP port 1800 verifies device identity. The Detailed profile also uses TCP
5555 for status and track information. Artwork is limited to player-hosted
paths and the explicitly permitted local Music Assistant image proxy; public
artwork URLs remain blocked.

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
