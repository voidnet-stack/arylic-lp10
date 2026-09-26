# Before a tagged release

- Confirm the release commit and integration manifest carry the intended
  version, and update the changelog.
- Review the repair profile's bundled factory credential decision and trusted
  LAN warning.
- Run the offline checks and validate the HACS download on the intended Home
  Assistant versions. The initial public candidate was checked on Core
  `2026.9.3`; a supported minimum Core version has not yet been established.
- Keep private packet captures, device logs, Home Assistant backups, and
  per-home player registries out of this repository.
- Create a GitHub Release from the tested commit and tag, mark release
  candidates as prereleases, and confirm HACS offers the named release.
